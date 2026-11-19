from __future__ import annotations

from pathlib import Path

from features.strategy.nas100_cellophane_data import config_loader as C

HERE = Path(__file__).resolve().parent
CFG = C.load_config(HERE / "config.yaml")
KEY = CFG["strategy_id"]
SIDE = "long"
SHEETS = C.sheets_of(CFG, SIDE)
SL_PCT = float(CFG[SIDE]["exit"]["sl_pct"])


def is_on(zs):
    out = all(z is not None and z >= s["c_in"] for z, s in zip(zs, SHEETS))
    return out


def is_decay(zs):
    out = any(z is not None and z < s["c_out"] for z, s in zip(zs, SHEETS))
    return out


def compute_signal(buf, prev_s, now_s):
    pairs = [buf.z(s, prev_s, now_s) for s in SHEETS]
    prev, now = [p[0] for p in pairs], [p[1] for p in pairs]
    fired = is_on(now) and not is_on(prev)
    out = {"signal": SIDE if fired else None, "decay": is_decay(now),
           "z": {SIDE: {s["id"]: z for s, z in zip(SHEETS, now)}}}
    return out


def stop(price):
    out = price * (1.0 - SL_PCT / 100.0)
    return out
