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
from features.strategy.polymarket.fade import screener
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
# 토큰당 마지막 처리 시각(throttle) + 열린 포지션 condition_id 캐시(hot path DB 제거)
_last_proc: dict[str, float] = {}
_open_cids: set[str] = set()
# 단발 급변 프린트 보류함 — 다음 틱이 같은 값을 확인해줘야 버퍼에 들어간다.
_pending_confirm: dict[str, float] = {}
_GLITCH_JUMP = 0.10          # 직전 대비 이만큼 튀면 확인 대기(10¢p)
# 구독 세대 — _yes_map 의 키가 바뀔 때마다 +1. WS 샤드들이 이 값을 보고 일제히 재구독한다.
_ws_generation: int = 0
# 스크리너 마지막 실행 시각/리포트 (대시보드 노출용)
_last_screen_ts: float = 0.0
_last_screen_report: dict = {}

# config.yaml 필수 키 — 부팅 시 하나라도 없으면 즉시 터진다(fail-fast, 하드코딩 fallback 없음).
_REQUIRED_CFG_KEYS = (
    "enabled", "sweep_interval_sec", "throttle_s",
    "ws_heartbeat_sec", "ws_reconnect_s", "ws_idle_sleep_s", "ws_shard_size",
    "screener",
    "seed_interval", "seed_points", "lookback_buffer_s",
    "lookback_s", "spike_rel", "spike_abs", "p0_lo", "p0_hi",
    "retrace_pct", "timeout_hours", "stop_loss_pct",
    "addon_enabled", "addon_max_count", "addon_min_rise_pct",
    "order_size_mode", "order_size_usd", "balance_buffer",
    "max_concurrent", "min_days_left", "glitch_filter",
    "replace_enabled", "replace_min_age_h", "replace_min_gain_ratio",
)


def _validate_cfg(cfg: dict) -> None:
    missing = [k for k in _REQUIRED_CFG_KEYS if k not in cfg]
    if missing:
        raise RuntimeError(f"[fade] config.yaml 필수 키 누락: {missing}")
    screener.validate_cfg(cfg["screener"])   # 중첩 섹션도 같은 fail-fast 규칙


