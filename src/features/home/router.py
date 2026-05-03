"""Home UI: HTML only in templates; assets under static/home/{css,js}."""
from __future__ import annotations

import os

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from common.utils import render_template
from features.home.market_stream import build_market_stream_payload

router = APIRouter(tags=["home"])

_STREAM_INTERVAL_SEC = float(os.getenv("HOME_STREAM_INTERVAL_SEC", "1.0"))


@router.get("/", response_class=HTMLResponse)
async def home() -> str:
    return render_template(
        "home/index.html",
        poll_interval_sec=_STREAM_INTERVAL_SEC,
    )


@router.get("/api/market-stream")
async def market_stream_json(
    symbol: str = Query("BTCUSDT", description="예: BTCUSDT, ETHUSDT"),
) -> dict:
    """백엔드가 Binance WS 캐시·REST 등으로 모은 스냅샷 JSON. 브라우저는 이 엔드포인트만 폴링."""
    sym = (symbol or "BTCUSDT").strip().upper() or "BTCUSDT"
    return await build_market_stream_payload(sym)
