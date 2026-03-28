"""Home UI: HTML only in templates; assets under static/home/{css,js}."""
from __future__ import annotations

import os

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse

from common.liq_series_cache import LIQ_RETAIN_BARS, LIQ_WINDOW, get_chart_payload_or_fetch
from common.utils import render_template
from features.home.market_stream import build_market_stream_payload

router = APIRouter(tags=["home"])

_STREAM_INTERVAL_SEC = float(os.getenv("HOME_STREAM_INTERVAL_SEC", "1.0"))
_CHART_POLL_SEC = float(os.getenv("CHART_POLL_SEC", "90"))


@router.get("/", response_class=HTMLResponse)
async def home() -> str:
    return render_template(
        "home/index.html",
        poll_interval_sec=_STREAM_INTERVAL_SEC,
        chart_poll_sec=_CHART_POLL_SEC,
        liq_window=LIQ_WINDOW,
        liq_retain_bars=LIQ_RETAIN_BARS,
    )


@router.get("/api/market-stream")
async def market_stream_json(
    symbol: str = Query("BTCUSDT", description="예: BTCUSDT, ETHUSDT"),
) -> dict:
    """백엔드가 Binance WS 캐시·REST 등으로 모은 스냅샷 JSON. 브라우저는 이 엔드포인트만 폴링."""
    sym = (symbol or "BTCUSDT").strip().upper() or "BTCUSDT"
    return await build_market_stream_payload(sym)


@router.get("/api/charts/liq")
async def charts_liq_json(symbol: str = Query("BTCUSDT", description="1h 시계열 + OI liq map (Redis 캐시)")):
    """캐시 우선; cold start 시 Binance REST로 즉시 채움 (`get_chart_payload_or_fetch`)."""
    sym = (symbol or "BTCUSDT").strip().upper() or "BTCUSDT"
    payload = await get_chart_payload_or_fetch(sym)
    if not payload:
        return JSONResponse(
            {"error": "cache_empty", "hint": "REST 빌드 실패 또는 데이터 부족. 잠시 후 다시 시도하세요."},
            status_code=503,
        )
    return payload
