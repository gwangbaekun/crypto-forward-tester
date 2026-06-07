"""spc_oiaccel_combine — 합체 운용 대시보드 라우터 (v2).

combine 전용 DB 기록을 두지 않는다. 멤버 전략의 개별 forward_test 기록(strategy 태그)을
그대로 조회·합산하고, venue별 사이징은 config에서 읽어 보여준다.
"""
from __future__ import annotations

import pathlib

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from features.strategy.spc_oiaccel_combine.coordinator import COMBINE_TAG, load_combine_config

router = APIRouter(prefix=f"/quant/{COMBINE_TAG}", tags=[COMBINE_TAG])

_STATIC = pathlib.Path(__file__).parent / "static"


def _member_symbols() -> dict:
    """멤버 전략 → 심볼. combine 엔트리의 members 목록(단일 출처) 기준."""
    from features.strategy.common.config_loader import get_master_config, get_combine_members
    master = get_master_config() or {}
    return {sid: (master.get(sid, {}) or {}).get("symbol")
            for sid in get_combine_members(COMBINE_TAG)}


@router.get("/config", response_class=JSONResponse)
async def config() -> JSONResponse:
    """venue × member 사이징/레버리지 설정."""
    return JSONResponse({"ok": True, **load_combine_config()})


@router.get("/members", response_class=JSONResponse)
async def members() -> JSONResponse:
    """멤버별 개별 forward_test 성적(전략 태그 직접 조회) + venue 사이징.

    개별 전략은 독립 운영되며 자기 페이퍼 기록을 갖는다. combine은 그 기록을
    venue notional_ratio로 사이징해 실행할 뿐이므로, 성적은 개별 기록을 그대로 본다.
    """
    from db.session import get_session
    from db.models import ForwardTrade

    venues = load_combine_config().get("venues") or {}
    symbols = _member_symbols()
    out = []
    session = get_session()
    try:
        for sid, symbol in symbols.items():
            closed = (session.query(ForwardTrade)
                      .filter(ForwardTrade.strategy == sid,
                              ForwardTrade.status != "open")
                      .order_by(ForwardTrade.opened_at.asc()).all())
            n = len(closed)
            wins = sum(1 for r in closed if (r.pnl_pct or 0) > 0)
            eq = 100.0
            for r in closed:
                eq *= (1 + (r.pnl_pct_net if r.pnl_pct_net is not None else (r.pnl_pct or 0)) / 100.0)
            op = (session.query(ForwardTrade)
                  .filter(ForwardTrade.strategy == sid, ForwardTrade.status == "open")
                  .order_by(ForwardTrade.opened_at.desc()).first())
            # venue별 사이징 표 (enabled venue만)
            sizing = {}
            for venue, vcfg in venues.items():
                if isinstance(vcfg, dict) and vcfg.get("enabled"):
                    m = (vcfg.get("members") or {}).get(sid) or {}
                    if m.get("notional_ratio") is not None:
                        sizing[venue] = {"notional_ratio": m["notional_ratio"],
                                         "leverage": vcfg.get("leverage")}
            out.append({
                "strategy": sid,
                "symbol": symbol,
                "closed_trades": n,
                "win_rate": round(wins / n * 100, 1) if n else 0,
                "total_pnl_pct": round(eq - 100.0, 4),
                "position": {"side": op.side, "entry_price": op.entry_price} if op else None,
                "sizing": sizing,
            })
    finally:
        session.close()
    return JSONResponse({"ok": True, "members": out})


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    html = (_STATIC / "dashboard.html").read_text(encoding="utf-8")
    return HTMLResponse(html)
