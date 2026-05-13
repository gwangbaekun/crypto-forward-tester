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
    from .config_loader import get_signal_params_for_tf

    tf_norm = (tf or "15m").strip().lower()
    if tf_norm not in {"15m", "4h"}:
        tf_norm = "15m"

    params = get_signal_params_for_tf(tf_norm)
    vol_mult = float(params.get("vol_mult", 2.5))
    return JSONResponse({"bars": [], "vol_mult": vol_mult, "tf": tf_norm})
