"""Fade 전략 — 엔진 (WebSocket 실시간).

워치리스트(유저가 직접 add/remove)만 대상. CLOB WS 로 실시간 오더북 mid 를 받아
스파이크(뉴스로 인한 급등)를 즉시 감지하고 NO 로 페이드한다.

mid(=best_bid/ask 중간가) 기준 감지의 이점:
  - book 스냅샷으로 구독 즉시 전 종목 가격 확보(REST 폴링 불필요)
  - 실시간(2분 폴링과 달리 10분짜리 짧은 스파이크도 포착)
  - thin-liquidity 단일 체결 프린트를 자동 필터(프린트는 last_price 만 움직이고
    book mid 는 안 움직임 → 실거래 불가능한 허수 진입 방지)

흐름:
  1. included 워치리스트의 YES 토큰을 WS 구독 → mid 버퍼(_price_hist)에 시계열 축적
  2. WS 업데이트마다: 열린 포지션 있으면 되돌림/손절 체크, 없으면 스파이크 → 진입
  3. 타임아웃 청산은 시간 기반이라 주기 sweep 으로 처리
  4. 진입/청산 시 오라클 릴레이 호출. ORACLE_RELAY_URL 미설정이면 sim(로그만).
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import yaml
from sqlalchemy import select

from db.session import get_session
from db.models import PolymarketFadeWatch, PolymarketFadePosition
from features.strategy.polymarket._data import client as poly_client
from features.strategy.polymarket._data import ws_client as ws
from features.strategy.polymarket.fade import signal as fade_signal
from features.strategy.polymarket.fade import oracle_client
from features.notifications.telegram_service import TelegramService

log = logging.getLogger("polymarket.fade")

_CFG_PATH = Path(__file__).parent / "config.yaml"
_cfg: dict = {}

# 대시보드 실시간 표시용 (in-memory, 라우터와 같은 프로세스)
_live_status: dict[str, dict] = {}

# 진입 직렬화(전액 사이징 이중 지출 방지) — 동시에 여러 마켓이 스파이크나도 한 번에 하나만
_entry_lock = asyncio.Lock()

# WS mid 시계열 버퍼: yes_token_id -> [(ts, mid)] (lookback + 여유만큼만 유지)
_price_hist: dict[str, list[tuple[int, float]]] = {}
# yes_token_id -> watch (구독 갱신 시 재구성)
_yes_map: dict[str, PolymarketFadeWatch] = {}
# 같은 마켓 재진입 쿨다운: condition_id -> 쿨다운 해제 ts
_cooldown: dict[str, int] = {}


def get_live_status() -> dict[str, dict]:
    return _live_status


def _tg(msg: str) -> None:
    try:
        ok, err = TelegramService().send_message(msg)
        if not ok and "not configured" not in err:
            log.warning("[fade] 텔레그램 전송 실패: %s", err)
    except Exception as e:
        log.debug("[fade] 텔레그램 예외: %s", e)


def _load_cfg() -> dict:
    with open(_CFG_PATH) as f:
        return yaml.safe_load(f)


def _watchlist_included() -> list[PolymarketFadeWatch]:
    db = get_session()
    try:
        stmt = select(PolymarketFadeWatch).where(PolymarketFadeWatch.status == "included")
        return list(db.execute(stmt).scalars().all())
    finally:
        db.close()


def _any_open_position() -> bool:
    """열린 포지션이 하나라도 있으면 True — 전액 순차(한 번에 하나) 신규 진입 차단."""
    db = get_session()
    try:
        stmt = select(PolymarketFadePosition).where(PolymarketFadePosition.status == "open")
        return db.execute(stmt).scalars().first() is not None
    finally:
        db.close()


def _open_position(condition_id: str) -> PolymarketFadePosition | None:
    db = get_session()
    try:
        stmt = select(PolymarketFadePosition).where(
            PolymarketFadePosition.condition_id == condition_id,
            PolymarketFadePosition.status == "open",
        )
        return db.execute(stmt).scalars().first()
    finally:
        db.close()


def _open_positions() -> list[PolymarketFadePosition]:
    db = get_session()
    try:
        stmt = select(PolymarketFadePosition).where(PolymarketFadePosition.status == "open")
        return list(db.execute(stmt).scalars().all())
    finally:
        db.close()


# ── mid 버퍼 + 스파이크 계산 ─────────────────────────────────────────────────

def _mid_lookback_ago(hist: list[tuple[int, float]], now: int, lookback_s: int) -> float | None:
    """lookback_s 전(또는 그 직전) mid. 데이터 부족하면 None."""
    p0 = None
    for ts, m in hist:
        if ts <= now - lookback_s:
            p0 = m
        else:
            break
    return p0


def _record_live(condition_id: str, hist: list[tuple[int, float]], has_position: bool) -> None:
    if not hist:
        return
    now, px = hist[-1]
    lookback = _cfg.get("lookback_s", 600)
    p0 = _mid_lookback_ago(hist, now, lookback)
    if p0 is None:                       # 아직 lookback 만큼 안 쌓임 → 표시만(스파이크 판정 X)
        _live_status[condition_id] = {
            "last_scan_ts": now, "has_position": has_position,
            "p0": round(px, 4), "price": round(px, 4), "rel_pct": 0.0,
            "abs_change": 0.0, "spike_now": False, "ts": now,
        }
        return
    spike = (_cfg["p0_lo"] <= p0 <= _cfg["p0_hi"]) and px >= p0 * _cfg["spike_rel"] \
        and (px - p0) >= _cfg["spike_abs"]
    _live_status[condition_id] = {
        "last_scan_ts": now, "has_position": has_position,
        "p0": round(p0, 4), "price": round(px, 4),
        "rel_pct": round((px / p0 - 1) * 100, 1) if p0 else 0.0,
        "abs_change": round(px - p0, 4), "spike_now": spike, "ts": now,
    }


async def _on_update(token_id: str) -> None:
    """WS 콜백 — 구독 토큰의 mid 가 갱신될 때마다 호출."""
    watch = _yes_map.get(token_id)
    if watch is None:
        return
    level = ws.price_book.get(token_id)
    if level is None or level.mid is None:
        return
    mid = float(level.mid)
    now = int(time.time())

    hist = _price_hist.setdefault(token_id, [])
    hist.append((now, mid))
    cutoff = now - (_cfg.get("lookback_s", 600) + 300)   # lookback + 5분 여유
    if hist[0][0] < cutoff:
        i = 0
        while i < len(hist) and hist[i][0] < cutoff:
            i += 1
        del hist[:max(0, i - 1)]         # cutoff 직전 1개는 남겨 p0 계산 보장

    open_pos = _open_position(watch.condition_id)
    _record_live(watch.condition_id, hist, has_position=open_pos is not None)

    if open_pos is not None:
        await _check_exit_price(open_pos, mid)
        return

    # 신규 진입 판정
    lookback = _cfg.get("lookback_s", 600)
    p0 = _mid_lookback_ago(hist, now, lookback)
    if p0 is None:
        return
    if not (_cfg["p0_lo"] <= p0 <= _cfg["p0_hi"]):
        return
    if mid < p0 * _cfg["spike_rel"] or mid - p0 < _cfg["spike_abs"]:
        return
    # 쿨다운
    if now < _cooldown.get(watch.condition_id, 0):
        return

    market = {"condition_id": watch.condition_id, "question": watch.question or "",
              "yes_token_id": watch.yes_token_id, "no_token_id": watch.no_token_id}
    sig = fade_signal.build_signal(market, p0, mid, now, _cfg)
    async with _entry_lock:
        await _enter(sig)


# ── 진입 ─────────────────────────────────────────────────────────────────────

async def _enter(sig: fade_signal.FadeSignal) -> None:
    if _any_open_position():
        log.info("[fade] 이미 열린 포지션 있음 → 진입 스킵 %s", sig.condition_id[:12])
        return

    no_price = 1 - sig.entry_px
    size_usd = _cfg.get("order_size_usd", 1.0)
    if _cfg.get("order_size_mode", "fixed") == "full":
        bal = await oracle_client.fetch_balance()
        if bal is not None:
            size_usd = bal * _cfg.get("balance_buffer", 0.98)

    log.info(
        "[fade] SIGNAL 진입 NO | %s | p0=%.4f entry=%.4f no_px=%.4f size=$%.2f target=%.4f stop=%.4f",
        sig.question[:50], sig.p0, sig.entry_px, no_price, size_usd, sig.target_px, sig.stop_px,
    )
    _tg(
        f"📡 <b>[Polymarket Fade] 진입 신호 감지</b>\n\n"
        f"<b>{sig.question[:80]}</b>\n\n"
        f"p0: <code>{sig.p0:.4f}</code>  →  entry(YES): <code>{sig.entry_px:.4f}</code>\n"
        f"NO 매수가: <code>{no_price:.4f}</code>  |  size: <code>${size_usd:.2f}</code>\n"
        f"target: <code>{sig.target_px:.4f}</code>  |  stop: <code>{sig.stop_px:.4f}</code>"
    )
    result = await oracle_client.place_order(
        side="NO", action="buy", condition_id=sig.condition_id, question=sig.question,
        token_id=sig.no_token_id, price=no_price, size_usd=size_usd,
        reason="fade_entry_spike",
    )
    status = (result.get("status") or "").lower()
    if status in ("failed", "skipped", "relay_failed"):
        log.warning("[fade] 진입 주문 미체결 → 포지션 미기록: %s", result)
        return
    _open_new_position(sig, result)


def _open_new_position(sig: fade_signal.FadeSignal, order_result: dict) -> None:
    db = get_session()
    try:
        row = PolymarketFadePosition(
            condition_id=sig.condition_id, question=sig.question[:500],
            no_token_id=sig.no_token_id, p0=sig.p0, entry_px=sig.entry_px,
            target_px=sig.target_px, stop_px=sig.stop_px,
            entry_ts=int(time.time()), timeout_ts=sig.timeout_ts, status="open",
            shares=order_result.get("shares"), entry_usd=order_result.get("usd"),
            order_id=order_result.get("order_id") or None,
            order_status=(order_result.get("status") or "logged")[:16],
        )
        db.add(row)
        db.commit()
    except Exception as e:
        db.rollback()
        log.warning("[fade] 포지션 저장 실패: %s", e)
    finally:
        db.close()


# ── 청산 ─────────────────────────────────────────────────────────────────────

async def _check_exit_price(pos: PolymarketFadePosition, current_mid: float) -> None:
    now_ts = int(time.time())
    result = fade_signal.check_exit(current_mid, now_ts, pos)
    if result is None:
        return
    exit_px, reason = result
    await _close_position(pos, exit_px, reason)


async def _close_position(pos: PolymarketFadePosition, exit_px: float, reason: str) -> None:
    now_ts = int(time.time())
    ret_pct = round((pos.entry_px - exit_px) / (1 - pos.entry_px) * 100, 2)

    db = get_session()
    try:
        row = db.get(PolymarketFadePosition, pos.id)
        if not (row and row.status == "open"):
            return                       # 이미 다른 경로로 청산됨
        row.status = "closed"; row.exit_px = exit_px; row.exit_ts = now_ts
        row.exit_reason = reason; row.ret_pct = ret_pct
        db.commit()
    except Exception as e:
        db.rollback()
        log.warning("[fade] 포지션 청산 저장 실패: %s", e)
        return
    finally:
        db.close()

    _cooldown[pos.condition_id] = now_ts + int(_cfg.get("cooldown_hours", 12) * 3600)

    log.info("[fade] SIGNAL 청산 %s | %s | exit=%.4f ret=%.2f%% shares=%s",
             reason, pos.question[:50] if pos.question else pos.condition_id[:12],
             exit_px, ret_pct, pos.shares)
    pnl_e = "✅" if ret_pct >= 0 else "❌"
    _tg(
        f"🔔 <b>[Polymarket Fade] 청산 신호 감지</b>\n\n"
        f"<b>{pos.question[:80] if pos.question else pos.condition_id[:12]}</b>\n\n"
        f"사유: <code>{reason}</code>\n"
        f"entry(YES): <code>{pos.entry_px:.4f}</code>  →  exit: <code>{exit_px:.4f}</code>\n"
        f"PnL: {pnl_e} <code>{ret_pct:+.2f}%</code>"
    )
    await oracle_client.place_order(
        side="NO", action="sell", condition_id=pos.condition_id, question=pos.question or "",
        token_id=pos.no_token_id or "", price=1 - exit_px, size_usd=(pos.entry_usd or 1.0),
        size_shares=pos.shares, reason=f"fade_exit_{reason}",
    )


async def _timeout_sweep() -> None:
    """시간 기반 타임아웃 청산 — WS 업데이트가 없어도 만기 지난 포지션 강제 청산."""
    now = int(time.time())
    for pos in _open_positions():
        if pos.timeout_ts and now >= pos.timeout_ts:
            level = ws.price_book.get(_yes_token_of(pos.condition_id) or "")
            mid = float(level.mid) if level and level.mid is not None else pos.entry_px
            await _close_position(pos, mid, "타임아웃")


def _yes_token_of(condition_id: str) -> str | None:
    for tok, w in _yes_map.items():
        if w.condition_id == condition_id:
            return tok
    return None


# ── 구독 관리 + 시드 ─────────────────────────────────────────────────────────

async def _refresh_subscriptions(ws_client=None) -> bool:
    """included 워치리스트를 _yes_map 에 반영. 새 토큰은 REST 로 버퍼 시드(warm-up 제거).
    실제 WS 구독은 _ws_loop 가 _yes_map 변경을 감지해 재연결로 처리."""
    global _yes_map
    included = _watchlist_included()
    new_map = {w.yes_token_id: w for w in included if w.yes_token_id}
    added = set(new_map) - set(_yes_map)
    _yes_map = new_map
    for tok in added:
        await _seed_hist(tok)
    return bool(added)


async def _seed_hist(yes_token_id: str) -> None:
    """REST 1h 히스토리로 mid 버퍼 시드 — 재시작 직후 lookback 공백 방지."""
    try:
        pts = await poly_client.fetch_prices(yes_token_id, interval="1h")
    except Exception:
        return
    if pts:
        _price_hist[yes_token_id] = [(int(p["ts"]), float(p["price"])) for p in pts[-120:]]
        w = _yes_map.get(yes_token_id)
        if w:
            _record_live(w.condition_id, _price_hist[yes_token_id],
                         has_position=_open_position(w.condition_id) is not None)


async def _apply_book(token_id: str, mid: float) -> None:
    """WS 로 받은 mid 를 버퍼에 반영 + 감지(콜백 본체)."""
    ws.price_book.setdefault(token_id, ws.PriceLevel(token_id=token_id)).mid = mid
    await _on_update(token_id)


async def _ws_loop() -> None:
    """fade 전용 WS 연결 — 구독 토큰을 직접 관리(공유 client 타이밍 버그 회피).
    워치리스트가 바뀌면 재연결해 새 토큰을 구독한다.
    """
    import aiohttp, json
    while True:
        toks = list(_yes_map.keys())
        if not toks:
            await asyncio.sleep(10); continue
        subscribed = set(toks)
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.ws_connect(ws.WS_URL, heartbeat=30) as conn:
                    await conn.send_json({"assets_ids": toks, "type": "market"})
                    log.info("[fade] WS 연결 — 구독 %d종목", len(toks))
                    async for msg in conn:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            break
                        data = json.loads(msg.data)
                        for m in (data if isinstance(data, list) else [data]):
                            if not isinstance(m, dict):
                                continue
                            ev = m.get("event_type")
                            if ev == "book":
                                tid = m.get("asset_id")
                                buys, sells = m.get("buys", []), m.get("sells", [])
                                if tid and buys and sells:
                                    bb = max(float(b["price"]) for b in buys if b.get("price"))
                                    ba = min(float(s["price"]) for s in sells if s.get("price"))
                                    await _apply_book(tid, (bb + ba) / 2)
                            elif ev == "price_change":
                                for tid in set(ws._parse_price_change(m)):
                                    if tid in _yes_map:
                                        await _on_update(tid)
                        # 워치리스트 변경 감지 → 재연결
                        if set(_yes_map.keys()) != subscribed:
                            log.info("[fade] 워치리스트 변경 → WS 재구독")
                            break
        except Exception as e:
            log.warning("[fade] WS 오류: %s — 5s 후 재연결", e)
            await asyncio.sleep(5)


async def run(ws_client=None) -> None:
    global _cfg
    _cfg = _load_cfg()
    if not _cfg.get("enabled", True):
        log.debug("[fade] disabled — skipping")
        return

    await _refresh_subscriptions(ws_client)   # ws_client 무시, 시드만 사용
    log.warning("[fade] WS 엔진 시작 — 구독 %d종목", len(_yes_map))

    async def _sweep_loop():
        while True:
            try:
                await _refresh_subscriptions(ws_client)
                await _timeout_sweep()
            except Exception as e:
                log.warning("[fade] 주기 루프 오류: %s", e)
            await asyncio.sleep(_cfg.get("sweep_interval_sec", 30))

    # gather 로 묶어 _ws_loop 예외가 삼켜지지 않게
    await asyncio.gather(_ws_loop(), _sweep_loop(), return_exceptions=True)
