"""spc_oiaccel_combine — 합체 운용 대시보드 라우터.

표준 make_router(엔진 기반)와 달리, 합체는 자체 엔진이 없고 두 멤버 전략의
체결을 합산해 보여준다. 경량 라우터로 stats API + 간단 대시보드만 제공.
"""
from __future__ import annotations

import pathlib

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from features.strategy.spc_oiaccel_combine.coordinator import COMBINE_TAG, get_combined_stats

router = APIRouter(prefix=f"/quant/{COMBINE_TAG}", tags=[COMBINE_TAG])

_STATIC = pathlib.Path(__file__).parent / "static"


@router.get("/stats", response_class=JSONResponse)
async def stats() -> JSONResponse:
    """합산 equity/MDD/승률 (계좌 기여 = pnl_pct × notional_ratio 반영)."""
    try:
        return JSONResponse({"ok": True, **get_combined_stats()})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/members", response_class=JSONResponse)
async def members() -> JSONResponse:
    """멤버 전략별 개별 성적 (개별 페이퍼 ForwardTrade 기준)."""
    from features.strategy.common.config_loader import get_master_config
    from db.session import get_session
    from db.models import ForwardTrade

    master = get_master_config() or {}
    out = []
    session = get_session()
    try:
        for sid, cfg in master.items():
            if not isinstance(cfg, dict) or cfg.get("combine_group") != COMBINE_TAG:
                continue
            rows = (session.query(ForwardTrade)
                    .filter(ForwardTrade.strategy == sid,
                            ForwardTrade.status != "open").all())
            n = len(rows)
            wins = sum(1 for r in rows if (r.pnl_pct or 0) > 0)
            out.append({
                "strategy": sid,
                "symbol": cfg.get("symbol"),
                "notional_ratio": cfg.get("notional_ratio"),
                "closed_trades": n,
                "win_rate": round(wins / n * 100, 1) if n else 0,
            })
    finally:
        session.close()
    return JSONResponse({"ok": True, "members": out})


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    html = (_STATIC / "dashboard.html").read_text(encoding="utf-8")
    return HTMLResponse(html)
