"""Spot-Perp CVD Divergence — Router."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import Query
from fastapi.responses import JSONResponse

from features.strategy.common.router_factory import make_router

router = make_router("spot_perp_cvd", default_tfs="15m")

# ── 서버 캐싱 klines 엔드포인트 ───────────────────────────────────────────────
# 브라우저가 Binance를 직접 호출하면 IP 밴 위험.
# 서버가 1회 fetch → 캐시, 브라우저는 이 엔드포인트만 사용.
#
# TTL: 1h 봉 기준 55분 (봉 마감 직후 첫 요청에서만 Binance REST 호출)

_TF_SECONDS: Dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
}
_TTL_BUFFER = 60  # 봉 마감 60초 전에 미리 갱신

_klines_cache: Dict[str, Any] = {}  # key: f"{symbol}:{interval}:{limit}"


def _cache_ttl(interval: str) -> int:
    bar_sec = _TF_SECONDS.get(interval, 3600)
    return max(bar_sec - _TTL_BUFFER, 60)


@router.get("/klines", response_class=JSONResponse)
async def klines(
    symbol:   str = Query("ARBUSDT"),
    interval: str = Query("15m"),
    limit:    int = Query(150, ge=10, le=500),
) -> JSONResponse:
    """
    Binance Futures klines — 서버 캐시 경유.

    TTL = bar_seconds - 60s 이므로 브라우저가 아무리 자주 폴링해도
    Binance REST 는 봉당 최대 1회 호출됨.
    """
    key  = f"{symbol.upper()}:{interval}:{limit}"
    now  = time.time()
    ttl  = _cache_ttl(interval)
    hit  = _klines_cache.get(key)
    if hit and now - hit["ts"] < ttl:
        return JSONResponse({"ok": True, "candles": hit["candles"], "cached": True})

    from common.binance_service import fetch_binance_klines
    df = await fetch_binance_klines(symbol.upper(), interval=interval, limit=limit)

    if df is None or df.empty:
        # 캐시 있으면 stale 캐시라도 반환 — 밴 상태일 수 있음
        if hit:
            return JSONResponse({"ok": True, "candles": hit["candles"], "cached": True, "stale": True})
        return JSONResponse({"ok": False, "error": "Binance fetch 실패 (rate limit 또는 네트워크)"}, status_code=503)

    candles: List[Dict] = [
        {
            "time":  int(ts.timestamp()),
            "open":  float(row["Open"]),
            "high":  float(row["High"]),
            "low":   float(row["Low"]),
            "close": float(row["Close"]),
        }
        for ts, row in df.iterrows()
    ]
    _klines_cache[key] = {"candles": candles, "ts": now}
    return JSONResponse({"ok": True, "candles": candles, "cached": False})
