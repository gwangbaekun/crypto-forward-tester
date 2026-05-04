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

PRE_ENTRY_SECONDS = 15.0


async def _fetch_liq_level_map(symbol: str, entry_tf: str) -> List[Dict]:
    try:
        from features.strategy.common.kline_bundle import _fetch_liq_level_map as _liq
        return await _liq(symbol, entry_tf=entry_tf)
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

    master_cfg = (get_master_config() or {}).get("eth_cvd_explosion_v2") or {}
    tfm = get_timeframes()
    entry_tf  = master_cfg.get("entry_tf")  or tfm["entry_tf"]
    higher_tf = master_cfg.get("higher_tf") or tfm["higher_tf"]
    tfs_list  = [x.strip() for x in tfs.split(",") if x.strip()]

    cache_key = f"eth_cvd_explosion_v2:{symbol}"
    now = _time.time()

    # ── pre-entry 윈도우 외에는 캐시만 반환 (REST fetch 없음) ───────────────
    _tf_sec = TF_TO_MINUTES.get(entry_tf, 60) * 60
    sec_to_close = _tf_sec - (now % _tf_sec)
    in_pre_entry_window = 0 < sec_to_close <= PRE_ENTRY_SECONDS

    if not in_pre_entry_window:
        cached = _signal_cache.get(cache_key)
        return cached["state"] if cached else {}

    # ── Full fetch: DataFrame + liq 병렬 ────────────────────────────────────
    fetch_results = await asyncio.gather(
        get_dfs_by_tf(symbol, tfs_list),
        _fetch_liq_level_map(symbol, entry_tf=entry_tf),
        return_exceptions=True,
    )
    if isinstance(fetch_results[0], Exception):
        raise fetch_results[0]
    dfs_by_tf: Dict[str, pd.DataFrame] = fetch_results[0]

    if isinstance(fetch_results[1], Exception):
        raise fetch_results[1]
    level_map: List[Dict] = fetch_results[1]
    if not level_map:
        raise ValueError(f"level_map empty for {symbol} {entry_tf} — candles present but liq compute returned nothing")

    magnets = {"level_map": level_map}

    # ── 현재 봉 기준 계산용 데이터 준비 ──────────────────────────────────────
    # 선진입 구조에서는 pre-entry(마감 60초) 구간에 forming 봉([-1])만 신호 계산에 사용.
    # completed 봉([-2])은 봉 전환 감지(new_bar_detected)용으로만 유지한다.
    entry_df = dfs_by_tf.get(entry_tf)
    if entry_df is None or len(entry_df) < 2:
        cached = _signal_cache.get(cache_key)
        return cached["state"] if cached else {}

    forming_row      = entry_df.iloc[-1]                        # 현재 forming 봉 (신호 계산용)
    completed_row    = entry_df.iloc[-2]                        # 직전 완성봉 (봉 전환 감지 보조)
    forming_ts_sec   = int(forming_row["open_time_ms"]) // 1000
    new_bar_detected = forming_ts_sec != _last_bar_time.get(cache_key, 0)

    forming_ts_ms = int(forming_row["open_time_ms"])
    bar_close_price = float(forming_row["close"]) or 0.0
    bar_high        = float(forming_row["high"])  or bar_close_price
    bar_low         = float(forming_row["low"])   or bar_close_price

    sig: Dict[str, Any] = {}
    should_compute = in_pre_entry_window and bar_close_price > 0

    if should_compute:
        # 선진입 전용: 마감 직전 현재 forming 봉 기반 신호 계산
        sweep_by_tf, _ = build_sweep_at(
            dfs_by_tf, forming_ts_ms, entry_tf=entry_tf
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

    if new_bar_detected:
        _last_bar_time[cache_key] = forming_ts_sec

    if not should_compute and cache_key in _signal_cache:
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

    if should_compute:
        _tick_and_notify("eth_cvd_explosion_v2", symbol, bar_close_price, state)

    return state
