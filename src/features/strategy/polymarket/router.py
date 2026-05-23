"""Polymarket 대시보드 라우터."""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse

from common.utils import render_template

router = APIRouter(prefix="/quant/polymarket", tags=["polymarket"])


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return render_template("polymarket_dashboard.html")


@router.get("/markets")
async def markets() -> JSONResponse:
    """현재 모니터링 중인 마켓 목록 (LC + PH 합산)."""
    try:
        from features.strategy.polymarket.late_convergence.engine import get_markets as lc_markets
        from features.strategy.polymarket.pair_hedge.engine import get_markets as ph_markets
        import time

        now = time.time()
        combined: dict[str, dict] = {}
        for m in lc_markets().values():
            combined[m.get("condition_id", "")] = m
        for m in ph_markets().values():
            combined[m.get("condition_id", "")] = m

        result = []
        for m in sorted(combined.values(), key=lambda x: x.get("end_ts") or 0):
            end_ts = m.get("end_ts")
            hours_left = (end_ts - now) / 3600 if end_ts else None
            result.append({
                "condition_id": m.get("condition_id"),
                "question":     m.get("question", "")[:120],
                "slug":         m.get("slug"),
                "yes_price":    m.get("yes_price"),
                "no_price":     m.get("no_price"),
                "volume_usd":   m.get("volume_usd"),
                "hours_to_end": round(hours_left, 2) if hours_left is not None else None,
                "end_ts":       end_ts,
            })
        return JSONResponse({"markets": result, "total": len(result)})
    except Exception as e:
        return JSONResponse({"error": str(e), "markets": []}, status_code=500)


@router.get("/signals")
async def signals(
    strategy: str = Query("all", description="all | late_convergence | pair_hedge | bayesian_fomc"),
    limit: int = Query(100),
    resolved: str = Query("all", description="all | yes | no"),
    outcome: str = Query("all", description="all | win | loss"),
) -> JSONResponse:
    try:
        from db.session import get_session
        from db.models import PolymarketSignal
        from sqlalchemy import select, desc

        db = get_session()
        try:
            stmt = select(PolymarketSignal).order_by(desc(PolymarketSignal.created_at))
            if strategy != "all":
                stmt = stmt.where(PolymarketSignal.strategy == strategy)
            if resolved == "yes":
                stmt = stmt.where(PolymarketSignal.is_resolved == 1)
            elif resolved == "no":
                stmt = stmt.where(PolymarketSignal.is_resolved == 0)
            if outcome == "win":
                stmt = stmt.where(PolymarketSignal.actual_pnl > 0)
            elif outcome == "loss":
                stmt = stmt.where(PolymarketSignal.actual_pnl < 0)
            stmt = stmt.limit(limit)
            rows = db.execute(stmt).scalars().all()

            data = []
            for r in rows:
                data.append({
                    "id":             r.id,
                    "strategy":       r.strategy,
                    "condition_id":   r.condition_id,
                    "question":       r.question,
                    "signal_type":    r.signal_type,
                    "side":           r.side,
                    "yes_price":      r.yes_price,
                    "no_price":       r.no_price,
                    "pair_cost":      r.pair_cost,
                    "divergence":     r.divergence,
                    "volume_usd":     r.volume_usd,
                    "hours_to_end":   r.hours_to_end,
                    "event_end_ts":   r.event_end_ts,
                    "is_resolved":    r.is_resolved,
                    "actual_outcome": r.actual_outcome,
                    "actual_pnl":     r.actual_pnl,
                    "resolved_at":    r.resolved_at.isoformat() if r.resolved_at else None,
                    "created_at":     r.created_at.isoformat() if r.created_at else None,
                })
            return JSONResponse({"signals": data, "total": len(data)})
        finally:
            db.close()
    except Exception as e:
        return JSONResponse({"error": str(e), "signals": []}, status_code=500)


@router.get("/stats")
async def stats() -> JSONResponse:
    try:
        from db.session import get_session
        from db.models import PolymarketSignal
        from sqlalchemy import select, func

        db = get_session()
        try:
            rows = db.execute(select(PolymarketSignal)).scalars().all()
            total = len(rows)
            resolved = [r for r in rows if r.is_resolved]
            unresolved = [r for r in rows if not r.is_resolved]

            pnls = [r.actual_pnl for r in resolved if r.actual_pnl is not None]
            avg_pnl = sum(pnls) / len(pnls) if pnls else None
            win = sum(1 for p in pnls if p > 0)
            loss = sum(1 for p in pnls if p < 0)
            win_rate = win / len(pnls) if pnls else None

            by_strategy: dict = {}
            for r in rows:
                s = r.strategy or "unknown"
                if s not in by_strategy:
                    by_strategy[s] = {"total": 0, "resolved": 0, "wins": 0, "pnls": []}
                by_strategy[s]["total"] += 1
                if r.is_resolved:
                    by_strategy[s]["resolved"] += 1
                    if r.actual_pnl is not None:
                        by_strategy[s]["pnls"].append(r.actual_pnl)
                        if r.actual_pnl > 0:
                            by_strategy[s]["wins"] += 1

            for s in by_strategy:
                ps = by_strategy[s]["pnls"]
                by_strategy[s]["avg_pnl"] = sum(ps) / len(ps) if ps else None
                by_strategy[s]["win_rate"] = by_strategy[s]["wins"] / len(ps) if ps else None
                del by_strategy[s]["pnls"]

            return JSONResponse({
                "total":       total,
                "resolved":    len(resolved),
                "unresolved":  len(unresolved),
                "wins":        win,
                "losses":      loss,
                "avg_pnl":     avg_pnl,
                "win_rate":    win_rate,
                "by_strategy": by_strategy,
            })
        finally:
            db.close()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
