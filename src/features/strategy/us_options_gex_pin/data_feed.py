"""US Options Expiry GEX Pinning — Data Feed (자체 포함).

us_options_chain(자체 수집, Cboe 지연시세)을 읽는다. deribit_chain 과 마찬가지로
btc_backtest DB 에 적재되므로 별도 커넥션을 쓴다 (DERIBIT_CHAIN_PG_URL 또는
DATABASE_URL 의 DB 이름만 btc_backtest 로 파생).
"""
from __future__ import annotations

import os

import pandas as pd
from sqlalchemy import create_engine, text

_engine = None


def _pg_url() -> str:
    url = os.getenv("US_OPTIONS_PG_URL") or os.getenv("DERIBIT_CHAIN_PG_URL")
    if url:
        return url
    base = os.getenv("DATABASE_URL", "postgresql://btc:btc@localhost:5432/btc_forwardtest")
    return base.rsplit("/", 1)[0] + "/btc_backtest"


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
