"""Deribit Expiry GEX Reversal — Realtime Feed / get_state (자체 포함).

strategy_loop 이 1h 마다, 대시보드가 수시로 호출한다. 엔진 tick(진입/청산/telegram)은
만기~청산 시간대(08:00~12:00 UTC)의 새 시(hour)에서만 1회 발동 — 대시보드 폴링이
중복 tick 을 일으키지 않도록 시(hour) 단위로 dedupe 한다.

telegram/DB 로깅은 공통 _tick_and_notify(프레임워크) 경유. 실거래는 master yaml
binance_live/ctrader_live=false 라 자동 스킵 — 엣지 측정 전용.
"""
from __future__ import annotations

import time as _time
from typing import Any, Dict

from features.strategy.common.base_realtime_feed import (
    _signal_cache,
    _tick_and_notify,
    _fire_and_forget,
)

from .config_loader import get_currency, get_signal_params
from .data_feed import load_recent_chain
from .signal import compute_signal

_STRATEGY_KEY = "deribit_gex_reversal"
_last_tick_hour: Dict[str, int] = {}   # key: cache_key → 처리한 시(hour) epoch


async def get_state(
    symbol: str = "BTCUSDT",
    tfs: str = "1h",
    ws_only: bool = False,
) -> Dict[str, Any]:
    params = get_signal_params()
    currency = get_currency()
    cache_key = f"{_STRATEGY_KEY}:{symbol}"
    now = _time.time()

    try:
        df = load_recent_chain(currency, days=params["chain_window_days"])
    except Exception as e:
        cached = _signal_cache.get(cache_key)
        if cached:
            return cached["state"]
        return {
            "symbol": symbol, "current_price": 0.0,
            "signal": {"signal": "none", "action": "idle", "reasons": [f"deribit_chain 조회 실패: {e}"]},
            "by_tf": {"1h": {"signal": {}}}, "entry_tf": "expiry",
        }

    ctx = compute_signal(df, now, params)
    sig = ctx["signal"]
    price = float(ctx.get("spot") or 0.0)

    state: Dict[str, Any] = {
        "symbol":        symbol,
        "current_price": price,
        "signal":        sig,
        "by_tf":         {"1h": {"signal": sig}},
        "entry_tf":      "expiry",
        "bar_high":      price,
        "bar_low":       price,
    }
    _signal_cache[cache_key] = {"state": state, "ts": now}

    # ── 엔진 tick: 만기~청산 시간대의 새 시(hour)에서만 1회 ──────────────────
    if not ws_only and price > 0 and ctx.get("should_tick"):
        hour_epoch = int(now // 3600)
        if _last_tick_hour.get(cache_key) != hour_epoch:
            _last_tick_hour[cache_key] = hour_epoch
            _fire_and_forget(_tick_and_notify(_STRATEGY_KEY, symbol, price, state))

    return state
