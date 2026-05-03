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
from common.liq_compute import compute_liq_level_map

TTL_SECONDS = 15


async def _fetch_liq_level_map(symbol: str, entry_tf: str) -> List[Dict]:
    """진입 직전 Binance REST에서 직접 연산. 캐시 없음."""
    try:
        return await compute_liq_level_map(symbol, entry_tf=entry_tf)
    except Exception as exc:
        print(f"[kline_bundle] liq 연산 실패 ({symbol} {entry_tf}): {exc}")
        return []


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

    async def get(self, symbol: str, tfs: List[str], entry_tf: str, bar_limit: int = 500) -> SimpleNamespace:
        """TTL 내 캐시 있으면 즉시 반환, 만료 시 fetch."""
        key = f"{symbol}:{','.join(sorted(tfs))}:{entry_tf}"
        cached = self._cache.get(key)
        if cached and (time.time() - cached.fetched_at) < TTL_SECONDS:
            return cached

        if key not in self._locks:
            self._locks[key] = asyncio.Lock()

        async with self._locks[key]:
            cached = self._cache.get(key)
            if cached and (time.time() - cached.fetched_at) < TTL_SECONDS:
                return cached
            bundle = await _fetch_bundle(symbol, tfs, entry_tf, bar_limit)
            self._cache[key] = bundle
            return bundle


def get_hub() -> KlineBundleHub:
    return KlineBundleHub()


async def _fetch_bundle(symbol: str, tfs: List[str], entry_tf: str, bar_limit: int = 500) -> SimpleNamespace:
    """TF별 klines + liq level_map 을 병렬 fetch → sweep_by_tf + magnets 구성."""
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

    all_results = await asyncio.gather(
        *[_one_tf(tf) for tf in tfs],
        _fetch_liq_level_map(symbol, entry_tf=entry_tf),
        return_exceptions=True,
    )

    liq_result = all_results[-1]
    kline_results = all_results[:-1]

    sweep_by_tf: Dict[str, Any] = {}
    for r in kline_results:
        if isinstance(r, Exception):
            continue
        tf, data = r
        sweep_by_tf[tf] = data

    level_map: List[Dict] = liq_result if isinstance(liq_result, list) else []

    if not price:
        for tf in tfs:
            bars = sweep_by_tf.get(tf, {}).get("data") or []
            if bars:
                price = float(bars[-1]["close"])
                break

    return SimpleNamespace(
        price=price,
        sweep_by_tf=sweep_by_tf,
        magnets={"level_map": level_map} if level_map else {},
        fetched_at=time.time(),
    )


async def build_kline_bundle(
    symbol: str,
    tfs: List[str],
    entry_tf: str,
    bar_limit: int = 500,
) -> SimpleNamespace:
    """get_hub().get() 래퍼 — 기존 코드 호환용."""
    return await get_hub().get(symbol, tfs, entry_tf, bar_limit)
