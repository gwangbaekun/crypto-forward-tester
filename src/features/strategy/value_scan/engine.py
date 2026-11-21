from __future__ import annotations

import json
import math
import urllib.request as _urllib_req
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from statistics import median
from typing import Any, Optional

from features.strategy.value_scan.paths import (
    DATA_DIR,
    LAST_ACTIVITY_FILE,
    get_scan_dir,
)


_KRX_ETF_CODES = {"069500", "122630", "114800", "091160"}

_scan_running = False
_last_scan_at: Optional[str] = None


def _fetch_kospi_sector_map() -> dict[str, str]:
    from html.parser import HTMLParser
    url = (
        "https://kind.krx.co.kr/corpgeneral/corpList.do"
        "?method=download&searchType=13&marketType=stockMkt"
    )
    req = _urllib_req.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://kind.krx.co.kr/"})
    with _urllib_req.urlopen(req, timeout=15) as r:
        content = r.read().decode("euc-kr", errors="ignore")

    class _P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows: list[list[str]] = []
            self._row: list[str] = []
            self._cell = ""
            self._in = False
        def handle_starttag(self, tag, attrs):
            if tag in ("td", "th"):
                self._in = True; self._cell = ""
            elif tag == "tr":
                self._row = []
        def handle_endtag(self, tag):
            if tag in ("td", "th"):
                self._row.append(self._cell.strip()); self._in = False
            elif tag == "tr" and self._row:
                self.rows.append(self._row)
        def handle_data(self, data):
            if self._in: self._cell += data

    p = _P(); p.feed(content)
    return {row[2].strip().zfill(6): row[3].strip() or "기타" for row in p.rows[1:] if len(row) >= 4}


def _fetch_kospi200_universe() -> list[tuple[str, str]]:
    try:
        sector_map = _fetch_kospi_sector_map()
    except Exception:
        sector_map = {}

    results: list[tuple[str, str]] = []
    for page in range(1, 5):
        url = (f"https://m.stock.naver.com/api/stocks/marketValue"
               f"?page={page}&pageSize=60&market=KOSPI&type=marketValue")
        try:
            req = _urllib_req.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _urllib_req.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
        except Exception as e:
            if page == 1:
                raise RuntimeError(f"Naver 시총 API 실패: {e}")
            break
        stocks = data.get("stocks", [])
        if not stocks:
            break
        for s in stocks:
            code = s.get("itemCode", "")
            if code and code not in _KRX_ETF_CODES:
                results.append((code, sector_map.get(code, "기타")))
        if len(results) >= 200:
            break

    if not results:
        raise RuntimeError("KOSPI 유니버스 조회 실패")
    return results[:200]


def _fetch_sp500_universe() -> list[str]:
    from html.parser import HTMLParser
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    req = _urllib_req.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with _urllib_req.urlopen(req, timeout=15) as r:
        content = r.read().decode("utf-8", errors="ignore")

    class _P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_table = self.in_row = self._in_cell = False
            self.col = 0
            self.symbols: list[str] = []
            self._cell = ""
        def handle_starttag(self, tag, attrs):
            attrs_d = dict(attrs)
            if tag == "table" and "wikitable" in attrs_d.get("class", "") and not self.symbols:
                self.in_table = True
            if self.in_table:
                if tag == "tr": self.in_row = True; self.col = 0
                elif tag in ("td", "th"): self._in_cell = True; self._cell = ""
        def handle_endtag(self, tag):
            if tag == "table" and self.in_table: self.in_table = False
            if self.in_table and tag in ("td", "th"):
                if self.in_row and self.col == 0:
                    sym = self._cell.strip().replace(".", "-")
                    if sym and sym not in ("Symbol", ""): self.symbols.append(sym)
                self.col += 1; self._in_cell = False
            elif tag == "tr": self.in_row = False
        def handle_data(self, data):
            if self._in_cell: self._cell += data

    p = _P(); p.feed(content)
    return p.symbols


