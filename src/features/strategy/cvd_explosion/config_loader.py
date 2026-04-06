"""CVD Explosion — config loader (btc_backtest 동일).

config.yaml 파일의 수정 시각(mtime)을 감시해 변경 시 자동 리로드.
backtest 대시보드에서 "SAVE & APPLY" 를 누르면 이 파일이 갱신되고,
다음 신호 계산 시 새 파라미터가 즉시 반영된다.
"""
from __future__ import annotations

import os
import pathlib
from typing import Any, Dict, Optional

import yaml

_CONFIG_PATH = pathlib.Path(__file__).parent / "config.yaml"

_cache: Dict[str, Any] = {}
_cache_mtime: float = -1.0


def load_config() -> Dict[str, Any]:
    """config.yaml 의 mtime 이 바뀌면 자동으로 재파싱."""
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
    return {
        "all":       list(tf.get("all") or ["1h", "4h"]),
        "entry_tf":  str(tf.get("entry_tf") or "1h"),
        "higher_tf": str(tf.get("higher_tf") or "4h"),
    }


def get_signal_params_for_tf(tf: str) -> Dict[str, Any]:
    cfg = load_config()
    sig = cfg.get("signal") or {}
    sc  = sig.get("scoring") or {}
    base = {
        "vol_avg_window":        int(sig.get("vol_avg_window",       20)),
        "vol_mult":              float(sig.get("vol_mult",           2.5)),
        "zone_gap":              int(sig.get("zone_gap",             3)),
        "cvd_accel_window":      int(sig.get("cvd_accel_window",     3)),
        "cvd_higher_window":     int(sig.get("cvd_higher_window",    10)),
        "confidence_threshold":  int(sig.get("confidence_threshold", 5)),
        "score_explosion":       int(sc.get("explosion",  3)),
        "score_solo":            int(sc.get("solo",       1)),
        "score_cvd_accel":       int(sc.get("cvd_accel",  2)),
        "score_cvd_higher":      int(sc.get("cvd_higher", 1)),
    }
    by_tf = sig.get("by_tf") or {}
    ov = by_tf.get(tf) or by_tf.get(str(tf))
    if isinstance(ov, dict):
        for k in base:
            if k in ov:
                v = ov[k]
                base[k] = float(v) if k == "vol_mult" else int(v)
    return base


def get_signal_params() -> Dict[str, Any]:
    return get_signal_params_for_tf(get_timeframes()["entry_tf"])


_VALID_SL_LIFT_MODES  = {"always", "never", "critical_only", "min_intensity", "rank_le"}
_INTENSITY_ORDER      = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]


def get_tpsl_params() -> Dict[str, Any]:
    cfg  = load_config()
    tp   = cfg.get("tpsl") or {}

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

    sl_lift_mode = str(tp.get("sl_lift_mode") or "always").strip().lower()
    if sl_lift_mode not in _VALID_SL_LIFT_MODES:
        sl_lift_mode = "always"

    sl_lift_min_intensity = str(tp.get("sl_lift_min_intensity") or "HIGH").strip().upper()
    if sl_lift_min_intensity not in _INTENSITY_ORDER:
        sl_lift_min_intensity = "HIGH"

    try:
        sl_lift_rank_le = int(tp.get("sl_lift_rank_le", 2))
    except (TypeError, ValueError):
        sl_lift_rank_le = 2

    return {
        "mode":                   mode,
        "rr_ratio":               rr_ratio,
        "risk_pct":               risk_pct,
        "sl_max_pct":             sl_max,
        "sl_lift_mode":           sl_lift_mode,
        "sl_lift_min_intensity":  sl_lift_min_intensity,
        "sl_lift_rank_le":        sl_lift_rank_le,
    }


def reload_config() -> None:
    """강제 리로드 (mtime 캐시 무효화)."""
    global _cache_mtime
    _cache_mtime = -1.0
