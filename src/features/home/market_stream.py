"""
홈 실시간 스트림용 페이로드: markPrice 캐시 + Binance/공개 API 지표 (OI, 펀딩, LSR, CVD 프록시).
LiquidationService.detect_sweep 등 무거운 경로는 사용하지 않음 (data_analysis 미포함 환경 호환).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import httpx

from common.binance_price_ws import BinancePriceWS
from common.binance_service import fetch_binance_klines, fetch_cvd_seed
from common.market_data import fetch_all_oi, fetch_binance_lsr


async def _fetch_premium_index(client: httpx.AsyncClient, symbol: str) -> Optional[Dict[str, Any]]:
    try:
        r = await client.get(
            "https://fapi.binance.com/fapi/v1/premiumIndex",
            params={"symbol": symbol.upper()},
        )
        if r.status_code != 200:
            return None
        j = r.json()
        return {
            "mark_price": float(j["markPrice"]),
            "index_price": float(j["indexPrice"]),
            "last_funding_rate": float(j["lastFundingRate"]),
            "next_funding_time_ms": j.get("nextFundingTime"),
            "interest_rate": float(j["interestRate"]) if j.get("interestRate") is not None else None,
        }
    except Exception as e:
        return {"error": str(e)}


async def _fetch_ticker_24h(client: httpx.AsyncClient, symbol: str) -> Optional[Dict[str, Any]]:
    try:
        r = await client.get(
            "https://fapi.binance.com/fapi/v1/ticker/24hr",
            params={"symbol": symbol.upper()},
        )
        if r.status_code != 200:
            return None
        j = r.json()
        return {
            "last_price": float(j["lastPrice"]),
            "price_change_pct": float(j["priceChangePercent"]),
            "high": float(j["highPrice"]),
            "low": float(j["lowPrice"]),
            "volume_base": float(j["volume"]),
            "volume_quote": float(j["quoteVolume"]),
            "open": float(j["openPrice"]),
        }
    except Exception as e:
        return {"error": str(e)}


async def _fetch_last_bar_taker_cvd_proxy(symbol: str, interval: str = "15m") -> Optional[Dict[str, Any]]:
    """
    최근 완료 봉 기준: taker 매수/매도 불균형 프록시 (베이스 수량).
    delta ≈ 2 * taker_buy_base - volume
    """
    try:
        df = await fetch_binance_klines(symbol.upper(), interval=interval, limit=5)
        if df is None or len(df) < 2:
            return None
        # 마지막 행은 미완성일 수 있어 직전 봉 사용
        row = df.iloc[-2]
        vol = float(row["Volume"])
        tb = float(row["TakerBuyBase"])
        sell_base = max(0.0, vol - tb)
        delta = tb - sell_base
        return {
            "timeframe": interval,
            "open_time": str(row.name),
            "volume_base": vol,
            "taker_buy_base": tb,
            "taker_sell_base_est": sell_base,
            "taker_delta_base": delta,
        }
    except Exception as e:
        return {"error": str(e)}


async def _fetch_agg_cvd_tail(symbol: str, limit: int = 1000) -> Optional[Dict[str, Any]]:
    """최근 체결 기준 누적 CVD (fetch_cvd_seed 마지막 점)."""
    try:
        series = await fetch_cvd_seed(symbol, limit=limit)
        if not series:
            return None
        first_ts, first_cvd = series[0][0], series[0][1]
        last_ts, last_cvd = series[-1][0], series[-1][1]
        return {
            "trades_sampled": len(series),
            "cvd_first": first_cvd,
            "cvd_last": last_cvd,
            "cvd_delta_window": last_cvd - first_cvd,
            "window_start_ms": first_ts,
            "window_end_ms": last_ts,
        }
    except Exception as e:
        return {"error": str(e)}


async def build_market_stream_payload(
    symbol: str = "BTCUSDT",
    *,
    oi_cache_sec: int = 10,
) -> Dict[str, Any]:
    """
    WebSocket으로 브라우저에 보낼 단일 JSON.
    """
    sym = symbol.upper().strip() or "BTCUSDT"
    marks = BinancePriceWS().get_display_snapshot()
    errors: Dict[str, str] = {}
    out: Dict[str, Any] = {
        "kind": "market_stream",
        "server_ts": time.time(),
        "symbol": sym,
        "marks": marks,
        "premium": None,
        "ticker_24h": None,
        "open_interest": None,
        "long_short_ratio": None,
        "cvd": {},
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        results = await asyncio.gather(
            _fetch_premium_index(client, sym),
            _fetch_ticker_24h(client, sym),
            fetch_binance_lsr(client, sym),
            fetch_all_oi(sym, cache_seconds=oi_cache_sec),
            _fetch_last_bar_taker_cvd_proxy(sym, "15m"),
            _fetch_agg_cvd_tail(sym, 1000),
            return_exceptions=True,
        )
        premium, ticker24, lsr, oi_rows, bar_proxy, agg_cvd = _unwrap_gather(results)

    out["premium"] = premium
    if premium is None:
        errors["premium"] = "unavailable"
    elif isinstance(premium, dict) and premium.get("error"):
        errors["premium"] = str(premium["error"])

    out["ticker_24h"] = ticker24
    if ticker24 is None:
        errors["ticker_24h"] = "unavailable"
    elif isinstance(ticker24, dict) and ticker24.get("error"):
        errors["ticker_24h"] = str(ticker24["error"])

    out["long_short_ratio"] = lsr
    if lsr is None:
        errors["long_short_ratio"] = "unavailable"

    out["open_interest"] = _format_oi_rows(oi_rows or [])
    if oi_rows is None:
        errors["open_interest"] = "unavailable"

    out["cvd"] = {
        "last_closed_15m_bar": bar_proxy,
        "recent_agg_trades": agg_cvd,
    }
    for key, block in (("last_closed_15m_bar", bar_proxy), ("recent_agg_trades", agg_cvd)):
        if isinstance(block, dict) and block.get("error"):
            errors[f"cvd_{key}"] = str(block["error"])

    if errors:
        out["errors"] = errors
    return out


def _unwrap_gather(results: tuple) -> tuple:
    out = []
    for r in results:
        if isinstance(r, Exception):
            out.append(None)
        else:
            out.append(r)
    return tuple(out)


def _format_oi_rows(rows: Any) -> List[Dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        ex = r.get("exchange")
        oi = r.get("oi")
        t = r.get("time")
        out.append({"exchange": ex, "oi": oi, "time": t})
    return out