def _enrich_kospi_with_yfinance(symbol: str, base: dict) -> dict:
    import yfinance as yf
    try:
        info = yf.Ticker(f"{symbol}.KS").info

        def _f(v: Any) -> float:
            return float(v) if v is not None else math.nan

        def _fp(v: Any) -> float:
            f = float(v) if v is not None else math.nan
            return f * 100 if not math.isnan(f) else math.nan

        cur_price = _f(info.get("currentPrice") or info.get("regularMarketPrice"))
        target    = _f(info.get("targetMeanPrice"))

        base.update({
            "roe":          _fp(info.get("returnOnEquity")),
            "roa":          _fp(info.get("returnOnAssets")),
            "op_margin":    _fp(info.get("operatingMargins")),
            "net_margin":   _fp(info.get("profitMargins")),
            "d_e":          _f(info.get("debtToEquity")),
            "current_ratio":_f(info.get("currentRatio")),
            "fcf":          _f(info.get("freeCashflow")),
            "rev_growth":   _fp(info.get("revenueGrowth")),
            "eps_growth":   _fp(info.get("earningsGrowth")),
            "ev_ebitda":    _f(info.get("enterpriseToEbitda")),
            "analyst_rec":  _f(info.get("recommendationMean")),
            "target_upside": (
                round((target / cur_price - 1) * 100, 1)
                if not math.isnan(target) and not math.isnan(cur_price) and cur_price > 0
                else math.nan
            ),
        })
    except Exception:
        pass
    return base


def _fetch_kospi_fundamental(symbol: str, sector: str) -> Optional[dict[str, Any]]:
    try:
        req = _urllib_req.Request(
            f"https://m.stock.naver.com/api/stock/{symbol}/integration",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with _urllib_req.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())

        info = {item["code"]: item.get("value", "") for item in data.get("totalInfos", [])}

        def _f(v: str) -> float:
            try:
                c = v.replace(",", "").replace("배", "").replace("원", "").replace("%", "").strip()
                return float(c) if c not in ("", "-") else math.nan
            except (ValueError, TypeError):
                return math.nan

        per = _f(info.get("per", ""))
        pbr = _f(info.get("pbr", ""))
        if any(math.isnan(v) for v in [per, pbr]):
            return None

        base = {
            "symbol": symbol, "name": data.get("stockName") or symbol,
            "market": "kospi", "sector": sector,
            "eps": _f(info.get("eps", "")), "bps": _f(info.get("bps", "")),
            "per": per, "pbr": pbr,
            "forward_per": _f(info.get("cnsPer", "")),
            "forward_eps": _f(info.get("cnsEps", "")),
            "div": _f(info.get("dividendYieldRatio", "")),
        }
        return _enrich_kospi_with_yfinance(symbol, base)
    except Exception:
        return None


def _fetch_nasdaq_fundamental(symbol: str) -> Optional[dict[str, Any]]:
    import yfinance as yf
    try:
        info = yf.Ticker(symbol).info
        if info.get("quoteType") in ("ETF", "MUTUALFUND"):
            return None

        def _f(v: Any) -> float:
            return float(v) if v is not None else math.nan

        def _fp(v: Any) -> float:
            f = float(v) if v is not None else math.nan
            return f * 100 if not math.isnan(f) else math.nan

        per = _f(info.get("trailingPE"))
        pbr = _f(info.get("priceToBook"))
        if math.isnan(per) and math.isnan(pbr):
            return None

        cur_price = _f(info.get("currentPrice") or info.get("regularMarketPrice"))
        target    = _f(info.get("targetMeanPrice"))
        target_upside = (
            round((target / cur_price - 1) * 100, 1)
            if not math.isnan(target) and not math.isnan(cur_price) and cur_price > 0
            else math.nan
        )

        return {
            "symbol": symbol, "name": info.get("shortName") or info.get("longName") or symbol,
            "market": "nasdaq", "sector": info.get("sector") or "Other",
            "eps": _f(info.get("trailingEps")), "bps": _f(info.get("bookValue")),
            "per": per, "pbr": pbr,
            "forward_eps": _f(info.get("forwardEps")),
            "forward_per": _f(info.get("forwardPE")),
            "div": _fp(info.get("dividendYield")) if info.get("dividendYield") else math.nan,
            "ev_ebitda": _f(info.get("enterpriseToEbitda")),
            "roe": _fp(info.get("returnOnEquity")),
            "roa": _fp(info.get("returnOnAssets")),
            "op_margin": _fp(info.get("operatingMargins")),
            "net_margin": _fp(info.get("profitMargins")),
            "d_e": _f(info.get("debtToEquity")),
            "current_ratio": _f(info.get("currentRatio")),
            "fcf": _f(info.get("freeCashflow")),
            "rev_growth": _fp(info.get("revenueGrowth")),
            "eps_growth": _fp(info.get("earningsGrowth")),
            "analyst_rec": _f(info.get("recommendationMean")),
            "target_upside": target_upside,
        }
    except Exception:
        return None


