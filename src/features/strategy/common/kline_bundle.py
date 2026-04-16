"""
KlineBundleHub — Binance klines → sweep_by_tf 번들 (RealtimeDataHub 패턴).

각 TF: {"data": [ {time, open, high, low, close, volume, cvd_delta}, ... ]}

tradingview_mcp의 RealtimeDataHub와 동일하게:
  - 심볼별 싱글톤 캐시 (TTL_SECONDS)
  - stampede 방지 Lock
  - 캐시 유효 시 즉시 반환, 만료 시 병렬 fetch

liq_series_cache 연동:
  - kline fetch와 동시에 liq level_map 조회 (on-demand cold fetch 포함)
  - backtest engine.py의 _zones_to_level_map과 동일 변환 로직
  - 실패 시 magnets={} 로 graceful fallback
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from common.binance_price_ws import get_cached_price
from common.binance_service import fetch_binance_klines
from common.liq_series_cache import get_chart_payload_or_fetch

TTL_SECONDS = 15  # tradingview RealtimeDataHub 기본값과 동일


def _zones_to_level_map(liq_map: Dict) -> List[Dict]:
    """
    long_liq_zones + short_liq_zones → flat level_map.
    backtest/src/strategies/cvd_explosion/engine.py 의 _zones_to_level_map 과 동일 로직.
    """
    out: List[Dict] = []
    for key in ("long_liq_zones", "short_liq_zones"):
        for z in (liq_map or {}).get(key) or []:
            lo = z.get("price_low") or z.get("price")
            hi = z.get("price_high") or z.get("price")
            if lo and hi:
                mid = (float(lo) + float(hi)) / 2
                out.append(
                    {
                        "price":     round(mid, 1),
                        "rank":      z.get("rank", 0),
                        "intensity": z.get("intensity", ""),
                        "oi_weight": round(float(z.get("oi_weight", 0)), 4),
                    }
                )
    return out


def _merge_level_maps(level_maps: List[List[Dict]]) -> List[Dict]:
    """
    가격 기준 중복 제거 (oi_weight 큰 항목 우선).
    backtest/src/strategies/cvd_explosion/engine.py 의 _merge_level_maps 와 동일 로직.
    """
    merged: Dict[float, Dict] = {}
    for levels in level_maps:
        for lvl in levels:
            try:
                p = round(float(lvl.get("price") or 0), 1)
            except (TypeError, ValueError):
                continue
            if p <= 0:
                continue
            cur = merged.get(p)
            try:
                oi_new = float(lvl.get("oi_weight") or 0)
                oi_cur = float((cur or {}).get("oi_weight") or 0)
            except (TypeError, ValueError):
                oi_new = oi_cur = 0.0
            if cur is None or oi_new > oi_cur:
                merged[p] = dict(lvl)
    out = list(merged.values())
    out.sort(key=lambda x: float(x.get("price") or 0))
    return out


async def _fetch_liq_level_map(symbol: str) -> List[Dict]:
    """
    liq_series_cache 에서 3d/2w/1m 윈도우 level_map 을 병합 반환.
    백테스트 _load_liq_ts_map_from_presets 와 동일 방식.
    실패 시 빈 리스트 반환 (magnets={} 로 graceful fallback).
    """
    try:
        payload = await get_chart_payload_or_fetch(symbol)
        if not payload or payload.get("error"):
            return []
        liq_latest = payload.get("liq_latest") or {}
        windows = liq_latest.get("windows") or {}
        if windows:
            maps = [_zones_to_level_map(windows[k]) for k in ("3d", "2w", "1m") if k in windows]
            return _merge_level_maps(maps)
        # fallback: 구버전 캐시(단일 맵)
        liq_map = liq_latest.get("map") or {}
        return _zones_to_level_map(liq_map)
    except Exception as exc:
        print(f"[kline_bundle] liq fetch 실패 ({symbol}): {exc}")
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

    # kline fetch (TF별) + liq level_map fetch 를 동시에 실행
    all_results = await asyncio.gather(
        *[_one_tf(tf) for tf in tfs],
        _fetch_liq_level_map(symbol),
        return_exceptions=True,
    )

    # 마지막 결과가 liq, 나머지가 TF별 kline
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

    bundle = SimpleNamespace(
        price=price,
        sweep_by_tf=sweep_by_tf,
        magnets={"level_map": level_map} if level_map else {},
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
