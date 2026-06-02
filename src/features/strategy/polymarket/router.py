"""Polymarket 대시보드 라우터."""
from __future__ import annotations

import time
from datetime import datetime, date, UTC
from itertools import groupby

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse

from common.utils import render_template

INITIAL_CAPITAL = 100.0

# 섹터 키워드 매핑 (소문자 매칭)
_SECTORS: dict[str, list[str]] = {
    "Weather":     ["weather", "rain", "temperature", "hurricane", "storm", "snow",
                    "tornado", "flood", "climate", "wind", "typhoon", "drought"],
    "Sports":      ["nba", "nfl", "nhl", "mlb", "soccer", "football", "baseball",
                    "tennis", "golf", "championship", "playoff", "league", "match",
                    "tournament", "super bowl", "world cup", "ufc", "boxing", "f1"],
    "Politics":    ["election", "president", "senate", "congress", "vote", "republican",
                    "democrat", "governor", "trump", "biden", "harris", "parliament",
                    "referendum", "ballot", "primary"],
    "Crypto":      ["bitcoin", "btc", "eth", "ethereum", "crypto", "sol", "doge",
                    "xrp", "bnb", "altcoin", "defi", "nft", "usdt", "stablecoin"],
    "Economics":   ["fed", "fomc", "inflation", "interest rate", "gdp", "recession",
                    "unemployment", "cpi", "ppi", "rate cut", "rate hike", "payroll",
                    "treasury", "yield"],
    "Entertainment": ["oscar", "grammy", "emmy", "award", "movie", "film", "actor",
                      "celebrity", "music", "album", "box office"],
}

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


@router.get("/analytics/dashboard", response_class=HTMLResponse)
async def analytics_dashboard():
    return render_template("polymarket_analytics.html")


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
                    "order_status":   r.order_status,
                    "poly_order_id":  r.poly_order_id,
                    "order_error":    r.order_error,
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


def _gambler_band_delete_stmt(min_price: float, max_price: float):
    """진입가가 [min_price, max_price] 인 시그널 삭제 (YES/NO/BOTH·단일 컬럼 모두)."""
    from db.models import PolymarketSignal
    from sqlalchemy import delete, or_, and_

    def _band(col):
        return and_(col.isnot(None), col >= min_price, col <= max_price)

    yes_band = _band(PolymarketSignal.yes_price)
    no_band = _band(PolymarketSignal.no_price)
    return delete(PolymarketSignal).where(
        or_(
            and_(PolymarketSignal.side == "YES", yes_band),
            and_(PolymarketSignal.side == "NO", no_band),
            # BOTH: 양쪽 가격이 모두 밴드 안 (0.1–0.4 실수 구간)
            and_(PolymarketSignal.side == "BOTH", yes_band, no_band),
            # 한쪽 컬럼만 채워진 LC row
            and_(PolymarketSignal.yes_price.isnot(None), PolymarketSignal.no_price.is_(None), yes_band),
            and_(PolymarketSignal.no_price.isnot(None), PolymarketSignal.yes_price.is_(None), no_band),
            and_(PolymarketSignal.side.is_(None), or_(yes_band, no_band)),
        )
    )


@router.delete("/reset-gambler-band")
async def reset_gambler_band_signals(
    confirm: str = Query(..., description="'yes'를 입력해야 실행"),
    min_price: float = Query(0.1, description="진입가 하한 (포함)"),
    max_price: float = Query(0.4, description="진입가 상한 (포함)"),
) -> JSONResponse:
    """0.10–0.40 등 잘못된 밴드 시그널만 삭제. confirm=yes 필수."""
    if confirm != "yes":
        return JSONResponse({"error": "confirm=yes 필요"}, status_code=400)
    if min_price > max_price:
        return JSONResponse({"error": "min_price must be <= max_price"}, status_code=400)
    try:
        from db.session import get_session

        db = get_session()
        try:
            result = db.execute(_gambler_band_delete_stmt(min_price, max_price))
            db.commit()
            return JSONResponse({
                "deleted": result.rowcount,
                "min_price": min_price,
                "max_price": max_price,
            })
        except Exception as e:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/reset-resolved")
async def reset_resolved_signals(confirm: str = Query(..., description="'yes'를 입력해야 실행")) -> JSONResponse:
    """resolved 시그널만 삭제. pending(미해소) 시그널은 보존."""
    if confirm != "yes":
        return JSONResponse({"error": "confirm=yes 필요"}, status_code=400)
    try:
        from db.session import get_session
        from db.models import PolymarketSignal
        from sqlalchemy import delete

        db = get_session()
        try:
            stmt = delete(PolymarketSignal).where(PolymarketSignal.is_resolved == 1)
            result = db.execute(stmt)
            db.commit()
            return JSONResponse({"deleted_resolved": result.rowcount})
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


