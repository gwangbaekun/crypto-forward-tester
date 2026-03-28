"""
KlineBundleHub — Binance klines → sweep_by_tf 번들 (RealtimeDataHub 패턴).

각 TF: {"data": [ {time, open, high, low, close, volume, cvd_delta}, ... ]}

tradingview_mcp의 RealtimeDataHub와 동일하게:
  - 심볼별 싱글톤 캐시 (TTL_SECONDS)
  - stampede 방지 Lock
  - 캐시 유효 시 즉시 반환, 만료 시 병렬 fetch
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from common.binance_price_ws import get_cached_price
from common.binance_service import fetch_binance_klines

TTL_SECONDS = 15  # tradingview RealtimeDataHub 기본값과 동일


class KlineBundleHub:
    """싱글톤 kline 데이터 허브. TTL 캐시 + stampede 방지."""

    _instance: Optional["KlineBundleHub"] = None

    def __new__(cls) -> "KlineBundleHub":
        if cls._instance is None:
            obj = super().__new__(cls)
            obj._cache: Dict[str, SimpleNamespace] = {}
            obj._locks: Dict[str, asyncio.Lock] = {}
            cls._instance = obj
        return cls._instance

    async def get(self, symbol: str, tfs: List[str], bar_limit: int = 500) -> SimpleNamespace:
        """TTL 내 캐시 있으면 즉시 반환, 만료 시 fetch."""
        key = f"{symbol}:{','.join(sorted(tfs))}"
        cached = self._cache.get(key)
        if cached and (time.time() - cached.fetched_at) < TTL_SECONDS:
            return cached

        if key not in self._locks:
            self._locks[key] = asyncio.Lock()

        async with self._locks[key]:
            cached = self._cache.get(key)
            if cached and (time.time() - cached.fetched_at) < TTL_SECONDS:
                return cached
            bundle = await _fetch_bundle(symbol, tfs, bar_limit)
            self._cache[key] = bundle
            return bundle


def get_hub() -> KlineBundleHub:
    return KlineBundleHub()


async def _fetch_bundle(symbol: str, tfs: List[str], bar_limit: int) -> SimpleNamespace:
    """TF별 klines를 병렬 fetch → sweep_by_tf 구성."""
    price: Optional[float] = get_cached_price(symbol)

    async def _one_tf(tf: str):
        df = await fetch_binance_klines(symbol, interval=tf, limit=bar_limit)
        if df is None or df.empty:
            return tf, {"data": []}
        data = []
        for ts, row in df.iterrows():
            vol = float(row["Volume"])
            tb = float(row["TakerBuyBase"])
            data.append(
                {
                    "time": int(ts.timestamp() * 1000),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": vol,
                    "cvd_delta": 2.0 * tb - vol,
                }
            )
        return tf, {"data": data}

    results = await asyncio.gather(*[_one_tf(tf) for tf in tfs], return_exceptions=True)
    sweep_by_tf: Dict[str, Any] = {}
    for r in results:
        if isinstance(r, Exception):
            continue
        tf, data = r
        sweep_by_tf[tf] = data

    if not price:
        for tf in tfs:
            bars = sweep_by_tf.get(tf, {}).get("data") or []
            if bars:
                price = float(bars[-1]["close"])
                break

    bundle = SimpleNamespace(
        price=price,
        sweep_by_tf=sweep_by_tf,
        magnets={},
        fetched_at=time.time(),
    )
    return bundle


# ── 하위 호환 래퍼 (기존 build_kline_bundle 호출 유지) ─────────────────────────
async def build_kline_bundle(
    symbol: str,
    tfs: List[str],
    bar_limit: int = 500,
) -> SimpleNamespace:
    """get_hub().get() 래퍼 — 기존 코드 호환용."""
    return await get_hub().get(symbol, tfs, bar_limit)
