"""Famous watchlist — static/famous_kospi.txt, famous_nasdaq.txt."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

_STATIC = Path(__file__).resolve().parent / "static"
_KOSPI_FILE = _STATIC / "famous_kospi.txt"
_NASDAQ_FILE = _STATIC / "famous_nasdaq.txt"


def _parse_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _parse_kospi_entry(line: str) -> dict[str, Optional[str]]:
    parts = line.split()
    if parts and re.fullmatch(r"\d{6}", parts[0]):
        code = parts[0].zfill(6)
        label = " ".join(parts[1:]).strip() or code
        return {"code": code, "label": label}
    return {"code": None, "label": line.strip()}


def _parse_nasdaq_entry(line: str) -> dict[str, str]:
    sym = line.split()[0].upper().replace(".", "-")
    return {"symbol": sym, "label": sym}


@lru_cache(maxsize=1)
def load_famous_kospi() -> list[dict[str, Optional[str]]]:
    return [_parse_kospi_entry(line) for line in _parse_lines(_KOSPI_FILE)]


@lru_cache(maxsize=1)
def load_famous_nasdaq() -> list[dict[str, str]]:
    return [_parse_nasdaq_entry(line) for line in _parse_lines(_NASDAQ_FILE)]


def clear_famous_cache() -> None:
    load_famous_kospi.cache_clear()
    load_famous_nasdaq.cache_clear()


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip().lower())


def match_kospi_position(entry: dict, pos: dict) -> bool:
    sym = (pos.get("symbol") or "").zfill(6)
    if entry.get("code") and sym == entry["code"]:
        return True
    name = _norm_name(pos.get("name") or "")
    label = _norm_name(entry.get("label") or "")
    if not name or not label:
        return False
    return name == label or label in name or name in label


def match_nasdaq_position(entry: dict, pos: dict) -> bool:
    sym = (pos.get("symbol") or "").upper().replace(".", "-")
    return sym == entry.get("symbol")


def _index_positions(positions: list[dict], market: str) -> list[dict]:
    return [p for p in positions if p.get("market") == market]


def build_famous_status(positions: Optional[list[dict]] = None) -> dict[str, Any]:
    """Watchlist + held/open P&L for dashboard."""
    positions = positions or []
    kospi_pos = _index_positions(positions, "kospi")
    nasdaq_pos = _index_positions(positions, "nasdaq")

    def _enrich_entries(entries: list, market: str, matcher) -> list[dict]:
        pos_list = kospi_pos if market == "kospi" else nasdaq_pos
        used_pos: set[int] = set()
        result: list[dict] = []
        for entry in entries:
            label = entry.get("label") or entry.get("symbol") or ""
            held_pos = None
            for i, p in enumerate(pos_list):
                if i in used_pos:
                    continue
                if matcher(entry, p):
                    held_pos = p
                    used_pos.add(i)
                    break
            item: dict[str, Any] = {
                "market": market,
                "label": label,
                "held": held_pos is not None,
            }
            if entry.get("code"):
                item["code"] = entry["code"]
            if entry.get("symbol"):
                item["symbol"] = entry["symbol"]
            if held_pos:
                item["position_symbol"] = held_pos.get("symbol")
                item["position_name"] = held_pos.get("name")
                item["pnl_pct"] = held_pos.get("pnl_pct")
                item["pnl_usd"] = held_pos.get("pnl_usd")
                item["invested_usd"] = held_pos.get("invested_usd")
            result.append(item)
        return result

    kospi_items = _enrich_entries(load_famous_kospi(), "kospi", match_kospi_position)
    nasdaq_items = _enrich_entries(load_famous_nasdaq(), "nasdaq", match_nasdaq_position)

    def _summary(items: list[dict]) -> dict:
        held = [x for x in items if x.get("held")]
        return {
            "total": len(items),
            "held": len(held),
            "not_held": len(items) - len(held),
            "coverage_pct": round(len(held) / len(items) * 100, 1) if items else 0,
        }

    return {
        "kospi": kospi_items,
        "nasdaq": nasdaq_items,
        "summary": {
            "kospi": _summary(kospi_items),
            "nasdaq": _summary(nasdaq_items),
        },
        "files": {
            "kospi": "static/famous_kospi.txt",
            "nasdaq": "static/famous_nasdaq.txt",
        },
    }


def position_is_famous(pos: dict) -> bool:
    market = pos.get("market")
    if market == "kospi":
        return any(match_kospi_position(e, pos) for e in load_famous_kospi())
    if market == "nasdaq":
        return any(match_nasdaq_position(e, pos) for e in load_famous_nasdaq())
    return False
