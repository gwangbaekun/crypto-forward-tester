"""Cross-process lock for value scan portfolio writes (API + scanner container)."""
from __future__ import annotations

import fcntl
import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import text

from db.config import get_engine_url
from db.session import get_session
from features.strategy.value_scan.paths import DATA_DIR

logger = logging.getLogger(__name__)

_SCAN_LOCK_ID = 83472631


class ValueScanBusy(Exception):
    """Another process holds the value scan lock."""


def _postgres_try_lock(session) -> bool:
    return bool(
        session.execute(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": _SCAN_LOCK_ID},
        ).scalar()
    )


def _postgres_unlock(session) -> None:
    session.execute(
        text("SELECT pg_advisory_unlock(:lock_id)"),
        {"lock_id": _SCAN_LOCK_ID},
    )


@contextmanager
def _sqlite_file_lock() -> Iterator[None]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / ".value_scan.lock"
    with path.open("w", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueScanBusy("another value scan is running") from exc
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextmanager
def value_scan_portfolio_lock() -> Iterator[None]:
    """Serialize load → mutate → save for positions/history across processes."""
    if get_engine_url().startswith("postgres"):
        session = get_session()
        acquired = False
        try:
            acquired = _postgres_try_lock(session)
            if not acquired:
                raise ValueScanBusy("another value scan is running")
            yield
        except ValueScanBusy:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            raise
        else:
            session.commit()
        finally:
            if acquired:
                try:
                    _postgres_unlock(session)
                except Exception:
                    logger.exception("[ValueScan] advisory unlock failed")
            session.close()
    else:
        with _sqlite_file_lock():
            yield
