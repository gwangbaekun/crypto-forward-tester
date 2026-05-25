"""Late Convergence Alpha — 엔진.

1. REST로 active 마켓 목록 주기적 갱신
2. Gamma API outcomePrices → PriceLevel 직접 구성 → signal.compute()
   (WS는 보조: 가격 업데이트 수신 시 추가 체크)
3. 시그널 발생 시 DB 저장 + 콘솔 출력
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import yaml

from features.strategy.polymarket._data.client import fetch_by_expiry
from features.strategy.polymarket._data import ws_client as ws
from features.strategy.polymarket._data.ws_client import PriceLevel
from features.strategy.polymarket.late_convergence import signal as lc_signal
from db.session import get_session
from db.models import PolymarketSignal

log = logging.getLogger("polymarket.late_convergence")

_CFG_PATH = Path(__file__).parent / "config.yaml"
_cfg: dict = {}
_markets: dict[str, dict] = {}
_last_signal_ts: dict[str, float] = {}
_COOLDOWN_S = 1800


def _load_cfg() -> dict:
    with open(_CFG_PATH) as f:
        return yaml.safe_load(f)


def _make_level(token_id: str | None, price: float | None) -> PriceLevel | None:
    """Gamma REST 가격으로 PriceLevel 구성 (WS 없을 때 fallback)."""
    if token_id is None or price is None:
        return None
    return PriceLevel(
        token_id=token_id,
        best_bid=None,
        best_ask=price,   # Gamma outcomePrices ≈ last trade price → ask 근사
        mid=price,
        last_price=price,
    )


async def _refresh_and_scan(ws_client: ws.CLOBWSClient) -> None:
    """마켓 갱신 + REST 가격으로 즉시 시그널 체크."""
    global _markets
    max_hours = _cfg.get("max_scan_hours", 48)
    min_vol   = _cfg.get("min_volume_usd", 5000)

    try:
        fetched = await fetch_by_expiry(max_hours=max_hours, min_volume=min_vol)
        _markets = {m["condition_id"]: m for m in fetched if m.get("condition_id")}
        for m in fetched:
            ws_client.add_tokens(m.get("yes_token_id"), m.get("no_token_id"))
        log.debug("[LC] markets refreshed: %d active (≤%.0fh)", len(_markets), max_hours)
    except Exception as e:
        log.warning("[LC] market refresh failed: %s", e)
        return

    # REST 가격으로 바로 시그널 체크
    for cid, market in list(_markets.items()):
        try:
            _check_market_rest(cid, market)
        except Exception as e:
            log.debug("[LC] rest scan error %s: %s", cid, e)


def _check_market_rest(cid: str, market: dict) -> None:
    """Gamma API 가격으로 시그널 평가."""
    yes_tid = market.get("yes_token_id")
    no_tid  = market.get("no_token_id")

    # WS 가격 우선, 없으면 Gamma REST 가격 사용
    yes_level = ws.price_book.get(yes_tid) if yes_tid else None
    no_level  = ws.price_book.get(no_tid)  if no_tid  else None

    if yes_level is None:
        yes_level = _make_level(yes_tid, market.get("yes_price"))
    if no_level is None:
        no_level = _make_level(no_tid, market.get("no_price"))

    sig = lc_signal.compute(market, yes_level, no_level, _cfg)
    if sig is None:
        return

    last = _last_signal_ts.get(cid, 0)
    if time.time() - last < _COOLDOWN_S:
        return
    _last_signal_ts[cid] = time.time()

    log.debug(
        "[LC] SIGNAL %s | %s | price=%.3f roi=+%.1f%% | %.1fh left | $%.0f vol",
        sig.side, sig.question[:50], sig.entry_price, sig.expected_roi * 100,
        sig.hours_to_end, sig.volume_usd,
    )
    row_id = _save_signal(sig)
    if row_id:
        asyncio.create_task(_place_order_and_update(sig, row_id))


def _save_signal(sig: lc_signal.LCSignal) -> int | None:
    """DB 저장 후 row id 반환."""
    db = get_session()
    try:
        row = PolymarketSignal(
            strategy      = "late_convergence",
            condition_id  = sig.condition_id,
            question      = sig.question[:500],
            signal_type   = f"LC_{sig.side}",
            yes_price     = sig.entry_price if sig.side == "YES" else None,
            no_price      = sig.entry_price if sig.side == "NO"  else None,
            pair_cost     = None,
            divergence    = sig.expected_roi,
            side          = sig.side,
            volume_usd    = sig.volume_usd,
            hours_to_end  = sig.hours_to_end,
            yes_token_id  = sig.yes_token_id,
            no_token_id   = sig.no_token_id,
            event_end_ts  = sig.end_ts,
            order_status  = "pending",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    except Exception as e:
        db.rollback()
        log.warning("[LC] DB save failed: %s", e)
        return None
    finally:
        db.close()


async def _place_order_and_update(sig: lc_signal.LCSignal, row_id: int) -> None:
    """실거래 주문 후 DB 업데이트. POLYMARKET_PK 없으면 skip."""
    import os
    from features.strategy.polymarket._data.live import _has_pk, _pk_valid
    if not (_has_pk() and _pk_valid()):
        return  # 유효 PK 없으면 시뮬 모드

    from features.strategy.polymarket._data.executor import place_order
    token_id = sig.yes_token_id if sig.side == "YES" else sig.no_token_id
    if not token_id:
        return

    result = await place_order(token_id, sig.entry_price)

    # DB 업데이트
    db = get_session()
    try:
        from sqlalchemy import select
        row = db.execute(select(PolymarketSignal).where(PolymarketSignal.id == row_id)).scalar_one_or_none()
        if row:
            row.poly_order_id = result.get("order_id") or ""
            row.order_status  = result.get("status", "failed")
            db.commit()
    except Exception as e:
        db.rollback()
        log.warning("[LC] order DB update failed: %s", e)
    finally:
        db.close()


async def on_price_update(token_id: str) -> None:
    """WS 콜백 — 실시간 업데이트 시 추가 체크."""
    for cid, market in list(_markets.items()):
        yes_tid = market.get("yes_token_id")
        no_tid  = market.get("no_token_id")
        if token_id not in (yes_tid, no_tid):
            continue
        try:
            _check_market_rest(cid, market)
        except Exception as e:
            log.debug("[LC] ws callback error: %s", e)


async def run(ws_client: ws.CLOBWSClient) -> None:
    global _cfg
    _cfg = _load_cfg()

    if not _cfg.get("enabled", True):
        log.debug("[LC] disabled — skipping")
        return

    ws.register_callback(on_price_update)
    interval = _cfg.get("poll_interval_sec", 300)

    while True:
        await _refresh_and_scan(ws_client)
        await asyncio.sleep(interval)


def get_markets() -> dict[str, dict]:
    """대시보드용 현재 모니터링 마켓 목록."""
    return _markets
