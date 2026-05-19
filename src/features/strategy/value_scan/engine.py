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

DATA_DIR            = Path(__file__).resolve().parents[5] / "data" / "value_forward"
POSITIONS_FILE      = DATA_DIR / "positions.json"
HISTORY_FILE        = DATA_DIR / "history.json"
SCANS_DIR           = DATA_DIR / "scans"
LAST_ACTIVITY_FILE  = DATA_DIR / "last_activity.json"

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

        return {
            "symbol": symbol, "name": data.get("stockName") or symbol,
            "market": "kospi", "sector": sector,
            "eps": _f(info.get("eps", "")), "bps": _f(info.get("bps", "")),
            "per": per, "pbr": pbr,
            "forward_per": _f(info.get("cnsPer", "")),
            "forward_eps": _f(info.get("cnsEps", "")),
            "div": _f(info.get("dividendYieldRatio", "")),
        }
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

        per = _f(info.get("trailingPE"))
        pbr = _f(info.get("priceToBook"))
        if math.isnan(per) and math.isnan(pbr):
            return None

        return {
            "symbol": symbol, "name": info.get("shortName") or info.get("longName") or symbol,
            "market": "nasdaq", "sector": info.get("sector") or "Other",
            "eps": _f(info.get("trailingEps")), "bps": _f(info.get("bookValue")),
            "per": per, "pbr": pbr,
            "forward_eps": _f(info.get("forwardEps")),
            "forward_per": _f(info.get("forwardPE")),
            "div": _f(info.get("dividendYield", math.nan)) * 100 if info.get("dividendYield") else math.nan,
        }
    except Exception:
        return None


# ── 섹터 중앙값 + 판정 ─────────────────────────────────────────────────────────

def _sector_medians(rows: list[dict]) -> dict[str, float]:
    by_sector: dict[str, list[float]] = {}
    for r in rows:
        per = r["per"]
        if not math.isnan(per) and per > 0:
            by_sector.setdefault(r["sector"], []).append(per)
    return {s: median(vals) for s, vals in by_sector.items() if vals}


def _rate(r: dict, sector_med: dict) -> str:
    per     = r["per"]
    eps     = r["eps"]
    fwd_eps = r["forward_eps"]
    pbr     = r["pbr"]
    med     = sector_med.get(r["sector"], math.nan)

    if math.isnan(per) or per <= 0:
        return "HOLD"
    if not math.isnan(med) and per > med * 1.20:
        return "SELL"
    if not math.isnan(eps) and not math.isnan(fwd_eps) and eps > 0 and fwd_eps < eps * 0.75:
        return "SELL"
    if (
        not math.isnan(med) and per < med * 0.70
        and not math.isnan(eps) and eps > 0
        and not math.isnan(pbr) and pbr > 0
        and not math.isnan(fwd_eps) and fwd_eps >= eps * 1.05
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
        r["rating"] = _rate(r, sec_med)
    return rows


# ── 포지션 I/O ────────────────────────────────────────────────────────────────

def _pos_key(market: str, symbol: str) -> str:
    return f"{market}_{symbol}"


def load_positions() -> dict[str, dict]:
    if POSITIONS_FILE.exists():
        return json.loads(POSITIONS_FILE.read_text())
    return {}


def load_history() -> list[dict]:
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text())
    return []


def _save_positions(pos: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    POSITIONS_FILE.write_text(json.dumps(pos, ensure_ascii=False, indent=2))


def _save_history(hist: list) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(hist, ensure_ascii=False, indent=2))


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


