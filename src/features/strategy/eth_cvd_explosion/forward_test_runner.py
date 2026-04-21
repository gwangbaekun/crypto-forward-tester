"""
ETH CVD Explosion — Realtime Feed.

base_realtime_feed.build_state() 의 [:-1] 방식 대신
sweep_builder.build_sweep_at() 를 직접 사용.

backtest 와 완전 동일한 look-ahead 필터 보장:
  - 상위 TF 봉: open_ms + tf_duration <= entry_bar_close_time 만 포함
  - [:-1] 방식은 Binance 응답에 forming 봉이 없을 때 오동작 가능성 있음
"""
from __future__ import annotations

import asyncio
import time as _time
from typing import Any, Dict, List, Optional

import pandas as pd

from features.strategy.common.base_realtime_feed import (
    _last_bar_time,
    _signal_cache,
    _tick_and_notify,
)

from .data_feed import get_dfs_by_tf
from .sweep_builder import TF_TO_MINUTES, build_sweep_at


async def _fetch_liq_level_map(symbol: str) -> List[Dict]:
    try:
        from features.strategy.common.kline_bundle import _fetch_liq_level_map as _liq
        return await _liq(symbol)
    except Exception:
        return []


async def get_state(
    symbol: str = "ETHUSDT",
    tfs: str = "15m,1h,4h",
    ws_only: bool = False,
) -> Dict[str, Any]:
    from features.strategy.common.config_loader import get_master_config
    from .config_loader import get_timeframes
    from .signal import compute_signal

    master_cfg = (get_master_config() or {}).get("eth_cvd_explosion") or {}
    tfm = get_timeframes()
    entry_tf  = master_cfg.get("entry_tf")  or tfm["entry_tf"]
    higher_tf = master_cfg.get("higher_tf") or tfm["higher_tf"]
    tfs_list  = [x.strip() for x in tfs.split(",") if x.strip()]

    cache_key = f"eth_cvd_explosion:{symbol}"
    now = _time.time()

    # ── ws_only / signal_interval 구간: 캐시 즉시 반환 ───────────────────────
    _tf_sec = TF_TO_MINUTES.get(entry_tf, 60) * 60
    _last_bt = _last_bar_time.get(cache_key, 0)
    _near_bar_close = now >= (_last_bt + _tf_sec - 30)

    signal_interval: Optional[int] = master_cfg.get("signal_interval")
    if ws_only and cache_key in _signal_cache and not _near_bar_close:
        return _signal_cache[cache_key]["state"]
    if signal_interval and cache_key in _signal_cache and not _near_bar_close:
        if now - _signal_cache[cache_key]["ts"] < signal_interval:
            return _signal_cache[cache_key]["state"]

    # ── Full fetch: DataFrame + liq 병렬 ────────────────────────────────────
    fetch_results = await asyncio.gather(
        get_dfs_by_tf(symbol, tfs_list),
        _fetch_liq_level_map(symbol),
        return_exceptions=True,
    )
    dfs_by_tf: Dict[str, pd.DataFrame] = (
        fetch_results[0] if not isinstance(fetch_results[0], Exception) else {}
    )
    level_map: List[Dict] = (
        fetch_results[1] if not isinstance(fetch_results[1], Exception) else []
    )
    magnets = {"level_map": level_map} if level_map else {}

    # ── 완성봉 감지 ──────────────────────────────────────────────────────────
    # [-1] 은 현재 형성 중인 봉, [-2] 가 방금 완성된 봉
    entry_df = dfs_by_tf.get(entry_tf)
    if entry_df is None or len(entry_df) < 2:
        cached = _signal_cache.get(cache_key)
        return cached["state"] if cached else {}

    completed_row    = entry_df.iloc[-2]
    completed_ts_ms  = int(completed_row["open_time_ms"])   # ms
    completed_ts_sec = completed_ts_ms // 1000              # sec (캐시 키용)
    new_bar_detected = completed_ts_sec != _last_bar_time.get(cache_key, 0)

    bar_close_price = float(completed_row["close"]) or 0.0
    bar_high        = float(completed_row["high"])  or bar_close_price
    bar_low         = float(completed_row["low"])   or bar_close_price

    sig: Dict[str, Any] = {}

    if new_bar_detected and bar_close_price > 0:
        # backtest 와 동일한 look-ahead 필터 적용
        sweep_by_tf, _ = build_sweep_at(
            dfs_by_tf, completed_ts_ms, entry_tf=entry_tf
        )
        try:
            sig = compute_signal(
                bar_close_price,
                sweep_by_tf,
                magnets,
                entry_tf=entry_tf,
                higher_tf=higher_tf,
            ) or {}
        except Exception as e:
            sig = {"error": str(e)}

        _last_bar_time[cache_key] = completed_ts_sec

    elif cache_key in _signal_cache:
        cached_sig = _signal_cache[cache_key]["state"].get("signal") or {}
        sig = {**cached_sig, "signal": "none"}

    state: Dict[str, Any] = {
        "symbol":        symbol,
        "current_price": bar_close_price,
        "signal":        sig,
        "by_tf":         {tf: {"signal": sig} for tf in tfs_list},
        "entry_tf":      entry_tf,
        "bar_high":      bar_high,
        "bar_low":       bar_low,
    }

    _signal_cache[cache_key] = {"state": state, "ts": now}

    if new_bar_detected and bar_close_price > 0:
        _tick_and_notify("eth_cvd_explosion", symbol, bar_close_price, state)

    return state
