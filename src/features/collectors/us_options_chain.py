from __future__ import annotations

import datetime as dt
import logging
import re
import time

import httpx
import pandas as pd

from features.collectors.pg import upsert

log = logging.getLogger(__name__)

CHAIN_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
UNIVERSE = ["SPY", "QQQ", "IWM", "DIA"]
REQUEST_DELAY_S = 0.25
ACTIVE_UTC = "12:00-21:15"
HEADERS = {"User-Agent": "forwardtest-quant"}

OCC_RE = re.compile(r"^(?P<root>[A-Z0-9]{1,6})(?P<date>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")


def parse_occ_symbol(s: str) -> tuple[dt.date | None, float | None, str | None]:
    m = OCC_RE.match(str(s).replace(" ", ""))
    if not m:
        return None, None, None
    d = dt.datetime.strptime(m["date"], "%y%m%d").date()
    return d, int(m["strike"]) / 1000.0, m["cp"]


def within_window(now: dt.datetime, window: str = ACTIVE_UTC) -> bool:
    start, _, end = window.partition("-")
    return dt.time.fromisoformat(start) <= now.time() <= dt.time.fromisoformat(end)


def fetch_chain(client: httpx.Client, symbol: str) -> pd.DataFrame:
    r = client.get(CHAIN_URL.format(sym=symbol), headers=HEADERS, timeout=30.0)
    r.raise_for_status()
    data = r.json().get("data", {})
    options = data.get("options", [])
    if not options:
        return pd.DataFrame()
    df = pd.DataFrame(options)
    parsed = df["option"].map(parse_occ_symbol)
    df["expiry"] = [p[0] for p in parsed]
    df["strike"] = [p[1] for p in parsed]
    df["option_type"] = [p[2] for p in parsed]
    df["underlying"] = symbol
    df["underlying_price"] = data.get("current_price")
    return df


def collect() -> dict:
    snap_ts = dt.datetime.now(dt.timezone.utc)
    if not within_window(snap_ts):
        return {"rows": 0, "skipped": "outside active window", "utc": snap_ts.strftime("%H:%M")}
    frames: list[pd.DataFrame] = []
    failed: list[str] = []

    with httpx.Client(follow_redirects=True) as client:
        for sym in UNIVERSE:
            try:
                df = fetch_chain(client, sym)
            except Exception as exc:
                failed.append(sym)
                log.warning("[Collector] %s 조회 실패: %s", sym, exc)
            else:
                if not df.empty:
                    frames.append(df)
            time.sleep(REQUEST_DELAY_S)

    if not frames:
        return {"rows": 0, "symbols": 0, "failed": failed}

    chain = pd.concat(frames, ignore_index=True)
    chain = chain.dropna(subset=["expiry", "strike", "option_type"])
    chain["snapshot_ts"] = snap_ts
    rows = upsert(chain, "us_options_chain", ["option", "snapshot_ts"])
    return {
        "rows": rows,
        "symbols": len(frames),
        "failed": failed,
        "snapshot_ts": snap_ts.isoformat(),
    }
