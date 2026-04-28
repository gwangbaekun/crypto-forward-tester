"""OI CVD Surge — config loader (mtime-based hot reload)."""
from __future__ import annotations

import os
import pathlib
from typing import Any, Dict, Optional

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
    all_tfs = list(tf.get("all") or ["1h"])
    return {
        "all":      sorted(all_tfs),
        "entry_tf": str(tf.get("entry_tf") or "1h"),
    }


def get_signal_params() -> Dict[str, Any]:
    cfg = load_config()
    sig = cfg.get("signal") or {}
    return {
        "lookback":             int(sig.get("lookback", 20)),
        "oi_lookback":          int(sig.get("oi_lookback", 5)),
        "oi_min_pct":           float(sig.get("oi_min_pct", 1.0)),
        "confidence_threshold": int(sig.get("confidence_threshold", 1)),
    }


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

    try:
        rr_ratio = float(tp.get("rr_ratio", 3.0))
    except (TypeError, ValueError):
        rr_ratio = 3.0
    try:
        slippage_pct = float(tp.get("slippage_pct") or 0.0)
    except (TypeError, ValueError):
        slippage_pct = 0.0

    return {
        "mode":         str(tp.get("mode") or "fixed_rr").strip().lower(),
        "rr_ratio":     rr_ratio,
        "sl_max_pct":   sl_max,
        "slippage_pct": slippage_pct,
    }
