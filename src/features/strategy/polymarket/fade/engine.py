"""Fade 전략 — 엔진.

워치리스트(유저가 직접 add/remove)만 스캔한다. late_convergence 처럼 전체 마켓을
자동으로 훑지 않음 — btc_backtest news_lag 대시보드와 동일하게 "내가 고른 종목만".

루프:
  1. included 워치리스트 종목마다 CLOB 1h 가격 히스토리 조회
  2. 열린 포지션 없으면: 최신 지점이 스파이크인지 detect_spikes 로 확인 → 진입
  3. 열린 포지션 있으면: 되돌림/손절/타임아웃 체크 → 청산
  4. 진입/청산 시 오라클 릴레이 호출(오사카 경유 buy/sell). ORACLE_RELAY_URL 미설정이면 sim.
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
from features.strategy.polymarket.fade import signal as fade_signal
from features.strategy.polymarket.fade import oracle_client

log = logging.getLogger("polymarket.fade")

_CFG_PATH = Path(__file__).parent / "config.yaml"
_cfg: dict = {}

# 대시보드 실시간 표시용 — 마켓별 최근 스캔 결과(in-memory, 라우터와 같은 프로세스)
_live_status: dict[str, dict] = {}


def get_live_status() -> dict[str, dict]:
    """condition_id → {last_scan_ts, p0, price, rel_pct, spike_now, has_position}."""
    return _live_status


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


def _record_status(condition_id: str, pts: list[dict], has_position: bool) -> None:
    st = fade_signal.latest_status(pts, _cfg)
    if st is None:
        return
    _live_status[condition_id] = {
        "last_scan_ts": int(time.time()),
        "has_position": has_position,
        **st,
    }


async def _scan_market(watch: PolymarketFadeWatch) -> None:
    if not watch.yes_token_id:
        return

    try:
        pts = await poly_client.fetch_prices(watch.yes_token_id, interval="1h")
    except Exception as e:
        log.debug("[fade] price fetch 실패 %s: %s", watch.condition_id[:12], e)
        return

    open_pos = _open_position(watch.condition_id)
    if pts:
        _record_status(watch.condition_id, pts, has_position=open_pos is not None)

    if open_pos is not None:
        await _check_exit(open_pos)
        return

    if len(pts) < 2:
        return

    spikes = fade_signal.detect_spikes(pts, _cfg)
    if not spikes:
        return
    idx, p0, entry_px = spikes[-1]
    if idx != len(pts) - 1:
        return  # 스파이크가 지금 시점(최신 캔들)이 아니면 신규 진입 아님 — 이미 지난 스파이크

    entry_ts = pts[idx]["ts"]
    market = {
        "condition_id": watch.condition_id, "question": watch.question,
        "yes_token_id": watch.yes_token_id, "no_token_id": watch.no_token_id,
    }
    sig = fade_signal.build_signal(market, p0, entry_px, entry_ts, _cfg)

    # NO 매수가 = 1 - YES 진입가. 사이징: full=가용 pUSD 전액, fixed=명목.
    no_price = 1 - sig.entry_px
    size_usd = _cfg.get("order_size_usd", 1.0)
    if _cfg.get("order_size_mode", "fixed") == "full":
        bal = await oracle_client.fetch_balance()
        if bal is not None:
            size_usd = bal * _cfg.get("balance_buffer", 0.98)
        # 릴레이 미설정(시뮬)이면 bal=None → 명목 유지

    log.info(
        "[fade] SIGNAL 진입 NO | %s | p0=%.4f entry=%.4f no_px=%.4f size=$%.2f target=%.4f stop=%.4f",
        sig.question[:50], sig.p0, sig.entry_px, no_price, size_usd, sig.target_px, sig.stop_px,
    )
    result = await oracle_client.place_order(
        side="NO", action="buy", condition_id=sig.condition_id, question=sig.question,
        token_id=sig.no_token_id, price=no_price, size_usd=size_usd,
        reason="fade_entry_spike",
    )
    # 실주문 실패/거부면 포지션 미기록(유령 포지션 방지). 시뮬(logged)은 기록.
    status = (result.get("status") or "").lower()
    if status in ("failed", "skipped", "relay_failed"):
        log.warning("[fade] 진입 주문 미체결 → 포지션 미기록: %s", result)
        return
    _open_new_position(sig, result)


def _open_new_position(sig: fade_signal.FadeSignal, order_result: dict) -> None:
    shares = order_result.get("shares")
    entry_usd = order_result.get("usd")
    status = order_result.get("status") or "logged"
    db = get_session()
    try:
        row = PolymarketFadePosition(
            condition_id=sig.condition_id, question=sig.question[:500],
            no_token_id=sig.no_token_id, p0=sig.p0, entry_px=sig.entry_px,
            target_px=sig.target_px, stop_px=sig.stop_px,
            entry_ts=int(time.time()), timeout_ts=sig.timeout_ts, status="open",
            shares=shares, entry_usd=entry_usd,
            order_id=order_result.get("order_id") or None, order_status=status[:16],
        )
        db.add(row)
        db.commit()
    except Exception as e:
        db.rollback()
        log.warning("[fade] 포지션 저장 실패: %s", e)
    finally:
        db.close()


async def _check_exit(pos: PolymarketFadePosition) -> None:
    watch = _get_watch(pos.condition_id)
    if watch is None or not watch.yes_token_id:
        return
    current_px = await poly_client.fetch_current_price(watch.yes_token_id)
    if current_px is None:
        return

    now_ts = int(time.time())
    result = fade_signal.check_exit(current_px, now_ts, pos)
    if result is None:
        return
    exit_px, reason = result
    ret_pct = round((pos.entry_px - exit_px) / (1 - pos.entry_px) * 100, 2)

    db = get_session()
    try:
        row = db.get(PolymarketFadePosition, pos.id)
        if row and row.status == "open":
            row.status = "closed"
            row.exit_px = exit_px
            row.exit_ts = now_ts
            row.exit_reason = reason
            row.ret_pct = ret_pct
            db.commit()
    except Exception as e:
        db.rollback()
        log.warning("[fade] 포지션 청산 저장 실패: %s", e)
        return
    finally:
        db.close()

    log.info(
        "[fade] SIGNAL 청산 %s | %s | exit=%.4f ret=%.2f%% shares=%s",
        reason, pos.question[:50] if pos.question else pos.condition_id[:12], exit_px, ret_pct, pos.shares,
    )
    # NO 매도가 = 1 - YES 청산가. 보유수량(shares) 그대로 매도.
    await oracle_client.place_order(
        side="NO", action="sell", condition_id=pos.condition_id, question=pos.question or "",
        token_id=pos.no_token_id or "", price=1 - exit_px, size_usd=(pos.entry_usd or 1.0),
        size_shares=pos.shares, reason=f"fade_exit_{reason}",
    )


def _get_watch(condition_id: str) -> PolymarketFadeWatch | None:
    db = get_session()
    try:
        return db.get(PolymarketFadeWatch, condition_id)
    finally:
        db.close()


async def tick() -> None:
    for watch in _watchlist_included():
        try:
            await _scan_market(watch)
        except Exception as e:
            log.debug("[fade] scan error %s: %s", watch.condition_id[:12], e)


async def run() -> None:
    global _cfg
    _cfg = _load_cfg()

    if not _cfg.get("enabled", True):
        log.debug("[fade] disabled — skipping")
        return

    interval = _cfg.get("poll_interval_sec", 300)
    while True:
        await tick()
        await asyncio.sleep(interval)