def _score_stock(r: dict, sector_med: dict) -> dict:
    return _score_nasdaq(r, sector_med)


def _score_nasdaq(r: dict, sector_med: dict) -> dict:

    def _v(key: str) -> float:
        v = r.get(key)
        return math.nan if v is None else float(v)

    def ok(v: float) -> bool:
        return not math.isnan(v)

    val = 0
    per = _v("per")
    med = sector_med.get(r["sector"], math.nan)
    if ok(per) and per > 0 and ok(med) and med > 0:
        ratio = per / med
        if   ratio <= 0.45: val += 15
        elif ratio <= 0.65: val += 11
        elif ratio <= 0.85: val += 7
        elif ratio <= 1.00: val += 3
        elif ratio <= 1.20: val += 1

    ev_ebitda = _v("ev_ebitda")
    if ok(ev_ebitda) and ev_ebitda > 0:
        if   ev_ebitda < 8:  val += 9
        elif ev_ebitda < 12: val += 6
        elif ev_ebitda < 18: val += 3
        elif ev_ebitda < 25: val += 1

    eps_g    = _v("eps_growth")
    fwd_per  = _v("forward_per")
    if ok(fwd_per) and fwd_per > 0 and ok(eps_g) and eps_g > 0:
        peg = fwd_per / eps_g
        if   peg < 0.8: val += 6
        elif peg < 1.2: val += 4
        elif peg < 1.8: val += 2

    val_score = min(val, 30)

    qual = 0
    roe = _v("roe")
    if ok(roe):
        if   roe >= 30: qual += 12
        elif roe >= 20: qual += 9
        elif roe >= 12: qual += 6
        elif roe >= 5:  qual += 2

    op_margin = _v("op_margin")
    if ok(op_margin):
        if   op_margin >= 35: qual += 10
        elif op_margin >= 20: qual += 7
        elif op_margin >= 10: qual += 4
        elif op_margin >= 0:  qual += 1

    roa = _v("roa")
    if ok(roa):
        if   roa >= 15: qual += 8
        elif roa >= 8:  qual += 5
        elif roa >= 0:  qual += 2

    qual_score = min(qual, 30)

    health = 0
    de = _v("d_e")
    if ok(de):
        if   de < 0:   health += 10
        elif de < 30:  health += 10
        elif de < 80:  health += 7
        elif de < 150: health += 3
    else:
        health += 4

    cr = _v("current_ratio")
    if ok(cr):
        if   cr >= 3.0: health += 6
        elif cr >= 2.0: health += 5
        elif cr >= 1.5: health += 3
        elif cr >= 1.0: health += 1

    fcf = _v("fcf")
    if ok(fcf):
        health += 4 if fcf > 0 else 0

    health_score = min(health, 20)

    growth = 0
    rev_g = _v("rev_growth")
    if ok(rev_g):
        if   rev_g >= 25: growth += 8
        elif rev_g >= 15: growth += 6
        elif rev_g >= 5:  growth += 3
        elif rev_g >= 0:  growth += 1

    if ok(eps_g):
        if   eps_g >= 25: growth += 7
        elif eps_g >= 15: growth += 5
        elif eps_g >= 5:  growth += 2

    growth_score = min(growth, 15)

    composite = val_score + qual_score + health_score + growth_score

    return {
        "score":           composite,
        "score_valuation": val_score,
        "score_quality":   qual_score,
        "score_health":    health_score,
        "score_growth":    growth_score,
    }


