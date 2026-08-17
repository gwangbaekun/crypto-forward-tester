"""Gamma Wall — Router (`drawdown_signal/router.py` 와 같은 구조).

읽기 엔드포인트는 전부 원장만 본다. DB/체인을 타는 건 `POST /run` 뿐이라
대시보드를 아무리 열어도 조회 비용은 하루 1회 스캔 그대로다.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse

from common.utils import render_template

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/quant/us_options_gamma_wall", tags=["us_options_gamma_wall"])

_TTL = 120


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return render_template("us_options_gamma_wall_dashboard.html")


@router.get("/stats", response_class=JSONResponse)
async def stats():
    """코호트별 집계 — out_of_sample 과 backfilled 를 절대 합치지 않는다."""
    try:
        from features.strategy.us_options_gamma_wall.cache import gw_cache
        from features.strategy.us_options_gamma_wall.stats import build_stats

        return JSONResponse(gw_cache.get("stats", _TTL, build_stats))
    except Exception as e:
        logger.exception("gamma_wall stats error")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/ledger", response_class=JSONResponse)
async def ledger(limit: int = 200):
    try:
        from features.strategy.us_options_gamma_wall.cache import gw_cache
        from features.strategy.us_options_gamma_wall.stats import build_ledger_rows

        return JSONResponse({"rows": gw_cache.get(f"ledger:{limit}", _TTL,
                                                  lambda: build_ledger_rows(limit))})
    except Exception as e:
        logger.exception("gamma_wall ledger error")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/levels", response_class=JSONResponse)
async def levels():
    """오늘 쓸 벽 레벨 — 원장의 마지막 세션."""
    try:
        from features.strategy.us_options_gamma_wall.cache import gw_cache
        from features.strategy.us_options_gamma_wall.stats import latest_levels

        return JSONResponse(gw_cache.get("levels", _TTL, latest_levels))
    except Exception as e:
        logger.exception("gamma_wall levels error")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/datasource", response_class=JSONResponse)
async def datasource():
    """DB 연결·테이블 신선도·원장 상태. 조회가 수 초 걸려 별도 캐시 키를 쓴다."""
    try:
        from features.strategy.us_options_gamma_wall.cache import gw_cache
        from features.strategy.us_options_gamma_wall.engine import datasource_status

        return JSONResponse(gw_cache.get("datasource", _TTL, datasource_status))
    except Exception as e:
        logger.exception("gamma_wall datasource error")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/run", response_class=JSONResponse)
async def run_now(background: BackgroundTasks, dry: bool = False):
    """수동 스캔. 스케줄러 틱과 락을 공유하므로 동시에 원장을 덮지 않는다."""
    from features.strategy.us_options_gamma_wall import engine
    from features.strategy.us_options_gamma_wall.cache import gw_cache

    if engine.is_running():
        return JSONResponse({"status": "already_running"}, status_code=409)

    def _job():
        try:
            engine.run_exclusive(dry=dry)
        finally:
            gw_cache.invalidate()

    background.add_task(_job)
    return JSONResponse({"status": "started", "dry": dry})