def _refresh_open_cache() -> None:
    global _open_cids
    db = get_session()
    try:
        rows = db.execute(
            select(PolymarketFadePosition.condition_id)
            .where(PolymarketFadePosition.status == "open")
        ).scalars().all()
        _open_cids = set(rows)
    finally:
        db.close()


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
    lookback = _cfg["lookback_s"]
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
    """WS 콜백 — 구독 토큰의 mid 가 갱신될 때마다 호출.

    활발한 종목은 초당 수백 건 → DB 를 hot path 에서 제거(인메모리 _open_cids) +
    토큰당 throttle 로 이벤트 루프 포화 방지(대시보드 hang 방지).
    """
    watch = _yes_map.get(token_id)
    if watch is None:
        return
    level = ws.price_book.get(token_id)
    if level is None or level.mid is None:
        return
    mid = float(level.mid)
    now = time.time()

    # 토큰당 throttle — 초당 수백 업데이트를 최대 1회/_THROTTLE_S 로 제한(감지/DB skip)
    if now - _last_proc.get(token_id, 0.0) < _cfg["throttle_s"]:
        return
    _last_proc[token_id] = now
    now = int(now)

    hist = _price_hist.setdefault(token_id, [])
    # 가짜 프린트 방어 — 호가창이 비면 API/WS 가 중간값(0.50)을 흘리는 일이 있다.
    # 백테스트 실측: 5배 이상 점프 스파이크 359건 중 138건(38%)이 이 가짜였고,
    # 한 시각에 61개 마켓이 동시에 0.50 을 찍은 적도 있다. 직전 값에서 한 번에
    # 크게 튀는 단발 프린트는 다음 틱이 확인해줄 때까지 버퍼에 넣지 않는다.
    if _cfg["glitch_filter"] and hist:
        prev = hist[-1][1]
        if abs(mid - prev) >= _GLITCH_JUMP and _pending_confirm.get(token_id) != round(mid, 4):
            _pending_confirm[token_id] = round(mid, 4)   # 다음 틱에 같은 값이면 인정
            return
    _pending_confirm.pop(token_id, None)
    hist.append((now, mid))
    cutoff = now - (_cfg["lookback_s"] + _cfg["lookback_buffer_s"])
    if hist and hist[0][0] < cutoff:
        i = 0
        while i < len(hist) and hist[i][0] < cutoff:
            i += 1
        del hist[:max(0, i - 1)]

    has_pos = watch.condition_id in _open_cids
    _record_live(watch.condition_id, hist, has_position=has_pos)

    if has_pos:
        open_pos = _open_position(watch.condition_id)   # DB 조회는 포지션 보유 종목만(≤1)
        if open_pos is not None:
            await _check_exit_price(open_pos, mid)
            # 청산 안 됐으면(여전히 open) 피라미딩 add-on 판정.
            # full(전액) 모드는 자본을 최초 진입에 전부 투입 → add-on 여력이 없으므로 skip.
            if (watch.condition_id in _open_cids and _cfg["addon_enabled"]
                    and _cfg["order_size_mode"] != "full"):
                await _maybe_addon(watch, open_pos, hist, now, mid)
        return

    # 만기 임박 진입 금지 — 되돌아올 시간 자체가 없다.
    # (백테스트 실측: 만기 30일 이내 진입을 빼면 MDD p95 −18.2% → −12.2%)
    min_days = float(_cfg["min_days_left"])
    if min_days > 0 and watch.end_ts:
        if (watch.end_ts - now) / 86400.0 <= min_days:
            return

    # 신규 진입 판정
    lookback = _cfg["lookback_s"]
    p0 = _mid_lookback_ago(hist, now, lookback)
    if p0 is None:
        return
    if not (_cfg["p0_lo"] <= p0 <= _cfg["p0_hi"]):
        return
    if mid < p0 * _cfg["spike_rel"] or mid - p0 < _cfg["spike_abs"]:
        return

    # 슬롯 상한 — 스파이크 검증을 **통과한 뒤** 판정한다. 새 신호가 얼마나 좋은지
    #   알아야 기존 포지션과 비교해 갈아탈지 정할 수 있기 때문이다.
    #   full  : 자본을 한 포지션에 전부 투입하므로 사실상 1개.
    #   그 외 : max_concurrent 개. 게이트가 없으면 신호가 몰릴 때(실측: 같은 시각
    #           33건) 잔액이 바닥날 때까지 무제한 진입하고 이후 주문은 전부 실패한다.
    limit = 1 if _cfg["order_size_mode"] == "full" else int(_cfg["max_concurrent"])
    if len(_open_cids) >= limit:
        if not _cfg["replace_enabled"]:
            return
        if not await _free_slot_for(watch.condition_id, now, _upside(p0, mid)):
            return

    market = {"condition_id": watch.condition_id, "question": watch.question or "",
              "yes_token_id": watch.yes_token_id, "no_token_id": watch.no_token_id}
    sig = fade_signal.build_signal(market, p0, mid, now, _cfg)
    async with _entry_lock:              # 사이징 이중지출 방지(직렬화)
        await _enter(sig)


# ── 진입 ─────────────────────────────────────────────────────────────────────



def _upside(p0: float, px: float) -> float:
    """이 진입에서 되돌림 목표까지 갔을 때 먹을 수익률(NO 기준).

        목표가 = px − retrace·(px − p0)   →   수익 = retrace·(px − p0) / (1 − px)

    상방이 (1 − px) 로 나뉘는 게 핵심이다. 같은 폭이라도 진입가가 높을수록 더 먹는다.
    """
    if px >= 1.0:
        return 0.0
    return float(_cfg["retrace_pct"]) * (px - p0) / (1.0 - px)


def _remaining_upside(pos: PolymarketFadePosition, mid: float) -> float:
    """열린 포지션에 **아직 남은** 먹을 것. 이미 먹은 건 빼고 본다.

        남은 수익 = (현재가 − 목표가) / (1 − 진입가)

    이익 구간이라도 목표까지 얼마 안 남았으면 이 값이 작다 — 그때 더 좋은 신호가
    오면 갈아타는 게 맞다. '이익 중이면 유지' 규칙을 쓰지 않는 이유가 이것이다.
    """
    if pos.entry_px >= 1.0:
        return 0.0
    return max(0.0, (mid - float(pos.target_px)) / (1.0 - float(pos.entry_px)))


