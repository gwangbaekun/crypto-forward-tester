"""
Spot-Perp CVD Divergence — Realtime Feed (Forward Test).

eth_cvd_explosion.forward_test_runner 와 동일한 선진입 구조:
  - 봉 마감 60초 전 pre-entry 윈도우에서만 perp + spot 데이터 fetch
  - 봉 마감 직전 forming 봉 close 기준으로 신호 계산 → 백테스트와 동일한 진입 시점
  - 윈도우 밖: 캐시 반환 (REST fetch 없음)

backtest와의 대응:
  - 진입가   = forming 봉 close  (backtest: c[i])
  - CVD%    = 마지막 lookback개 완성봉 기준  (backtest: shift(1))
  - CVD exit = 봉 마감 tick에서 1회 (backtest: 봉 단위)
  - intrabar SL = WS 가격 직접 판정 (strategy_loop.py 호출)
"""
from __future__ import annotations

import time as _time
from typing import Any, Dict

from features.strategy.common.base_realtime_feed import (
    _last_bar_time,
    _signal_cache,
    _tick_and_notify,
    _fire_and_forget,
)

from .config_loader import get_timeframes
from .data_feed import get_dfs
from .signal import compute_signal

PRE_ENTRY_SECONDS = 60.0

_TF_TO_SEC: Dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
}


async def get_state(
    symbol: str = "SOLUSDT",
    tfs: str = "1h",
    ws_only: bool = False,
) -> Dict[str, Any]:
    tfm = get_timeframes()
    entry_tf = tfm["entry_tf"]

    cache_key = f"spot_perp_cvd:{symbol}"
    now = _time.time()

    tf_sec = _TF_TO_SEC.get(entry_tf, 3600)
    sec_to_close = tf_sec - (now % tf_sec)
    in_pre_entry = 0 < sec_to_close <= PRE_ENTRY_SECONDS

    # 윈도우 밖: 캐시만 반환 (entry_tf는 항상 현재 config 값으로 갱신)
    if not in_pre_entry:
        cached = _signal_cache.get(cache_key)
        if not cached:
            return {}
        state = {**cached["state"], "entry_tf": entry_tf}
        if isinstance(state.get("signal"), dict):
            state["signal"] = {**state["signal"], "entry_tf": entry_tf}
        return state

    # ── Pre-entry 윈도우: fetch + 신호 계산 ───────────────────────────────────
    perp_df, spot_df = await get_dfs(symbol, interval=entry_tf, limit=200)

    if perp_df is None or perp_df.empty or len(perp_df) < 2:
        cached = _signal_cache.get(cache_key)
        return cached["state"] if cached else {}

    forming_row    = perp_df.iloc[-1]
    forming_ts_sec = int(forming_row["open_time_ms"]) // 1000
    new_bar_detected = forming_ts_sec != _last_bar_time.get(cache_key, 0)

    bar_close_price = float(forming_row["close"]) or 0.0
    bar_high        = float(forming_row["high"])  or bar_close_price
    bar_low         = float(forming_row["low"])   or bar_close_price

    sig: Dict[str, Any] = {}
    if bar_close_price > 0:
        try:
            sig = compute_signal(perp_df, spot_df) or {}
        except Exception as e:
            sig = {"error": str(e)}

    if new_bar_detected:
        _last_bar_time[cache_key] = forming_ts_sec

    if not bar_close_price and cache_key in _signal_cache:
        cached_sig = _signal_cache[cache_key]["state"].get("signal") or {}
        sig = {**cached_sig, "signal": "none"}

    state: Dict[str, Any] = {
        "symbol":        symbol,
        "current_price": bar_close_price,
        "signal":        sig,
        "by_tf":         {entry_tf: {"signal": sig}},
        "entry_tf":      entry_tf,
        "bar_high":      bar_high,
        "bar_low":       bar_low,
    }

    _signal_cache[cache_key] = {"state": state, "ts": now}

    if bar_close_price > 0 and new_bar_detected:
        _fire_and_forget(_tick_and_notify("spot_perp_cvd", symbol, bar_close_price, state))

    return state
