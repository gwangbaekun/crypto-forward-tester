"""
Spot-Perp CVD Divergence — Data Feed.

Fetches perp klines (Binance Futures) + spot klines (Binance Spot) in parallel.
Returns (perp_df, spot_df) where each has columns:
  open_time_ms, open, high, low, close, volume, cvd_delta
"""
from __future__ import annotations

import asyncio
from typing import Optional, Tuple

import httpx
import pandas as pd

from common.binance_service import fetch_binance_klines


async def _fetch_spot_klines(
    symbol: str,
    interval: str,
    limit: int = 200,
) -> Optional[pd.DataFrame]:
    """Binance SPOT klines (api.binance.com/api/v3/klines)."""
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol.upper(), "interval": interval, "limit": min(limit, 1000)}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            klines = resp.json()
    except Exception as e:
        print(f"[spot_perp_cvd data_feed] spot klines fetch error ({symbol} {interval}): {e}")
        return None

    if not klines:
        return None

    rows = []
    for k in klines:
        total_vol = float(k[5])
        taker_buy = float(k[9])
        rows.append({
            "open_time_ms": int(k[0]),
            "open":         float(k[1]),
            "high":         float(k[2]),
            "low":          float(k[3]),
            "close":        float(k[4]),
            "volume":       total_vol,
            "cvd_delta":    2.0 * taker_buy - total_vol,
        })
    return pd.DataFrame(rows).reset_index(drop=True)


async def get_dfs(
    symbol: str,
    interval: str = "1h",
    limit: int = 200,
) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """Returns (perp_df, spot_df). Either may be None on fetch failure."""
    perp_raw, spot_df = await asyncio.gather(
        fetch_binance_klines(symbol, interval=interval, limit=limit),
        _fetch_spot_klines(symbol, interval=interval, limit=limit),
        return_exceptions=True,
    )

    if isinstance(perp_raw, Exception) or perp_raw is None:
        perp_df = None
    else:
        perp_df = pd.DataFrame({
            "open_time_ms": [int(ts.timestamp() * 1000) for ts in perp_raw.index],
            "open":         perp_raw["Open"].astype(float).values,
            "high":         perp_raw["High"].astype(float).values,
            "low":          perp_raw["Low"].astype(float).values,
            "close":        perp_raw["Close"].astype(float).values,
            "volume":       perp_raw["Volume"].astype(float).values,
            "cvd_delta":    (2.0 * perp_raw["TakerBuyBase"] - perp_raw["Volume"]).astype(float).values,
        }).reset_index(drop=True)

    if isinstance(spot_df, Exception):
        spot_df = None

    return perp_df, spot_df
