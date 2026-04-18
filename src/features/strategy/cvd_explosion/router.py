"""CVD Explosion — Router."""
from fastapi import Query
from fastapi.responses import JSONResponse

from features.strategy.common.router_factory import make_router

router = make_router("cvd_explosion", default_tfs="15m,4h")


@router.get("/chart_data", response_class=JSONResponse)
async def chart_data(
    symbol: str = Query("BTCUSDT"),
    limit: int = Query(120),
    tf: str = Query("15m"),
):
    """
    선택 TF(기본 15m) 캔들 + vol_ratio + cvd_delta 반환 (차트 표시용).

    vol_ratio = 현재봉 볼륨 / 직전 vol_avg_window봉 평균볼륨 (signal.py 와 동일 로직).
    """
    from common.binance_service import fetch_binance_klines

    from .config_loader import get_signal_params_for_tf

    tf_norm = (tf or "15m").strip().lower()
    if tf_norm not in {"15m", "4h"}:
        tf_norm = "15m"

    params = get_signal_params_for_tf(tf_norm)
    vol_avg_window = int(params.get("vol_avg_window", 20))
    vol_mult = float(params.get("vol_mult", 2.5))

    df = await fetch_binance_klines(symbol, interval=tf_norm, limit=limit + vol_avg_window + 5)
    if df is None or df.empty:
        return JSONResponse({"bars": [], "vol_mult": vol_mult, "tf": tf_norm})

    vol = df["Volume"].astype(float)
    # signal.py 와 동일: 직전 vol_avg_window봉 평균 (현재봉 제외)
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
