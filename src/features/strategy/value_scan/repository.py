from __future__ import annotations

import json
import logging
from typing import Optional

from db.models import ValueScanSnapshot
from db.session import get_session
from features.strategy.value_scan.paths import (
    DATA_DIR,
    SCANS_KOSPI_DIR,
    SCANS_NASDAQ_DIR,
)


logger = logging.getLogger(__name__)


def save_snapshot_to_db(date: str, market: str, rows: list[dict]) -> None:
    from datetime import datetime as _dt

    rows_json = json.dumps(rows, ensure_ascii=False)
    session = get_session()
    try:
        existing = session.query(ValueScanSnapshot).filter_by(date=date, market=market).first()
        if existing:
            existing.rows_json = rows_json
            existing.saved_at = _dt.utcnow()
        else:
            session.add(ValueScanSnapshot(date=date, market=market, rows_json=rows_json))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def load_latest_snapshot_from_db(market: str) -> Optional[dict]:
    session = get_session()
    try:
        row = (
            session.query(ValueScanSnapshot)
            .filter_by(market=market)
            .order_by(ValueScanSnapshot.date.desc(), ValueScanSnapshot.saved_at.desc())
            .first()
        )
        if row is None:
            return None
        return {"date": row.date, "market": market, "rows": json.loads(row.rows_json)}
    finally:
        session.close()


def load_latest_scan_json(market: str) -> Optional[dict]:
    scan_dir = SCANS_NASDAQ_DIR if market == "nasdaq" else SCANS_KOSPI_DIR
    files = sorted(scan_dir.glob("*.json")) if scan_dir.exists() else []
    if not files:
        legacy_dir = DATA_DIR / "scans"
        if legacy_dir.exists():
            files = sorted(legacy_dir.glob(f"*_{market}.json"))
    if not files:
        return None
    raw = json.loads(files[-1].read_text(encoding="utf-8"))
    return {
        "rows": raw.get("rows", []),
        "date": raw.get("date"),
        "market": market,
        "source": "json",
    }


def _scan_row_count(data: Optional[dict]) -> int:
    if not data:
        return 0
    return len(data.get("rows") or [])


def load_latest_scan_result(market: str) -> dict:
    db_data: Optional[dict] = None
    try:
        db_data = load_latest_snapshot_from_db(market)
    except Exception:
        logger.exception("[ValueScan] load_latest_snapshot_from_db failed market=%s", market)

    json_data = load_latest_scan_json(market)
    db_n = _scan_row_count(db_data)
    json_n = _scan_row_count(json_data)

    if json_n > db_n:
        best = json_data
    elif db_n > 0:
        best = db_data
        best["source"] = "db"
    elif json_n > 0:
        best = json_data
    else:
        return {"rows": [], "date": None, "market": market, "source": "none"}

    return best

