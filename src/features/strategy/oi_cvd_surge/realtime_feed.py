"""
OI CVD Surge — Realtime Feed (Forward Test).

Close-only stop 원칙:
  1h 봉 마감 시에만 신호 계산 + tick 실행.
  봉 사이 구간은 캐시 즉시 반환.
"""
from __future__ import annotations

import time as _time
from typing import Any, Dict, Optional

from features.strategy.common.base_realtime_feed import (
    _last_bar_time,
    _signal_cache,
    _tick_and_notify,
)

from .config_loader import get_signal_params, get_timeframes
from .data_feed import get_merged_df
from .signal import compute_signal

_TF_TO_SEC = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
}

STRATEGY_KEY = "oi_cvd_surge"


async def get_state(
    symbol: str = "BTCUSDT",
    tfs: str = "1h",
    ws_only: bool = False,
) -> Dict[str, Any]:
    from features.strategy.common.config_loader import get_master_config

    master_cfg = (get_master_config() or {}).get(STRATEGY_KEY) or {}
    tfm = get_timeframes()
    entry_tf = master_cfg.get("entry_tf") or tfm["entry_tf"]

    cache_key = f"{STRATEGY_KEY}:{symbol}"
    now = _time.time()

    # ── 캐시 구간: 봉 마감 30초 전까지 즉시 반환 ─────────────────────────────
    _tf_sec = _TF_TO_SEC.get(entry_tf, 3600)
    _last_bt = _last_bar_time.get(cache_key, 0)
    _near_bar_close = now >= (_last_bt + _tf_sec - 30)

    signal_interval: Optional[int] = master_cfg.get("signal_interval")
    if ws_only and cache_key in _signal_cache and not _near_bar_close:
        return _signal_cache[cache_key]["state"]
    if signal_interval and cache_key in _signal_cache and not _near_bar_close:
        if now - _signal_cache[cache_key]["ts"] < signal_interval:
            return _signal_cache[cache_key]["state"]

    # ── Full fetch: kline + OI 병합 ───────────────────────────────────────────
    sp = get_signal_params()
    bar_limit = sp["lookback"] + sp["oi_lookback"] + 50  # 여유 있게

    df = await get_merged_df(symbol, entry_tf, bar_limit=bar_limit, oi_limit=200)

    if df is None or len(df) < 2:
        cached = _signal_cache.get(cache_key)
        return cached["state"] if cached else {"symbol": symbol, "signal": {}}

    # 완성봉 = [-2] ([-1] 은 현재 형성 중)
    completed_row    = df.iloc[-2]
    completed_ts_sec = int(completed_row["open_time_ms"]) // 1000
    new_bar_detected = completed_ts_sec != _last_bar_time.get(cache_key, 0)

    bar_close_price = float(completed_row["close"]) or 0.0
    bar_high        = float(completed_row["high"])  or bar_close_price
    bar_low         = float(completed_row["low"])   or bar_close_price

    sig: Dict[str, Any] = {}

    if new_bar_detected and bar_close_price > 0:
        # 형성 중 봉 제거 → 완성봉까지만 전달
        completed_df = df.iloc[:-1].copy()
        try:
            sig = compute_signal(completed_df, bar_close_price) or {}
        except Exception as e:
            sig = {"signal": "none", "error": str(e)}

        _last_bar_time[cache_key] = completed_ts_sec

    elif cache_key in _signal_cache:
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

    if new_bar_detected and bar_close_price > 0:
        from features.strategy.common.base_realtime_feed import _fire_and_forget
        _fire_and_forget(_tick_and_notify(STRATEGY_KEY, symbol, bar_close_price, state))

    return state