# ──────────────────────────────────────────────
# Sector analytics
# ──────────────────────────────────────────────

def _entry_price_row(row) -> float | None:
    if row.side == "YES":
        return row.yes_price
    if row.side == "NO":
        return row.no_price
    return row.yes_price or row.no_price


def _dedupe_markets(rows: list) -> list:
    """condition_id 당 최신 resolved row 1건."""
    by_cid: dict[str, object] = {}
    for r in rows:
        cid = r.condition_id or f"id:{r.id}"
        prev = by_cid.get(cid)
        if prev is None:
            by_cid[cid] = r
            continue
        rt, pt = r.resolved_at, prev.resolved_at
        if rt and (not pt or rt > pt):
            by_cid[cid] = r
    return list(by_cid.values())


def _aggregate_sector_rows(rows: list) -> dict:
    from features.strategy.polymarket.sectors import classify_sector

    out: dict[str, dict] = {}
    for r in rows:
        sec = classify_sector(r.question)
        if sec not in out:
            out[sec] = {"signals": 0, "wins": 0, "losses": 0, "pending": 0, "pnls": []}
        out[sec]["signals"] += 1
        if not r.is_resolved:
            out[sec]["pending"] += 1
            continue
        if r.actual_pnl is None:
            continue
        out[sec]["pnls"].append(r.actual_pnl)
        if r.actual_pnl > 0:
            out[sec]["wins"] += 1
        elif r.actual_pnl < 0:
            out[sec]["losses"] += 1
    return out


