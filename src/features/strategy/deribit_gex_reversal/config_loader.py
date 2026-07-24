"""Deribit Expiry GEX Reversal — config loader (mtime-aware hot reload).

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


def get_currency() -> str:
    return str(load_config().get("currency", "BTC")).upper()


def get_timeframes() -> Dict[str, Any]:
    # 이 전략은 봉 기반이 아니라 만기 이벤트 기반이지만, 스캐폴드 호환을 위해
    # entry_tf 를 시간(1h) tick 으로 둔다 (strategy_loop 이 1h 마다 호출).
    return {"all": ["1h"], "entry_tf": "1h"}


def get_signal_params() -> Dict[str, Any]:
    cfg = load_config()
    sig = cfg.get("signal")
    if not isinstance(sig, dict):
        raise ValueError("config.yaml: 'signal' must be a mapping")
    return {
        "expiry_hour_utc":     int(sig.get("expiry_hour_utc", 8)),
        "entry_window_hours":  int(sig.get("entry_window_hours", 3)),
        "exit_hour_utc":       int(sig.get("exit_hour_utc", 12)),
        "atm_band":            float(sig.get("atm_band", 0.025)),
        "pctl_trigger":        float(sig.get("pctl_trigger", 0.80)),
        "require_gex_negative": bool(sig.get("require_gex_negative", True)),
        "snap_tol_min":        int(sig.get("snap_tol_min", 20)),
        "chain_window_days":   int(sig.get("chain_window_days", 45)),
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
