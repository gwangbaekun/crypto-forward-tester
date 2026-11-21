from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, JSONResponse

from common.utils import render_template

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/quant/value_scan", tags=["value_scan"])


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return render_template("value_scan_dashboard.html")


@router.get("/mobile/dashboard", response_class=HTMLResponse)
async def mobile_dashboard():
    return render_template("value_scan_mobile.html")


@router.get("/activity", response_class=JSONResponse)
async def activity():
    try:
        from features.strategy.value_scan.cache import vs_cache
        from features.strategy.value_scan.engine import get_last_activity

        return JSONResponse(vs_cache.get("activity", 600, get_last_activity))
    except Exception as e:
        logger.exception("activity error")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/scan/status", response_class=JSONResponse)
async def scan_status():
    from features.strategy.value_scan.engine import get_scan_status
    return JSONResponse(await get_scan_status())


@router.get("/scan/schedule", response_class=JSONResponse)
async def scan_schedule():
    from features.strategy.value_scan.engine import is_scan_running
    from features.strategy.value_scan.scan_schedule import build_schedule_status

    return JSONResponse(build_schedule_status(running=is_scan_running()))


@router.get("/scan/results", response_class=JSONResponse)
async def scan_results(market: str = Query("nasdaq", description="kospi | nasdaq")):
    from features.strategy.value_scan.cache import vs_cache
    from features.strategy.value_scan.famous import row_is_famous
    from features.strategy.value_scan.repository import load_latest_scan_result

    data = load_latest_scan_result(market)
    for r in data["rows"]:
        r["is_famous"] = row_is_famous(r)
    if data.get("rows"):
        data = vs_cache.get(f"scan_results_{market}", 86400, lambda: data)
    return JSONResponse(data)


@router.post("/scan", response_class=JSONResponse)
async def trigger_scan(
    background_tasks: BackgroundTasks,
    market: str = Query("all", description="kospi | nasdaq | all"),
):
    from features.strategy.value_scan.engine import get_scan_status, run_daily
    status = await get_scan_status()
    if status["running"]:
        return JSONResponse({"error": "scan already running"}, status_code=409)

    markets = ["kospi", "nasdaq"] if market == "all" else [market]

    def _run():
        run_daily(markets=markets)

    background_tasks.add_task(_run)
    return JSONResponse({"started": True, "market": market})
