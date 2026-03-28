"""SQLAlchemy session factory."""
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from db.config import get_engine_url
from db.models import Base

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
    """Create tables if missing."""
    eng = _get_engine()
    Base.metadata.create_all(bind=eng)
