"""US Options Expiry GEX Pinning — Data Feed.

us_options_chain(Cboe 지연시세)을 forward DB 에서 읽는다.
"""
from __future__ import annotations

import os

import pandas as pd
from sqlalchemy import create_engine, text

_engine = None


def _pg_url() -> str:
    return os.getenv("DATABASE_URL", "postgresql://btc:btc@localhost:5432/btc_forwardtest")


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(_pg_url(), pool_pre_ping=True)
    return _engine


def load_recent_chain(underlying: str = "SPY", days: int = 20) -> pd.DataFrame:
    """최근 `days` 일, 특정 underlying 의 옵션체인. 빈 프레임 가능(수집 전)."""
    eng = _get_engine()
    q = text(
        "SELECT snapshot_ts, expiry, strike, option_type, open_interest, "
        "       gamma, iv, underlying_price "
        "FROM us_options_chain "
        "WHERE underlying = :u "
        "  AND snapshot_ts >= now() - ((:d)::text || ' days')::interval"
    )
    df = pd.read_sql(q, eng, params={"u": underlying.upper(), "d": int(days)})
    if df.empty:
        return df
    df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"], utc=True)
    df["expiry"] = pd.to_datetime(df["expiry"]).dt.date
    return df
