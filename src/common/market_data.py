"""
Binance/공개 API: LSR, 멀티 거래소 OI (home 스트림 전용).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import httpx

TIMEOUT = 10.0

_cache_oi: Dict[str, tuple] = {}
_cache_oi_ts: Dict[str, float] = {}


async def _get_json(client: httpx.AsyncClient, url: str, params: dict | None = None) -> Optional[Any]:
    try:
        resp = await client.get(url, params=params or {})
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[market_data] fetch error {url}: {e}")
    return None


async def fetch_binance_lsr(client: httpx.AsyncClient, symbol: str = "BTCUSDT") -> Optional[Dict[str, Any]]:
    data = await _get_json(
        client,
        "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
        {"symbol": symbol, "period": "15m", "limit": "1"},
    )
    if data and len(data) > 0:
        row = data[0]
        return {
            "exchange": "binance",
            "long_ratio": float(row["longAccount"]),
            "short_ratio": float(row["shortAccount"]),
            "long_short_ratio": float(row["longShortRatio"]),
            "time": int(row["timestamp"]),
        }
    return None


async def fetch_binance_oi(client: httpx.AsyncClient, symbol: str = "BTCUSDT") -> Optional[Dict[str, Any]]:
    data = await _get_json(client, "https://fapi.binance.com/fapi/v1/openInterest", {"symbol": symbol})
    if data:
        return {
            "exchange": "binance",
            "symbol": symbol,
            "oi": float(data["openInterest"]),
            "time": data.get("time"),
        }
    return None


async def fetch_bybit_oi(client: httpx.AsyncClient, symbol: str = "BTCUSDT") -> Optional[Dict[str, Any]]:
    data = await _get_json(
        client,
        "https://api.bybit.com/v5/market/open-interest",
        {"category": "linear", "symbol": symbol, "intervalTime": "15min", "limit": "1"},
    )
    if data and data.get("result", {}).get("list"):
        row = data["result"]["list"][0]
        return {
            "exchange": "bybit",
            "symbol": symbol,
            "oi": float(row["openInterest"]),
            "time": int(row["timestamp"]),
        }
    return None


async def fetch_okx_oi(client: httpx.AsyncClient, symbol: str = "BTC-USDT-SWAP") -> Optional[Dict[str, Any]]:
    data = await _get_json(
        client,
        "https://www.okx.com/api/v5/public/open-interest",
        {"instType": "SWAP", "instId": symbol},
    )
    if data and data.get("data"):
        row = data["data"][0]
        return {
            "exchange": "okx",
            "symbol": symbol,
            "oi": float(row["oi"]),
            "time": int(row["ts"]),
        }
    return None


async def fetch_all_oi(
    symbol_binance: str = "BTCUSDT",
    cache_seconds: int = 60,
) -> List[Dict[str, Any]]:
    key = symbol_binance
    now = time.time()
    if cache_seconds > 0 and key in _cache_oi_ts and (now - _cache_oi_ts[key]) < cache_seconds:
        return _cache_oi.get(key) or []

    okx_symbol = symbol_binance.replace("USDT", "") + "-USDT-SWAP"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        results = await asyncio.gather(
            fetch_binance_oi(client, symbol_binance),
            fetch_bybit_oi(client, symbol_binance),
            fetch_okx_oi(client, okx_symbol),
            return_exceptions=True,
        )
    out = [r for r in results if isinstance(r, dict)]
    if cache_seconds > 0:
        _cache_oi[key] = out
        _cache_oi_ts[key] = now
    return out
