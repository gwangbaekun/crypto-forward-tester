from __future__ import annotations

import datetime as _dt
import logging
import os

import pandas as pd

log = logging.getLogger(__name__)


def _url() -> str | None:
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    return url.replace("postgresql+psycopg2://", "postgresql://")


def _pg_type(series: pd.Series) -> str:
    if pd.api.types.is_datetime64_any_dtype(series):
        return "TIMESTAMPTZ"
    kind = series.dtype.kind
    if kind in ("i", "u"):
        return "BIGINT"
    if kind == "f":
        return "DOUBLE PRECISION"
    if kind == "b":
        return "BOOLEAN"
    if kind == "O":
        sample = series.dropna()
        if len(sample) and isinstance(sample.iloc[0], _dt.date) and not isinstance(sample.iloc[0], _dt.datetime):
            return "DATE"
    return "TEXT"


def upsert(df: pd.DataFrame, table: str, keys: list[str]) -> int:
    url = _url()
    if url is None or df.empty:
        return 0

    import psycopg2
    import psycopg2.extras

    cols = list(df.columns)
    quoted = ", ".join(f'"{c}"' for c in cols)
    col_defs = ", ".join(f'"{c}" {_pg_type(df[c])}' for c in cols)
    key_cols = ", ".join(f'"{k}"' for k in keys)
    values = [tuple(None if pd.isna(v) else v for v in row) for row in df.values.tolist()]

    conn = psycopg2.connect(url)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({col_defs})')
            cur.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS "{table}_natkey" ON "{table}" ({key_cols})')
            psycopg2.extras.execute_values(
                cur,
                f'INSERT INTO "{table}" ({quoted}) VALUES %s ON CONFLICT ({key_cols}) DO NOTHING',
                values,
                page_size=1000,
            )
    finally:
        conn.close()
    return len(values)
