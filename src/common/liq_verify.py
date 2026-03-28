"""
1h Liq 캐시 데이터 일치 검증 (REST 재빌드·거래소 klines 샘플).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

import httpx

from common.liq_series_cache import build_payload_for_symbol, fetch_klines_1h, get_cached_chart_payload


def _close_tol(a: float, b: float) -> bool:
    tol = max(1e-4, 1e-9 * max(abs(a), abs(b), 1.0))
    return abs(a - b) <= tol


def _match_rate_float_series(
    a: List[float],
    b: List[float],
    *,
    skip_both_nan: bool = False,
) -> Tuple[float, int, int, List[int]]:
    """(일치율, 비교한 개수, 불일치 개수, 불일치 인덱스 최대 8개)."""
    n = min(len(a), len(b))
    if n == 0:
        return 0.0, 0, 0, []
    ok = 0
    total = 0
    bad: List[int] = []
    for i in range(n):
        x, y = a[i], b[i]
        if skip_both_nan and math.isnan(x) and math.isnan(y):
            continue
        if skip_both_nan and (math.isnan(x) or math.isnan(y)):
            total += 1
            if len(bad) < 8:
                bad.append(i)
            continue
        total += 1
        if _close_tol(x, y):
            ok += 1
        elif len(bad) < 8:
            bad.append(i)
    rate = ok / total if total else 0.0
    return round(rate, 6), total, total - ok, bad


def _to_float_list(xs: Any) -> List[float]:
    out: List[float] = []
    for v in xs or []:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            out.append(float("nan"))
    return out


def _zones_top_fp(liq: Dict[str, Any]) -> Tuple[Tuple[Any, ...], Tuple[Any, ...]]:
    m = (liq or {}).get("map") or {}
    lz = m.get("long_liq_zones") or []
    sz = m.get("short_liq_zones") or []

    def row(z: Dict[str, Any]) -> Tuple[float, float, str]:
        return (
            round(float(z.get("price_low", 0)), 2),
            round(float(z.get("price_high", 0)), 2),
            str(z.get("intensity", "")),
        )

    return (tuple(row(z) for z in lz[:5]), tuple(row(z) for z in sz[:5]))


async def run_liq_consistency_checks(symbol: str) -> Dict[str, Any]:
    """
    1) 캐시 vs 방금 REST로 다시 빌드한 페이로드 (종가·OI·테이커Δ 일치율 + 청산 구간 지문)
    2) 캐시 시계열 마지막 N봉 종가 vs Binance klines 동일 limit 직접 호출
    """
    sym = (symbol or "BTCUSDT").strip().upper() or "BTCUSDT"
    out: Dict[str, Any] = {"schema_version": "1", "symbol": sym, "checks": {}}

    cached = await get_cached_chart_payload(sym)
    fresh = await build_payload_for_symbol(sym)

    if not cached or cached.get("error"):
        out["checks"]["cache_vs_fresh"] = {
            "ok": False,
            "reason": "no_cache_or_error",
            "detail": cached.get("error") if isinstance(cached, dict) else None,
        }
    elif fresh.get("error"):
        out["checks"]["cache_vs_fresh"] = {
            "ok": False,
            "reason": "fresh_build_failed",
            "detail": fresh.get("error"),
        }
    else:
        cc = cached.get("chart") or {}
        fc = fresh.get("chart") or {}
        c_close = _to_float_list(cc.get("close"))
        f_close = _to_float_list(fc.get("close"))
        c_oi = _to_float_list(cc.get("oi"))
        f_oi = _to_float_list(fc.get("oi"))
        c_td = _to_float_list(cc.get("cvd_delta"))
        f_td = _to_float_list(fc.get("cvd_delta"))

        cr, ct, cm, cidx = _match_rate_float_series(c_close, f_close)
        oir, ot, om, oidx = _match_rate_float_series(c_oi, f_oi, skip_both_nan=True)
        tdr, tt, tm, tidx = _match_rate_float_series(c_td, f_td)

        zc = _zones_top_fp(cached.get("liq_latest") or {})
        zf = _zones_top_fp(fresh.get("liq_latest") or {})
        out["checks"]["cache_vs_fresh"] = {
            "ok": True,
            "note": "캐시 스냅샷 vs 동일 시점 REST 재빌드. 캐시가 오래됐으면 종가 일치율이 떨어질 수 있음.",
            "cached_updated_at": (cached.get("meta") or {}).get("updated_at"),
            "fresh_build_ms": (fresh.get("meta") or {}).get("build_ms"),
            "close": {"match_rate": cr, "compared": ct, "mismatch_count": cm, "mismatch_index_sample": cidx},
            "oi": {"match_rate": oir, "compared": ot, "mismatch_count": om, "mismatch_index_sample": oidx},
            "cvd_delta": {"match_rate": tdr, "compared": tt, "mismatch_count": tm, "mismatch_index_sample": tidx},
            "zones_top5_fingerprint_match": zc == zf,
        }

    last_n = 12
    if cached and not cached.get("error") and (cached.get("chart") or {}).get("close"):
        chart = cached["chart"]
        n = min(last_n, len(chart["close"]))
        cache_tail_close = _to_float_list(chart["close"][-n:])
        async with httpx.AsyncClient(timeout=25.0) as client:
            raw = await fetch_klines_1h(client, sym, n)
        ex_closes = [float(k[4]) for k in raw] if raw else []
        if len(ex_closes) == len(cache_tail_close):
            r2, t2, m2, idx2 = _match_rate_float_series(cache_tail_close, ex_closes)
            out["checks"]["cache_vs_binance_klines"] = {
                "ok": True,
                "note": f"캐시 시계열 마지막 {n}봉 종가 vs 동일 limit Binance fapi/v1/klines",
                "last_bars": n,
                "close_match_rate": r2,
                "compared": t2,
                "mismatch_count": m2,
                "mismatch_index_sample": idx2,
            }
        else:
            out["checks"]["cache_vs_binance_klines"] = {
                "ok": False,
                "reason": "length_mismatch",
                "cache_len": len(cache_tail_close),
                "exchange_len": len(ex_closes),
            }
    else:
        out["checks"]["cache_vs_binance_klines"] = {
            "ok": False,
            "reason": "no_cached_series",
        }

    return out