def _rate_nasdaq_by_score(r: dict) -> str:
    score  = r.get("score", 0) or 0
    qual   = r.get("score_quality", 0) or 0
    health = r.get("score_health", 0) or 0
    de     = r.get("d_e", math.nan)
    roe    = r.get("roe", math.nan)

    if not math.isnan(de) and de > 400:
        return "SELL"
    if not math.isnan(roe) and roe < -15:
        return "SELL"

    if score >= 62 and qual >= 14 and health >= 8:
        return "BUY"
    if score <= 27 or health <= 4:
        return "SELL"
    return "HOLD"


def _sector_medians(rows: list[dict]) -> dict[str, float]:
    by_sector: dict[str, list[float]] = {}
    for r in rows:
        per = r["per"]
        if not math.isnan(per) and per > 0:
            by_sector.setdefault(r["sector"], []).append(per)
    return {s: median(vals) for s, vals in by_sector.items() if vals}


_SECTOR_BUY_CFG: dict[str, dict] = {
    "Technology":             {"per_ratio": 0.55, "fwd_growth": 1.20},
    "Healthcare":             {"per_ratio": 0.60, "fwd_growth": 1.12},
    "Consumer Cyclical":      {"per_ratio": 0.60, "fwd_growth": 1.12},
    "Consumer Discretionary": {"per_ratio": 0.60, "fwd_growth": 1.12},
    "Industrials":            {"per_ratio": 0.62, "fwd_growth": 1.10},
    "Communication Services": {"per_ratio": 0.62, "fwd_growth": 1.10},
    "Energy":                 {"per_ratio": 0.58, "fwd_growth": 1.12},
    "Basic Materials":        {"per_ratio": 0.58, "fwd_growth": 1.10},
    "Materials":              {"per_ratio": 0.58, "fwd_growth": 1.10},
    "Financial Services":     {"per_ratio": 0.62, "fwd_growth": 1.08},
    "Financials":             {"per_ratio": 0.62, "fwd_growth": 1.08},
    "Consumer Defensive":     {"per_ratio": 0.68, "fwd_growth": 1.05},
    "Consumer Staples":       {"per_ratio": 0.68, "fwd_growth": 1.05},
    "Utilities":              {"per_ratio": 0.72, "fwd_growth": 1.03},
    "_default":               {"per_ratio": 0.60, "fwd_growth": 1.10},
}


def _rate(r: dict, sector_med: dict) -> str:
    per     = r["per"]
    eps     = r["eps"]
    fwd_eps = r["forward_eps"]
    pbr     = r["pbr"]
    med     = sector_med.get(r["sector"], math.nan)
    cfg     = _SECTOR_BUY_CFG.get(r["sector"], _SECTOR_BUY_CFG["_default"])

    if math.isnan(per) or per <= 0:
        return "HOLD"
    if not math.isnan(med) and per > med * 1.20:
        return "SELL"
    if not math.isnan(eps) and not math.isnan(fwd_eps) and eps > 0 and fwd_eps < eps * 0.75:
        return "SELL"
    if (
        not math.isnan(med) and per < med * cfg["per_ratio"]
        and not math.isnan(eps) and eps > 0
        and not math.isnan(pbr) and pbr > 0
        and not math.isnan(fwd_eps) and fwd_eps >= eps * cfg["fwd_growth"]
    ):
        return "BUY"
    return "HOLD"


