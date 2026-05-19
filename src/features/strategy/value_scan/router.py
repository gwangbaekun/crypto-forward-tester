"""Value Scan — Router."""
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


@router.get("/positions", response_class=JSONResponse)
async def positions():
    try:
        from features.strategy.value_scan.engine import get_positions_with_pnl
        return JSONResponse({"positions": get_positions_with_pnl()})
    except Exception as e:
        logger.exception("positions error")
        return JSONResponse({"error": str(e), "positions": []}, status_code=500)


@router.get("/history", response_class=JSONResponse)
async def history(limit: int = Query(100)):
    try:
        from features.strategy.value_scan.engine import load_history
        hist = sorted(load_history(), key=lambda x: x.get("exit_date", ""), reverse=True)
        return JSONResponse({"history": hist[:limit]})
    except Exception as e:
        logger.exception("history error")
        return JSONResponse({"error": str(e), "history": []}, status_code=500)


@router.get("/stats", response_class=JSONResponse)
async def stats():
    try:
        from features.strategy.value_scan.engine import get_summary_stats
        return JSONResponse(get_summary_stats())
    except Exception as e:
        logger.exception("stats error")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/activity", response_class=JSONResponse)
async def activity():
    try:
        from features.strategy.value_scan.engine import get_last_activity
        return JSONResponse(get_last_activity())
    except Exception as e:
        logger.exception("activity error")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/scan/status", response_class=JSONResponse)
async def scan_status():
    from features.strategy.value_scan.engine import get_scan_status
    return JSONResponse(await get_scan_status())


@router.post("/positions/migrate", response_class=JSONResponse)
async def migrate_positions():
    """구 포맷(entry_price/entry_date) → 신 포맷(lots/invested_usd) 마이그레이션."""
    try:
        from features.strategy.value_scan.engine import (
            load_positions, _save_positions, UNIT_USD
        )
        positions = load_positions()
        migrated = 0
        for pos in positions.values():
            if "lots" not in pos:
                entry_price = pos.pop("entry_price", None)
                entry_date  = pos.pop("entry_date", None)
                pos["first_entry_date"] = entry_date
                pos["lots"]             = [{"date": entry_date, "price": entry_price}]
                pos["invested_usd"]     = UNIT_USD
                migrated += 1
        _save_positions(positions)
        return JSONResponse({"migrated": migrated, "total": len(positions)})
    except Exception as e:
        logger.exception("migrate error")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/positions/fix_prices", response_class=JSONResponse)
async def fix_null_prices():
    """lots 중 price가 null인 항목을 현재 종가로 backfill."""
    try:
        import math
        from features.strategy.value_scan.engine import (
            load_positions, _fetch_price, _save_positions
        )
        positions = load_positions()
        fixed_lots = 0
        for pos in positions.values():
            price = None
            for lot in pos.get("lots", []):
                if lot.get("price") is None:
                    if price is None:
                        price = _fetch_price(pos["market"], pos["symbol"])
                    if not math.isnan(price):
                        lot["price"] = price
                        fixed_lots += 1
        _save_positions(positions)
        return JSONResponse({"fixed_lots": fixed_lots})
    except Exception as e:
        logger.exception("fix_prices error")
        return JSONResponse({"error": str(e)}, status_code=500)


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
