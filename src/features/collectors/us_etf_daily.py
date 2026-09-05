from __future__ import annotations

import logging

import pandas as pd

from features.collectors.pg import upsert

log = logging.getLogger(__name__)

SYMBOLS = ["SPY", "QQQ", "IWM", "DIA"]
FIELDS = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
}


def normalize(raw: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    frames = []
    for sym in symbols:
        if isinstance(raw.columns, pd.MultiIndex):
            if sym not in raw.columns.get_level_values(0):
                continue
            sub = raw[sym]
        else:
            sub = raw
        have = [c for c in FIELDS if c in sub.columns]
        if not have:
            continue
        sub = sub.rename(columns=FIELDS)[[FIELDS[c] for c in have]].reset_index()
        sub.columns = [c.lower() if isinstance(c, str) else c for c in sub.columns]
        sub = sub.rename(columns={"index": "date"})
        sub["symbol"] = sym
        frames.append(sub.dropna(subset=["close"]))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def collect(days: int = 400) -> dict:
    import yfinance as yf

    raw = yf.download(
        SYMBOLS,
        period=f"{days}d",
        auto_adjust=False,
        group_by="ticker",
        progress=False,
        threads=False,
    )
    if raw is None or raw.empty:
        return {"rows": 0}
    df = normalize(raw, SYMBOLS)
    if df.empty:
        return {"rows": 0}
    rows = upsert(df, "us_etf_daily", ["symbol", "date"])
    return {"rows": rows, "symbols": df["symbol"].nunique()}
