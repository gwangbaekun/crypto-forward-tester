"""
ATR Breakout config loader.
"""
from __future__ import annotations

import pathlib
from functools import lru_cache
from typing import Any, Dict

from features.strategy.quant_strategies.common.config_loader import load_strategy_config

_CONFIG_PATH = pathlib.Path(__file__).parent / "config.yaml"


@lru_cache(maxsize=1)
def load_config() -> Dict[str, Any]:
    return load_strategy_config(_CONFIG_PATH)


def get_signal_params() -> Dict[str, Any]:
    cfg = load_config()
    sig = cfg.get("signal") or {}
    return {
        "confidence_threshold": sig.get("confidence_threshold", 5),
        "atr_compress_ratio": sig.get("atr_compress_ratio", 0.7),
        "recent_atr_window": sig.get("recent_atr_window", 10),
        "hist_atr_window": sig.get("hist_atr_window", 20),
        "box_window": sig.get("box_window", 10),
        "cvd_window": sig.get("cvd_window", 5),
    }


def get_tpsl_params() -> Dict[str, Any]:
    cfg = load_config()
    tp = cfg.get("tpsl") or {}
    return {
        "tp_multiplier": tp.get("tp_multiplier", 2.0),
    }


def reload_config() -> None:
    load_config.cache_clear()
