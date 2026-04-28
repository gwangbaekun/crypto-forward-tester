"""OI CVD Surge — Data Feed (Forward Test).

Binance API → kline DataFrame + OI DataFrame
"""
from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

import pandas as pd

from common.binance_service import fetch_binance_klines, get_open_interest

# entry_tf → Binance OI hist period
_TF_TO_OI_PERIOD: Dict[str, str] = {
    "1m": "5m", "3m": "5m", "5m": "5m",
    "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h",
    "6h": "6h", "12h": "12h", "1d": "1d",
}


async def get_kline_df(
    symbol: str,
    tf: str,
    limit: int = 300,
) -> Optional[pd.DataFrame]:
    """
    Binance klines → DataFrame with columns:
      open_time_ms, open, high, low, close, volume, cvd_delta
    """
    raw = await fetch_binance_klines(symbol, interval=tf, limit=limit)
    if raw is None or raw.empty:
        return None
    df = pd.DataFrame({
        "open_time_ms": [int(ts.timestamp() * 1000) for ts in raw.index],
        "open":      raw["Open"].astype(float).values,
        "high":      raw["High"].astype(float).values,
        "low":       raw["Low"].astype(float).values,
        "close":     raw["Close"].astype(float).values,
        "volume":    raw["Volume"].astype(float).values,
        "cvd_delta": (2.0 * raw["TakerBuyBase"] - raw["Volume"]).astype(float).values,
    }).reset_index(drop=True)
    return df


async def get_oi_df(
    symbol: str,
    entry_tf: str,
    limit: int = 200,
) -> Optional[pd.DataFrame]:
    """
    Binance openInterestHist → DataFrame with columns:
      open_time_ms, open_interest
    """
    period = _TF_TO_OI_PERIOD.get(entry_tf, "1h")
    rows = await get_open_interest(symbol, period=period, limit=limit)
    if not rows:
        return None
    df = pd.DataFrame({
        "open_time_ms":  [int(r["timestamp"]) for r in rows],
        "open_interest": [float(r["sumOpenInterest"]) for r in rows],
    }).sort_values("open_time_ms").reset_index(drop=True)
    return df


async def get_merged_df(
    symbol: str,
    entry_tf: str,
    bar_limit: int = 300,
    oi_limit: int = 200,
) -> Optional[pd.DataFrame]:
    """
    kline df 와 OI df 를 open_time_ms 기준으로 병합.
    OI 없는 봉은 ffill.
    """
    kline_df, oi_df = await asyncio.gather(
        get_kline_df(symbol, entry_tf, limit=bar_limit),
        get_oi_df(symbol, entry_tf, limit=oi_limit),
        return_exceptions=True,
    )

    if isinstance(kline_df, Exception) or kline_df is None:
        return None

    df = kline_df.copy()

    if isinstance(oi_df, Exception) or oi_df is None:
        df["open_interest"] = float("nan")
        return df

    # merge_asof: OI 타임스탬프가 kline 과 완전히 일치하지 않을 때 가장 가까운 이전 값 사용
    df_sorted = df.sort_values("open_time_ms")
    oi_sorted = oi_df.sort_values("open_time_ms")

    merged = pd.merge_asof(
        df_sorted,
        oi_sorted.rename(columns={"open_interest": "open_interest"}),
        on="open_time_ms",
        direction="backward",
    )
    merged["open_interest"] = merged["open_interest"].ffill()
    return merged.reset_index(drop=True)
