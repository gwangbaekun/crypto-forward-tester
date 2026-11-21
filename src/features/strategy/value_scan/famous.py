from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

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


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip().lower())


def match_kospi_row(entry: dict, row: dict) -> bool:
    sym = (row.get("symbol") or "").zfill(6)
    if entry.get("code") and sym == entry["code"]:
        return True
    name = _norm_name(row.get("name") or "")
    label = _norm_name(entry.get("label") or "")
    if not name or not label:
        return False
    return name == label or label in name or name in label


def match_nasdaq_row(entry: dict, row: dict) -> bool:
    sym = (row.get("symbol") or "").upper().replace(".", "-")
    return sym == entry.get("symbol")


def row_is_famous(row: dict) -> bool:
    market = row.get("market")
    if market == "kospi":
        return any(match_kospi_row(e, row) for e in load_famous_kospi())
    if market == "nasdaq":
        return any(match_nasdaq_row(e, row) for e in load_famous_nasdaq())
    return False
