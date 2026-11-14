from __future__ import annotations

import httpx

FAPI = "https://fapi.binance.com"
API = "https://api.binance.com"
BAR = 300
OI_PAGE = 500
KLINE_PAGE = 1000
LAG = {"open_interest": BAR, "long_short_ratio": 2 * BAR, "spot_sell_volume": BAR}


def get(url, params):
    r = httpx.get(url, params=params, timeout=30)
    r.raise_for_status()
    out = r.json()
    if not isinstance(out, list):
        raise ValueError(f"{url} 응답이 list 가 아니다: {out}")
    return out


def pages(url, base, time_of, page, need, end_s):
    rows = {}
    end_ms = end_s * 1000
    while len(rows) < need:
        got = get(url, {**base, "limit": page, "endTime": end_ms})
        fresh = {time_of(r): r for r in got if time_of(r) not in rows}
        if not fresh:
            raise ValueError(f"{url}: {len(rows)}행에서 끝났다. {need}행이 필요하다")
        rows.update(fresh)
        end_ms = min(fresh) - 1
    out = [rows[t] for t in sorted(rows)][-need:]
    return out


def open_interest(symbol, need, end_s):
    got = pages(f"{FAPI}/futures/data/openInterestHist", {"symbol": symbol, "period": "5m"},
                lambda r: r["timestamp"], OI_PAGE, need, end_s - LAG["open_interest"])
    out = [(r["timestamp"] // 1000 + LAG["open_interest"], float(r["sumOpenInterest"])) for r in got]
    return out


def long_short_ratio(symbol, need, end_s):
    got = pages(f"{FAPI}/futures/data/globalLongShortAccountRatio", {"symbol": symbol, "period": "5m"},
                lambda r: r["timestamp"], OI_PAGE, need, end_s - LAG["long_short_ratio"])
    out = [(r["timestamp"] // 1000 + LAG["long_short_ratio"], float(r["longShortRatio"])) for r in got]
    return out


def spot_sell_volume(symbol, need, end_s):
    got = pages(f"{API}/api/v3/klines", {"symbol": symbol, "interval": "5m"}, lambda r: r[0], KLINE_PAGE, need + 1,
                end_s)
    closed = [r for r in got if r[0] // 1000 + BAR <= end_s][-need:]
    if len(closed) < need:
        raise ValueError(f"닫힌 현물 5분봉이 {len(closed)}개뿐이다. {need}개가 필요하다")
    out = [(r[0] // 1000 + LAG["spot_sell_volume"], float(r[5]) - float(r[9])) for r in closed]
    return out
