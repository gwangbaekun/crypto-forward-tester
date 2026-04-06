"""
CVD Explosion — Signal Function (forwardtest 호환)

핵심 아이디어:
  볼륨 폭발봉(단독) + CVD 가속 방향 일치 → 추세 시작 진입

Confidence 채점 (7점 만점, ≥ 5점 진입):
  +3  볼륨 폭발봉 (vol_ratio >= vol_mult)
  +1  단독봉     (이전 zone_gap 봉 내 폭발 없음)
  +2  CVD 가속   (직전 N봉 CVD > 이전 N봉 CVD, 봉 방향 일치)
  +1  상위TF CVD 방향 일치

TP/SL:
  `config tpsl.mode` — magnet | magnet_rr (magnet 동일 + TP advance) | fixed_rr.
  구현: `tpsl_resolve.py`, 청산: `engine.check_exit`.

진입 시점:
  1h 봉 종가 (현재봉 기준) — look-ahead bias 없음

파라미터: config.yaml → signal / tpsl 섹션
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .config_loader import get_signal_params_for_tf, get_timeframes, get_tpsl_params
from .tpsl_resolve import MODE_MAGNET_RR, resolve_tpsl, tpsl_mode_label


# ── 공통 헬퍼 ─────────────────────────────────────────────────────────────────

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


# ── 지표 헬퍼 ─────────────────────────────────────────────────────────────────

def _vol_ratio(bars: List[Dict], window: int) -> float:
    """현재봉 vol / 직전 window봉 평균 vol."""
    if len(bars) < 2:
        return 0.0
    hist = bars[-(window + 1):-1]      # 현재봉 제외한 이전 window봉
    if not hist:
        return 0.0
    avg = sum(_f(b.get("volume", 0)) for b in hist) / len(hist)
    if avg <= 0:
        return 0.0
    return _f(bars[-1].get("volume", 0)) / avg


def _is_explosion(bars: List[Dict], vol_mult: float,
                  window: int) -> bool:
    return _vol_ratio(bars, window) >= vol_mult


def _is_solo(bars: List[Dict], zone_gap: int,
             vol_mult: float, window: int) -> bool:
    """현재봉 직전 zone_gap봉 내에 폭발봉이 없으면 True (단독봉)."""
    if len(bars) < zone_gap + 2:
        return False
    prev = bars[-(zone_gap + 1):-1]    # 현재봉 바로 앞 zone_gap봉
    for idx, b in enumerate(prev):
        # 해당 봉까지의 슬라이스로 vol_ratio 계산 (look-ahead 없음)
        slice_end = len(bars) - zone_gap - 1 + idx + 1
        sub = bars[:slice_end]
        if _is_explosion(sub, vol_mult, window):
            return False
    return True


def _cvd_accel(bars: List[Dict], window: int) -> float:
    """
    CVD 가속도: 직전 window봉 CVD합 - 그 이전 window봉 CVD합.
    양수 → 매수 가속, 음수 → 매도 가속.
    """
    if len(bars) < window * 2 + 1:
        return 0.0
    # 현재봉 제외
    prev = bars[-(window * 2 + 1):-1]
    recent = sum(_f(b.get("cvd_delta", 0)) for b in prev[-window:])
    older  = sum(_f(b.get("cvd_delta", 0)) for b in prev[:window])
    return recent - older


def _cvd_sum(bars: List[Dict], window: int) -> float:
    recent = bars[-window:] if len(bars) >= window else bars
    return sum(_f(b.get("cvd_delta", 0)) for b in recent)


def _candle_dir(bars: List[Dict]) -> str:
    """현재봉 방향: 'up' | 'dn'."""
    if not bars:
        return "dn"
    last = bars[-1]
    return "up" if _f(last.get("close", 0)) >= _f(last.get("open", 0)) else "dn"


def _m15_structure_levels(
    sweep_by_tf: Dict[str, Any],
    lookback_bars: int,
) -> tuple[Optional[float], Optional[float]]:
    """
    15m 중요 가격대(최근 지지/저항) 계산.
    - support: 직전 lookback 봉 저가 최솟값
    - resistance: 직전 lookback 봉 고가 최댓값
    """
    bars_15m = _bars_from_sweep(sweep_by_tf.get("15m") or {})
    n = max(2, int(lookback_bars))
    if len(bars_15m) < n + 1:
        return None, None
    recent = bars_15m[-(n + 1):-1]
    support = min(_f(b.get("low", 0)) for b in recent)
    resistance = max(_f(b.get("high", 0)) for b in recent)
    if support <= 0 or resistance <= 0:
        return None, None
    return round(support, 2), round(resistance, 2)


# ── 신호 함수 ─────────────────────────────────────────────────────────────────

def compute_signal(
    current_price: float,
    sweep_by_tf: Dict[str, Any],
    magnets: Dict[str, Any],
    *,
    entry_tf: Optional[str] = None,
    higher_tf: Optional[str] = None,
    signal_overrides: Optional[Dict[str, Any]] = None,
    tpsl_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    CVD Explosion 신호 계산.

    Parameters
    ----------
    current_price    : float — 현재 가격 (1h 종가)
    sweep_by_tf      : {"1h": {"data": [...]}, "4h": {"data": [...]}}
    magnets          : {"level_map": [{"price": float, ...}]}
    signal_overrides : config.yaml signal 섹션 값을 런타임에 덮어쓸 dict.
                       예) {"vol_mult": 3.0, "confidence_threshold": 4,
                            "score_explosion": 3, "score_solo": 1,
                            "score_cvd_accel": 2, "score_cvd_higher": 1}
    tpsl_overrides   : config.yaml tpsl 섹션 값을 런타임에 덮어쓸 dict.

    Returns
    -------
    signal: "long" | "short" | "none"
    confidence: int
    tp / sl: float | None
    reasons: [str]
    + 디버그 필드들
    """
    tfm = get_timeframes()
    entry_key = (entry_tf or tfm["entry_tf"]).strip()
    higher_key = (higher_tf or tfm["higher_tf"]).strip()
    pe = dict(get_signal_params_for_tf(entry_key))
    ph = dict(get_signal_params_for_tf(higher_key))

    # signal_overrides 를 pe / ph 에 병합
    _sig_ov: Dict[str, Any] = dict(signal_overrides or {})
    if _sig_ov:
        for k in ("vol_avg_window", "zone_gap", "cvd_accel_window",
                  "cvd_higher_window", "confidence_threshold"):
            if k in _sig_ov:
                pe[k] = int(_sig_ov[k])
                ph[k] = int(_sig_ov[k])
        if "vol_mult" in _sig_ov:
            pe["vol_mult"] = float(_sig_ov["vol_mult"])
            ph["vol_mult"] = float(_sig_ov["vol_mult"])

    # higher_tf_veto: signal_overrides 우선, 없으면 config
    higher_tf_veto = bool(_sig_ov.get("higher_tf_veto", pe.get("higher_tf_veto", False)))

    vol_avg_w    = int(pe["vol_avg_window"])
    vol_mult     = float(pe["vol_mult"])
    zone_gap     = int(pe["zone_gap"])
    cvd_accel_w  = int(pe["cvd_accel_window"])
    cvd_higher_w = int(ph["cvd_higher_window"])
    conf_thr     = int(pe["confidence_threshold"])

    # 채점 가중치 (signal_overrides 로 조정 가능)
    sc_exp    = int(_sig_ov.get("score_explosion",  3))
    sc_solo   = int(_sig_ov.get("score_solo",       1))
    sc_cvd    = int(_sig_ov.get("score_cvd_accel",  2))
    sc_cvd_hi = int(_sig_ov.get("score_cvd_higher", 1))

    bars_entry  = _bars_from_sweep(sweep_by_tf.get(entry_key)  or {})
    bars_higher = _bars_from_sweep(sweep_by_tf.get(higher_key) or {})
    level_map: List[Dict] = list((magnets or {}).get("level_map") or [])

    if not bars_entry:
        return _no_signal(f"{entry_key} 데이터 없음", level_map=level_map)

    # ── 지표 계산 ─────────────────────────────────────────────────────────
    vr      = _vol_ratio(bars_entry, vol_avg_w)
    is_exp  = vr >= vol_mult
    is_solo = _is_solo(bars_entry, zone_gap, vol_mult, vol_avg_w) if is_exp else False
    cdir    = _candle_dir(bars_entry)
    accel   = _cvd_accel(bars_entry, cvd_accel_w)
    cvd_hi  = _cvd_sum(bars_higher, cvd_higher_w)
    entry   = _f(bars_entry[-1].get("close", 0)) or current_price

    # ── 채점 ──────────────────────────────────────────────────────────────
    bull = bear = 0
    reasons: List[str] = []

    # explosion score
    if is_exp:
        if cdir == "up":
            bull += sc_exp
            reasons.append(f"[EXP] 상승폭발봉 vr={vr:.2f}x ✅")
        else:
            bear += sc_exp
            reasons.append(f"[EXP] 하락폭발봉 vr={vr:.2f}x ✅")
    else:
        reasons.append(f"[EXP] 미달 vr={vr:.2f}x < {vol_mult}x")

    # solo score
    if is_solo:
        if cdir == "up":
            bull += sc_solo
        else:
            bear += sc_solo
        reasons.append(f"[SOLO] 단독봉 ✅ (직전 {zone_gap}봉 내 폭발 없음)")
    elif is_exp:
        reasons.append(f"[SOLO] 클러스터봉 — 단독봉 아님")

    # CVD 가속 score
    accel_dir = "up" if accel > 0 else "dn"
    if is_exp and accel_dir == cdir:
        if cdir == "up":
            bull += sc_cvd
        else:
            bear += sc_cvd
        reasons.append(f"[CVD_ACCEL] 가속 일치 {accel:+.0f} ✅")
    elif is_exp:
        reasons.append(f"[CVD_ACCEL] 가속 역방향 {accel:+.0f} ❌")

    # 상위TF CVD score
    cvd_lbl = f"CVD_{higher_key}"
    if cvd_hi > 0:
        bull += sc_cvd_hi
        reasons.append(f"[{cvd_lbl}] {higher_key} CVD↑ {cvd_hi:.0f}")
    elif cvd_hi < 0:
        bear += sc_cvd_hi
        reasons.append(f"[{cvd_lbl}] {higher_key} CVD↓ {cvd_hi:.0f}")

    tpsl_params = dict(get_tpsl_params())
    # tpsl_overrides 병합 — 여기서 적용되므로 position_meta 에도 자동 반영
    if tpsl_overrides:
        tpsl_params.update(tpsl_overrides)

    m15_lb = int(tpsl_params.get("m15_structure_lookback_bars", 8))
    m15_support, m15_resistance = _m15_structure_levels(sweep_by_tf, m15_lb)

    max_score = sc_exp + sc_solo + sc_cvd + sc_cvd_hi

    # ── 공통 debug 필드 ───────────────────────────────────────────────────
    common = {
        "bull_score":    bull,
        "bear_score":    bear,
        "max_score":     max_score,
        "conf_threshold": conf_thr,
        "vol_ratio":     round(vr, 3),
        "is_explosion":  is_exp,
        "is_solo":       is_solo,
        "cvd_accel":     round(accel, 1),
        "cvd_higher":    round(cvd_hi, 1),
        "cvd_higher_tf": higher_key,
        "level_map":     level_map,
        "tpsl_mode":     tpsl_mode_label(tpsl_params),
        "signal_mode":   "cvd_exp_v1",
        "reasons":       reasons,
        "m15_support":   m15_support,
        "m15_resistance": m15_resistance,
    }

    # ── 진입 판단 ─────────────────────────────────────────────────────────
    if bull >= conf_thr and bull > bear:
        if higher_tf_veto and cvd_hi < 0:
            reasons.append(f"[VETO] {higher_key} CVD {cvd_hi:.0f} 반대 — long 진입 거부")
            return {**_no_signal(None), **common}
        tp, sl = resolve_tpsl("long", entry, level_map, tpsl_params)
        if tp is None or sl is None:
            if tpsl_params.get("mode") == "fixed_rr":
                reasons.append("[대기] LONG TP/SL 산출 실패 (tpsl.risk_pct / rr_ratio 확인)")
            else:
                reasons.append("[대기] LONG liq map 없음 또는 TP/SL 부족 — liq cache 필요")
            return {**_no_signal(None), **common}
        reasons.append(
            f"[진입] LONG  confidence={bull}/7  mode={common['tpsl_mode']}  TP={tp:,.2f}  SL={sl:,.2f}"
        )
        out = {"signal": "long", "confidence": bull, "tp": tp, "sl": sl, **common}
        out["position_meta"] = _position_meta_for_entry(tpsl_params)
        return out

    if bear >= conf_thr and bear > bull:
        if higher_tf_veto and cvd_hi > 0:
            reasons.append(f"[VETO] {higher_key} CVD {cvd_hi:.0f} 반대 — short 진입 거부")
            return {**_no_signal(None), **common}
        tp, sl = resolve_tpsl("short", entry, level_map, tpsl_params)
        if tp is None or sl is None:
            if tpsl_params.get("mode") == "fixed_rr":
                reasons.append("[대기] SHORT TP/SL 산출 실패 (tpsl.risk_pct / rr_ratio 확인)")
            else:
                reasons.append("[대기] SHORT liq map 없음 또는 TP/SL 부족 — liq cache 필요")
            return {**_no_signal(None), **common}
        reasons.append(
            f"[진입] SHORT confidence={bear}/7  mode={common['tpsl_mode']}  TP={tp:,.2f}  SL={sl:,.2f}"
        )
        out = {"signal": "short", "confidence": bear, "tp": tp, "sl": sl, **common}
        out["position_meta"] = _position_meta_for_entry(tpsl_params)
        return out

    reasons.append(f"[대기] bull={bull} bear={bear} — 임계값({conf_thr}/{max_score}) 미달")
    return {**_no_signal(None), **common}


