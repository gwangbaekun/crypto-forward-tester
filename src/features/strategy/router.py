"""
Quant strategy 가 소비하는 API.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/strategy", tags=["strategy"])


@router.get(
    "/market-snapshot",
    summary="실시간 마크·펀딩 등 (quant용)",
    description="`/api/market-stream`과 동일 페이로드 (전략이 틱/폴링용으로 사용).",
)
async def market_snapshot(symbol: str = Query("BTCUSDT")):
    from features.home.market_stream import build_market_stream_payload

    sym = (symbol or "BTCUSDT").strip().upper() or "BTCUSDT"
    return await build_market_stream_payload(sym)
