"""Deribit Expiry GEX Reversal — Data Feed (자체 포함).

deribit_chain(자체 수집 옵션체인)을 읽는다. 이 테이블은 forward DB(btc_forwardtest)가
아니라 btc_backtest DB 에 oracle_sync.py 로 적재된다. 그래서 별도 커넥션을 쓴다.

우선순위:
  1) 환경변수 DERIBIT_CHAIN_PG_URL 이 있으면 그대로
  2) 없으면 DATABASE_URL 의 DB 이름만 btc_backtest 로 바꿔 파생
     (도커: host.docker.internal, 로컬: localhost 자동 대응)
"""
from __future__ import annotations

import os
from typing import Optional

import pandas as pd
from sqlalchemy import create_engine, text

_engine = None


def _pg_url() -> str:
    url = os.getenv("DERIBIT_CHAIN_PG_URL")
    if url:
        return url
    base = os.getenv("DATABASE_URL", "postgresql://btc:btc@localhost:5432/btc_forwardtest")
    return base.rsplit("/", 1)[0] + "/btc_backtest"


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(_pg_url(), pool_pre_ping=True)
    return _engine


def load_recent_chain(currency: str = "BTC", days: int = 45) -> pd.DataFrame:
    """최근 `days` 일 옵션체인 스냅샷. 빈 프레임 가능(수집 전/중단)."""
    eng = _get_engine()
    q = text(
        "SELECT snapshot_ts, expiry, strike, option_type, open_interest, "
        "       mark_iv, underlying_price "
        "FROM deribit_chain "
        "WHERE currency = :c "
        "  AND snapshot_ts >= now() - ((:d)::text || ' days')::interval"
    )
    df = pd.read_sql(q, eng, params={"c": currency, "d": int(days)})
    if df.empty:
        return df
    df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"], utc=True)
    df["expiry"] = pd.to_datetime(df["expiry"]).dt.date
    return df


def latest_spot(currency: str = "BTC") -> Optional[float]:
    """가장 최근 스냅샷의 underlying_price 중앙값."""
    eng = _get_engine()
    q = text(
        "SELECT underlying_price FROM deribit_chain "
        "WHERE currency = :c "
        "ORDER BY snapshot_ts DESC LIMIT 400"
    )
    try:
        df = pd.read_sql(q, eng, params={"c": currency})
        if df.empty:
            return None
        return float(df["underlying_price"].median())
    except Exception:
        return None
