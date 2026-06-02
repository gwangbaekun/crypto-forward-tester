"""Value Scan 포지션·청산 기록 — PostgreSQL / SQLite (DATABASE_URL)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import joinedload

from db.models import (
    ValueScanClosedLot,
    ValueScanClosedTrade,
    ValueScanLot,
    ValueScanMarketMeta,
    ValueScanPosition,
    ValueScanSnapshot,
)
from db.session import get_session
from features.strategy.value_scan.paths import (
    DATA_DIR,
    HISTORY_FILE,
    LAST_ACTIVITY_FILE,
    POSITIONS_FILE,
    SCANS_DIR,
)

UNIT_USD = 1.0


def _pos_key(market: str, symbol: str) -> str:
    return f"{market}_{symbol}"

logger = logging.getLogger(__name__)


def _position_to_dict(pos: ValueScanPosition) -> dict[str, Any]:
    return {
        "symbol": pos.symbol,
        "name": pos.name,
        "market": pos.market,
        "sector": pos.sector,
        "first_entry_date": pos.first_entry_date,
        "invested_usd": pos.invested_usd,
        "per": pos.per,
        "sector_median": pos.sector_median,
        "eps": pos.eps,
        "fwd_eps": pos.fwd_eps,
        "pbr": pos.pbr,
        "lots": [
            {"date": lot.lot_date, "price": lot.price}
            for lot in pos.lots
        ],
    }


def _closed_to_dict(trade: ValueScanClosedTrade) -> dict[str, Any]:
    return {
        "symbol": trade.symbol,
        "name": trade.name,
        "market": trade.market,
        "sector": trade.sector,
        "first_entry_date": trade.first_entry_date,
        "exit_date": trade.exit_date,
        "exit_price": trade.exit_price,
        "exit_reason": trade.exit_reason,
        "invested_usd": trade.invested_usd,
        "pnl_pct": trade.pnl_pct,
        "pnl_usd": trade.pnl_usd,
        "hold_days": trade.hold_days,
        "per": trade.per,
        "sector_median": trade.sector_median,
        "eps": trade.eps,
        "fwd_eps": trade.fwd_eps,
        "pbr": trade.pbr,
        "lots": [
            {"date": lot.lot_date, "price": lot.price}
            for lot in trade.lots
        ],
    }


def _apply_position_fields(pos: ValueScanPosition, data: dict[str, Any]) -> None:
    pos.name = data.get("name") or pos.symbol
    pos.sector = data.get("sector")
    pos.first_entry_date = data.get("first_entry_date") or ""
    pos.invested_usd = float(data.get("invested_usd") or UNIT_USD)
    pos.per = data.get("per")
    pos.sector_median = data.get("sector_median")
    pos.eps = data.get("eps")
    pos.fwd_eps = data.get("fwd_eps")
    pos.pbr = data.get("pbr")


def reset_value_scan_data(*, wipe_files: bool = True) -> dict[str, Any]:
    """DB 포지션·청산·스캔 메타 전부 삭제. (선택) data/value_forward 파일도 정리."""
    session = get_session()
    try:
        n_lots = session.query(ValueScanLot).delete(synchronize_session=False)
        n_pos = session.query(ValueScanPosition).delete(synchronize_session=False)
        n_clots = session.query(ValueScanClosedLot).delete(synchronize_session=False)
        n_closed = session.query(ValueScanClosedTrade).delete(synchronize_session=False)
        n_meta = session.query(ValueScanMarketMeta).delete(synchronize_session=False)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    removed_files: list[str] = []
    if wipe_files and DATA_DIR.exists():
        for pattern in ("*.json", "*.json.bak"):
            for p in DATA_DIR.glob(pattern):
                p.unlink(missing_ok=True)
                removed_files.append(str(p))
        if SCANS_DIR.exists():
            for p in SCANS_DIR.glob("*.json"):
                p.unlink(missing_ok=True)
                removed_files.append(str(p))

    logger.info("[ValueScan] reset DB lots=%s pos=%s closed=%s meta=%s", n_lots, n_pos, n_closed, n_meta)
    return {
        "ok": True,
        "deleted": {
            "open_lots": n_lots,
            "open_positions": n_pos,
            "closed_lots": n_clots,
            "closed_trades": n_closed,
            "market_meta": n_meta,
        },
        "files_removed": len(removed_files),
    }


def db_counts() -> dict[str, int]:
    session = get_session()
    try:
        return {
            "open_positions": session.query(ValueScanPosition).count(),
            "open_lots": session.query(ValueScanLot).count(),
            "closed_trades": session.query(ValueScanClosedTrade).count(),
        }
    finally:
        session.close()


def load_positions_from_db() -> dict[str, dict]:
    session = get_session()
    try:
        rows = (
            session.query(ValueScanPosition)
            .options(joinedload(ValueScanPosition.lots))
            .all()
        )
        return {_pos_key(p.market, p.symbol): _position_to_dict(p) for p in rows}
    finally:
        session.close()


def load_history_from_db() -> list[dict]:
    session = get_session()
    try:
        rows = (
            session.query(ValueScanClosedTrade)
            .options(joinedload(ValueScanClosedTrade.lots))
            .order_by(ValueScanClosedTrade.exit_date.desc(), ValueScanClosedTrade.id.desc())
            .all()
        )
        return [_closed_to_dict(t) for t in rows]
    finally:
        session.close()


def save_positions_to_db(positions: dict[str, dict]) -> None:
    session = get_session()
    try:
        session.query(ValueScanLot).delete(synchronize_session=False)
        session.query(ValueScanPosition).delete(synchronize_session=False)
        session.flush()

        for data in positions.values():
            pos = ValueScanPosition(
                market=data["market"],
                symbol=data["symbol"],
            )
            _apply_position_fields(pos, data)
            session.add(pos)
            session.flush()
            for lot in data.get("lots") or []:
                session.add(
                    ValueScanLot(
                        position_id=pos.id,
                        lot_date=lot.get("date") or data.get("first_entry_date") or "",
                        price=lot.get("price"),
                        unit_usd=UNIT_USD,
                    )
                )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def save_history_to_db(history: list[dict]) -> None:
    session = get_session()
    try:
        session.query(ValueScanClosedLot).delete(synchronize_session=False)
        session.query(ValueScanClosedTrade).delete(synchronize_session=False)
        session.flush()

        for data in history:
            trade = ValueScanClosedTrade(
                market=data["market"],
                symbol=data["symbol"],
                name=data.get("name") or data["symbol"],
                sector=data.get("sector"),
                first_entry_date=data.get("first_entry_date") or "",
                exit_date=data.get("exit_date") or "",
                exit_price=data.get("exit_price"),
                exit_reason=data.get("exit_reason"),
                invested_usd=data.get("invested_usd"),
                pnl_pct=data.get("pnl_pct"),
                pnl_usd=data.get("pnl_usd"),
                hold_days=data.get("hold_days"),
                per=data.get("per"),
                sector_median=data.get("sector_median"),
                eps=data.get("eps"),
                fwd_eps=data.get("fwd_eps"),
                pbr=data.get("pbr"),
            )
            session.add(trade)
            session.flush()
            for lot in data.get("lots") or []:
                session.add(
                    ValueScanClosedLot(
                        trade_id=trade.id,
                        lot_date=lot.get("date") or "",
                        price=lot.get("price"),
                        unit_usd=UNIT_USD,
                    )
                )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def load_positions_from_json() -> dict[str, dict]:
    if POSITIONS_FILE.exists():
        return json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
    return {}


def load_history_from_json() -> list[dict]:
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    return []


def migrate_json_files_to_db(*, archive: bool = True) -> dict[str, Any]:
    """data/value_forward/*.json → DB. 성공 시 json 을 .bak 으로 rename."""
    positions = load_positions_from_json()
    history = load_history_from_json()
    if not positions and not history:
        return {
            "ok": False,
            "reason": "no json data",
            "json_paths": {
                "positions": str(POSITIONS_FILE),
                "history": str(HISTORY_FILE),
            },
        }

    save_positions_to_db(positions)
    save_history_to_db(history)
    counts = db_counts()

    archived: list[str] = []
    if archive:
        for path in (POSITIONS_FILE, HISTORY_FILE):
            if path.exists():
                bak = path.with_suffix(path.suffix + ".bak")
                path.rename(bak)
                archived.append(str(bak))

    logger.info(
        "[ValueScan] JSON→DB migrated: %d positions, %d closed",
        counts["open_positions"],
        counts["closed_trades"],
    )
    return {
        "ok": True,
        "migrated_positions": len(positions),
        "migrated_history": len(history),
        "db": counts,
        "archived": archived,
        "json_paths": {
            "positions": str(POSITIONS_FILE),
            "history": str(HISTORY_FILE),
            "scans_dir": str(DATA_DIR / "scans"),
            "last_activity": str(DATA_DIR / "last_activity.json"),
        },
    }


_EXIT_ONLY_KEYS = frozenset({
    "exit_date", "exit_price", "exit_reason", "pnl_pct", "pnl_usd", "hold_days",
})


def restore_mistaken_exits(
    market: str,
    *,
    exit_reason: str = "DELISTED",
    exit_date: Optional[str] = None,
) -> dict[str, Any]:
    """잘못 청산된 기록(예: 타 시장 스캔으로 DELISTED) → 오픈 포지션으로 복구."""
    positions = load_positions_from_db()
    history = load_history_from_db()
    restored: list[str] = []
    kept: list[dict] = []

    for rec in history:
        match = (
            rec.get("market") == market
            and rec.get("exit_reason") == exit_reason
            and (exit_date is None or rec.get("exit_date") == exit_date)
        )
        if match:
            key = _pos_key(rec["market"], rec["symbol"])
            if key not in positions:
                pos = {k: v for k, v in rec.items() if k not in _EXIT_ONLY_KEYS}
                positions[key] = pos
                restored.append(key)
            continue
        kept.append(rec)

    if restored:
        save_positions_to_db(positions)
        save_history_to_db(kept)

    return {
        "ok": True,
        "market": market,
        "exit_reason": exit_reason,
        "exit_date": exit_date,
        "restored": len(restored),
        "symbols": restored[:50],
        "open_count": len(positions),
        "closed_remaining": len(kept),
    }


def ensure_migrated_from_json_if_needed() -> Optional[dict]:
    """DB 비어 있고 json 있으면 1회 자동 이전."""
    counts = db_counts()
    if counts["open_positions"] or counts["closed_trades"]:
        return None
    if not POSITIONS_FILE.exists() and not HISTORY_FILE.exists():
        return None
    return migrate_json_files_to_db(archive=True)


# ── Scan Snapshot (DB) ────────────────────────────────────────────────────────

def save_snapshot_to_db(date: str, market: str, rows: list[dict]) -> None:
    """스캔 결과를 DB에 upsert (date+market 기준)."""
    from datetime import datetime as _dt
    rows_json = json.dumps(rows, ensure_ascii=False)
    with get_session() as s:
        existing = s.query(ValueScanSnapshot).filter_by(date=date, market=market).first()
        if existing:
            existing.rows_json = rows_json
            existing.saved_at  = _dt.utcnow()
        else:
            s.add(ValueScanSnapshot(date=date, market=market, rows_json=rows_json))
        s.commit()


def load_latest_snapshot_from_db(market: str) -> Optional[dict]:
    """DB에서 해당 market의 최신 스캔 결과 반환. 없으면 None."""
    with get_session() as s:
        row = (
            s.query(ValueScanSnapshot)
            .filter_by(market=market)
            .order_by(ValueScanSnapshot.date.desc(), ValueScanSnapshot.saved_at.desc())
            .first()
        )
        if row is None:
            return None
        return {"date": row.date, "market": market, "rows": json.loads(row.rows_json)}
