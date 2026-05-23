"""Polymarket 대시보드 라우터."""
from __future__ import annotations

from datetime import datetime, date, UTC
from itertools import groupby

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse

from common.utils import render_template

INITIAL_CAPITAL = 100.0

router = APIRouter(prefix="/quant/polymarket", tags=["polymarket"])


def _parse_since(since: str | None) -> datetime | None:
    """'YYYY-MM-DD' 문자열 → UTC datetime. None이면 None 반환."""
    if not since:
        return None
    try:
        return datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return render_template("polymarket_dashboard.html")


@router.get("/markets")
async def markets() -> JSONResponse:
    """현재 모니터링 중인 마켓 목록 (LC + PH 합산)."""
    try:
        import time
        from features.strategy.polymarket.late_convergence.engine import get_markets as lc_markets
        from features.strategy.polymarket.pair_hedge.engine import get_markets as ph_markets

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
    strategy: str = Query("all"),
    limit: int = Query(100),
    resolved: str = Query("all"),
    outcome: str = Query("all"),
    since: str | None = Query(None, description="YYYY-MM-DD (created_at 기준)"),
) -> JSONResponse:
    try:
        from db.session import get_session
        from db.models import PolymarketSignal
        from sqlalchemy import select, desc

        since_dt = _parse_since(since)
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
            if since_dt:
                stmt = stmt.where(PolymarketSignal.created_at >= since_dt)
            stmt = stmt.limit(limit)
            rows = db.execute(stmt).scalars().all()

            data = [
                {
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
                }
                for r in rows
            ]
            return JSONResponse({"signals": data, "total": len(data)})
        finally:
            db.close()
    except Exception as e:
        return JSONResponse({"error": str(e), "signals": []}, status_code=500)


@router.get("/cumulative-pnl")
async def cumulative_pnl(
    strategy: str = Query("late_convergence"),
    initial: float = Query(INITIAL_CAPITAL),
    since: str | None = Query(None, description="YYYY-MM-DD (resolved_at 기준)"),
) -> JSONResponse:
    """
    해소된 시그널을 날짜별로 묶어 균등 배분 + 재투자했을 때 누적 자산 곡선.
    since 지정 시 해당 날짜 이후 resolved 데이터만 사용.
    """
    try:
        from db.session import get_session
        from db.models import PolymarketSignal
        from sqlalchemy import select

        since_dt = _parse_since(since)
        db = get_session()
        try:
            stmt = (
                select(PolymarketSignal)
                .where(
                    PolymarketSignal.is_resolved == 1,
                    PolymarketSignal.actual_pnl.isnot(None),
                )
                .order_by(PolymarketSignal.resolved_at)
            )
            if strategy != "all":
                stmt = stmt.where(PolymarketSignal.strategy == strategy)
            if since_dt:
                stmt = stmt.where(PolymarketSignal.resolved_at >= since_dt)
            rows = db.execute(stmt).scalars().all()

            if not rows:
                return JSONResponse({"curve": [], "final": initial, "total_pct": 0.0, "initial": initial})

            def day_key(r):
                ts = r.resolved_at or r.created_at
                return ts.date() if ts else date(2000, 1, 1)

            capital = initial
            curve = [{"date": "start", "capital": round(capital, 4), "trades": 0, "wins": 0}]

            for day, group in groupby(rows, key=day_key):
                batch = list(group)
                n = len(batch)
                per_bet = capital / n
                day_capital = 0.0
                wins = 0
                for r in batch:
                    day_capital += per_bet * (1.0 + r.actual_pnl)
                    if r.actual_pnl > 0:
                        wins += 1
                capital = day_capital
                curve.append({
                    "date":    str(day),
                    "capital": round(capital, 4),
                    "trades":  n,
                    "wins":    wins,
                })

            total_pct = (capital - initial) / initial * 100
            return JSONResponse({
                "initial":   initial,
                "final":     round(capital, 4),
                "total_pct": round(total_pct, 2),
                "curve":     curve,
            })
        finally:
            db.close()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/reset")
async def reset_signals(confirm: str = Query(..., description="'yes'를 입력해야 실행")) -> JSONResponse:
    """polymarket_signals 전체 삭제. confirm=yes 필수."""
    if confirm != "yes":
        return JSONResponse({"error": "confirm=yes 필요"}, status_code=400)
    try:
        from db.session import get_session
        from db.models import PolymarketSignal
        from sqlalchemy import delete

        db = get_session()
        try:
            result = db.execute(delete(PolymarketSignal))
            db.commit()
            return JSONResponse({"deleted": result.rowcount})
        except Exception as e:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/stats")
async def stats(
    since: str | None = Query(None, description="YYYY-MM-DD (created_at 기준)"),
) -> JSONResponse:
    try:
        from db.session import get_session
        from db.models import PolymarketSignal
        from sqlalchemy import select

        since_dt = _parse_since(since)
        db = get_session()
        try:
            stmt = select(PolymarketSignal)
            if since_dt:
                stmt = stmt.where(PolymarketSignal.created_at >= since_dt)
            rows = db.execute(stmt).scalars().all()

            total = len(rows)
            resolved   = [r for r in rows if r.is_resolved]
            unresolved = [r for r in rows if not r.is_resolved]

            pnls     = [r.actual_pnl for r in resolved if r.actual_pnl is not None]
            avg_pnl  = sum(pnls) / len(pnls) if pnls else None
            win      = sum(1 for p in pnls if p > 0)
            loss     = sum(1 for p in pnls if p < 0)
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
                by_strategy[s]["avg_pnl"]  = sum(ps) / len(ps) if ps else None
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