async def _free_slot_for(new_cid: str, now: int, new_upside: float) -> bool:
    """슬롯을 하나 비운다. 비웠으면 True.

    기준은 **남은 먹을 것 비교**다. 열린 포지션 중 남은 수익이 가장 적은 것을 골라,
    새 신호가 그보다 `replace_min_gain_ratio` 배 이상 좋으면 갈아탄다.
    이익 중이어도 판다 — 목표까지 얼마 안 남은 포지션이 슬롯을 잡고 있는 것보다
    더 크게 되돌릴 새 신호로 옮기는 게 회전 전략의 취지다.

    replace_min_age_h 는 진입/청산 반복만 막는 최소 장치다(스파이크가 몰릴 때
    같은 슬롯을 초 단위로 갈아치우는 걸 방지).
    """
    if new_cid in _open_cids:
        return False
    min_age_s = float(_cfg["replace_min_age_h"]) * 3600
    ratio = float(_cfg["replace_min_gain_ratio"])

    weakest = None
    for cid in list(_open_cids):
        pos = _open_position(cid)
        if pos is None:
            continue
        if now - int(pos.entry_ts or now) < min_age_s:
            continue
        yes_tok = _yes_token_of(cid)
        lvl = ws.price_book.get(yes_tok) if yes_tok else None
        if not (lvl and lvl.mid is not None):
            continue                                    # 가격을 모르면 판단 보류
        mid = float(lvl.mid)
        rem = _remaining_upside(pos, mid)
        if weakest is None or rem < weakest[0]:
            weakest = (rem, pos, mid)

    if weakest is None:
        return False
    rem, pos, mid = weakest
    if new_upside < rem * ratio:
        return False                                    # 새 신호가 더 낫지 않다

    pnl = (pos.entry_px - mid) / (1 - pos.entry_px)
    log.info("[fade] 슬롯 교체 — %s (보유 %.1fh · 평가 %+.1f%% · 남은먹을것 %.1f%%) "
             "→ 새 신호 %.1f%%",
             (pos.question or pos.condition_id)[:40],
             (now - int(pos.entry_ts or now)) / 3600, pnl * 100, rem * 100,
             new_upside * 100)
    await _close_position(pos, mid, "슬롯교체")
    return pos.condition_id not in _open_cids


def _yes_token_of(condition_id: str) -> str | None:
    """condition_id → YES 토큰. _yes_map 의 역방향 조회(슬롯이 찼을 때만 호출)."""
    for tok, w in _yes_map.items():
        if w.condition_id == condition_id:
            return tok
    return None


async def _size_for_entry() -> float | None:
    """진입 금액. None 이면 진입하지 않는다.

    full   : 가용 잔액 전액(×buffer). 한 포지션에 다 넣는다.
    slots  : 가용 잔액 ×buffer ÷ 남은 슬롯. 자본이 줄면 주문도 같이 줄어
             '잔액 없는데 계속 쏘는' 상태가 안 된다.
    fixed  : order_size_usd 고정. 다만 잔액을 확인해서 모자라면 진입을 접는다 —
             확인 없이 쏘면 릴레이에서 실패만 반복되고 알림이 폭주한다.
    """
    mode = _cfg["order_size_mode"]
    buf = _cfg["balance_buffer"]
    bal = await oracle_client.fetch_balance()

    if mode == "full":
        if bal is None:
            return _cfg["order_size_usd"]      # 릴레이 미설정(시뮬) — 기존 동작 유지
        return bal * buf

    if mode == "slots":
        if bal is None:
            return _cfg["order_size_usd"]
        free = max(1, int(_cfg["max_concurrent"]) - len(_open_cids))
        size = bal * buf / free
        if size <= 0:
            log.info("[fade] 진입 스킵 — 가용 잔액 없음 (bal=%.2f)", bal)
            return None
        return size

    # fixed
    size = float(_cfg["order_size_usd"])
    if bal is not None and bal * buf < size:
        log.info("[fade] 진입 스킵 — 잔액 부족 (bal=%.2f < size=%.2f)", bal, size)
        return None
    return size


