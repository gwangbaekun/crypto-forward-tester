"""US Options Expiry GEX Pinning — config loader (mtime-aware hot reload).

자체 포함 — 다른 전략과 공유하지 않는다 (스캐폴드 패턴만 동일).
"""
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


def get_underlying() -> str:
    return str(load_config().get("underlying", "SPY")).upper()


def get_timeframes() -> Dict[str, Any]:
    # 일일 이벤트 기반 — 스캐폴드 호환 위해 entry_tf 를 1d tick 으로 둔다.
    return {"all": ["1d"], "entry_tf": "1d"}


def get_signal_params() -> Dict[str, Any]:
    cfg = load_config()
    sig = cfg.get("signal")
    if not isinstance(sig, dict):
        raise ValueError("config.yaml: 'signal' must be a mapping")
    return {
        "max_days_to_expiry":   int(sig.get("max_days_to_expiry", 2)),
        "min_distance_pct":     float(sig.get("min_distance_pct", 0.5)),
        "require_gex_positive": bool(sig.get("require_gex_positive", True)),
        "contract_multiplier":  float(sig.get("contract_multiplier", 100)),
        "exit_hour_utc":        int(sig.get("exit_hour_utc", 20)),
        "chain_window_days":    int(sig.get("chain_window_days", 20)),
    }


def get_tpsl_params() -> Dict[str, Any]:
    cfg = load_config()
    tp = cfg.get("tpsl") or {}
    try:
        sl_pct = float(tp.get("sl_pct", 0.0))
    except (TypeError, ValueError):
        sl_pct = 0.0
    return {"sl_pct": sl_pct}


def reload_config() -> None:
    global _cache_mtime
    _cache_mtime = -1.0