def run_scan(market: str, max_workers: int = 10) -> list[dict]:
    if market == "kospi":
        universe = _fetch_kospi200_universe()
        rows: list[dict] = []
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_fetch_kospi_fundamental, sym, sec): sym for sym, sec in universe}
            for fut in as_completed(futures):
                r = fut.result()
                if r is not None:
                    rows.append(r)
    else:
        symbols = _fetch_sp500_universe()
        rows = []
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_fetch_nasdaq_fundamental, sym): sym for sym in symbols}
            for fut in as_completed(futures):
                r = fut.result()
                if r is not None:
                    rows.append(r)

    sec_med = _sector_medians(rows)
    for r in rows:
        r["sector_median"] = sec_med.get(r["sector"], math.nan)
        r.update(_score_stock(r, sec_med))
        if market == "kospi":
            r["rating"] = _rate(r, sec_med)
        else:
            r["rating"] = _rate_nasdaq_by_score(r)
    return rows


def _nan_to_none(v: Any) -> Any:
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _clean_row(r: dict) -> dict:
    return {k: _nan_to_none(v) for k, v in r.items()}


def save_snapshot(today: str, market: str, rows: list[dict]) -> None:
    clean = [_clean_row(r) for r in rows]

    scan_dir = get_scan_dir(market)
    scan_dir.mkdir(parents=True, exist_ok=True)
    path = scan_dir / f"{today}.json"
    path.write_text(json.dumps(
        {"date": today, "market": market, "rows": clean},
        ensure_ascii=False, indent=2,
    ))

    try:
        from features.strategy.value_scan.cache import vs_cache
        from features.strategy.value_scan.repository import save_snapshot_to_db
        save_snapshot_to_db(today, market, clean)
        vs_cache.invalidate(f"scan_results_{market}")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("save_snapshot DB upsert failed: %s", e)


def run_daily(markets: list[str] = None, max_workers: int = 10) -> dict:
    global _scan_running, _last_scan_at
    if _scan_running:
        return {"error": "scan already running"}

    _scan_running = True
    try:
        if markets is None:
            markets = ["kospi", "nasdaq"]
        today     = datetime.now(UTC).strftime("%Y-%m-%d")
        all_rows: list[dict] = []

        from features.strategy.value_scan.scan_schedule import record_market_scan

        per_market_stats: dict[str, dict] = {}

        for m in markets:
            rows = run_scan(m, max_workers)
            save_snapshot(today, m, rows)
            all_rows.extend(rows)
            m_buy = sum(1 for r in rows if r["rating"] == "BUY")
            m_sell = sum(1 for r in rows if r["rating"] == "SELL")
            per_market_stats[m] = {"scanned": len(rows), "buy": m_buy, "sell": m_sell}

        _last_scan_at = datetime.now(UTC).isoformat()

        buy_n  = sum(1 for r in all_rows if r["rating"] == "BUY")
        sell_n = sum(1 for r in all_rows if r["rating"] == "SELL")

        for m in markets:
            st = per_market_stats[m]
            record_market_scan(
                m,
                scanned=st["scanned"],
                buy=st["buy"],
                sell=st["sell"],
            )

        activity = {
            "date":        today,
            "saved_at":    _last_scan_at,
            "markets":     markets,
            "scanned":     len(all_rows),
            "buy":         buy_n,
            "sell":        sell_n,
            "per_market":  per_market_stats,
        }
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LAST_ACTIVITY_FILE.write_text(json.dumps(activity, ensure_ascii=False, indent=2))

        return {
            "date":        today,
            "scanned":     len(all_rows),
            "buy":         buy_n,
            "sell":        sell_n,
        }
    finally:
        _scan_running = False
        from features.strategy.value_scan.cache import vs_cache
        vs_cache.invalidate()


def get_last_activity() -> dict:
    if LAST_ACTIVITY_FILE.exists():
        return json.loads(LAST_ACTIVITY_FILE.read_text())
    return {}


def is_scan_running() -> bool:
    return _scan_running


async def get_scan_status(**kwargs) -> dict:
    from features.strategy.value_scan.scan_schedule import build_schedule_status

    payload = build_schedule_status(running=_scan_running)
    payload["last_scan_at"] = _last_scan_at
    return payload