async def _enter(sig: fade_signal.FadeSignal) -> None:
    # full 모드는 1포지션만(호출부에서 _open_cids 게이트). fixed 모드는 다른 마켓 동시 진입 가능.
    # 이중지출은 _entry_lock(호출부 직렬화) + 주문 시 fetch_balance 재조회로 방지.
    no_price = 1 - sig.entry_px
    size_usd = await _size_for_entry()
    if size_usd is None:
        return

    log.info(
        "[fade] SIGNAL 진입 NO | %s | p0=%.4f entry=%.4f no_px=%.4f size=$%.2f target=%.4f stop=%.4f",
        sig.question[:50], sig.p0, sig.entry_px, no_price, size_usd, sig.target_px, sig.stop_px,
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
    # 실체결 검증: order_id/shares 없으면 포지션 기록 안 함(과거 유령 포지션 재발 방지).
    if not result.get("order_id") or not result.get("shares"):
        log.warning("[fade] 진입 응답에 order_id/shares 없음 → 유령 방지 위해 포지션 미기록: %s", result)
        return
    # 텔레그램은 실체결 확인 후에만 발사 — 미체결이 반복돼도 알림이 폭주하지 않게
    # (과거: 체결 전 발사 → 매 throttle 재시도마다 텔레그램).
    _tg(
        f"📡 <b>[Polymarket Fade] 진입 체결</b>\n\n"
        f"<b>{sig.question[:80]}</b>\n\n"
        f"p0: <code>{sig.p0:.4f}</code>  →  entry(YES): <code>{sig.entry_px:.4f}</code>\n"
        f"NO 매수가: <code>{no_price:.4f}</code>  |  size: <code>${size_usd:.2f}</code>\n"
        f"target: <code>{sig.target_px:.4f}</code>  |  stop: <code>{sig.stop_px:.4f}</code>"
    )
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
            last_leg_px=sig.entry_px,   # 초기 레그가 — add-on 상승폭 게이트 기준
        )
        db.add(row)
        db.commit()
        _open_cids.add(sig.condition_id)
    except Exception as e:
        db.rollback()
        log.warning("[fade] 포지션 저장 실패: %s", e)
    finally:
        db.close()


# ── 피라미딩 add-on (열린 포지션에 새 스파이크 시 평단·SL 상향) ────────────────

async def _maybe_addon(watch: PolymarketFadeWatch, pos: PolymarketFadePosition,
                       hist: list[tuple[int, float]], now: int, mid: float) -> None:
    """열린 포지션 마켓에 새 스파이크 → add-on 판정.

    게이트: (1) 최대 횟수, (2) 신규 진입과 동일한 스파이크 기준(p0 대비 상대/절대),
    (3) 직전 레그가 대비 addon_min_rise_pct 이상 추가 상승(10분창 매틱 폭주 방지).
    통과 시 _entry_lock 안에서 _add_on 실행.
    """
    if (pos.addon_count or 0) >= int(_cfg["addon_max_count"]):
        return
    lookback = _cfg["lookback_s"]
    p0 = _mid_lookback_ago(hist, now, lookback)
    if p0 is None or not (_cfg["p0_lo"] <= p0 <= _cfg["p0_hi"]):
        return
    if mid < p0 * _cfg["spike_rel"] or mid - p0 < _cfg["spike_abs"]:
        return
    last_leg = pos.last_leg_px if pos.last_leg_px is not None else pos.entry_px
    if mid < last_leg * (1 + _cfg["addon_min_rise_pct"]):
        return
    async with _entry_lock:
        await _add_on(pos.condition_id, p0, mid)