@router.get("/analytics/overview")
async def analytics_overview(
    since: str | None = Query(None, description="YYYY-MM-DD (created_at)"),
    dedupe: str = Query("market", description="market | signal"),
    resolved_only: bool = Query(False),
) -> JSONResponse:
    """세분화 섹터별 집계 + 구 router Other 대비 breakdown."""
    try:
        from db.session import get_session
        from db.models import PolymarketSignal
        from sqlalchemy import select
        from features.strategy.polymarket.sectors import SECTOR_ORDER, classify_sector

        since_dt = _parse_since(since)
        db = get_session()
        try:
            stmt = select(PolymarketSignal).where(
                PolymarketSignal.strategy == "late_convergence",
            )
            if since_dt:
                stmt = stmt.where(PolymarketSignal.created_at >= since_dt)
            if resolved_only:
                stmt = stmt.where(PolymarketSignal.is_resolved == 1)
            rows = db.execute(stmt).scalars().all()
        finally:
            db.close()

        signal_rows = list(rows)
        market_rows = _dedupe_markets(signal_rows)
        use_rows = market_rows if dedupe == "market" else signal_rows
        agg = _aggregate_sector_rows(use_rows)
        raw_agg = _aggregate_sector_rows(signal_rows) if dedupe == "market" else agg

        sectors = []
        for sec in SECTOR_ORDER:
            d = agg.get(sec)
            if not d:
                continue
            resolved_n = d["wins"] + d["losses"]
            raw_n = raw_agg.get(sec, {}).get("signals", d["signals"])
            sectors.append({
                "sector":       sec,
                "label":        sec.replace("_", " "),
                "signals":      d["signals"],
                "raw_signals":  raw_n,
                "resolved":     resolved_n,
                "pending":      d["pending"],
                "wins":         d["wins"],
                "losses":       d["losses"],
                "win_rate":     round(d["wins"] / resolved_n, 4) if resolved_n else None,
                "avg_pnl":      round(sum(d["pnls"]) / len(d["pnls"]), 4) if d["pnls"] else None,
                "is_gambling":  sec.startswith("Esports") or sec.startswith("Sports_"),
            })

        router_other = sum(1 for r in use_rows if _sector(r.question or "") == "Other")
        granular_other = agg.get("Other", {}).get("signals", 0)

        return JSONResponse({
            "since":              since,
            "dedupe":             dedupe,
            "total_signals":      len(signal_rows),
            "total_markets":      len(market_rows),
            "rows_analyzed":      len(use_rows),
            "router_other_count": router_other,
            "granular_other":     granular_other,
            "sectors":            sectors,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/analytics/markets")
async def analytics_markets(
    sector: str = Query(..., description="세분화 섹터명"),
    since: str | None = Query(None),
    dedupe: str = Query("market"),
    limit: int = Query(50, le=200),
    sort: str = Query("signals", description="signals | loss | win_rate"),
) -> JSONResponse:
    """섹터 내 마켓(질문)별 집계."""
    try:
        from db.session import get_session
        from db.models import PolymarketSignal
        from sqlalchemy import select
        from features.strategy.polymarket.sectors import classify_sector

        since_dt = _parse_since(since)
        db = get_session()
        try:
            stmt = select(PolymarketSignal).where(
                PolymarketSignal.strategy == "late_convergence",
            )
            if since_dt:
                stmt = stmt.where(PolymarketSignal.created_at >= since_dt)
            rows = db.execute(stmt).scalars().all()
        finally:
            db.close()

        filtered = [r for r in rows if classify_sector(r.question) == sector]
        if dedupe == "market":
            filtered = _dedupe_markets(filtered)

        by_q: dict[str, dict] = {}
        for r in filtered:
            q = (r.question or "")[:500]
            if q not in by_q:
                by_q[q] = {
                    "question": q,
                    "condition_id": r.condition_id,
                    "signals": 0,
                    "wins": 0,
                    "losses": 0,
                    "entries": [],
                }
            by_q[q]["signals"] += 1
            e = _entry_price_row(r)
            if e is not None:
                by_q[q]["entries"].append(e)
            if r.is_resolved and r.actual_pnl is not None:
                if r.actual_pnl > 0:
                    by_q[q]["wins"] += 1
                elif r.actual_pnl < 0:
                    by_q[q]["losses"] += 1

        markets = []
        for d in by_q.values():
            entries = d.pop("entries")
            n = d["wins"] + d["losses"]
            markets.append({
                **d,
                "win_rate": round(d["wins"] / n, 4) if n else None,
                "avg_entry": round(sum(entries) / len(entries), 4) if entries else None,
            })

        if sort == "loss":
            markets.sort(key=lambda x: (-x["losses"], -x["signals"]))
        elif sort == "win_rate":
            markets.sort(key=lambda x: (-(x["win_rate"] or 0), -x["signals"]))
        else:
            markets.sort(key=lambda x: -x["signals"])

        return JSONResponse({
            "sector":  sector,
            "total":   len(markets),
            "markets": markets[:limit],
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/analytics/curve")
async def analytics_curve(
    since: str | None = Query(None),
    initial: float = Query(INITIAL_CAPITAL),
    include_sectors: str | None = Query(None, description="콤마 구분 포함 섹터"),
    exclude_sectors: str | None = Query(
        "Esports_Prop,Esports_Match,Sports_Prop,Sports_Match,Weather",
        description="콤마 구분 제외 섹터",
    ),
) -> JSONResponse:
    """필터된 섹터만으로 일별 재투자 PnL 곡선."""
    try:
        from db.session import get_session
        from db.models import PolymarketSignal
        from sqlalchemy import select
        from features.strategy.polymarket.sectors import classify_sector, DEFAULT_EXCLUDE

        since_dt = _parse_since(since)
        inc = {s.strip() for s in (include_sectors or "").split(",") if s.strip()}
        exc = {s.strip() for s in (exclude_sectors or "").split(",") if s.strip()} or set(DEFAULT_EXCLUDE)

        db = get_session()
        try:
            stmt = (
                select(PolymarketSignal)
                .where(
                    PolymarketSignal.strategy == "late_convergence",
                    PolymarketSignal.is_resolved == 1,
                    PolymarketSignal.actual_pnl.isnot(None),
                )
                .order_by(PolymarketSignal.resolved_at)
            )
            if since_dt:
                stmt = stmt.where(PolymarketSignal.resolved_at >= since_dt)
            rows = db.execute(stmt).scalars().all()
        finally:
            db.close()

        filtered = []
        for r in rows:
            sec = classify_sector(r.question)
            if inc and sec not in inc:
                continue
            if sec in exc:
                continue
            filtered.append(r)

        if not filtered:
            return JSONResponse({"curve": [], "final": initial, "total_pct": 0.0, "initial": initial, "trades": 0})

        def day_key(r):
            ts = r.resolved_at or r.created_at
            return ts.date() if ts else date(2000, 1, 1)

        capital = initial
        curve = [{"date": "start", "capital": round(capital, 4), "trades": 0, "wins": 0}]
        for day, group in groupby(filtered, key=day_key):
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
            "trades":    len(filtered),
            "curve":     curve,
            "exclude_sectors": list(exc),
            "include_sectors": list(inc) if inc else None,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ──────────────────────────────────────────────
# Rotation Optimize Dashboard
# ──────────────────────────────────────────────

@router.get("/rotation/dashboard", response_class=HTMLResponse)
async def rotation_dashboard():
    return render_template("polymarket_rotation.html")


@router.get("/rotation/live-wallet")
async def rotation_live_wallet() -> JSONResponse:
    """Polymarket CLOB API 잔액 + 오픈 포지션 실시간 조회."""
    try:
        from features.strategy.polymarket._data.live import fetch_live_wallet
        data = await fetch_live_wallet()
        return JSONResponse(data)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/rotation/debug-balance")
async def rotation_debug_balance() -> JSONResponse:
    """디버그: /balance-allowance 원본 응답 전체 반환 (pUSD 확인용)."""
    try:
        from features.strategy.polymarket._data.live import fetch_balance_raw
        data = await fetch_balance_raw()
        return JSONResponse(data)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _entry_price(row) -> float | None:
    """시그널의 실제 진입 가격 (side 기준)."""
    if row.side == "NO":
        return row.no_price
    return row.yes_price


def _sector(question: str) -> str:
    q = (question or "").lower()
    for sector, keywords in _SECTORS.items():
        if any(kw in q for kw in keywords):
            return sector
    return "Other"


@router.get("/rotation/top-signals")
async def rotation_top_signals(
    limit: int = Query(200),
    skew_min: float = Query(0.80, description="dominant price 최솟값 (LC 기준 0.80)"),
    skew_max: float = Query(0.97, description="dominant price 최댓값 (완전 수렴 전, LC 기준 0.97)"),
) -> JSONResponse:
    """LC 엔진 시그널을 직접 공급 — score = dominant_price / hours_left (고확률 × 단기 우선)."""
    try:
        from features.strategy.polymarket.late_convergence.engine import get_markets as lc_markets
        from features.strategy.polymarket.sectors import classify_sector, ALLOWED_SECTORS

        now_ts = time.time()

        results = []
        for m in lc_markets().values():
            end_ts = m.get("end_ts")
            if not end_ts:
                continue
            hours_left = (end_ts - now_ts) / 3600
            if hours_left <= 0:
                continue

            yes_p = m.get("yes_price")
            no_p  = m.get("no_price")
            if yes_p is None or no_p is None:
                continue

            dominant_price = max(yes_p, no_p)
            dominant_side  = "YES" if yes_p >= no_p else "NO"

            if dominant_price < skew_min or dominant_price > skew_max:
                continue

            sec = classify_sector(m.get("question") or "")
            if sec not in ALLOWED_SECTORS:
                continue

            # score: 고확률 × 단기 → 자본 회전 속도 최대화
            # 95% @ 1h = 0.95, 80% @ 2h = 0.40, 95% @ 24h = 0.0396
            score        = dominant_price / max(hours_left, 0.1)
            expected_roi = (1 - dominant_price) / dominant_price

            results.append({
                "condition_id":   m.get("condition_id"),
                "question":       (m.get("question") or "")[:120],
                "sector":         sec,
                "yes_price":      round(yes_p, 4),
                "no_price":       round(no_p,  4),
                "dominant_side":  dominant_side,
                "dominant_price": round(dominant_price, 4),
                "expected_roi":   round(expected_roi * 100, 2),
                "hours_left":     round(hours_left, 2),
                "score":          round(score, 4),
                "volume_usd":     m.get("volume_usd"),
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return JSONResponse({
            "markets":       results[:limit],
            "total":         len(results),
            "allow_sectors": sorted(ALLOWED_SECTORS),
            "skew_min":      skew_min,
            "skew_max":      skew_max,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/rotation/wallet")
async def rotation_wallet(
    since: str | None = Query(None, description="YYYY-MM-DD (resolved_at 기준)"),
) -> JSONResponse:
    """실제 체결된 시그널(matched/live/delayed/failed)만으로 $100 시작 복리 시뮬.

    skipped/pending/NULL order_status는 제외 — 실제 LC 엔진이 실행한 것만 반영.
    베팅 단위: shares×price ≥ $1.02 (V2 최솟값).
    """
    import math

    _EXECUTED = {"matched", "live", "delayed", "failed"}

    def _bet_usd(entry_price: float) -> float:
        if not entry_price or entry_price <= 0:
            return 1.02
        shares = math.ceil(1.02 / entry_price * 100) / 100
        return round(shares * entry_price, 4)

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
                    PolymarketSignal.strategy == "late_convergence",
                    PolymarketSignal.order_status.in_(list(_EXECUTED)),
                )
                .order_by(PolymarketSignal.resolved_at)
            )
            if since_dt:
                stmt = stmt.where(PolymarketSignal.resolved_at >= since_dt)
            rows = db.execute(stmt).scalars().all()
        finally:
            db.close()

        wallet = 100.0
        curve = [{"date": "start", "wallet": round(wallet, 4), "trades": 0}]

        def day_key(r):
            ts = r.resolved_at or r.created_at
            return ts.date() if ts else date(2000, 1, 1)

        for day, group in groupby(rows, key=day_key):
            batch = list(group)
            for r in batch:
                entry = (r.yes_price if r.side == "YES" else r.no_price) or 0.9
                bet = _bet_usd(entry)
                wallet += bet * r.actual_pnl
            curve.append({
                "date":   str(day),
                "wallet": round(wallet, 4),
                "trades": len(batch),
            })

        total_pct = (wallet - 100.0) / 100.0 * 100
        return JSONResponse({
            "initial":               100.0,
            "current_wallet":        round(wallet, 4),
            "total_pct":             round(total_pct, 2),
            "recommended_positions": max(1, int(wallet / 1.02)),
            "curve":                 curve,
            "note":                  f"executed signals only (matched/live/delayed/failed), total={len(rows)}",
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/rotation/retry-failed")
async def retry_failed_orders() -> JSONResponse:
    """skipped/failed 미해소 시그널 재주문.

    order_status in (skipped, failed) + is_resolved=0 인 레코드를
    다시 place_order 호출해 실제 주문 시도.
    """
    try:
        from db.session import get_session
        from db.models import PolymarketSignal
        from sqlalchemy import select
        from features.strategy.polymarket._data.executor import place_order

        db = get_session()
        try:
            rows = db.execute(
                select(PolymarketSignal).where(
                    PolymarketSignal.is_resolved == 0,
                    PolymarketSignal.order_status.in_(["skipped", "failed"]),
                )
            ).scalars().all()
        finally:
            db.close()

        if not rows:
            return JSONResponse({"retried": 0, "results": []})

        # LC config — max_order_usd
        try:
            import yaml, pathlib
            cfg_path = pathlib.Path(__file__).parent / "late_convergence" / "config.yaml"
            cfg = yaml.safe_load(cfg_path.read_text())
        except Exception:
            cfg = {}

        results = []
        for row in rows:
            token_id = row.yes_token_id if row.side == "YES" else row.no_token_id
            entry_price = row.yes_price if row.side == "YES" else row.no_price
            if not token_id or not entry_price:
                results.append({"id": row.id, "status": "skipped", "error": "token_id or price missing"})
                continue

            result = await place_order(token_id, entry_price, max_usd=cfg.get("max_order_usd", 0.0))

            db2 = get_session()
            try:
                r = db2.execute(select(PolymarketSignal).where(PolymarketSignal.id == row.id)).scalar_one_or_none()
                if r:
                    r.order_status = result.get("status", "failed")
                    r.poly_order_id = result.get("order_id") or ""
                    r.order_error = result.get("error") or ""
                    db2.commit()
            except Exception as e:
                db2.rollback()
            finally:
                db2.close()

            results.append({
                "id":       row.id,
                "question": (row.question or "")[:60],
                "status":   result.get("status"),
                "order_id": result.get("order_id"),
                "error":    result.get("error"),
            })

        matched = sum(1 for r in results if r["status"] == "matched")
        return JSONResponse({"retried": len(results), "matched": matched, "results": results})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/rotation/loss-sectors")
async def rotation_loss_sectors(
    since: str | None = Query(None, description="YYYY-MM-DD (resolved_at 기준)"),
) -> JSONResponse:
    """LC 손실 시그널을 섹터별로 분류해 반환."""
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
                    PolymarketSignal.strategy == "late_convergence",
                )
            )
            if since_dt:
                stmt = stmt.where(PolymarketSignal.resolved_at >= since_dt)
            rows = db.execute(stmt).scalars().all()
        finally:
            db.close()

        sector_data: dict[str, dict] = {}
        for r in rows:
            s = _sector(r.question or "")
            if s not in sector_data:
                sector_data[s] = {"wins": 0, "losses": 0, "pnls": []}
            if r.actual_pnl > 0:
                sector_data[s]["wins"] += 1
            else:
                sector_data[s]["losses"] += 1
            sector_data[s]["pnls"].append(r.actual_pnl)

        result = []
        for sector, d in sector_data.items():
            total = d["wins"] + d["losses"]
            avg_pnl = sum(d["pnls"]) / len(d["pnls"]) if d["pnls"] else 0
            wr = d["wins"] / total if total else 0
            result.append({
                "sector":   sector,
                "total":    total,
                "wins":     d["wins"],
                "losses":   d["losses"],
                "win_rate": round(wr * 100, 1),
                "avg_pnl":  round(avg_pnl * 100, 2),
            })

        result.sort(key=lambda x: x["losses"], reverse=True)
        return JSONResponse({"sectors": result})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