def update_positions(all_rows: list[dict], today: str) -> dict:
    positions = load_positions()
    history   = load_history()

    rating_map = {_pos_key(r["market"], r["symbol"]): r for r in all_rows}
    sell_keys  = {
        _pos_key(r["market"], r["symbol"])
        for r in all_rows if r["rating"] == "SELL"
    }

    new_entries: list[dict] = []
    new_exits:   list[dict] = []

    # ── SELL 또는 상장폐지 → 청산 ────────────────────────────────────────────
    for key, pos in list(positions.items()):
        cur = rating_map.get(key)
        if (cur is None) or (key in sell_keys):
            exit_price = _fetch_price(pos["market"], pos["symbol"])
            lots       = pos.get("lots", [])
            avg_pnl_pct, pnl_usd = _calc_pnl(lots, exit_price)
            record = {
                **pos,
                "exit_date":   today,
                "exit_price":  None if math.isnan(exit_price) else exit_price,
                "exit_reason": cur["rating"] if cur else "DELISTED",
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
    SCANS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCANS_DIR / f"{today}_{market}.json"
    path.write_text(json.dumps(
        {"date": today, "market": market, "rows": [_clean_row(r) for r in rows]},
        ensure_ascii=False, indent=2,
    ))


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

        for m in markets:
            rows = run_scan(m, max_workers)
            save_snapshot(today, m, rows)
            all_rows.extend(rows)

        result        = update_positions(all_rows, today)
        _last_scan_at = datetime.now(UTC).isoformat()

        buy_n  = sum(1 for r in all_rows if r["rating"] == "BUY")
        sell_n = sum(1 for r in all_rows if r["rating"] == "SELL")

        activity = {
            "date":        today,
            "saved_at":    _last_scan_at,
            "scanned":     len(all_rows),
            "buy":         buy_n,
            "sell":        sell_n,
            "new_entries": result["new_entries"],
            "new_exits":   result["new_exits"],
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


def get_last_activity() -> dict:
    if LAST_ACTIVITY_FILE.exists():
        return json.loads(LAST_ACTIVITY_FILE.read_text())
    return {}


async def get_scan_status(**kwargs) -> dict:
    return {"running": _scan_running, "last_scan_at": _last_scan_at}


def _enrich_position(pos: dict) -> dict:
    """단일 포지션에 현재가·P&L 계산 (스레드 안전)."""
    lots = pos.get("lots", [])
    cur  = _fetch_price(pos["market"], pos["symbol"])
    avg_pnl_pct, pnl_usd = _calc_pnl(lots, cur)

    valid_prices = [l["price"] for l in lots if l.get("price") and l["price"] > 0]
    avg_entry    = sum(valid_prices) / len(valid_prices) if valid_prices else None

    return {
        **pos,
        "lot_count":       len(lots),
        "avg_entry_price": avg_entry,
        "current_price":   None if math.isnan(cur) else cur,
        "pnl_pct":         avg_pnl_pct,
        "pnl_usd":         pnl_usd,
    }


def get_positions_with_pnl(max_workers: int = 20) -> list[dict]:
    positions = load_positions()
    if not positions:
        return []

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_enrich_position, pos) for pos in positions.values()]
        result  = [f.result() for f in futures]

    result.sort(key=lambda x: (x.get("pnl_usd") or 0), reverse=True)
    return result


def get_summary_stats() -> dict:
    positions = load_positions()
    history   = load_history()
    closed    = [h for h in history if h.get("pnl_pct") is not None]
    wins      = [h for h in closed if (h["pnl_pct"] or 0) > 0]
    avg_pnl   = sum(h["pnl_pct"] for h in closed) / len(closed) if closed else None

    total_invested = sum(p.get("invested_usd", 0) for p in positions.values())
    hist_pnl_usd   = sum(h.get("pnl_usd") or 0 for h in history)

    return {
        "open_count":        len(positions),
        "closed_count":      len(closed),
        "win_rate":          round(len(wins) / len(closed) * 100, 1) if closed else None,
        "avg_pnl_pct":       round(avg_pnl, 2) if avg_pnl is not None else None,
        "total_invested_usd": round(total_invested, 2),
        "hist_pnl_usd":      round(hist_pnl_usd, 4),
        "last_scan_at":      _last_scan_at,
        "scan_running":      _scan_running,
    }