async def _add_on(condition_id: str, p0: float, add_px: float) -> None:
    """add-on 주문 실행 → 체결 검증 후 평단/SL 재계산 반영."""
    cur = _open_position(condition_id)      # 락 안에서 최신 상태 재확인
    if cur is None:
        return
    if (cur.addon_count or 0) >= int(_cfg["addon_max_count"]):
        return

    no_price = 1 - add_px
    size_usd = await _size_for_entry()
    if not size_usd or size_usd <= 0:
        log.info("[fade] add-on 스킵 — 가용 잔액 없음 %s", condition_id[:12])
        return

    log.info(
        "[fade] ADD-ON NO | %s | leg#%d add(YES)=%.4f no_px=%.4f size=$%.2f",
        (cur.question or condition_id)[:50], (cur.addon_count or 0) + 1, add_px, no_price, size_usd,
    )
    result = await oracle_client.place_order(
        side="NO", action="buy", condition_id=condition_id, question=cur.question or "",
        token_id=cur.no_token_id or "", price=no_price, size_usd=size_usd, reason="fade_addon",
    )
    status = (result.get("status") or "").lower()
    if status in ("failed", "skipped", "relay_failed"):
        log.warning("[fade] add-on 미체결 → 평단 미반영: %s", result)
        return
    if not result.get("order_id") or not result.get("shares"):
        log.warning("[fade] add-on 응답에 order_id/shares 없음 → 미반영: %s", result)
        return
    _apply_addon(cur.id, add_px, result)


def _apply_addon(pos_id: int, add_px: float, order_result: dict) -> None:
    """shares 가중 평균으로 평단(entry_px) 상향 → target/stop 재산출(SL 라인 상승)."""
    db = get_session()
    try:
        row = db.get(PolymarketFadePosition, pos_id)
        if not (row and row.status == "open"):
            return
        old_sh = row.shares or 0.0
        add_sh = order_result.get("shares") or 0.0
        new_sh = old_sh + add_sh
        old_entry = row.entry_px
        new_entry = ((old_entry * old_sh + add_px * add_sh) / new_sh) if new_sh > 0 else old_entry
        retrace = _cfg["retrace_pct"]
        stop_loss = _cfg["stop_loss_pct"]
        row.entry_px = new_entry
        row.target_px = new_entry - retrace * (new_entry - row.p0)
        row.stop_px = new_entry + stop_loss * (1 - new_entry)   # 평단 상승 → SL 라인 상승
        row.shares = new_sh
        row.entry_usd = (row.entry_usd or 0.0) + (order_result.get("usd") or 0.0)
        row.addon_count = (row.addon_count or 0) + 1
        row.last_leg_px = add_px
        cnt = row.addon_count
        cid = row.condition_id
        target, stop = row.target_px, row.stop_px
        db.commit()
    except Exception as e:
        db.rollback()
        log.warning("[fade] add-on 저장 실패: %s", e)
        return
    finally:
        db.close()

    log.info(
        "[fade] ADD-ON 반영 %s | 평단(YES) %.4f→%.4f target=%.4f stop=%.4f shares=%.2f cnt=%d",
        cid[:12], old_entry, new_entry, target, stop, new_sh, cnt,
    )
    _tg(
        f"➕ <b>[Polymarket Fade] 물타기 add-on (leg#{cnt})</b>\n\n"
        f"평단(YES): <code>{old_entry:.4f}</code> → <code>{new_entry:.4f}</code>\n"
        f"target: <code>{target:.4f}</code>  |  stop↑: <code>{stop:.4f}</code>\n"
        f"총 shares: <code>{new_sh:.2f}</code>"
    )


# ── 수동 강제청산 (유령/잔여 포지션 정리용) ──────────────────────────────────

def force_close_position(condition_id: str, reason: str = "manual_force_close") -> dict:
    """열린 포지션을 즉시 closed 처리 + 인메모리 슬롯 해제. 릴레이 주문은 보내지 않음
    (유령/이미 정리된 포지션 정리 전용). 반환: {ok, closed, condition_id}."""
    db = get_session()
    try:
        row = (
            db.query(PolymarketFadePosition)
            .filter(PolymarketFadePosition.condition_id == condition_id,
                    PolymarketFadePosition.status == "open")
            .first()
        )
        if row is None:
            return {"ok": False, "error": "열린 포지션 없음", "condition_id": condition_id}
        row.status = "closed"
        row.exit_ts = int(time.time())
        row.exit_reason = reason
        db.commit()
    finally:
        db.close()
    _open_cids.discard(condition_id)   # 슬롯 즉시 해제 → 재시작 없이 신규 진입 재개
    log.info("[fade] 수동 강제청산 %s (%s)", condition_id[:12], reason)
    return {"ok": True, "closed": True, "condition_id": condition_id, "reason": reason}


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

    _open_cids.discard(pos.condition_id)

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


