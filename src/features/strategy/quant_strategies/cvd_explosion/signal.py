"""
CVD Explosion — Signal (btc_backtest 와 동일 + forward entry_tf 필드)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .config_loader import get_signal_params_for_tf, get_timeframes, get_tpsl_params
from .tpsl_resolve import MODE_MAGNET_RR, resolve_tpsl, tpsl_mode_label


def _f(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        x = float(v)
        return x if x == x else 0.0
    except (TypeError, ValueError):
        return 0.0


def _bars_from_sweep(sweep: Dict) -> List[Dict]:
    return sweep.get("data") or []


def _vol_ratio(bars: List[Dict], window: int) -> float:
    if len(bars) < 2:
        return 0.0
    hist = bars[-(window + 1):-1]
    if not hist:
        return 0.0
    avg = sum(_f(b.get("volume", 0)) for b in hist) / len(hist)
    if avg <= 0:
        return 0.0
    return _f(bars[-1].get("volume", 0)) / avg


def _is_explosion(bars: List[Dict], vol_mult: float, window: int) -> bool:
    return _vol_ratio(bars, window) >= vol_mult


def _is_solo(bars: List[Dict], zone_gap: int, vol_mult: float, window: int) -> bool:
    if len(bars) < zone_gap + 2:
        return False
    prev = bars[-(zone_gap + 1):-1]
    for idx, b in enumerate(prev):
        slice_end = len(bars) - zone_gap - 1 + idx + 1
        sub = bars[:slice_end]
        if _is_explosion(sub, vol_mult, window):
            return False
    return True


def _cvd_accel(bars: List[Dict], window: int) -> float:
    if len(bars) < window * 2 + 1:
        return 0.0
    prev = bars[-(window * 2 + 1):-1]
    recent = sum(_f(b.get("cvd_delta", 0)) for b in prev[-window:])
    older = sum(_f(b.get("cvd_delta", 0)) for b in prev[:window])
    return recent - older


def _cvd_sum(bars: List[Dict], window: int) -> float:
    recent = bars[-window:] if len(bars) >= window else bars
    return sum(_f(b.get("cvd_delta", 0)) for b in recent)


def _candle_dir(bars: List[Dict]) -> str:
    if not bars:
        return "dn"
    last = bars[-1]
    return "up" if _f(last.get("close", 0)) >= _f(last.get("open", 0)) else "dn"


def compute_signal(
    current_price: float,
    sweep_by_tf: Dict[str, Any],
    magnets: Dict[str, Any],
    *,
    entry_tf: Optional[str] = None,
    higher_tf: Optional[str] = None,
) -> Dict[str, Any]:
    tfm = get_timeframes()
    entry_key = (entry_tf or tfm["entry_tf"]).strip()
    higher_key = (higher_tf or tfm["higher_tf"]).strip()
    pe = get_signal_params_for_tf(entry_key)
    ph = get_signal_params_for_tf(higher_key)
    vol_avg_w = int(pe["vol_avg_window"])
    vol_mult = float(pe["vol_mult"])
    zone_gap = int(pe["zone_gap"])
    cvd_accel_w = int(pe["cvd_accel_window"])
    cvd_higher_w = int(ph["cvd_higher_window"])
    conf_thr = int(pe["confidence_threshold"])

    bars_entry = _bars_from_sweep(sweep_by_tf.get(entry_key) or {})
    bars_higher = _bars_from_sweep(sweep_by_tf.get(higher_key) or {})
    level_map: List[Dict] = list((magnets or {}).get("level_map") or [])

    if not bars_entry:
        return _no_signal(f"{entry_key} 데이터 없음", level_map=level_map, entry_tf=entry_key, higher_tf=higher_key)

    vr = _vol_ratio(bars_entry, vol_avg_w)
    is_exp = vr >= vol_mult
    is_solo = _is_solo(bars_entry, zone_gap, vol_mult, vol_avg_w) if is_exp else False
    cdir = _candle_dir(bars_entry)
    accel = _cvd_accel(bars_entry, cvd_accel_w)
    cvd_hi = _cvd_sum(bars_higher, cvd_higher_w)
    entry = _f(bars_entry[-1].get("close", 0)) or current_price

    bull = bear = 0
    reasons: List[str] = []

    if is_exp:
        if cdir == "up":
            bull += 3
            reasons.append(f"[EXP] 상승폭발봉 vr={vr:.2f}x ✅")
        else:
            bear += 3
            reasons.append(f"[EXP] 하락폭발봉 vr={vr:.2f}x ✅")
    else:
        reasons.append(f"[EXP] 미달 vr={vr:.2f}x < {vol_mult}x")

    if is_solo:
        if cdir == "up":
            bull += 1
        else:
            bear += 1
        reasons.append(f"[SOLO] 단독봉 ✅ (직전 {zone_gap}봉 내 폭발 없음)")
    elif is_exp:
        reasons.append("[SOLO] 클러스터봉 — 단독봉 아님")

    accel_dir = "up" if accel > 0 else "dn"
    if is_exp and accel_dir == cdir:
        if cdir == "up":
            bull += 2
        else:
            bear += 2
        reasons.append(f"[CVD_ACCEL] 가속 일치 {accel:+.0f} ✅")
    elif is_exp:
        reasons.append(f"[CVD_ACCEL] 가속 역방향 {accel:+.0f} ❌")

    cvd_lbl = f"CVD_{higher_key}"
    if cvd_hi > 0:
        bull += 1
        reasons.append(f"[{cvd_lbl}] {higher_key} CVD↑ {cvd_hi:.0f}")
    elif cvd_hi < 0:
        bear += 1
        reasons.append(f"[{cvd_lbl}] {higher_key} CVD↓ {cvd_hi:.0f}")

    tpsl_params = get_tpsl_params()

    common = {
        "bull_score":   bull,
        "bear_score":   bear,
        "vol_ratio":    round(vr, 3),
        "is_explosion": is_exp,
        "is_solo":      is_solo,
        "cvd_accel":    round(accel, 1),
        "cvd_higher":   round(cvd_hi, 1),
        "cvd_higher_tf": higher_key,
        "entry_tf":     entry_key,
        "higher_tf":    higher_key,
        "level_map":    level_map,
        "tpsl_mode":    tpsl_mode_label(tpsl_params),
        "signal_mode":  "cvd_exp_v1",
        "reasons":      reasons,
    }

    if bull >= conf_thr and bull > bear:
        tp, sl = resolve_tpsl("long", entry, level_map, tpsl_params)
        if tp is None or sl is None:
            if tpsl_params.get("mode") == "fixed_rr":
                reasons.append("[대기] LONG TP/SL 산출 실패 (tpsl.risk_pct / rr_ratio 확인)")
            else:
                reasons.append("[대기] LONG liq map 없음 또는 TP/SL 부족 — liq cache 필요")
            return {**_no_signal(None, level_map=level_map, entry_tf=entry_key, higher_tf=higher_key), **common}
        reasons.append(
            f"[진입] LONG  confidence={bull}/7  mode={common['tpsl_mode']}  TP={tp:,.2f}  SL={sl:,.2f}"
        )
        out = {"signal": "long", "confidence": bull, "tp": tp, "sl": sl, **common}
        out["position_meta"] = _position_meta_for_entry(tpsl_params)
        return out

    if bear >= conf_thr and bear > bull:
        tp, sl = resolve_tpsl("short", entry, level_map, tpsl_params)
        if tp is None or sl is None:
            if tpsl_params.get("mode") == "fixed_rr":
                reasons.append("[대기] SHORT TP/SL 산출 실패 (tpsl.risk_pct / rr_ratio 확인)")
            else:
                reasons.append("[대기] SHORT liq map 없음 또는 TP/SL 부족 — liq cache 필요")
            return {**_no_signal(None, level_map=level_map, entry_tf=entry_key, higher_tf=higher_key), **common}
        reasons.append(
            f"[진입] SHORT confidence={bear}/7  mode={common['tpsl_mode']}  TP={tp:,.2f}  SL={sl:,.2f}"
        )
        out = {"signal": "short", "confidence": bear, "tp": tp, "sl": sl, **common}
        out["position_meta"] = _position_meta_for_entry(tpsl_params)
        return out

    reasons.append(f"[대기] bull={bull} bear={bear} — 임계값({conf_thr}) 미달")
    return {**_no_signal(None, level_map=level_map, entry_tf=entry_key, higher_tf=higher_key), **common}


def _position_meta_for_entry(tpsl_params: Dict[str, Any]) -> Dict[str, Any]:
    mode = tpsl_mode_label(tpsl_params)
    meta: Dict[str, Any] = {"tpsl_mode": mode}
    if mode == MODE_MAGNET_RR:
        meta["tp_advances"] = 0
    return meta


def _no_signal(
    reason: Optional[str],
    level_map: List[Dict] = None,
    entry_tf: str = "1h",
    higher_tf: str = "4h",
) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "signal": "none",
        "confidence": 0,
        "tp": None,
        "sl": None,
        "entry_tf": entry_tf,
        "higher_tf": higher_tf,
        "reasons": [reason] if reason else [],
    }
    if level_map is not None:
        base["level_map"] = level_map
    return base
