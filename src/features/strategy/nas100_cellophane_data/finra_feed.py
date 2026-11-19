from __future__ import annotations

import datetime as dt
from email.utils import parsedate_to_datetime

import httpx

URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{day}.txt"
HEADER = "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market"
COLUMN = {"short_volume": 2, "short_exempt_volume": 3, "total_volume": 4}


def day_file(day, symbol, column):
    r = httpx.get(URL.format(day=f"{day:%Y%m%d}"), timeout=30)
    if r.status_code == 403:
        return None
    r.raise_for_status()
    stamp = r.headers.get("Last-Modified")
    if stamp is None:
        raise ValueError(f"{day}: 200 인데 Last-Modified 가 없다")
    published = parsedate_to_datetime(stamp).astimezone(dt.timezone.utc)
    if published.date() < day:
        raise ValueError(f"{day}: 올린 시각이 거래일보다 이르다: {published}")
    lines = r.text.splitlines()
    if lines[0] != HEADER:
        raise ValueError(f"{day}: 헤더가 다르다: {lines[0]}")
    hits = [line.split("|") for line in lines[1:] if line.split("|")[1:2] == [symbol]]
    if len(hits) != 1:
        raise ValueError(f"{day}: {symbol} 줄이 {len(hits)}개다")
    out = (int(published.timestamp()), day, float(hits[0][COLUMN[column]]))
    return out


def weekdays(start, end):
    n = (end - start).days + 1
    out = [d for d in (start + dt.timedelta(days=i) for i in range(n)) if d.weekday() < 5]
    return out


def rows(symbol, column, start, end):
    got = [day_file(d, symbol, column) for d in weekdays(start, end)]
    out = [g for g in got if g is not None]
    return out
