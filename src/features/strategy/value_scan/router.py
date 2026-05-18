"""Value Scan — Router."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, JSONResponse

from common.utils import render_template

router = APIRouter(prefix="/quant/value_scan", tags=["value_scan"])


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return render_template("value_scan_dashboard.html")


@router.get("/positions", response_class=JSONResponse)
async def positions():
    from features.strategy.value_scan.engine import get_positions_with_pnl
    return JSONResponse({"positions": get_positions_with_pnl()})


@router.get("/history", response_class=JSONResponse)
async def history(limit: int = Query(100)):
    from features.strategy.value_scan.engine import load_history
    hist = load_history()
    hist_sorted = sorted(hist, key=lambda x: x.get("exit_date", ""), reverse=True)
    return JSONResponse({"history": hist_sorted[:limit]})


@router.get("/stats", response_class=JSONResponse)
async def stats():
    from features.strategy.value_scan.engine import get_summary_stats
    return JSONResponse(get_summary_stats())


@router.get("/scan/status", response_class=JSONResponse)
async def scan_status():
    from features.strategy.value_scan.engine import get_scan_status
    return JSONResponse(get_scan_status())


@router.post("/scan", response_class=JSONResponse)
async def trigger_scan(
    background_tasks: BackgroundTasks,
    market: str = Query("all", description="kospi | nasdaq | all"),
):
    from features.strategy.value_scan.engine import get_scan_status, run_daily
    if get_scan_status()["running"]:
        return JSONResponse({"error": "scan already running"}, status_code=409)

    markets = ["kospi", "nasdaq"] if market == "all" else [market]

    def _run():
        run_daily(markets=markets)

    background_tasks.add_task(_run)
    return JSONResponse({"started": True, "market": market})
