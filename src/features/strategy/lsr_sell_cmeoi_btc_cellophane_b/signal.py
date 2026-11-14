from __future__ import annotations

from pathlib import Path

from features.strategy.btc_cellophane_data import config_loader as C

HERE = Path(__file__).resolve().parent
CFG = C.load_config(HERE / "config.yaml")
KEY = CFG["strategy_id"]
SHEETS = {side: C.sheets_of(CFG, side) for side in C.SIDES}
EXITS = {side: C.exit_of(CFG, side) for side in C.SIDES}
OPPOSITE = {"long": "short", "short": "long"}


def is_on(zs, sheets):
    out = all(z is not None and z >= s["c_in"] for z, s in zip(zs, sheets))
    return out


def decide(pairs):
    fired = []
    for side in C.SIDES:
        prev = is_on([p[0] for p in pairs[side]], SHEETS[side])
        now = is_on([p[1] for p in pairs[side]], SHEETS[side])
        if now and not prev:
            fired.append(side)
    if len(fired) == 2:
        raise ValueError("long 과 short 가 같은 5분봉에 켜졌다. 정한 규칙이 없다")
    signal = fired[0] if fired else None
    return signal


def compute_signal(buf):
    pairs = {side: [buf.z(s) for s in SHEETS[side]] for side in C.SIDES}
    z_now = {side: {s["id"]: p[1] for s, p in zip(SHEETS[side], pairs[side])} for side in C.SIDES}
    out = {"signal": decide(pairs), "z": z_now}
    return out


def stops(side, price):
    ex = EXITS[side]
    sign = 1.0 if side == "long" else -1.0
    dist = price * ex["sl_pct"] / 100.0
    sl = price - sign * dist
    tp = price + sign * dist * ex["rr_ratio"]
    return sl, tp
