from __future__ import annotations

import datetime as dt
import os
import re
import time
from contextlib import closing

import httpx
import pandas as pd
import psycopg2

from features.collectors.retention import store_snapshot

CHAIN_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
UNIVERSE = ["SPY", "QQQ", "IWM", "DIA"]
REQUEST_DELAY_S = 0.25
ACTIVE_UTC = "20:30-22:00"
HEADERS = {"User-Agent": "forwardtest-quant"}

OCC_RE = re.compile(r"^(?P<root>[A-Z0-9]{1,6})(?P<date>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")


def parse_occ_symbol(s: str) -> tuple[dt.date | None, float | None, str | None]:
    m = OCC_RE.match(str(s).replace(" ", ""))
    if not m:
        result = (None, None, None)
        return result
    d = dt.datetime.strptime(m["date"], "%y%m%d").date()
    result = (d, int(m["strike"]) / 1000.0, m["cp"])
    return result


def within_window(now: dt.datetime, window: str = ACTIVE_UTC) -> bool:
    start, _, end = window.partition("-")
    active = dt.time.fromisoformat(start) <= now.time() <= dt.time.fromisoformat(end)
    return active


def fetch_chain(client: httpx.Client, symbol: str) -> pd.DataFrame:
    r = client.get(CHAIN_URL.format(sym=symbol), headers=HEADERS, timeout=30.0)
    r.raise_for_status()
    data = r.json()["data"]
    options = data["options"]
    if not options:
        raise ValueError(f"{symbol} option chain이 비어 있습니다")
    df = pd.DataFrame(options)
    parsed = df["option"].map(parse_occ_symbol)
    df["expiry"] = [p[0] for p in parsed]
    df["strike"] = [p[1] for p in parsed]
    df["option_type"] = [p[2] for p in parsed]
    df["underlying"] = symbol
    df["underlying_price"] = data["current_price"]
    return df


def collect() -> dict:
    snap_ts = dt.datetime.now(dt.timezone.utc)
    if not within_window(snap_ts):
        result = {"rows": 0, "skipped": "outside active window", "utc": snap_ts.strftime("%H:%M")}
        return result
    frames: list[pd.DataFrame] = []

    with httpx.Client(follow_redirects=True) as client:
        for sym in UNIVERSE:
            df = fetch_chain(client, sym)
            df = df.dropna(subset=["expiry", "strike", "option_type"])
            if df.empty:
                raise ValueError(f"{sym} 저장 가능한 option contract가 없습니다")
            frames.append(df)
            time.sleep(REQUEST_DELAY_S)

    chain = pd.concat(frames, ignore_index=True)
    chain["snapshot_ts"] = snap_ts
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg2://", "postgresql://")
    with closing(psycopg2.connect(url)) as conn:
        with conn, conn.cursor() as cur:
            rows = store_snapshot(cur, chain)
    result = {
        "rows": rows,
        "symbols": len(frames),
        "failed": [],
        "snapshot_ts": snap_ts.isoformat(),
    }
    return result