async def _poll_book() -> None:
    """WS 가 유지하는 현재 book mid 를 주기적으로 다시 읽어 live_status 갱신 + 감지.
    조용한 종목도 대시보드가 살아있게 하고, 놓친 WS 업데이트를 커버(폴링 아님 — 로컬 book 읽기)."""
    for tok, watch in list(_yes_map.items()):
        level = ws.price_book.get(tok)
        if not level or level.mid is None:
            continue
        await _on_update(tok)


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
    실제 WS 구독은 _ws_loop 가 _ws_generation 변화를 감지해 재연결로 처리.

    제거분도 세대를 올린다 — 스크리너 auto_drop 이 종목을 빼면 그만큼 구독도 줄어야
    한다(이전엔 추가만 감지해서, 빠진 종목을 계속 구독한 채로 남았다)."""
    global _yes_map, _ws_generation
    included = _watchlist_included()
    new_map = {w.yes_token_id: w for w in included if w.yes_token_id}
    added   = set(new_map) - set(_yes_map)
    removed = set(_yes_map) - set(new_map)
    _yes_map = new_map
    if added or removed:
        _ws_generation += 1
    if added:
        # 병렬 시드 — 순차로 돌리면 스크리너가 넣은 수백 종목에서 sweep 루프가
        # 수 분간 막히고, 그 사이 타임아웃 청산이 통째로 밀린다(포지션 있는 상태에서 치명적).
        sem = asyncio.Semaphore(_cfg["screener"]["history_concurrency"])

        async def _seed_one(t: str) -> None:
            async with sem:
                await _seed_hist(t)

        await asyncio.gather(*[_seed_one(t) for t in added], return_exceptions=True)
    for tok in removed:           # 버퍼도 같이 정리 (자동 해제가 잦으면 메모리 누수)
        _price_hist.pop(tok, None)
        _last_proc.pop(tok, None)
    return bool(added or removed)


async def _seed_hist(yes_token_id: str) -> None:
    """REST 1h 히스토리로 mid 버퍼 시드 — 재시작 직후 lookback 공백 방지."""
    try:
        pts = await poly_client.fetch_prices(yes_token_id, interval=_cfg["seed_interval"])
    except Exception:
        return
    if pts:
        _price_hist[yes_token_id] = [(int(p["ts"]), float(p["price"])) for p in pts[-_cfg["seed_points"]:]]
        w = _yes_map.get(yes_token_id)
        if w:
            _record_live(w.condition_id, _price_hist[yes_token_id],
                         has_position=_open_position(w.condition_id) is not None)


async def _apply_book(token_id: str, mid: float) -> None:
    """WS 로 받은 mid 를 버퍼에 반영 + 감지(콜백 본체)."""
    ws.price_book.setdefault(token_id, ws.PriceLevel(token_id=token_id)).mid = mid
    await _on_update(token_id)


async def _run_screener_if_due(force: bool = False) -> dict | None:
    """interval_hours 마다 워치리스트 자동 스크리닝. 실패해도 주기 루프를 죽이지 않는다.

    스크리너가 죽으면 워치리스트가 낡을 뿐이지만, 엔진이 죽으면 열린 포지션의
    청산 로직까지 멈춘다 — 그래서 여기서 예외를 삼킨다(스크리너에 한해서만).
    """
    global _last_screen_ts, _last_screen_report
    scfg = _cfg["screener"]
    if not scfg["enabled"] and not force:
        return None
    due = time.time() - _last_screen_ts >= scfg["interval_hours"] * 3600
    if not (due or force):
        return None
    _last_screen_ts = time.time()
    try:
        _last_screen_report = await screener.run_screen(scfg)
        return _last_screen_report
    except Exception as e:
        log.warning("[fade] 스크리너 실패(워치리스트는 유지): %s", e)
        return None


def get_screen_report() -> dict:
    """대시보드용 — 마지막 스크리닝 리포트."""
    return {"last_run_ts": int(_last_screen_ts), **_last_screen_report}


async def _ws_shard(tokens: list[str], generation: int) -> None:
    """WS 연결 1개가 tokens 를 구독. 워치리스트 세대가 바뀌면 반환한다."""
    import aiohttp, json
    while _ws_generation == generation:
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.ws_connect(ws.WS_URL, heartbeat=_cfg["ws_heartbeat_sec"]) as conn:
                    await conn.send_json({"assets_ids": tokens, "type": "market"})
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
                                bids, asks = m.get("bids", []), m.get("asks", [])
                                if tid and bids and asks:
                                    bb = max(float(b["price"]) for b in bids if b.get("price"))
                                    ba = min(float(a["price"]) for a in asks if a.get("price"))
                                    await _apply_book(tid, (bb + ba) / 2)
                            elif ev == "price_change":
                                for ch in m.get("price_changes", []):
                                    tid = ch.get("asset_id")
                                    if tid not in _yes_map:
                                        continue
                                    bb, ba = ch.get("best_bid"), ch.get("best_ask")
                                    if bb and ba:
                                        await _apply_book(tid, (float(bb) + float(ba)) / 2)
                        if _ws_generation != generation:
                            break
        except Exception as e:
            if _ws_generation != generation:
                return
            log.warning("[fade] WS 샤드 오류: %s — %.0fs 후 재연결", e, _cfg["ws_reconnect_s"])
            await asyncio.sleep(_cfg["ws_reconnect_s"])


async def _ws_loop() -> None:
    """fade 전용 WS — 구독 토큰을 직접 관리(공유 client 타이밍 버그 회피).

    **샤딩 필수**: subscribe 프레임에 64KB 한계가 있다(토큰당 81B). 실측상 800토큰
    (63.3KB)부터 서버가 에러도 close 도 없이 메시지만 흘리며 커버리지가 0.9%로
    무너진다 — 조용히 죽는 종류의 실패라 로그로는 안 보인다. 500토큰/연결은 100%
    커버 확인. 스크리너가 워치리스트를 900종목대로 늘리므로 단일 연결로는 못 버틴다.
    """
    while True:
        toks = list(_yes_map.keys())
        if not toks:
            await asyncio.sleep(_cfg["ws_idle_sleep_s"]); continue
        gen  = _ws_generation
        size = _cfg["ws_shard_size"]
        shards = [toks[i:i + size] for i in range(0, len(toks), size)]
        log.info("[fade] WS 구독 %d종목 / 샤드 %d개 (연결당 최대 %d)",
                 len(toks), len(shards), size)
        await asyncio.gather(*[_ws_shard(s, gen) for s in shards],
                             return_exceptions=True)
        log.info("[fade] 워치리스트 변경 → WS 재구독")


async def run(ws_client=None) -> None:
    global _cfg
    _cfg = _load_cfg()
    _validate_cfg(_cfg)          # 필수 키 누락 시 부팅에서 즉시 터짐(loop try/except가 삼키기 전)
    if not _cfg["enabled"]:
        log.debug("[fade] disabled — skipping")
        return

    _refresh_open_cache()
    await _refresh_subscriptions(ws_client)   # ws_client 무시, 시드만 사용
    log.warning("[fade] WS 엔진 시작 — 구독 %d종목", len(_yes_map))

    async def _sweep_loop():
        while True:
            try:
                _refresh_open_cache()
                await _run_screener_if_due()   # 워치리스트 갱신 → 아래 구독 반영까지 한 틱에
                await _refresh_subscriptions(ws_client)
                await _poll_book()
                await _timeout_sweep()
            except Exception as e:
                log.warning("[fade] 주기 루프 오류: %s", e)
            await asyncio.sleep(_cfg["sweep_interval_sec"])

    # gather 로 묶어 _ws_loop 예외가 삼켜지지 않게
    await asyncio.gather(_ws_loop(), _sweep_loop(), return_exceptions=True)
