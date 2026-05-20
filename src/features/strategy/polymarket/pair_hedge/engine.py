"""Pair Hedge — 엔진.

YES_ask + NO_ask < max_pair_cost 이면 수학적 무위험 차익.
REST 가격으로 주기적 스캔 + WS 실시간 보조.
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
from features.strategy.polymarket.pair_hedge import signal as ph_signal
from db.session import get_session
from db.models import PolymarketSignal

log = logging.getLogger("polymarket.pair_hedge")

_CFG_PATH = Path(__file__).parent / "config.yaml"
_cfg: dict = {}
_markets: dict[str, dict] = {}
_last_signal_ts: dict[str, float] = {}
_COOLDOWN_S = 300


def _load_cfg() -> dict:
    with open(_CFG_PATH) as f:
        return yaml.safe_load(f)


def _make_level(token_id: str | None, price: float | None) -> PriceLevel | None:
    if token_id is None or price is None:
        return None
    return PriceLevel(token_id=token_id, best_bid=None, best_ask=price, mid=price, last_price=price)


async def _refresh_and_scan(ws_client: ws.CLOBWSClient) -> None:
    global _markets
    max_hours = _cfg.get("max_scan_hours", 72)
    min_vol   = _cfg.get("min_volume_usd", 10000)

    try:
        fetched = await fetch_by_expiry(max_hours=max_hours, min_volume=min_vol)
        _markets = {m["condition_id"]: m for m in fetched if m.get("condition_id")}
        for m in fetched:
            ws_client.add_tokens(m.get("yes_token_id"), m.get("no_token_id"))
        print(f"[PH] markets refreshed: {len(_markets)} active")
    except Exception as e:
        log.warning("[PH] market refresh failed: %s", e)
        return

    for cid, market in list(_markets.items()):
        try:
            _check_market(cid, market)
        except Exception as e:
            log.debug("[PH] rest scan error %s: %s", cid, e)


def _check_market(cid: str, market: dict) -> None:
    yes_tid = market.get("yes_token_id")
    no_tid  = market.get("no_token_id")

    yes_level = ws.price_book.get(yes_tid) if yes_tid else None
    no_level  = ws.price_book.get(no_tid)  if no_tid  else None

    if yes_level is None:
        yes_level = _make_level(yes_tid, market.get("yes_price"))
    if no_level is None:
        no_level = _make_level(no_tid, market.get("no_price"))

    sig = ph_signal.compute(market, yes_level, no_level, _cfg)
    if sig is None:
        return

    last = _last_signal_ts.get(cid, 0)
    if time.time() - last < _COOLDOWN_S:
        return
    _last_signal_ts[cid] = time.time()

    print(f"[PH] PAIR COST={sig.pair_cost:.4f} PROFIT=+{sig.profit_pct:.2f}% | {sig.question[:50]} | ${sig.volume_usd:.0f} vol")
    _save_signal(sig)


def _save_signal(sig: ph_signal.PairSignal) -> None:
    db = get_session()
    try:
        row = PolymarketSignal(
            strategy     = "pair_hedge",
            condition_id = sig.condition_id,
            question     = sig.question[:500],
            signal_type  = "PAIR_HEDGE",
            yes_price    = sig.yes_ask,
            no_price     = sig.no_ask,
            pair_cost    = sig.pair_cost,
            divergence   = sig.profit,
            side         = "BOTH",
            volume_usd   = sig.volume_usd,
            hours_to_end = None,
            yes_token_id = sig.yes_token_id,
            no_token_id  = sig.no_token_id,
        )
        db.add(row)
        db.commit()
    except Exception as e:
        db.rollback()
        log.warning("[PH] DB save failed: %s", e)
    finally:
        db.close()


async def on_price_update(token_id: str) -> None:
    for cid, market in list(_markets.items()):
        yes_tid = market.get("yes_token_id")
        no_tid  = market.get("no_token_id")
        if token_id not in (yes_tid, no_tid):
            continue
        try:
            _check_market(cid, market)
        except Exception as e:
            log.debug("[PH] ws callback error: %s", e)


async def run(ws_client: ws.CLOBWSClient) -> None:
    global _cfg
    _cfg = _load_cfg()

    if not _cfg.get("enabled", True):
        log.info("[PH] disabled — skipping")
        return

    ws.register_callback(on_price_update)
    interval = _cfg.get("poll_interval_sec", 120)

    while True:
        await _refresh_and_scan(ws_client)
        await asyncio.sleep(interval)


def get_markets() -> dict[str, dict]:
    return _markets
