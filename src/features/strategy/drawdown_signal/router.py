"""Drawdown Ladder — Router. (`value_scan/router.py` 와 같은 구조)

읽기 엔드포인트는 전부 원장만 본다 — 네트워크를 타는 건 `POST /run` 뿐이라
대시보드를 아무리 열어도 yfinance 호출량은 하루 1회 스캔 그대로다.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, JSONResponse

from common.utils import render_template

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/quant/drawdown_signal", tags=["drawdown_signal"])

_STATS_TTL = 120
_EPISODES_TTL = 120


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return render_template("drawdown_signal_dashboard.html")


@router.get("/stats", response_class=JSONResponse)
async def stats():
    """원장 집계 — 코호트(out-of-sample / backfilled)·자산군별."""
    try:
        from features.strategy.drawdown_signal.cache import dd_cache
        from features.strategy.drawdown_signal.stats import build_stats

        return JSONResponse(dd_cache.get("stats", _STATS_TTL, build_stats))
    except Exception as e:
        logger.exception("drawdown stats error")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/episodes", response_class=JSONResponse)
async def episodes(
    status: str | None = Query(None, description="open | closed"),
    cohort: str | None = Query(None, description="out_of_sample | backfilled"),
):
    try:
        from features.strategy.drawdown_signal.cache import dd_cache
        from features.strategy.drawdown_signal.stats import build_episodes

        key = f"episodes_{status or 'all'}_{cohort or 'all'}"
        return JSONResponse(
            dd_cache.get(key, _EPISODES_TTL, lambda: build_episodes(status, cohort))
        )
    except Exception as e:
        logger.exception("drawdown episodes error")
        return JSONResponse({"error": str(e), "open": [], "closed": []}, status_code=500)


@router.get("/status", response_class=JSONResponse)
async def status():
    """스케줄 상태 — 마지막 실행일·오늘 실행 여부·다음 실행 시각."""
    try:
        import datetime as dt

        from features.strategy.drawdown_signal.engine import (
            RUN_HOUR_UTC,
            UNIVERSE,
            is_due,
            is_running,
            load_ledger,
        )

        led, first = load_ledger()
        now = dt.datetime.now(dt.timezone.utc)
        today = now.date().isoformat()
        ran_today = led.get("last_run") == today

        next_run = now.replace(hour=RUN_HOUR_UTC, minute=0, second=0, microsecond=0)
        if ran_today or now.hour >= RUN_HOUR_UTC:
            next_run += dt.timedelta(days=1)

        return JSONResponse({
            "started_at": led.get("started_at"),
            "last_run": led.get("last_run"),
            "ran_today": ran_today,
            "first_run_pending": first,
            "due_now": is_due(),
            "running": is_running(),
            "run_hour_utc": RUN_HOUR_UTC,
            "next_run_utc": next_run.isoformat(),
            "universe_size": len(UNIVERSE),
        })
    except Exception as e:
        logger.exception("drawdown status error")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/universe", response_class=JSONResponse)
async def universe():
    """유니버스 — 자산군별로 묶어서. (원장에 아직 없는 종목도 보이게)"""
    from features.strategy.drawdown_signal.engine import UNIVERSE
    from features.strategy.drawdown_signal.stats import (
        ASSET_CLASS_LABELS,
        ASSET_CLASSES,
        asset_class,
    )

    groups: dict[str, list[dict]] = {c: [] for c in ASSET_CLASSES}
    for sym, name in UNIVERSE.items():
        groups[asset_class(sym)].append({"symbol": sym, "name": name})
    return JSONResponse({
        "total": len(UNIVERSE),
        "groups": [
            {"key": c, "label": ASSET_CLASS_LABELS[c], "symbols": groups[c]}
            for c in ASSET_CLASSES if groups[c]
        ],
    })


@router.post("/run", response_class=JSONResponse)
async def trigger_run(
    background_tasks: BackgroundTasks,
    dry: bool = Query(False, description="true 면 텔레그램 알림 없이 원장만 갱신"),
):
    """수동 1회 실행. 스케줄러와 같은 코드 경로(`engine.run_exclusive`)를 쓴다."""
    from features.strategy.drawdown_signal.engine import is_running

    # 여기서의 409 는 빠른 거절일 뿐이고, 실제 배타성은 run_exclusive 의 락이 보장한다.
    if is_running():
        return JSONResponse({"error": "run already in progress"}, status_code=409)

    def _run():
        try:
            from features.strategy.drawdown_signal.cache import dd_cache
            from features.strategy.drawdown_signal.engine import run_exclusive

            result = run_exclusive(dry=dry)
            if result is None:
                return
            dd_cache.invalidate()
            logger.info(
                "[Drawdown] manual run — 열림 %s · 신규 %s · 청산 %s",
                result["open"], len(result["new_signals"]), len(result["closed"]),
            )
        except Exception:
            logger.exception("[Drawdown] manual run failed")

    background_tasks.add_task(_run)
    return JSONResponse({"started": True, "dry": dry})
