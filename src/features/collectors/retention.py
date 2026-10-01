from __future__ import annotations

import datetime as dt
import os
from contextlib import closing
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

TABLE = "us_options_chain"
PENDING_TABLE = "us_options_chain_pending"
PENDING_SNAPSHOTS = "us_options_chain_pending_snapshots"
LOCK_KEY = 2026100101
LATEST_COLUMNS = [
    "option", "bid", "ask", "last_trade_price", "iv", "delta", "gamma",
    "open_interest", "volume", "underlying_price", "expiry", "strike",
    "option_type", "underlying", "snapshot_ts",
]
PENDING_COLUMNS = [
    "session", "option", "snapshot_ts", "expiry", "strike", "option_type",
    "open_interest", "gamma", "underlying_price",
]




def _prune(cur) -> dict:
    cur.execute(
        f"DELETE FROM {TABLE} c USING ("
        f"  SELECT underlying, max(snapshot_ts) AS snapshot_ts FROM {TABLE} GROUP BY underlying"
        ") latest WHERE c.underlying = latest.underlying AND c.snapshot_ts < latest.snapshot_ts"
    )
    latest_deleted = cur.rowcount
    cur.execute(
        f"DELETE FROM {PENDING_SNAPSHOTS} p USING gamma_wall_ledger l "
        "WHERE l.id = 1 AND l.blob->'sessions' ? p.session::text "
        "AND (l.blob->'sessions'->p.session::text->>'snapshot_ts')::timestamptz >= p.snapshot_ts"
    )
    processed_deleted = cur.rowcount
    cur.execute(
        f"DELETE FROM {PENDING_TABLE} p USING {PENDING_SNAPSHOTS} s "
        "WHERE p.session = s.session AND p.snapshot_ts <> s.snapshot_ts"
    )
    result = {"old_rows_deleted": latest_deleted, "processed_sessions_deleted": processed_deleted}
    return result


def maintain_storage() -> dict:
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg2://", "postgresql://")
    with closing(psycopg2.connect(url)) as conn:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY + 1,))
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
            cur.execute("SELECT to_regclass(%s), to_regclass(%s), to_regclass(%s)",
                        (TABLE, PENDING_SNAPSHOTS, PENDING_TABLE))
            latest, headers, pending = cur.fetchone()
            if latest is None or (headers is None) != (pending is None):
                raise RuntimeError("Options schema가 누락되거나 불완전합니다")
            migrated = headers is None
            if migrated:
                migration = Path(__file__).resolve().parents[3] / "scripts" / "recover_us_options_chain.sql"
                cur.execute(migration.read_text())
            result = _prune(cur)
            result["migrated"] = migrated
    return result


def prepare_pending(chain: pd.DataFrame) -> tuple[dt.date, pd.DataFrame]:
    eligible = chain[
        (chain["underlying"] == "SPY")
        & (chain["open_interest"] > 0)
        & (chain["gamma"] > 0)
    ].copy()
    last_trade = pd.to_datetime(eligible["last_trade_time"], errors="coerce").max()
    if pd.isna(last_trade):
        raise ValueError("SPY last_trade_time으로 session을 특정할 수 없습니다")
    session = last_trade.date()
    dte = eligible["expiry"].map(lambda expiry: (expiry - session).days)
    pending = eligible[dte.between(0, 30)].copy()
    pending["session"] = session
    pending = pending[PENDING_COLUMNS]
    result = (session, pending)
    return result


def _upsert(cur, frame: pd.DataFrame, table: str, keys: list[str]) -> None:
    columns = list(frame.columns)
    quoted = ", ".join(f'"{column}"' for column in columns)
    key_columns = ", ".join(f'"{column}"' for column in keys)
    updates = ", ".join(
        f'"{column}" = EXCLUDED."{column}"' for column in columns if column not in keys
    )
    values = [
        tuple(None if pd.isna(value) else value for value in row)
        for row in frame.itertuples(index=False, name=None)
    ]
    execute_values(
        cur,
        f'INSERT INTO "{table}" ({quoted}) VALUES %s '
        f'ON CONFLICT ({key_columns}) DO UPDATE SET {updates}',
        values,
        page_size=1000,
    )


def store_snapshot(cur, chain: pd.DataFrame) -> int:
    if chain.empty or chain["snapshot_ts"].isna().any():
        raise ValueError("빈 snapshot은 저장할 수 없습니다")
    if chain["snapshot_ts"].nunique() != 1 or chain["option"].duplicated().any():
        raise ValueError("동일 시각의 계약별 단일 snapshot이 필요합니다")
    latest = chain[LATEST_COLUMNS]
    session, pending = prepare_pending(chain)
    snapshot_ts = chain["snapshot_ts"].iloc[0]
    symbols = chain["underlying"].unique().tolist()

    cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
    cur.execute(
        f"SELECT max(snapshot_ts) FROM {TABLE} WHERE underlying = ANY(%s)",
        (symbols,),
    )
    previous_ts = cur.fetchone()[0]
    if previous_ts is not None and previous_ts > snapshot_ts:
        raise ValueError("현재 저장된 snapshot보다 오래된 데이터입니다")

    _upsert(cur, latest, TABLE, ["option"])
    cur.execute("SELECT blob FROM gamma_wall_ledger WHERE id = 1")
    row = cur.fetchone()
    if row is not None:
        sessions = row[0]["sessions"]
        key = session.isoformat()
        if key in sessions:
            processed_ts = pd.Timestamp(sessions[key]["snapshot_ts"])
            if processed_ts >= snapshot_ts:
                _prune(cur)
                rows = len(latest)
                return rows

    spot = chain.loc[chain["underlying"] == "SPY", "underlying_price"].iloc[0]
    cur.execute(
        f"INSERT INTO {PENDING_SNAPSHOTS} (session, snapshot_ts, underlying_price) "
        "VALUES (%s, %s, %s) ON CONFLICT (session) DO UPDATE "
        "SET snapshot_ts = EXCLUDED.snapshot_ts, underlying_price = EXCLUDED.underlying_price",
        (session, snapshot_ts, None if pd.isna(spot) else float(spot)),
    )
    if not pending.empty:
        _upsert(cur, pending, PENDING_TABLE, ["session", "option"])
    _prune(cur)
    rows = len(latest)
    return rows
