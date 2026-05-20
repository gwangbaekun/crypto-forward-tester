"""FRED 경제 데이터 async 클라이언트.

사용 시리즈:
  DFEDTARU  — Fed Funds Target Rate Upper Bound (daily)
  CPIAUCSL  — CPI (monthly)
  PCEPI     — PCE (monthly)
  UNRATE    — Unemployment Rate (monthly)

API key: fred.stlouisfed.org → My Account → API Keys (무료)
env var: FRED_API_KEY
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import httpx
import pandas as pd

FRED_BASE = "https://api.stlouisfed.org/fred"
TIMEOUT   = 20.0


def _resolve_key(api_key: str | None) -> str:
    key = api_key or os.environ.get("FRED_API_KEY", "")
    if not key:
        raise ValueError(
            "FRED API key 필요. FRED_API_KEY 환경변수 또는 api_key= 인자로 전달.\n"
            "무료 발급: https://fred.stlouisfed.org/docs/api/api_key.html"
        )
    return key


async def fetch_series(
    series_id: str,
    start: date,
    end: date,
    api_key: str | None = None,
) -> pd.Series:
    """FRED 시리즈를 pd.Series(date → float) 로 반환."""
    key = _resolve_key(api_key)
    params = {
        "series_id": series_id,
        "observation_start": start.isoformat(),
        "observation_end": end.isoformat(),
        "api_key": key,
        "file_type": "json",
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.get(f"{FRED_BASE}/series/observations", params=params)
        r.raise_for_status()
        obs = r.json().get("observations", [])

    data = {}
    for o in obs:
        if o.get("value", ".") == ".":
            continue
        data[date.fromisoformat(o["date"])] = float(o["value"])
    return pd.Series(data).sort_index()


async def build_fred_cache(
    api_key: str | None = None,
    start_year: int = 2010,
) -> dict[str, pd.Series]:
    """4개 시리즈 한번에 fetch → {series_id: pd.Series}."""
    import asyncio
    start = date(start_year - 2, 1, 1)
    end   = date.today()
    ids   = ["DFEDTARU", "CPIAUCSL", "PCEPI", "UNRATE"]
    results = await asyncio.gather(*[fetch_series(s, start, end, api_key) for s in ids])
    return dict(zip(ids, results))


def latest_before(series: pd.Series, as_of: date) -> float | None:
    sub = series.loc[:as_of]
    return float(sub.iloc[-1]) if not sub.empty else None


def yoy(series: pd.Series, as_of: date, current: float) -> float:
    prior = latest_before(series, date(as_of.year - 1, as_of.month, as_of.day))
    if prior is None or prior == 0:
        return 0.0
    return (current - prior) / prior * 100.0
