"""
Value Scan Engine — KOSPI 200 + S&P 500 섹터 상대 가치 스캔 + Forward Test 포지션 관리

판정 기준:
  BUY  : per < sector_median × 0.70  AND  eps > 0  AND  pbr > 0
         AND  fwd_eps 필수  AND  fwd_eps >= eps × 1.05
  SELL : per > sector_median × 1.20  OR   fwd_eps < eps × 0.75
  HOLD : 나머지
"""
from __future__ import annotations

import json
import math
import urllib.request as _urllib_req
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Optional

from features.strategy.value_scan.paths import (
    DATA_DIR,
    HISTORY_FILE,
    LAST_ACTIVITY_FILE,
    POSITIONS_FILE,
    SCANS_DIR,
)

UNIT_USD = 1.0  # $1 per BUY signal

_KRX_ETF_CODES = {"069500", "122630", "114800", "091160"}

_scan_running = False
_last_scan_at: Optional[str] = None


# ── 유니버스 조회 ─────────────────────────────────────────────────────────────

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


# ── 펀더멘털 조회 ─────────────────────────────────────────────────────────────

def _enrich_kospi_with_yfinance(symbol: str, base: dict) -> dict:
    """yfinance .KS 로 quality/health/growth/sentiment 필드 보완."""
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
            # base valuation
            "eps": _f(info.get("trailingEps")), "bps": _f(info.get("bookValue")),
            "per": per, "pbr": pbr,
            "forward_eps": _f(info.get("forwardEps")),
            "forward_per": _f(info.get("forwardPE")),
            "div": _fp(info.get("dividendYield")) if info.get("dividendYield") else math.nan,
            "ev_ebitda": _f(info.get("enterpriseToEbitda")),
            # quality
            "roe": _fp(info.get("returnOnEquity")),
            "roa": _fp(info.get("returnOnAssets")),
            "op_margin": _fp(info.get("operatingMargins")),
            "net_margin": _fp(info.get("profitMargins")),
            # health
            "d_e": _f(info.get("debtToEquity")),
            "current_ratio": _f(info.get("currentRatio")),
            "fcf": _f(info.get("freeCashflow")),
            # growth
            "rev_growth": _fp(info.get("revenueGrowth")),
            "eps_growth": _fp(info.get("earningsGrowth")),
            # sentiment
            "analyst_rec": _f(info.get("recommendationMean")),
            "target_upside": target_upside,
        }
    except Exception:
        return None


def _score_stock(r: dict, sector_med: dict) -> dict:
    return _score_nasdaq(r, sector_med)


