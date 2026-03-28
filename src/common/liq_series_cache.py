"""
1h 캔들 + OI + 테이커 불균형(backtest `liq_cache_builder`와 동일 입력)을 Binance REST로 수집해
Redis(또는 메모리)에 `window=400` 대비 2배인 `retain_bars=800`만 저장.

backtest `serve_liq_cache` / `liq_cache_builder` 기준:
- interval 1h, window=400, min_bars=50
- 저장 상한: max(window) * 2 = 800봉
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
import pandas as pd

from common.oi_liq_map import build_oi_liq_map, compute_direction

# backtest DEFAULT_WINDOW / scripts 기본과 동일
LIQ_WINDOW = int(os.getenv("LIQ_WINDOW", "400"))
# 최대 룩백(window)의 2배만 Redis에 유지
LIQ_RETAIN_BARS = int(os.getenv("LIQ_RETAIN_BARS", str(LIQ_WINDOW * 2)))
LIQ_MIN_BARS = int(os.getenv("LIQ_MIN_BARS", "50"))
LIQ_REFRESH_SEC = float(os.getenv("LIQ_REFRESH_SEC", "120"))
# 캐시 cold start 시 첫 API 요청에서 Binance REST로 즉시 채움
LIQ_ON_DEMAND_FETCH = os.getenv("LIQ_ON_DEMAND_FETCH", "true").lower() in ("1", "true", "yes", "on")
REDIS_URL = os.getenv("REDIS_URL", "").strip()
REDIS_KEY_PREFIX = os.getenv("REDIS_KEY_PREFIX", "forwardtest:v1")
REDIS_TTL_SEC = int(os.getenv("REDIS_TTL_SEC", "7200"))

_memory_payload: Dict[str, Dict[str, Any]] = {}
_memory_lock = asyncio.Lock()
_redis_client: Any = None
_sym_fetch_locks: Dict[str, asyncio.Lock] = {}
_sym_fetch_locks_guard = asyncio.Lock()


def _redis_key(symbol: str) -> str:
    sym = symbol.upper().replace(" ", "")
    return f"{REDIS_KEY_PREFIX}:liq:{sym}"


async def _get_redis():
    global _redis_client
    if not REDIS_URL:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        import redis.asyncio as redis  # type: ignore

        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        return _redis_client
    except Exception as exc:
        print(f"[liq_series_cache] redis unavailable: {exc}")
        return None


async def _cache_write(symbol: str, payload: Dict[str, Any]) -> None:
    r = await _get_redis()
    if r is not None:
        try:
            await r.set(_redis_key(symbol), json.dumps(payload, ensure_ascii=False), ex=REDIS_TTL_SEC)
            return
        except Exception as exc:
            print(f"[liq_series_cache] redis set failed: {exc}")
    async with _memory_lock:
        _memory_payload[symbol.upper()] = payload


async def get_cached_chart_payload(symbol: str) -> Optional[Dict[str, Any]]:
    sym = symbol.upper().strip() or "BTCUSDT"
    r = await _get_redis()
    if r is not None:
        try:
            raw = await r.get(_redis_key(sym))
            if raw:
                return json.loads(raw)
        except Exception as exc:
            print(f"[liq_series_cache] redis get failed: {exc}")
    async with _memory_lock:
        return _memory_payload.get(sym)


async def _lock_for_symbol(sym: str) -> asyncio.Lock:
    async with _sym_fetch_locks_guard:
        if sym not in _sym_fetch_locks:
            _sym_fetch_locks[sym] = asyncio.Lock()
        return _sym_fetch_locks[sym]


async def get_chart_payload_or_fetch(symbol: str) -> Optional[Dict[str, Any]]:
    """
    캐시 히트 시 그대로 반환. cold start(캐시 없음)이면 Binance REST로 빌드 후 캐시에 쓰고 반환.
    동일 심볼 동시 요청은 락으로 한 번만 fetch.
    """
    sym = symbol.upper().strip() or "BTCUSDT"
    if not LIQ_ON_DEMAND_FETCH:
        return await get_cached_chart_payload(sym)

    hit = await get_cached_chart_payload(sym)
    if hit:
        return hit

    lock = await _lock_for_symbol(sym)
    async with lock:
        hit2 = await get_cached_chart_payload(sym)
        if hit2:
            return hit2
        await refresh_symbol(sym)

    out = await get_cached_chart_payload(sym)
    if out and isinstance(out.get("meta"), dict):
        meta = dict(out["meta"])
        meta["cold_fetch"] = True
        return {**out, "meta": meta}
    return out


def build_strategy_liq_snapshot(payload: Dict[str, Any], *, include_series: bool) -> Dict[str, Any]:
    """
    Quant 전략용 고정 스키마 JSON. 청산 구간은 backtest `oi_liq_map.build_oi_liq_map` 결과와 동일 구조.
    """
    sym = payload.get("symbol")
    meta = dict(payload.get("meta") or {})
    meta["algorithm"] = "oi_liq_map_v1"
    meta["reference"] = "btc_backtest/data/oi_liq_map.py — build_oi_liq_map (동일 클러스터/랭킹)"
    meta["inputs_note"] = "1h bars + OI hist + 테이커 불균형(클로즈 봉, klines 기반)"

    if payload.get("error"):
        return {
            "schema_version": "1",
            "ok": False,
            "symbol": sym,
            "error": payload["error"],
            "meta": meta,
        }

    liq = payload.get("liq_latest") or {}
    m = liq.get("map") or {}
    out: Dict[str, Any] = {
        "schema_version": "1",
        "ok": True,
        "symbol": sym,
        "meta": meta,
        "current_price": m.get("current_price"),
        "method": m.get("method"),
        "direction": liq.get("direction"),
        "zones": {
            "long_liq_below_price": m.get("long_liq_zones", []),
            "short_liq_above_price": m.get("short_liq_zones", []),
        },
    }
    if include_series:
        out["series_1h"] = payload.get("chart")
    return out


async def fetch_klines_1h(client: httpx.AsyncClient, symbol: str, limit: int) -> List[list]:
    r = await client.get(
        "https://fapi.binance.com/fapi/v1/klines",
        params={"symbol": symbol.upper(), "interval": "1h", "limit": min(limit, 1500)},
    )
    r.raise_for_status()
    return r.json()


async def fetch_oi_hist_1h(client: httpx.AsyncClient, symbol: str, need: int) -> List[Dict[str, Any]]:
    """openInterestHist period=1h, 최대 500개 단위로 과거까지 이어붙임 (시간 오름차순)."""
    chunks: List[List[Dict[str, Any]]] = []
    end_time: Optional[int] = None
    sym = symbol.upper()
    collected = 0
    while collected < need:
        batch_need = min(500, need - collected)
        params: Dict[str, Any] = {"symbol": sym, "period": "1h", "limit": batch_need}
        if end_time is not None:
            params["endTime"] = end_time
        resp = await client.get("https://fapi.binance.com/futures/data/openInterestHist", params=params)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        chunks.append(batch)
        collected += len(batch)
        end_time = int(batch[0]["timestamp"]) - 1
        if len(batch) < batch_need:
            break
    if not chunks:
        return []
    merged: List[Dict[str, Any]] = []
    for ch in reversed(chunks):
        merged = list(ch) + merged
    by_ts: Dict[int, Dict[str, Any]] = {}
    for row in merged:
        by_ts[int(row["timestamp"])] = row
    sorted_rows = [by_ts[k] for k in sorted(by_ts.keys())]
    return sorted_rows[-need:] if len(sorted_rows) > need else sorted_rows


def _klines_to_df(raw: List[list]) -> pd.DataFrame:
    rows = []
    for k in raw:
        ot = int(k[0])
        o, h, lo, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
        vol = float(k[5])
        tb = float(k[9]) if len(k) > 9 else 0.0
        sell = max(0.0, vol - tb)
        taker_delta = tb - sell
        rows.append(
            {
                "open_time_ms": ot,
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": vol,
                "cvd_delta": taker_delta,
            }
        )
    return pd.DataFrame(rows)


def _merge_oi(df_k: pd.DataFrame, oi_rows: List[Dict[str, Any]]) -> pd.DataFrame:
    if not oi_rows:
        df = df_k.copy()
        df["oi"] = float("nan")
        df["oi_delta"] = 0.0
        return df
    df_oi = pd.DataFrame(oi_rows)
    df_oi["open_time_ms"] = df_oi["timestamp"].astype("int64")
    df_oi["oi"] = df_oi["sumOpenInterest"].astype(float)
    df_oi = df_oi[["open_time_ms", "oi"]].sort_values("open_time_ms")
    df = df_k.sort_values("open_time_ms").copy()
    df = pd.merge_asof(df, df_oi, on="open_time_ms", direction="backward")
    df["oi"] = pd.to_numeric(df["oi"], errors="coerce").ffill()
    df["oi_delta"] = df["oi"].diff().fillna(0.0)
    return df


def _bars_for_map(df: pd.DataFrame) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        out.append(
            {
                "time": int(row["open_time_ms"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "oi": float(row["oi"]) if pd.notna(row.get("oi")) else 0.0,
                "oi_delta": float(row["oi_delta"]) if pd.notna(row.get("oi_delta")) else 0.0,
                "cvd_delta": float(row["cvd_delta"]),
            }
        )
    return out


async def build_payload_for_symbol(symbol: str) -> Dict[str, Any]:
    sym = symbol.upper().strip() or "BTCUSDT"
    retain = max(LIQ_RETAIN_BARS, LIQ_WINDOW + LIQ_MIN_BARS)
    t0 = time.time()
    async with httpx.AsyncClient(timeout=45.0) as client:
        raw_k = await fetch_klines_1h(client, sym, retain)
        if not raw_k:
            return {
                "symbol": sym,
                "error": "no_klines",
                "meta": {"updated_at": None, "retain_bars": retain, "window": LIQ_WINDOW},
            }
        df_k = _klines_to_df(raw_k)
        need_oi = len(df_k)
        oi_rows = await fetch_oi_hist_1h(client, sym, max(need_oi, LIQ_WINDOW + 10))
        df = _merge_oi(df_k, oi_rows)
        df = df.dropna(subset=["close"])
        if len(df) > retain:
            df = df.iloc[-retain:].reset_index(drop=True)

    bars_all = _bars_for_map(df)
    if len(bars_all) < LIQ_MIN_BARS:
        return {
            "symbol": sym,
            "error": "insufficient_bars",
            "meta": {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "retain_bars": retain,
                "window": LIQ_WINDOW,
                "bars": len(bars_all),
            },
        }

    window_slice = bars_all[-LIQ_WINDOW :] if len(bars_all) >= LIQ_WINDOW else bars_all
    last_close = float(window_slice[-1]["close"])
    liq = build_oi_liq_map(window_slice, current_price=last_close, min_bars=LIQ_MIN_BARS)
    direction = compute_direction(liq.get("long_liq_zones", []), liq.get("short_liq_zones", []))

    t_ms = [b["time"] for b in bars_all]
    chart = {
        "t_ms": t_ms,
        "close": [float(b["close"]) for b in bars_all],
        "oi": [float(b["oi"]) if b.get("oi") is not None else None for b in bars_all],
        "oi_delta": [float(b["oi_delta"]) for b in bars_all],
        "cvd_delta": [float(b["cvd_delta"]) for b in bars_all],
    }

    meta = {
        "window": LIQ_WINDOW,
        "retain_bars": retain,
        "min_bars": LIQ_MIN_BARS,
        "bars": len(bars_all),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "build_ms": round((time.time() - t0) * 1000, 1),
        "redis": bool(REDIS_URL),
    }

    return {
        "symbol": sym,
        "meta": meta,
        "chart": chart,
        "liq_latest": {
            "map": liq,
            "direction": direction,
        },
    }


async def refresh_symbol(symbol: str) -> None:
    try:
        payload = await build_payload_for_symbol(symbol)
        await _cache_write(symbol.upper(), payload)
    except Exception as exc:
        print(f"[liq_series_cache] refresh {symbol} failed: {exc}")


def _liq_symbols() -> List[str]:
    return [s.strip().upper() for s in os.getenv("LIQ_SYMBOLS", "BTCUSDT,ETHUSDT").split(",") if s.strip()]


async def refresh_loop() -> None:
    while True:
        for sym in _liq_symbols():
            await refresh_symbol(sym)
        await asyncio.sleep(LIQ_REFRESH_SEC)
