"""US Options Gamma Wall (SPY→US500) — 엔진."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import pathlib
import re
import threading
import time

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)

UNDERLYING = "SPY"
EXEC_SYMBOL = "US500"          # 참고용 — 실거래 없음
MAX_DTE_DAYS = 30              # 벽 집계에 넣을 만기 범위
CONTRACT_MULTIPLIER = 100
LEDGER = pathlib.Path(__file__).resolve().parents[4] / "data" / "gamma_wall_ledger.json"
_LOCK = threading.Lock()
_engine = None


# ────────────────────────────── 데이터 ──────────────────────────────
def _pg_url() -> str:
    return os.getenv("DATABASE_URL", "postgresql://btc:btc@localhost:5432/btc_forwardtest")


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(_pg_url(), pool_pre_ping=True)
    return _engine


def _redact(url: str) -> str:
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", url)


PROBE_TIMEOUT_MS = 20000


def datasource_status() -> dict:
    """대시보드용 데이터 소스 점검 — 원장이 왜 비어있는지 화면에서 보이게 한다.

    조회는 인덱스 없는 테이블 전수 스캔이라 수 초 걸린다. 라우터에서 캐시하고
    대시보드는 비동기로 채운다.
    """
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


def load_chain(days: int = 400) -> pd.DataFrame:
    q = text(
        "SELECT snapshot_ts, last_trade_time, expiry, strike, option_type, "
        "       open_interest, gamma, underlying_price "
        "FROM us_options_chain "
        "WHERE underlying = :u AND open_interest > 0 AND gamma > 0 "
        "  AND snapshot_ts >= now() - ((:d)::text || ' days')::interval"
    )
    df = pd.read_sql(q, _get_engine(), params={"u": UNDERLYING, "d": int(days)})
    if df.empty:
        return df
    df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"], utc=True)
    df["expiry"] = pd.to_datetime(df["expiry"]).dt.date
    return df


def load_daily(days: int = 400) -> pd.DataFrame:
    q = text(
        "SELECT date, open, high, low, close FROM us_etf_daily "
        "WHERE symbol = :s AND date >= now()::date - :d ORDER BY date"
    )
    df = pd.read_sql(q, _get_engine(), params={"s": UNDERLYING, "d": int(days)})
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.drop_duplicates("date").set_index("date")


# ────────────────────────────── 벽 계산 ──────────────────────────────
def compute_walls(snap: pd.DataFrame, session: dt.date) -> dict | None:
    """한 스냅샷 → 행사가별 GEX 집계 → call/put 벽.

    만기 가중은 따로 하지 않는다 — GEX 가 이미 OI 로 가중되므로 OI 40만짜리
    일일 만기와 298만짜리 월물이 자동으로 제 몫만큼만 기여한다.
    """
    if snap.empty:
        return None
    S = float(snap.underlying_price.iloc[0])
    if not np.isfinite(S) or S <= 0:
        return None

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
    call_wall = float(above.idxmax())   # 스팟 위 최대 (+)GEX = 저항
    put_wall = float(below.idxmin())    # 스팟 아래 최소 (−)GEX = 지지
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
    """다음 세션의 OHLC 로 벽이 지켜졌는지 채점.

    귀무 대조군을 반드시 같이 계산한다: 벽과 **같은 폭**을 스팟 기준 대칭으로
    놓은 밴드. 벽이 '폭'이 아니라 '위치' 정보를 담고 있어야만 실제 벽이 대조군을
    이긴다. 이게 없으면 단순히 밴드가 넓어서 잘 담긴 것과 구분되지 않는다.
    """
    S, cw, pw = rec["spot"], rec["call_wall"], rec["put_wall"]
    hi, lo, cl = float(nxt.high), float(nxt.low), float(nxt.close)
    half = ((cw - S) + (S - pw)) / 2.0
    return {
        "next_session": str(nxt.name),
        "next_high": hi, "next_low": lo, "next_close": cl,
        "touched_call": bool(hi >= cw),
        "touched_put": bool(lo <= pw),
        # 벽을 찍고 되돌아왔는가 (관통이 아니라 반발)
        "respected_call": bool(hi >= cw and cl < cw),
        "respected_put": bool(lo <= pw and cl > pw),
        "contained": bool(pw <= cl <= cw),
        "null_contained": bool((S - half) <= cl <= (S + half)),
        "next_range_pct": round((hi - lo) / cl * 100, 3),
    }


# ────────────────────────────── 원장 ──────────────────────────────
def _db_enabled() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


def _fwd_engine():
    return create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)


def load_ledger() -> dict:
    """DATABASE_URL 이 있으면 DB 가 정본 — 컨테이너 파일시스템은 재배포마다 날아간다."""
    if _db_enabled():
        try:
            with _fwd_engine().begin() as c:
                c.execute(text("CREATE TABLE IF NOT EXISTS gamma_wall_ledger "
                               "(id INT PRIMARY KEY, blob JSONB NOT NULL, updated_at TIMESTAMPTZ)"))
                row = c.execute(text("SELECT blob FROM gamma_wall_ledger WHERE id=1")).fetchone()
            if row and row[0]:
                return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        except Exception:
            logger.exception("gamma_wall 원장 DB 읽기 실패 — 파일로 폴백하지 않는다")
            raise
        return {"sessions": {}, "last_run": None}
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return {"sessions": {}, "last_run": None}


def save_ledger(led: dict) -> None:
    if _db_enabled():
        with _fwd_engine().begin() as c:
            c.execute(text("CREATE TABLE IF NOT EXISTS gamma_wall_ledger "
                           "(id INT PRIMARY KEY, blob JSONB NOT NULL, updated_at TIMESTAMPTZ)"))
            c.execute(text("INSERT INTO gamma_wall_ledger (id, blob, updated_at) "
                           "VALUES (1, CAST(:b AS JSONB), now()) "
                           "ON CONFLICT (id) DO UPDATE SET blob=EXCLUDED.blob, updated_at=now()"),
                      {"b": json.dumps(led, ensure_ascii=False)})
        return
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(led, ensure_ascii=False, indent=1))


# ────────────────────────────── 실행 ──────────────────────────────
def is_due() -> bool:
    """하루 1회. 미국 세션 마감 + OI 갱신 이후를 노린다 (13:00 UTC 이후)."""
    led = load_ledger()
    last = led.get("last_run")
    now = dt.datetime.now(dt.timezone.utc)
    if now.hour < 13:
        return False
    if not last:
        return True
    return dt.datetime.fromisoformat(last).date() < now.date()


def run(dry: bool = False) -> dict:
    chain = load_chain()
    if chain.empty:
        return {"error": "us_options_chain 비어있음 (수집 대기)", "added": 0, "scored": 0}
    daily = load_daily()

    led = load_ledger()
    sessions: dict = led.setdefault("sessions", {})
    first_run = not sessions

    # 세션 = 그 스냅이 담고 있는 **미국 거래일**. snapshot_ts 의 UTC 날짜를 쓰면 안 된다:
    # 00:17 UTC 수집은 20:17 ET 라 이미 닫힌 전날 세션을 담고, 주말 재수집은 금요일을
    # 담는다. 수집기 `fetch_session_date` 와 같은 규칙 — 기초자산 마지막 체결(ET)이
    # 그 파일이 어느 세션인지 말해준다.
    lt = pd.to_datetime(chain["last_trade_time"], errors="coerce")
    sess_of = lt.groupby(chain.snapshot_ts).transform("max").dt.date
    if sess_of.isna().all():
        raise RuntimeError("last_trade_time 이 전부 비어있다 — 세션을 특정할 수 없다")
    chain["session"] = sess_of
    added = 0
    for sess, grp in chain.groupby("session"):
        key = sess.isoformat()
        if key in sessions:
            continue
        last_ts = grp.snapshot_ts.max()
        rec = compute_walls(grp[grp.snapshot_ts == last_ts], sess)
        if rec is None:
            continue
        rec["backfilled"] = first_run      # 사후 소급분과 실시간 기록분을 영구히 분리
        sessions[key] = rec
        added += 1

    # 채점: 다음 거래일 OHLC 가 도착한 것만
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
        save_ledger(led)
    return {"added": added, "scored": scored, "total": len(sessions), "first_run": first_run}


def is_running() -> bool:
    return _LOCK.locked()


def run_exclusive(dry: bool = False) -> dict | None:
    """스케줄러 틱과 대시보드 수동 실행이 원장을 동시에 덮는 것을 막는다."""
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
