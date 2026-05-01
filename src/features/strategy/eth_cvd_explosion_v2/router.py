"""ETH CVD Explosion V2 — Router."""
from fastapi import Query
from fastapi.responses import JSONResponse

from features.strategy.common.router_factory import make_router

router = make_router("eth_cvd_explosion_v2", default_tfs="15m,1h,4h")


@router.get("/liq_levels", response_class=JSONResponse)
async def liq_levels(symbol: str = Query("ETHUSDT")):
    """3d/2w/1m 3개 window 병합 liq level_map 반환 (차트 표시용)."""
    from common.liq_series_cache import get_chart_payload_or_fetch

    try:
        payload = await get_chart_payload_or_fetch(symbol)
        if not payload or payload.get("error"):
            return JSONResponse({"ok": False, "levels": [], "by_window": {}})
        multi = payload.get("liq_multi_window") or {}
        merged = multi.get("merged") or []
        by_window = {k: {"bars": v["bars"], "count": len(v["level_map"])} for k, v in (multi.get("by_window") or {}).items()}
        return JSONResponse({"ok": True, "levels": merged, "by_window": by_window})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc), "levels": []}, status_code=500)


@router.get("/chart_data", response_class=JSONResponse)
async def chart_data(
    symbol: str = Query("ETHUSDT"),
    limit: int = Query(120),
    tf: str = Query("1h"),
):
    """
    선택 TF(기본 1h) 캔들 + vol_ratio + cvd_delta 반환 (차트 표시용).
    """
    from common.binance_service import fetch_binance_klines

    from .config_loader import get_signal_params_for_tf

    tf_norm = (tf or "1h").strip().lower()
    if tf_norm not in {"15m", "1h", "4h"}:
        tf_norm = "1h"

    params = get_signal_params_for_tf(tf_norm)
    vol_avg_window = int(params.get("vol_avg_window", 20))
    vol_mult = float(params.get("vol_mult", 2.5))

    df = await fetch_binance_klines(symbol, interval=tf_norm, limit=limit + vol_avg_window + 5)
    if df is None or df.empty:
        return JSONResponse({"bars": [], "vol_mult": vol_mult, "tf": tf_norm})

    vol = df["Volume"].astype(float)
    vol_avg = vol.shift(1).rolling(vol_avg_window).mean()
    vol_ratio = (vol / vol_avg).fillna(0.0)
    cvd_delta = 2.0 * df["TakerBuyBase"].astype(float) - vol

    bars_out = []
    for i in range(len(df)):
        ts = df.index[i]
        row = df.iloc[i]
        vr = float(vol_ratio.iloc[i])
        bars_out.append(
            {
                "time":         int(ts.timestamp()),
                "open":         float(row["Open"]),
                "high":         float(row["High"]),
                "low":          float(row["Low"]),
                "close":        float(row["Close"]),
                "vol_ratio":    round(vr, 3),
                "cvd_delta":    round(float(cvd_delta.iloc[i]), 1),
                "is_explosion": vr >= vol_mult,
            }
        )

    return JSONResponse({"bars": bars_out[-limit:], "vol_mult": vol_mult, "tf": tf_norm})
