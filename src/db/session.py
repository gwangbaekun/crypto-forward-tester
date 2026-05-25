"""SQLAlchemy session factory."""
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from db.config import get_engine_url
from db.models import create_tables

_engine = None
_SessionLocal = None


def _get_engine():
    global _engine
    if _engine is None:
        url = get_engine_url()
        if url.startswith("sqlite"):
            Path("data").mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False} if "sqlite" in url else {},
        )
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_get_engine())
    return _SessionLocal


def get_session() -> Session:
    return get_session_factory()()


def init_db() -> None:
    """Create tables if missing + 새 컬럼 safe-migrate."""
    eng = _get_engine()
    create_tables(eng)
    _migrate_add_columns(eng)


def _migrate_add_columns(eng) -> None:
    """누락된 컬럼만 ALTER TABLE로 추가 (멱등)."""
    from sqlalchemy import text, inspect
    insp = inspect(eng)
    existing = {c["name"] for c in insp.get_columns("polymarket_signals")}
    adds = []
    if "poly_order_id" not in existing:
        adds.append("ALTER TABLE polymarket_signals ADD COLUMN poly_order_id VARCHAR(128)")
    if "order_status" not in existing:
        adds.append("ALTER TABLE polymarket_signals ADD COLUMN order_status VARCHAR(16)")
    if adds:
        with eng.begin() as conn:
            for stmt in adds:
                conn.execute(text(stmt))
