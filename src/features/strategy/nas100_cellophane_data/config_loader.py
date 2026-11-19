from __future__ import annotations

import yaml


def load_config(path):
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    return cfg


def sheets_of(cfg, side):
    sheets = [{"id": s["id"], "term": int(s["term"]), "c_in": float(s["c_in"]), "c_out": float(s["c_out"])}
              for s in cfg[side]["sheets"]]
    return sheets


def needed(term):
    out = 2 * term
    return out
