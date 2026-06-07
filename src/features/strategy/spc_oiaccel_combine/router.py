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
            # 닫힌 트레이드 — strategy 태그로 직접 조회 (symbol 필터 없음 → 누락 방지)
            closed = (session.query(ForwardTrade)
                      .filter(ForwardTrade.strategy == sid,
                              ForwardTrade.status != "open")
                      .order_by(ForwardTrade.opened_at.asc()).all())
            n = len(closed)
            wins = sum(1 for r in closed if (r.pnl_pct or 0) > 0)
            # 개별 누적손익 — pnl_pct_net 복리 (포지션 명목 기준, 비중 미적용)
            eq = 100.0
            for r in closed:
                eq *= (1 + (r.pnl_pct_net if r.pnl_pct_net is not None else (r.pnl_pct or 0)) / 100.0)
            total_pnl = round(eq - 100.0, 4)
            # 현재 오픈 포지션
            op = (session.query(ForwardTrade)
                  .filter(ForwardTrade.strategy == sid,
                          ForwardTrade.status == "open")
                  .order_by(ForwardTrade.opened_at.desc()).first())
            position = None
            if op:
                position = {"side": op.side, "entry_price": op.entry_price}
            out.append({
                "strategy": sid,
                "symbol": cfg.get("symbol"),
                "notional_ratio": cfg.get("notional_ratio"),
                "closed_trades": n,
                "win_rate": round(wins / n * 100, 1) if n else 0,
                "total_pnl_pct": total_pnl,
                "position": position,
            })
    finally:
        session.close()
    return JSONResponse({"ok": True, "members": out})


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    html = (_STATIC / "dashboard.html").read_text(encoding="utf-8")
    return HTMLResponse(html)
