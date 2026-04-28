"""
OI CVD Surge — Signal (Forward Test).

백테스트 engine.py 의 벡터화 로직을 단일 봉(완성봉 마지막 행)에 적용.

신호 조건:
  LONG:  직전 lookback봉 CVD합 > 0  AND  직전 oi_lookback봉 OI 변화율 >= oi_min_pct
  SHORT: 직전 lookback봉 CVD합 < 0  AND  직전 oi_lookback봉 OI 변화율 >= oi_min_pct

SL  = 직전 lookback봉 저가/고가 (sl_max_pct 로 캡)
TP  = 진입가 ± SL거리 × rr_ratio

df 컬럼 요구사항:
  open_time_ms, high, low, close, cvd_delta, open_interest
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import pandas as pd

from .config_loader import get_signal_params, get_tpsl_params


def _f(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        x = float(v)
        return x if not math.isnan(x) else 0.0
    except (TypeError, ValueError):
        return 0.0


def compute_signal(
    df: pd.DataFrame,
    current_price: float,
    *,
    signal_overrides: Optional[Dict[str, Any]] = None,
    tpsl_overrides:   Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    df: 완성봉만 포함된 DataFrame (형성 중 봉 제거 후 전달).
        마지막 행(iloc[-1])이 방금 마감된 봉.

    백테스트 shift(1) 패턴 재현:
      roll 계산 구간 = [i-lookback : i]  (마지막 봉 자체는 제외)
      oi_pct        = oi[i-1] vs oi[i-1-oi_lookback]
    """
    _none = {"signal": "none", "confidence": 0, "tp": None, "sl": None, "reasons": []}

    if df is None or len(df) < 2:
        return {**_none, "reasons": ["[OI] 데이터 부족"]}

    sp = dict(get_signal_params())
    tp_params = dict(get_tpsl_params())

    if signal_overrides:
        if "lookback"    in signal_overrides: sp["lookback"]    = int(signal_overrides["lookback"])
        if "oi_lookback" in signal_overrides: sp["oi_lookback"] = int(signal_overrides["oi_lookback"])
        if "oi_min_pct"  in signal_overrides: sp["oi_min_pct"]  = float(signal_overrides["oi_min_pct"])
    if tpsl_overrides:
        tp_params.update(tpsl_overrides)

    lookback    = int(sp["lookback"])
    oi_lookback = int(sp["oi_lookback"])
    oi_min_pct  = float(sp["oi_min_pct"])
    rr_ratio    = float(tp_params.get("rr_ratio", 3.0))
    sl_max_pct  = tp_params.get("sl_max_pct")

    n = len(df)
    i = n - 1  # 마지막 완성봉

    # lookback + oi_lookback 봉 이상 필요
    if i < lookback + oi_lookback:
        return {**_none, "reasons": [f"[OI] warmup 부족 ({n} < {lookback + oi_lookback + 1})"]}

    # 직전 lookback봉 구간: [i-lookback : i]  (현재봉 제외)
    window = df.iloc[i - lookback : i]
    roll_h  = float(window["high"].max())
    roll_l  = float(window["low"].min())
    cvd_net = float(window["cvd_delta"].sum())

    # OI: 현재봉 제외(i-1) 기준으로 oi_lookback봉 전 대비 변화율
    oi_now  = _f(df["open_interest"].iat[i - 1])
    oi_prev = _f(df["open_interest"].iat[i - 1 - oi_lookback])

    if not math.isfinite(cvd_net):
        return {**_none, "reasons": ["[CVD] NaN"]}
    if oi_now <= 0 or oi_prev <= 0 or not math.isfinite(oi_now):
        return {**_none, "reasons": ["[OI] 데이터 없음 또는 0"]}

    oi_pct = (oi_now - oi_prev) / oi_prev * 100.0
    oi_surge = oi_pct >= oi_min_pct

    entry_px = _f(current_price)
    if entry_px <= 0:
        return {**_none, "reasons": ["[진입] 가격 이상"]}

    signal   = "none"
    tp = sl  = 0.0
    reasons: List[str] = []

    if oi_surge and cvd_net > 0 and roll_l > 0:
        sl_raw = roll_l
        if sl_max_pct and sl_max_pct > 0:
            sl_raw = max(sl_raw, entry_px * (1.0 - sl_max_pct / 100.0))
        sl_dist = entry_px - sl_raw
        if sl_dist > 0:
            signal = "long"
            sl = round(sl_raw, 2)
            tp = round(entry_px + sl_dist * rr_ratio, 2)
            reasons = [
                f"[OI] 급증 oi_pct={oi_pct:.2f}% >= {oi_min_pct}%",
                f"[CVD] 매수 압력 cvd_net={cvd_net:.0f} > 0",
                f"[진입] LONG RR={rr_ratio} TP={tp:.2f} SL={sl:.2f}",
            ]

    elif oi_surge and cvd_net < 0 and roll_h > 0:
        sl_raw = roll_h
        if sl_max_pct and sl_max_pct > 0:
            sl_raw = min(sl_raw, entry_px * (1.0 + sl_max_pct / 100.0))
        sl_dist = sl_raw - entry_px
        if sl_dist > 0:
            signal = "short"
            sl = round(sl_raw, 2)
            tp = round(entry_px - sl_dist * rr_ratio, 2)
            reasons = [
                f"[OI] 급증 oi_pct={oi_pct:.2f}% >= {oi_min_pct}%",
                f"[CVD] 매도 압력 cvd_net={cvd_net:.0f} < 0",
                f"[진입] SHORT RR={rr_ratio} TP={tp:.2f} SL={sl:.2f}",
            ]

    return {
        "signal":     signal,
        "confidence": 1 if signal != "none" else 0,
        "tp":         tp if signal != "none" else None,
        "sl":         sl if signal != "none" else None,
        "reasons":    reasons,
        "cvd_net":    round(cvd_net, 1),
        "oi_pct":     round(oi_pct, 3),
        "roll_high":  round(roll_h, 2),
        "roll_low":   round(roll_l, 2),
    }
