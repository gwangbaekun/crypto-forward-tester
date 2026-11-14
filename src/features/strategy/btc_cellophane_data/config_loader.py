from __future__ import annotations

import yaml

SIDES = ("long", "short")
BASE = {"metrics.count_long_short_ratio": "long_short_ratio",
        "klines.spot.sell_volume": "spot_sell_volume",
        "rel.venue.binance_vs_cme_open_interest.diff": "open_interest",
        "rel.venue.binance_vs_cme_open_interest.ratio": "open_interest"}


def load_config(path):
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    return cfg


def sheets_of(cfg, side):
    sheets = [{"id": s["id"], "term": int(s["term"]), "c_in": float(s["c_in"])} for s in cfg[side]["sheets"]]
    for s in sheets:
        if s["id"] not in BASE:
            raise ValueError(f"모르는 장 {s['id']}")
    return sheets


def exit_of(cfg, side):
    out = {"sl_pct": float(cfg[side]["exit"]["sl_pct"]), "rr_ratio": float(cfg[side]["exit"]["rr_ratio"])}
    return out


def needed(term):
    out = 2 * term
    return out


def sizes(sheets):
    out = {}
    for s in sheets:
        base = BASE[s["id"]]
        out[base] = max(out[base], needed(s["term"])) if base in out else needed(s["term"])
    return out
