"""
Quant strategy 가 소비하는 API. 홈(index.html)용 `/api/charts/liq`와 동일 캐시·REST 소스.
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from common.liq_series_cache import build_strategy_liq_snapshot, get_chart_payload_or_fetch

router = APIRouter(prefix="/api/strategy", tags=["strategy"])


@router.get(
    "/liq-snapshot",
    summary="OI 유도 청산 구간 스냅샷 (quant용)",
    description=(
        "캐시된 1h 빌드에서 최신 `build_oi_liq_map` 결과를 고정 스키마로 반환. "
        "`include_series=true`면 1h 시계열(종가·OI·테이커Δ) 포함."
    ),
)
async def liq_snapshot(
    symbol: str = Query("BTCUSDT", description="예: BTCUSDT, ETHUSDT"),
    include_series: bool = Query(
        False,
        description="true면 series_1h(t_ms, close, oi, oi_delta, cvd_delta) 포함",
    ),
):
    sym = (symbol or "BTCUSDT").strip().upper() or "BTCUSDT"
    payload = await get_chart_payload_or_fetch(sym)
    if not payload:
        return JSONResponse(
            {
                "schema_version": "1",
                "ok": False,
                "symbol": sym,
                "error": "unavailable",
                "hint": "캐시 없음 또는 REST 빌드 실패",
            },
            status_code=503,
        )
    return build_strategy_liq_snapshot(payload, include_series=include_series)


@router.get(
    "/market-snapshot",
    summary="실시간 마크·펀딩 등 (quant용)",
    description="`/api/market-stream`과 동일 페이로드 (전략이 틱/폴링용으로 사용).",
)
async def market_snapshot(symbol: str = Query("BTCUSDT")):
    from features.home.market_stream import build_market_stream_payload

    sym = (symbol or "BTCUSDT").strip().upper() or "BTCUSDT"
    return await build_market_stream_payload(sym)