def _position_meta_for_entry(tpsl_params: Dict[str, Any]) -> Dict[str, Any]:
    """백테스트 runner 가 포지션에 병합 — magnet_rr 청산·TP advance 용."""
    mode = tpsl_mode_label(tpsl_params)
    meta: Dict[str, Any] = {"tpsl_mode": mode}
    meta["slippage_pct"] = float(tpsl_params.get("slippage_pct") or 0.0)
    if mode == MODE_MAGNET_RR:
        meta["tp_advances"] = 0
        meta["sl_ratchet_step"]           = int(tpsl_params.get("sl_ratchet_step", 1))
        meta["sl_ratchet_buffer_pct"]     = float(tpsl_params.get("sl_ratchet_buffer_pct") or 0.0)
    meta["m15_structure_stop_enabled"] = bool(tpsl_params.get("m15_structure_stop_enabled", True))
    meta["m15_structure_lookback_bars"] = int(tpsl_params.get("m15_structure_lookback_bars", 8))
    meta["m15_structure_buffer_pct"] = float(tpsl_params.get("m15_structure_buffer_pct") or 0.05)
    return meta


def _no_signal(reason: Optional[str], level_map: List[Dict] = None) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "signal": "none", "confidence": 0, "tp": None, "sl": None,
        "reasons": [reason] if reason else [],
    }
    if level_map is not None:
        base["level_map"] = level_map
    return base
