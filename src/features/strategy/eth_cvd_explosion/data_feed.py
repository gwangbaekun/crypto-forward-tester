"""
ETH CVD Explosion — Data Feed (Forward Test).

Binance API → dfs_by_tf

backtest 의 data_feed.py 와 동일한 인터페이스:
  get_dfs_by_tf(...) → Dict[str, pd.DataFrame]

이후 파이프라인은 양쪽 동일:
  build_sweep_at(dfs_by_tf, ts_ms, entry_tf=entry_tf) → sweep_by_tf
  compute_signal(price, sweep_by_tf, magnets, ...)
"""
from __future__ import annotations

import asyncio
from typing import Dict, List

import pandas as pd

from common.binance_service import fetch_binance_klines


async def get_dfs_by_tf(
    symbol: str,
    tfs: List[str],
    bar_limit: int = 500,
) -> Dict[str, pd.DataFrame]:
    """Binance API에서 TF별 캔들 DataFrame 반환."""

    async def _one_tf(tf: str):
        df = await fetch_binance_klines(symbol, interval=tf, limit=bar_limit)
        if df is None or df.empty:
            return tf, pd.DataFrame(
                columns=["open_time_ms", "open", "high", "low", "close", "volume", "cvd_delta"]
            )
        result = pd.DataFrame({
            "open_time_ms": [int(ts.timestamp() * 1000) for ts in df.index],
            "open":      df["Open"].astype(float).values,
            "high":      df["High"].astype(float).values,
            "low":       df["Low"].astype(float).values,
            "close":     df["Close"].astype(float).values,
            "volume":    df["Volume"].astype(float).values,
            "cvd_delta": (2.0 * df["TakerBuyBase"] - df["Volume"]).astype(float).values,
        }).reset_index(drop=True)
        return tf, result

    results = await asyncio.gather(*[_one_tf(tf) for tf in tfs], return_exceptions=True)
    return {tf: df for r in results if not isinstance(r, Exception) for tf, df in [r]}
