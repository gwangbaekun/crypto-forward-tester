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
async def stats(fast: bool = Query(False, description="DB only, no live quotes")):
    try:
        from features.strategy.value_scan.engine import get_book_stats, get_summary_stats

        payload = get_book_stats() if fast else get_summary_stats()
        return JSONResponse(payload)
    except Exception as e:
        logger.exception("stats error")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/famous", response_class=JSONResponse)
async def famous_watchlist():
    """static/famous_*.txt 워치리스트 + 오픈 포지션 매칭."""
    try:
        from features.strategy.value_scan.engine import get_positions_with_pnl
        from features.strategy.value_scan.famous import build_famous_status

        positions = get_positions_with_pnl()
        return JSONResponse(build_famous_status(positions))
    except Exception as e:
        logger.exception("famous error")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/activity", response_class=JSONResponse)
async def activity():
    try:
        from features.strategy.value_scan.engine import get_last_activity
        return JSONResponse(get_last_activity())
    except Exception as e:
        logger.exception("activity error")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/storage", response_class=JSONResponse)
async def storage_info():
    """포지션·청산 저장소 (DB + legacy json 경로)."""
    from db.config import get_engine_url
    from features.strategy.value_scan.paths import (
        DATA_DIR,
        HISTORY_FILE,
        LAST_ACTIVITY_FILE,
        POSITIONS_FILE,
        SCANS_DIR,
    )
    from features.strategy.value_scan.repository import db_counts

    return JSONResponse({
        "primary": "postgresql" if get_engine_url().startswith("postgres") else "sqlite",
        "database_url_set": bool(get_engine_url().startswith("postgres")),
        "tables": {
            "open": "value_scan_positions + value_scan_lots",
            "closed": "value_scan_closed_trades + value_scan_closed_lots",
        },
        "counts": db_counts(),
        "json_legacy": {
            "dir": str(DATA_DIR),
            "positions": str(POSITIONS_FILE),
            "positions_exists": POSITIONS_FILE.exists(),
            "history": str(HISTORY_FILE),
            "history_exists": HISTORY_FILE.exists(),
            "scans_dir": str(SCANS_DIR),
            "last_activity": str(LAST_ACTIVITY_FILE),
        },
    })


@router.post("/positions/restore-mistaken-exits", response_class=JSONResponse)
async def restore_mistaken_exits(
    market: str = Query("nasdaq", description="kospi | nasdaq"),
    exit_reason: str = Query("DELISTED"),
    exit_date: str | None = Query(None, description="YYYY-MM-DD, default all dates"),
):
    """타 시장 스캔 등으로 잘못 DELISTED 된 포지션을 오픈으로 되돌림."""
    try:
        from features.strategy.value_scan.repository import restore_mistaken_exits as _restore

        return JSONResponse(_restore(market, exit_reason=exit_reason, exit_date=exit_date))
    except Exception as e:
        logger.exception("restore mistaken exits error")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/migrate/json-to-db", response_class=JSONResponse)
async def migrate_json_to_db(archive: bool = Query(True)):
    """data/value_forward/positions.json·history.json → PostgreSQL/SQLite."""
    try:
        from features.strategy.value_scan.repository import migrate_json_files_to_db

        return JSONResponse(migrate_json_files_to_db(archive=archive))
    except Exception as e:
        logger.exception("json-to-db migrate error")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/scan/status", response_class=JSONResponse)
async def scan_status():
    from features.strategy.value_scan.engine import get_scan_status
    return JSONResponse(await get_scan_status())


@router.get("/scan/schedule", response_class=JSONResponse)
async def scan_schedule():
    """시장별 마지막 스캔 시각·오늘 스캔 여부 (DB)."""
    from features.strategy.value_scan.engine import is_scan_running
    from features.strategy.value_scan.scan_schedule import build_schedule_status

    return JSONResponse(build_schedule_status(running=is_scan_running()))


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


@router.post("/reset", response_class=JSONResponse)
async def reset_all(wipe_files: bool = Query(True)):
    """포지션·청산·스캔 메타 전부 삭제 후 처음부터."""
    try:
        from features.strategy.value_scan.repository import reset_value_scan_data

        return JSONResponse(reset_value_scan_data(wipe_files=wipe_files))
    except Exception as e:
        logger.exception("reset error")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


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
