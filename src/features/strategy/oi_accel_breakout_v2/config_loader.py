"""OI Accel Breakout v2 — config loader (mtime-based hot reload)."""
from __future__ import annotations

import os
import pathlib
from typing import Any, Dict

import yaml

_CONFIG_PATH = pathlib.Path(__file__).parent / "config.yaml"

_cache: Dict[str, Any] = {}
_cache_mtime: float = -1.0


def load_config() -> Dict[str, Any]:
    global _cache, _cache_mtime
    try:
        mtime = os.path.getmtime(_CONFIG_PATH)
    except OSError:
        return _cache
    if mtime != _cache_mtime:
        try:
            with open(_CONFIG_PATH, encoding="utf-8") as f:
                _cache = yaml.safe_load(f) or {}
            _cache_mtime = mtime
        except Exception:
            pass
    return _cache


def get_timeframes() -> Dict[str, Any]:
    cfg = load_config()
    tf = cfg.get("timeframes") or {}
    all_tfs = list(tf.get("all") or ["15m"])
    return {
        "all":      sorted(all_tfs),
        "entry_tf": str(tf.get("entry_tf") or "15m"),
    }


def get_signal_params() -> Dict[str, Any]:
    cfg = load_config()
    sig = cfg.get("signal") or {}
    return {
        "accel_lookback":       int(sig.get("accel_lookback", 3)),
        "accel_z_threshold":    float(sig.get("accel_z_threshold", 2.5)),
        "z_period":             int(sig.get("z_period", 50)),
        "cvd_lookback":         int(sig.get("cvd_lookback", 10)),
        "cvd_threshold":        float(sig.get("cvd_threshold", 1.5)),
        "atr_squeeze_pct":      float(sig.get("atr_squeeze_pct", 0.5)),
        "atr_period":           int(sig.get("atr_period", 14)),
        "ema_period":           int(sig.get("ema_period", 100)),
        "tp_ratio":             float(sig.get("tp_ratio", 3.0)),
        "sides":                str(sig.get("sides", "both")).strip().lower(),
        "confidence_threshold": int(sig.get("confidence_threshold", 1)),
    }


def get_tpsl_params() -> Dict[str, Any]:
    cfg = load_config()
    tp = cfg.get("tpsl") or {}
    try:
        sl_pct = float(tp.get("sl_pct", 1.5))
    except (TypeError, ValueError):
        sl_pct = 1.5
    try:
        slippage_pct = float(tp.get("slippage_pct") or 0.0)
    except (TypeError, ValueError):
        slippage_pct = 0.0
    return {
        "mode":         str(tp.get("mode") or "tp_sl").strip().lower(),
        "sl_pct":       sl_pct,
        "slippage_pct": slippage_pct,
    }
