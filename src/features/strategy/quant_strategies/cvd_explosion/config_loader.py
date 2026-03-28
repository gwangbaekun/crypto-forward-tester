"""CVD Explosion — config (btc_backtest 동일)."""
from __future__ import annotations

import pathlib
from functools import lru_cache
from typing import Any, Dict, Optional

import yaml

_CONFIG_PATH = pathlib.Path(__file__).parent / "config.yaml"


@lru_cache(maxsize=1)
def load_config() -> Dict[str, Any]:
    if not _CONFIG_PATH.exists():
        return {}
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_timeframes() -> Dict[str, Any]:
    cfg = load_config()
    tf = cfg.get("timeframes") or {}
    return {
        "all":       list(tf.get("all") or ["1h", "4h"]),
        "entry_tf":  str(tf.get("entry_tf") or "1h"),
        "higher_tf": str(tf.get("higher_tf") or "4h"),
    }


def get_signal_params_for_tf(tf: str) -> Dict[str, Any]:
    cfg = load_config()
    sig = cfg.get("signal") or {}
    base = {
        "vol_avg_window":        int(sig.get("vol_avg_window", 20)),
        "vol_mult":              float(sig.get("vol_mult", 2.5)),
        "zone_gap":              int(sig.get("zone_gap", 3)),
        "cvd_accel_window":      int(sig.get("cvd_accel_window", 3)),
        "cvd_higher_window":     int(sig.get("cvd_higher_window", 10)),
        "confidence_threshold":  int(sig.get("confidence_threshold", 5)),
    }
    by_tf = sig.get("by_tf") or {}
    ov = by_tf.get(tf)
    if ov is None:
        ov = by_tf.get(str(tf))
    if isinstance(ov, dict):
        for k in base:
            if k in ov:
                v = ov[k]
                if k == "vol_mult":
                    base[k] = float(v)
                else:
                    base[k] = int(v)
    return base


def get_signal_params() -> Dict[str, Any]:
    return get_signal_params_for_tf(get_timeframes()["entry_tf"])


def get_tpsl_params() -> Dict[str, Any]:
    cfg = load_config()
    tp = cfg.get("tpsl") or {}
    raw = tp.get("sl_max_pct")
    sl_max: Optional[float] = None
    if raw is not None and raw != "":
        try:
            sl_max = float(raw)
        except (TypeError, ValueError):
            sl_max = None
    if sl_max is not None and sl_max <= 0:
        sl_max = None
    mode = str(tp.get("mode") or "magnet").strip().lower()
    try:
        rr_ratio = float(tp.get("rr_ratio", 1.5))
    except (TypeError, ValueError):
        rr_ratio = 1.5
    try:
        risk_pct = float(tp.get("risk_pct", 1.0))
    except (TypeError, ValueError):
        risk_pct = 1.0
    return {
        "mode":       mode,
        "rr_ratio":   rr_ratio,
        "risk_pct":   risk_pct,
        "sl_max_pct": sl_max,
    }


def reload_config() -> None:
    load_config.cache_clear()
