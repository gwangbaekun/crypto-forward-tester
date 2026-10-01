from __future__ import annotations

import os

import pandas as pd
from sqlalchemy import create_engine, text

_engine = None


def _pg_url() -> str:
    url = os.environ["DATABASE_URL"]
    return url


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(_pg_url(), pool_pre_ping=True)
    return _engine


def load_recent_chain(underlying: str = "SPY", days: int = 20) -> pd.DataFrame:
    eng = _get_engine()
    q = text(
        "SELECT snapshot_ts, expiry, strike, option_type, open_interest, "
        "       gamma, iv, underlying_price "
        "FROM us_options_chain "
        "WHERE underlying = :u "
        "  AND snapshot_ts = (SELECT max(snapshot_ts) FROM us_options_chain WHERE underlying = :u) "
        "  AND snapshot_ts >= now() - ((:d)::text || ' days')::interval"
    )
    df = pd.read_sql(q, eng, params={"u": underlying.upper(), "d": int(days)})
    if df.empty:
        return df
    df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"], utc=True)
    df["expiry"] = pd.to_datetime(df["expiry"]).dt.date
    return df
