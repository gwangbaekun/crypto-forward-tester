"""Spot-Perp CVD Divergence — config loader (mtime-aware hot reload)."""
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
    from features.strategy.common.config_loader import get_master_config
    master_strat = (get_master_config() or {}).get("spot_perp_cvd") or {}

    # strategies_master.yaml 우선, config.yaml 은 fallback
    cfg = load_config()
    tf = cfg.get("timeframes") or {}

    master_tfs = master_strat.get("timeframes") or []
    all_tfs = list(master_tfs) if master_tfs else list(tf.get("all") or ["1h"])

    master_entry_tf = master_strat.get("entry_tf")
    entry_tf = str(master_entry_tf or tf.get("entry_tf") or "1h")

    return {
        "all":      sorted(all_tfs),
        "entry_tf": entry_tf,
    }


def get_signal_params() -> Dict[str, Any]:
    cfg = load_config()
    sig = cfg.get("signal")
    if not isinstance(sig, dict):
        raise ValueError("config.yaml: 'signal' must be a mapping")
    for k in ("lookback", "spot_cvd_threshold", "perp_cvd_threshold", "confidence_threshold"):
        if k not in sig:
            raise ValueError(f"config.yaml: signal.{k} is required")
    return {
        "lookback":             int(sig["lookback"]),
        "spot_cvd_threshold":   float(sig["spot_cvd_threshold"]),
        "perp_cvd_threshold":   float(sig["perp_cvd_threshold"]),
        "confidence_threshold": int(sig["confidence_threshold"]),
    }


def get_tpsl_params() -> Dict[str, Any]:
    cfg = load_config()
    tp = cfg.get("tpsl") or {}
    try:
        sl_pct = float(tp.get("sl_pct", 2.0))
    except (TypeError, ValueError):
        sl_pct = 2.0
    try:
        slippage_pct = float(tp.get("slippage_pct") or 0.0)
    except (TypeError, ValueError):
        slippage_pct = 0.0
    return {
        "mode":         "cvd_exit",
        "sl_pct":       sl_pct,
        "slippage_pct": slippage_pct,
    }


def reload_config() -> None:
    global _cache_mtime
    _cache_mtime = -1.0
