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
    """멤버 전략별 성적 — 합체 시작 이후 기록만 (strategy=spc_oiaccel_combine을
    position_meta.src_strategy로 분해). 개별 전략의 과거 페이퍼 기록은 섞지 않는다."""
    import json
    from features.strategy.common.config_loader import get_master_config
    from db.session import get_session
    from db.models import ForwardTrade

    master = get_master_config() or {}
    meta = {}
    order = []
    for sid, cfg in master.items():
        if isinstance(cfg, dict) and cfg.get("combine_group") == COMBINE_TAG:
            meta[sid] = {"symbol": cfg.get("symbol"), "notional_ratio": cfg.get("notional_ratio")}
            order.append(sid)

    agg = {sid: {"closed": [], "open": None} for sid in order}
    session = get_session()
    try:
        rows = (session.query(ForwardTrade)
                .filter(ForwardTrade.strategy == COMBINE_TAG)
                .order_by(ForwardTrade.opened_at.asc()).all())
        for r in rows:
            src = None
            if r.position_meta:
                try:
                    src = json.loads(r.position_meta).get("src_strategy")
                except Exception:
                    pass
            if src not in agg:
                continue
            if r.status == "open":
                agg[src]["open"] = r
            else:
                agg[src]["closed"].append(r)
    finally:
        session.close()

    out = []
    for sid in order:
        closed = agg[sid]["closed"]
        n = len(closed)
        wins = sum(1 for r in closed if (r.pnl_pct or 0) > 0)
        eq = 100.0
        for r in closed:
            eq *= (1 + (r.pnl_pct_net if r.pnl_pct_net is not None else (r.pnl_pct or 0)) / 100.0)
        op = agg[sid]["open"]
        out.append({
            "strategy": sid,
            "symbol": meta[sid]["symbol"],
            "notional_ratio": meta[sid]["notional_ratio"],
            "closed_trades": n,
            "win_rate": round(wins / n * 100, 1) if n else 0,
            "total_pnl_pct": round(eq - 100.0, 4),
            "position": {"side": op.side, "entry_price": op.entry_price} if op else None,
        })
    return JSONResponse({"ok": True, "members": out})


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    html = (_STATIC / "dashboard.html").read_text(encoding="utf-8")
    return HTMLResponse(html)
