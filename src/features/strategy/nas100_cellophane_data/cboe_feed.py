from __future__ import annotations

import csv
import datetime as dt
import io
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import httpx

URL = "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/{name}_History.csv"
HEADER = ["DATE", "OPEN", "HIGH", "LOW", "CLOSE"]
ET = ZoneInfo("America/New_York")
DAY_END = dt.time(23, 59)


def day_end_s(day):
    out = int(dt.datetime.combine(day, DAY_END, tzinfo=ET).timestamp())
    return out


def rows(name, start):
    r = httpx.get(URL.format(name=name), timeout=30)
    r.raise_for_status()
    stamp = r.headers.get("Last-Modified")
    if stamp is None:
        raise ValueError(f"{name}: Last-Modified 가 없다")
    published = int(parsedate_to_datetime(stamp).timestamp())
    table = list(csv.reader(io.StringIO(r.text)))
    if table[0] != HEADER:
        raise ValueError(f"{name}: 헤더가 다르다: {table[0]}")
    days = [(dt.datetime.strptime(d, "%m/%d/%Y").date(), float(c)) for d, _, _, _, c in table[1:]]
    out = [(d, c) for d, c in days if d >= start]
    return out, published
