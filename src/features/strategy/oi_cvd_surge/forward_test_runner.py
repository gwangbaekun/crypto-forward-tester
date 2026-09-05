"""
OI CVD Surge — Realtime Feed (Forward Test).

spot_perp_cvd.forward_test_runner 와 동일한 선진입 구조:
  - 봉 마감 60초 전 pre-entry 윈도우에서만 kline + OI 데이터 fetch
  - 봉 마감 직전 forming 봉 close 기준으로 신호 계산 → 백테스트와 동일한 진입 시점
  - 윈도우 밖: 캐시 반환 (REST fetch 없음)

backtest와의 대응:
  - 진입가   = forming 봉 close  (backtest: c[i])
  - roll/CVD = 직전 lookback 완성봉  (backtest: shift(1).rolling)
  - oi_pct   = oi[i-1] vs oi[i-1-oi_lookback]  (backtest: pct_change().shift(1))
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

from .config_loader import get_signal_params, get_timeframes
from .data_feed import get_merged_df
from .signal import compute_signal

PRE_ENTRY_SECONDS = 60.0

_TF_TO_SEC: Dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
}


async def get_state(
    symbol: str = "BTCUSDT",
    tfs: str = "1h",
    ws_only: bool = False,
) -> Dict[str, Any]:
    tfm = get_timeframes()
    entry_tf = tfm["entry_tf"]

    cache_key = f"oi_cvd_surge:{symbol}"
    now = _time.time()

    tf_sec = _TF_TO_SEC.get(entry_tf, 3600)
    sec_to_close = tf_sec - (now % tf_sec)
    in_pre_entry = 0 < sec_to_close <= PRE_ENTRY_SECONDS

    # 윈도우 밖: 캐시 있으면 즉시 반환
    if not in_pre_entry:
        cached = _signal_cache.get(cache_key)
        if cached:
            state = {**cached["state"], "entry_tf": entry_tf}
            if isinstance(state.get("signal"), dict):
                state["signal"] = {**state["signal"], "entry_tf": entry_tf}
            return state
        # 캐시 없음 (서버 재시작 등) → fetch해서 지표 채움, 진입/알림은 스킵

    # ── Fetch + 신호 계산 (pre-entry: 진입 tick 포함 / 그 외: 표시 전용) ──────
    sp = get_signal_params()
    bar_limit = sp["lookback"] + sp["oi_lookback"] + 50

    df = await get_merged_df(symbol, entry_tf, bar_limit=bar_limit, oi_limit=200)

    if df is None or df.empty or len(df) < 2:
        cached = _signal_cache.get(cache_key)
        return cached["state"] if cached else {}

    forming_row    = df.iloc[-1]
    forming_ts_sec = int(forming_row["open_time_ms"]) // 1000
    new_bar_detected = forming_ts_sec != _last_bar_time.get(cache_key, 0)

    bar_close_price = float(forming_row["close"]) or 0.0
    bar_high        = float(forming_row["high"])  or bar_close_price
    bar_low         = float(forming_row["low"])   or bar_close_price

    sig: Dict[str, Any] = {}
    if bar_close_price > 0:
        try:
            sig = compute_signal(df, bar_close_price) or {}
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

    # 진입 tick + 알림은 pre-entry 윈도우에서 new_bar일 때만
    if in_pre_entry and bar_close_price > 0 and new_bar_detected:
        _fire_and_forget(_tick_and_notify("oi_cvd_surge", symbol, bar_close_price, state))

    return state
