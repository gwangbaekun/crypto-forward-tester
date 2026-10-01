from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import threading
import time

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

from features.collectors.retention import LOCK_KEY

logger = logging.getLogger(__name__)

UNDERLYING = "SPY"
EXEC_SYMBOL = "US500"
MAX_DTE_DAYS = 30
CONTRACT_MULTIPLIER = 100
_LOCK = threading.Lock()
_engine = None


def _pg_url() -> str:
    url = os.environ["DATABASE_URL"]
    return url


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(_pg_url(), pool_pre_ping=True)
    return _engine


def _redact(url: str) -> str:
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", url)


PROBE_TIMEOUT_MS = 20000


def datasource_status() -> dict:
    started = time.monotonic()
    today = dt.datetime.now(dt.timezone.utc).date()

    def _age(d: dt.date | None) -> int | None:
        return (today - d).days if d else None

    out: dict = {
        "db_url": _redact(_pg_url()),
        "ok": False,
        "error": None,
        "chain": None,
        "daily": None,
        "ledger": None,
        "running": is_running(),
        "elapsed_ms": 0,
    }

    try:
        with _get_engine().connect() as c:
            c.execute(text(f"SET statement_timeout = {PROBE_TIMEOUT_MS}"))
            row = c.execute(text(
                "SELECT count(*), min(snapshot_ts), max(snapshot_ts) "
                "FROM us_options_chain WHERE underlying = :u"
            ), {"u": UNDERLYING}).fetchone()
            out["chain"] = {
                "table": "us_options_chain",
                "underlying": UNDERLYING,
                "rows": int(row[0] or 0),
                "oldest": row[1].date().isoformat() if row[1] else None,
                "latest": row[2].date().isoformat() if row[2] else None,
                "days_old": _age(row[2].date() if row[2] else None),
            }
            row = c.execute(text(
                "SELECT count(*), min(date), max(date) FROM us_etf_daily WHERE symbol = :s"
            ), {"s": UNDERLYING}).fetchone()
            out["daily"] = {
                "table": "us_etf_daily",
                "symbol": UNDERLYING,
                "rows": int(row[0] or 0),
                "oldest": row[1].isoformat() if row[1] else None,
                "latest": row[2].isoformat() if row[2] else None,
                "days_old": _age(row[2]),
            }
        out["ok"] = True
    except Exception as exc:
        out["error"] = str(exc).strip().split("\n")[0]

    try:
        led = load_ledger()
        sessions = led.get("sessions") or {}
        out["ledger"] = {
            "store": "db" if _db_enabled() else "file",
            "sessions": len(sessions),
            "scored": sum(1 for r in sessions.values() if isinstance(r, dict) and "result" in r),
            "last_run": led.get("last_run"),
        }
    except Exception as exc:
        out["ledger"] = {"error": str(exc).strip().split("\n")[0]}

    out["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return out


def load_chain() -> pd.DataFrame:
    q = text(
        "SELECT s.session, s.snapshot_ts, p.option, p.expiry, p.strike, p.option_type, "
        "       p.open_interest, p.gamma, s.underlying_price "
        "FROM us_options_chain_pending_snapshots s "
        "LEFT JOIN us_options_chain_pending p ON p.session = s.session AND p.snapshot_ts = s.snapshot_ts "
        "ORDER BY s.session"
    )
    df = pd.read_sql(q, _get_engine())
    if df.empty:
        return df
    df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"], utc=True)
    df["expiry"] = pd.to_datetime(df["expiry"]).dt.date
    df["session"] = pd.to_datetime(df["session"]).dt.date
    return df


def load_daily(sessions: list[dt.date]) -> pd.DataFrame:
    if not sessions:
        df = pd.DataFrame(columns=["open", "high", "low", "close"])
        return df
    q = text(
        "SELECT DISTINCT d.date, d.open, d.high, d.low, d.close "
        "FROM unnest(CAST(:sessions AS date[])) AS wanted(session) "
        "CROSS JOIN LATERAL ("
        "  SELECT date, open, high, low, close FROM us_etf_daily "
        "  WHERE symbol = :s AND date > wanted.session ORDER BY date LIMIT 1"
        ") d ORDER BY d.date"
    )
    df = pd.read_sql(q, _get_engine(), params={"s": UNDERLYING, "sessions": sessions})
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.set_index("date")
    return df


def compute_walls(snap: pd.DataFrame, session: dt.date) -> dict | None:
    if snap.empty:
        return None
    S = float(snap.underlying_price.iloc[0])
    if not np.isfinite(S) or S <= 0:
        raise ValueError("Gamma Wall underlying_price는 유효한 양수여야 합니다")

    dte = np.array([(e - session).days for e in snap.expiry])
    g = snap[(dte >= 0) & (dte <= MAX_DTE_DAYS)]
    if g.empty:
        return None

    sign = np.where(g.option_type.to_numpy() == "C", 1.0, -1.0)
    gex = g.gamma.to_numpy() * g.open_interest.to_numpy() * CONTRACT_MULTIPLIER * S * S * 0.01 * sign
    per_k = pd.Series(gex).groupby(g.strike.to_numpy()).sum().sort_index()

    above, below = per_k[per_k.index > S], per_k[per_k.index < S]
    if above.empty or below.empty:
        return None
    call_wall = float(above.idxmax())
    put_wall = float(below.idxmin())
    if not (put_wall < S < call_wall):
        return None

    return {
        "session": session.isoformat(),
        "snapshot_ts": snap.snapshot_ts.max().isoformat(),
        "spot": round(S, 4),
        "call_wall": call_wall,
        "put_wall": put_wall,
        "call_gex_m": round(float(above.max()) / 1e6, 1),
        "put_gex_m": round(float(below.min()) / 1e6, 1),
        "total_gex_bn": round(float(per_k.sum()) / 1e9, 3),
        "band_up_pct": round((call_wall / S - 1) * 100, 3),
        "band_dn_pct": round((1 - put_wall / S) * 100, 3),
        "contracts": int(len(g)),
    }


def score(rec: dict, nxt: pd.Series) -> dict:
    S, cw, pw = rec["spot"], rec["call_wall"], rec["put_wall"]
    hi, lo, cl = float(nxt.high), float(nxt.low), float(nxt.close)
    half = ((cw - S) + (S - pw)) / 2.0
    return {
        "next_session": str(nxt.name),
        "next_high": hi, "next_low": lo, "next_close": cl,
        "touched_call": bool(hi >= cw),
        "touched_put": bool(lo <= pw),
        "respected_call": bool(hi >= cw and cl < cw),
        "respected_put": bool(lo <= pw and cl > pw),
        "contained": bool(pw <= cl <= cw),
        "null_contained": bool((S - half) <= cl <= (S + half)),
        "next_range_pct": round((hi - lo) / cl * 100, 3),
    }


def _db_enabled() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


def load_ledger() -> dict:
    with _get_engine().connect() as c:
        row = c.execute(text("SELECT blob FROM gamma_wall_ledger WHERE id=1")).fetchone()
    if row is None:
        ledger = {"sessions": {}, "last_run": None}
    else:
        ledger = row[0]
    return ledger


def save_ledger(led: dict, processed: list[dict]) -> None:
    with _get_engine().begin() as c:
        c.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_KEY})
        c.execute(text("INSERT INTO gamma_wall_ledger (id, blob, updated_at) "
                       "VALUES (1, CAST(:b AS JSONB), now()) "
                       "ON CONFLICT (id) DO UPDATE SET blob=EXCLUDED.blob, updated_at=now()"),
                  {"b": json.dumps(led, ensure_ascii=False)})
        if processed:
            c.execute(text(
                "DELETE FROM us_options_chain_pending_snapshots "
                "WHERE session = :session AND snapshot_ts = :snapshot_ts"
            ), processed)


