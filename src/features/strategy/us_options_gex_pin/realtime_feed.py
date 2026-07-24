"""US Options Expiry GEX Pinning — Realtime Feed / get_state (자체 포함).

strategy_loop 이 하루 1회, 대시보드가 수시로 호출한다. 엔진 tick(진입/청산/telegram)은
그날 스냅샷 기준 하루 1회만 발동 — 날짜 단위로 dedupe 한다.

주의: underlying(SPY 등)은 Binance 가격이 없으므로 공통 _tick_and_notify(BTC WS 가격
의존)를 쓰지 않고, 이 전략 전용 독립 notify 경로를 둔다 (engine.tick + telegram +
signal log). 실거래 없음 — 엣지 측정 전용.
"""
from __future__ import annotations

import time as _time
from typing import Any, Dict

from features.strategy.common.base_realtime_feed import _signal_cache, _fire_and_forget

from .config_loader import get_underlying, get_signal_params
from .data_feed import load_recent_chain
from .signal import compute_signal

_STRATEGY_KEY = "us_options_gex_pin"
_last_tick_day: Dict[str, str] = {}   # key: cache_key → 처리한 날짜(UTC, YYYY-MM-DD)


async def _tick_and_notify_local(symbol: str, price: float, state: Dict[str, Any]) -> None:
    """이 전략 전용 tick+알림 (Binance 가격 비의존, 실거래 없음)."""
    try:
        from .engine import get_engine
        from features.strategy.common.signal_logger import log_signal_snapshot
        _fire_and_forget(log_signal_snapshot(_STRATEGY_KEY, symbol, state))

        engine = get_engine()
        res = engine.tick(symbol, {**state, "current_price": price}, report_text=None)
        if not res:
            return
        events = res.get("events") or []
        if not events:
            return
        from features.strategy.common.notifier import send_event_alerts
        try:
            send_event_alerts(_STRATEGY_KEY, symbol, events, sync_info={})
        except Exception as e:
            print(f"[{_STRATEGY_KEY}] 알림 전송 오류: {e}")
    except Exception as e:
        print(f"[{_STRATEGY_KEY}] tick 오류: {e}")


async def get_state(
    symbol: str = "SPY",
    tfs: str = "1d",
    ws_only: bool = False,
) -> Dict[str, Any]:
    params = get_signal_params()
    underlying = get_underlying()
    # 대시보드에서 심볼을 넘겨도 config 의 underlying 을 신뢰 (데이터 소스 일관성)
    symbol = underlying
    cache_key = f"{_STRATEGY_KEY}:{symbol}"
    now = _time.time()

    try:
        df = load_recent_chain(underlying, days=params["chain_window_days"])
    except Exception as e:
        cached = _signal_cache.get(cache_key)
        if cached:
            return cached["state"]
        return {
            "symbol": symbol, "current_price": 0.0,
            "signal": {"signal": "none", "action": "idle", "reasons": [f"us_options_chain 조회 실패: {e}"]},
            "by_tf": {"1d": {"signal": {}}}, "entry_tf": "expiry",
        }

    ctx = compute_signal(df, now, params)
    sig = ctx["signal"]
    price = float(ctx.get("spot") or 0.0)

    state: Dict[str, Any] = {
        "symbol":        symbol,
        "current_price": price,
        "signal":        sig,
        "by_tf":         {"1d": {"signal": sig}},
        "entry_tf":      "expiry",
        "bar_high":      price,
        "bar_low":       price,
    }
    _signal_cache[cache_key] = {"state": state, "ts": now}

    # ── 엔진 tick: 그날 1회만 (날짜 dedupe) ─────────────────────────────────
    if not ws_only and price > 0 and ctx.get("should_tick"):
        day = _time.strftime("%Y-%m-%d", _time.gmtime(now))
        if _last_tick_day.get(cache_key) != day:
            _last_tick_day[cache_key] = day
            _fire_and_forget(_tick_and_notify_local(symbol, price, state))

    return state