def _score_nasdaq(r: dict, sector_med: dict) -> dict:
    """5-factor composite score 0-100 for a NASDAQ stock."""

    def _v(key: str) -> float:
        v = r.get(key)
        return math.nan if v is None else float(v)

    def ok(v: float) -> bool:
        return not math.isnan(v)

    # ── Valuation (30 pts) ────────────────────────────────────────────────────
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

    # ── Quality (30 pts) ──────────────────────────────────────────────────────
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

    # ── Health (20 pts) ───────────────────────────────────────────────────────
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

    # ── Growth (15 pts) ───────────────────────────────────────────────────────
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

    # ── Sentiment (5 pts) ─────────────────────────────────────────────────────
    rec = _v("analyst_rec")
    if ok(rec) and rec > 0:
        if   rec <= 1.5: sent_score = 5
        elif rec <= 2.0: sent_score = 4
        elif rec <= 2.5: sent_score = 3
        elif rec <= 3.0: sent_score = 1
        else:            sent_score = 0
    else:
        sent_score = 0

    composite = val_score + qual_score + health_score + growth_score + sent_score

    return {
        "score":           composite,
        "score_valuation": val_score,
        "score_quality":   qual_score,
        "score_health":    health_score,
        "score_growth":    growth_score,
        "score_sentiment": sent_score,
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

    if score >= 65 and qual >= 14 and health >= 8:
        return "BUY"
    if score <= 28 or health <= 4:
        return "SELL"
    return "HOLD"


# ── 섹터 중앙값 + 판정 ─────────────────────────────────────────────────────────

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


# ── 종가 조회 ─────────────────────────────────────────────────────────────────

def _fetch_kospi_price(symbol: str) -> float:
    try:
        import yfinance as yf
        fi = yf.Ticker(f"{symbol}.KS").fast_info
        price = fi.get("lastPrice") or fi.get("regularMarketPreviousClose")
        if price:
            return float(price)
    except Exception:
        pass
    return math.nan


def _fetch_nasdaq_price(symbol: str) -> float:
    try:
        import yfinance as yf
        fi = yf.Ticker(symbol).fast_info
        price = fi.get("lastPrice") or fi.get("regularMarketPreviousClose")
        if price:
            return float(price)
    except Exception:
        pass
    return math.nan


def _fetch_price(market: str, symbol: str) -> float:
    return _fetch_kospi_price(symbol) if market == "kospi" else _fetch_nasdaq_price(symbol)


# ── 스캔 ──────────────────────────────────────────────────────────────────────

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
        r["rating"] = _rate_nasdaq_by_score(r)
    return rows


# ── 포지션 I/O ────────────────────────────────────────────────────────────────

def _pos_key(market: str, symbol: str) -> str:
    return f"{market}_{symbol}"


def load_positions() -> dict[str, dict]:
    from features.strategy.value_scan.repository import (
        ensure_migrated_from_json_if_needed,
        load_positions_from_db,
    )

    ensure_migrated_from_json_if_needed()
    return load_positions_from_db()


def load_history() -> list[dict]:
    from features.strategy.value_scan.repository import (
        ensure_migrated_from_json_if_needed,
        load_history_from_db,
    )

    ensure_migrated_from_json_if_needed()
    return load_history_from_db()


def _save_positions(pos: dict) -> None:
    from features.strategy.value_scan.repository import save_positions_to_db

    save_positions_to_db(pos)


def _save_history(hist: list) -> None:
    from features.strategy.value_scan.repository import save_history_to_db

    save_history_to_db(hist)


def _nan_to_none(v: Any) -> Any:
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _clean_row(r: dict) -> dict:
    return {k: _nan_to_none(v) for k, v in r.items()}


# ── 일간 업데이트 ─────────────────────────────────────────────────────────────

def _calc_pnl(lots: list[dict], exit_price: float) -> tuple[Optional[float], Optional[float]]:
    """lots 리스트와 exit_price로 (avg_pnl_pct, pnl_usd) 계산."""
    if math.isnan(exit_price) or not lots:
        return None, None
    valid = [l for l in lots if l.get("price") and l["price"] > 0]
    if not valid:
        return None, None
    returns = [(exit_price - l["price"]) / l["price"] for l in valid]
    avg_pnl_pct = sum(returns) / len(returns) * 100
    invested = len(lots) * UNIT_USD
    pnl_usd = invested * avg_pnl_pct / 100
    return round(avg_pnl_pct, 2), round(pnl_usd, 4)


def update_positions(
    all_rows: list[dict],
    today: str,
    markets: Optional[list[str]] = None,
) -> dict:
    """포지션 갱신.

    - ``markets``: 이번에 실제로 스캔한 시장만 전달 (KOSPI 스캔 시 NASDAQ 포지션은 건드리지 않음).
    - 청산: rating == SELL 인 경우만. 스캔 결과에 없거나 HOLD/BUY 면 오픈 유지.
    """
    positions = load_positions()
    history   = load_history()

    if markets is None:
        markets = sorted({r["market"] for r in all_rows if r.get("market")})
    markets_set = set(markets)

    rating_map = {_pos_key(r["market"], r["symbol"]): r for r in all_rows}
    sell_keys  = {
        _pos_key(r["market"], r["symbol"])
        for r in all_rows if r["rating"] == "SELL"
    }

    new_entries: list[dict] = []
    new_exits:   list[dict] = []

    # ── SELL → 청산 (이번에 스캔한 시장의 포지션만) ───────────────────────────
    for key, pos in list(positions.items()):
        if pos.get("market") not in markets_set:
            continue
        if key not in sell_keys:
            continue
        cur = rating_map.get(key)
        exit_price = _fetch_price(pos["market"], pos["symbol"])
        lots       = pos.get("lots", [])
        avg_pnl_pct, pnl_usd = _calc_pnl(lots, exit_price)
        record = {
            **pos,
            "exit_date":   today,
            "exit_price":  None if math.isnan(exit_price) else exit_price,
            "exit_reason": (cur or {}).get("rating") or "SELL",
            "pnl_pct":     avg_pnl_pct,
            "pnl_usd":     pnl_usd,
            "hold_days":   (
                datetime.strptime(today, "%Y-%m-%d")
                - datetime.strptime(pos["first_entry_date"], "%Y-%m-%d")
            ).days,
        }
        history.append(record)
        del positions[key]
        new_exits.append(record)

    # ── BUY → $1 lot 추가 (신규 or 기존 포지션) ─────────────────────────────
    for r in all_rows:
        if r["rating"] != "BUY":
            continue
        key   = _pos_key(r["market"], r["symbol"])
        price = _fetch_price(r["market"], r["symbol"])
        lot   = {"date": today, "price": None if math.isnan(price) else price}

        if key in positions:
            positions[key]["lots"].append(lot)
            positions[key]["invested_usd"] = round(
                positions[key].get("invested_usd", 0) + UNIT_USD, 4
            )
        else:
            entry = _clean_row({
                "symbol":           r["symbol"],
                "name":             r["name"],
                "market":           r["market"],
                "sector":           r["sector"],
                "first_entry_date": today,
                "lots":             [lot],
                "invested_usd":     UNIT_USD,
                "per":              r["per"],
                "sector_median":    r.get("sector_median", math.nan),
                "eps":              r["eps"],
                "fwd_eps":          r["forward_eps"],
                "pbr":              r["pbr"],
            })
            positions[key] = entry
            new_entries.append(entry)

    _save_positions(positions)
    _save_history(history)
    return {"new_entries": new_entries, "new_exits": new_exits, "open": positions}


def save_snapshot(today: str, market: str, rows: list[dict]) -> None:
    clean = [_clean_row(r) for r in rows]

    # JSON 백업 (기존)
    SCANS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCANS_DIR / f"{today}_{market}.json"
    path.write_text(json.dumps(
        {"date": today, "market": market, "rows": clean},
        ensure_ascii=False, indent=2,
    ))

    # DB upsert (영속)
    try:
        from features.strategy.value_scan.repository import save_snapshot_to_db
        save_snapshot_to_db(today, market, clean)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("save_snapshot DB upsert failed: %s", e)


# ── 엔트리포인트 ──────────────────────────────────────────────────────────────

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

        result        = update_positions(all_rows, today, markets=markets)
        _last_scan_at = datetime.now(UTC).isoformat()

        buy_n  = sum(1 for r in all_rows if r["rating"] == "BUY")
        sell_n = sum(1 for r in all_rows if r["rating"] == "SELL")

        for m in markets:
            st = per_market_stats.get(m, {})
            m_entries = sum(1 for e in result["new_entries"] if e.get("market") == m)
            m_exits = sum(1 for e in result["new_exits"] if e.get("market") == m)
            record_market_scan(
                m,
                scanned=st.get("scanned", 0),
                buy=st.get("buy", 0),
                sell=st.get("sell", 0),
                new_entries=m_entries,
                new_exits=m_exits,
            )

        activity = {
            "date":        today,
            "saved_at":    _last_scan_at,
            "markets":     markets,
            "scanned":     len(all_rows),
            "buy":         buy_n,
            "sell":        sell_n,
            "new_entries": result["new_entries"],
            "new_exits":   result["new_exits"],
            "per_market":  per_market_stats,
        }
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LAST_ACTIVITY_FILE.write_text(json.dumps(activity, ensure_ascii=False, indent=2))

        return {
            "date":        today,
            "scanned":     len(all_rows),
            "buy":         buy_n,
            "sell":        sell_n,
            "new_entries": len(result["new_entries"]),
            "new_exits":   len(result["new_exits"]),
            "open":        len(result["open"]),
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


def _enrich_position(pos: dict) -> dict:
    """단일 포지션에 현재가·P&L 계산 (스레드 안전)."""
    lots = pos.get("lots", [])
    cur  = _fetch_price(pos["market"], pos["symbol"])
    avg_pnl_pct, pnl_usd = _calc_pnl(lots, cur)

    valid_prices = [l["price"] for l in lots if l.get("price") and l["price"] > 0]
    avg_entry    = sum(valid_prices) / len(valid_prices) if valid_prices else None

    from features.strategy.value_scan.famous import position_is_famous

    return {
        **pos,
        "lot_count":       len(lots),
        "avg_entry_price": avg_entry,
        "current_price":   None if math.isnan(cur) else cur,
        "pnl_pct":         avg_pnl_pct,
        "pnl_usd":         pnl_usd,
        "is_famous":       position_is_famous(pos),
    }


def _get_positions_with_pnl_uncached(max_workers: int = 20) -> list[dict]:
    positions = load_positions()
    if not positions:
        return []

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_enrich_position, pos) for pos in positions.values()]
        result  = [f.result() for f in futures]

    result.sort(key=lambda x: (x.get("pnl_usd") or 0), reverse=True)
    return result