def is_due() -> bool:
    led = load_ledger()
    last = led.get("last_run")
    now = dt.datetime.now(dt.timezone.utc)
    if now.hour < 13:
        return False
    if not last:
        return True
    return dt.datetime.fromisoformat(last).date() < now.date()


def _run(dry: bool) -> dict:
    led = load_ledger()
    sessions: dict = led["sessions"]
    first_run = not sessions
    chain = load_chain()
    processed = []
    added = 0
    no_signal = 0
    for sess, grp in chain.groupby("session"):
        key = sess.isoformat()
        last_ts = grp.snapshot_ts.max()
        identity = {"session": sess, "snapshot_ts": last_ts.to_pydatetime()}
        if key in sessions and pd.Timestamp(sessions[key]["snapshot_ts"]) >= last_ts:
            processed.append(identity)
            continue
        snap = grp[grp.snapshot_ts == last_ts]
        spot = float(snap.underlying_price.iloc[0])
        if not np.isfinite(spot) or spot <= 0:
            raise ValueError("Gamma Wall underlying_price는 유효한 양수여야 합니다")
        contracts = snap[snap["option"].notna()]
        rec = compute_walls(contracts, sess)
        if rec is None:
            if key in sessions:
                del sessions[key]
            processed.append(identity)
            no_signal += 1
            continue
        if key in sessions:
            rec["backfilled"] = sessions[key]["backfilled"]
        else:
            rec["backfilled"] = first_run
            added += 1
        sessions[key] = rec
        processed.append(identity)

    unscored = [dt.date.fromisoformat(key) for key, rec in sessions.items() if "result" not in rec]
    daily = load_daily(unscored)
    scored = 0
    if not daily.empty:
        idx = list(daily.index)
        for key, rec in sessions.items():
            if "result" in rec:
                continue
            s = dt.date.fromisoformat(key)
            later = [d for d in idx if d > s]
            if not later:
                continue
            rec["result"] = score(rec, daily.loc[later[0]])
            scored += 1

    led["last_run"] = dt.datetime.now(dt.timezone.utc).isoformat()
    if not dry:
        save_ledger(led, processed)
    result = {
        "added": added, "scored": scored, "total": len(sessions),
        "first_run": first_run, "no_signal": no_signal,
    }
    return result


def run(dry: bool = False) -> dict:
    with _get_engine().begin() as c:
        c.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_KEY + 1})
        result = _run(dry)
    return result


def is_running() -> bool:
    return _LOCK.locked()


def run_exclusive(dry: bool = False) -> dict | None:
    if not _LOCK.acquire(blocking=False):
        return None
    try:
        return run(dry=dry)
    finally:
        _LOCK.release()


async def get_state(symbol: str = "SPY", tfs: str = "1d") -> dict | None:
    if not is_due():
        return None
    return await asyncio.to_thread(run_exclusive)