def get_positions_with_pnl(max_workers: int = 20) -> list[dict]:
    from features.strategy.value_scan.cache import vs_cache
    return vs_cache.get("positions", 300, lambda: _get_positions_with_pnl_uncached(max_workers))


def _fnum(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


def _portfolio_slice(positions: list[dict]) -> dict:
    invested = sum(_fnum(p.get("invested_usd")) for p in positions)
    unreal   = sum(_fnum(p.get("pnl_usd")) for p in positions)
    winners  = sum(1 for p in positions if _fnum(p.get("pnl_usd")) > 0)
    losers   = sum(1 for p in positions if _fnum(p.get("pnl_usd")) < 0)
    flat     = len(positions) - winners - losers
    return {
        "open_count":          len(positions),
        "invested_usd":        round(invested, 2),
        "unrealized_pnl_usd":  round(unreal, 4),
        "unrealized_pnl_pct":  round(unreal / invested * 100, 2) if invested else None,
        "portfolio_value_usd": round(invested + unreal, 4),
        "open_winners":        winners,
        "open_losers":         losers,
        "open_flat":           flat,
    }


def _closed_stats(history: list[dict]) -> dict[str, Any]:
    closed = [h for h in history if h.get("pnl_pct") is not None]
    wins   = [h for h in closed if _fnum(h.get("pnl_pct")) > 0]
    avg_pnl = (
        sum(_fnum(h.get("pnl_pct")) for h in closed) / len(closed)
        if closed else None
    )
    return {
        "closed_count": len(closed),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "avg_pnl_pct": round(avg_pnl, 2) if avg_pnl is not None else None,
        "hist_pnl_usd": round(sum(_fnum(h.get("pnl_usd")) for h in history), 4),
        "hist_invested_usd": round(sum(_fnum(h.get("invested_usd")) for h in history), 2),
    }


def _book_open_slice(positions: dict[str, dict], market: Optional[str] = None) -> dict:
    rows = [
        p for p in positions.values()
        if market is None or p.get("market") == market
    ]
    invested = sum(_fnum(p.get("invested_usd")) for p in rows)
    return {
        "open_count": len(rows),
        "invested_usd": round(invested, 2),
        "unrealized_pnl_usd": None,
        "unrealized_pnl_pct": None,
        "portfolio_value_usd": round(invested, 2) if invested else None,
        "open_winners": None,
        "open_losers": None,
        "open_flat": None,
    }


def get_book_stats() -> dict:
    """DB만 사용 — live 시세 없이 배치·청산 요약 (빠름)."""
    positions = load_positions()
    history   = load_history()
    closed    = _closed_stats(history)
    portfolio = _book_open_slice(positions)
    by_market = {m: _book_open_slice(positions, m) for m in ("kospi", "nasdaq")}

    return {
        **closed,
        **portfolio,
        "total_invested_usd": portfolio["invested_usd"],
        "total_pnl_usd": closed["hist_pnl_usd"],
        "by_market": by_market,
        "unit_usd": UNIT_USD,
        "last_scan_at": _last_scan_at,
        "scan_running": _scan_running,
        "live_pnl": False,
    }


def get_summary_stats() -> dict:
    """오픈 포지션 mark-to-market 포함 (yfinance 조회 — 느림)."""
    positions = load_positions()
    history   = load_history()
    closed    = _closed_stats(history)

    open_enriched = get_positions_with_pnl() if positions else []
    portfolio     = _portfolio_slice(open_enriched)
    by_market     = {
        m: _portfolio_slice([p for p in open_enriched if p.get("market") == m])
        for m in ("kospi", "nasdaq")
    }

    hist_pnl = closed["hist_pnl_usd"]

    return {
        "open_count":         portfolio["open_count"],
        "closed_count":       closed["closed_count"],
        "win_rate":           closed["win_rate"],
        "avg_pnl_pct":        closed["avg_pnl_pct"],
        "total_invested_usd": portfolio["invested_usd"],
        "hist_pnl_usd":       hist_pnl,
        "hist_invested_usd":  closed["hist_invested_usd"],
        "unrealized_pnl_usd": portfolio["unrealized_pnl_usd"],
        "unrealized_pnl_pct": portfolio["unrealized_pnl_pct"],
        "portfolio_value_usd": portfolio["portfolio_value_usd"],
        "total_pnl_usd":      round(hist_pnl + portfolio["unrealized_pnl_usd"], 4),
        "open_winners":       portfolio["open_winners"],
        "open_losers":        portfolio["open_losers"],
        "open_flat":          portfolio["open_flat"],
        "by_market":          by_market,
        "unit_usd":           UNIT_USD,
        "last_scan_at":       _last_scan_at,
        "scan_running":       _scan_running,
        "live_pnl":           True,
    }


def _get_benchmark_return_uncached(market: str) -> dict:
    """SPY (nasdaq) or ^KS11 (kospi) total return % since earliest entry date."""
    positions = load_positions()
    history   = load_history()

    dates: list[str] = []
    for p in positions.values():
        if p.get("market") == market and p.get("first_entry_date"):
            dates.append(p["first_entry_date"])
    for h in history:
        if h.get("market") == market and h.get("first_entry_date"):
            dates.append(h["first_entry_date"])

    ticker = "SPY" if market == "nasdaq" else "^KS11"
    if not dates:
        return {"since": None, "benchmark_pct": None, "ticker": ticker}

    since = min(dates)
    try:
        import yfinance as yf
        from datetime import timedelta
        end = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%d")
        hist = yf.download(ticker, start=since, end=end, progress=False, auto_adjust=True)
        if hist.empty or len(hist) < 2:
            return {"since": since, "benchmark_pct": None, "ticker": ticker}
        first = float(hist["Close"].iloc[0])
        last  = float(hist["Close"].iloc[-1])
        return {
            "since":         since,
            "benchmark_pct": round((last - first) / first * 100, 2),
            "ticker":        ticker,
        }
    except Exception:
        return {"since": since, "benchmark_pct": None, "ticker": ticker}


def get_benchmark_return(market: str) -> dict:
    from features.strategy.value_scan.cache import vs_cache
    return vs_cache.get(f"benchmark_{market}", 3600, lambda: _get_benchmark_return_uncached(market))
