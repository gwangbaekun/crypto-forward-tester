"""
OI Acceleration Breakout v2 — Signal (Forward Test).

백테스트 oi_accel_breakout_v2/engine.py(벡터화 run)의 진입 로직을 완성봉 시리즈에
동일하게 계산 후 마지막 행(=방금 마감된 봉)에서 평가. shift(1) 패턴을 그대로 재현하므로
지표는 마지막 완성봉 직전(i-1)까지 데이터로 산출되고 진입은 마지막 완성봉 종가에 한다.

진입 (양방향 모두 OI 가속 z 필요, 방향은 CVD 부호로 결정):
  공통 게이트: atr% <= atr_squeeze_pct  (저변동성 횡보 레짐)
  LONG : oi_accel_z >= accel_z_threshold  AND  cvd% >= cvd_threshold   AND  close >= ema
  SHORT: oi_accel_z >= accel_z_threshold  AND  cvd% <= -cvd_threshold  AND  close <= ema

SL = entry ± sl_pct%
TP = entry ± (SL거리) × tp_ratio

df 컬럼 요구: open_time_ms, high, low, close, volume, cvd_delta, open_interest
호출 측(realtime_feed)은 형성 중 봉을 제거한 완성봉 df 와 그 마지막 봉 종가(current_price)를 전달.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import numpy as np
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
    _none = {"signal": "none", "confidence": 0, "tp": None, "sl": None, "reasons": []}

    if df is None or len(df) < 2:
        return {**_none, "reasons": ["[OI v2] 데이터 부족"]}

    sp = dict(get_signal_params())
    tp_params = dict(get_tpsl_params())

    if signal_overrides:
        for k in ("accel_lookback", "z_period", "cvd_lookback", "atr_period", "ema_period"):
            if k in signal_overrides:
                sp[k] = int(signal_overrides[k])
        for k in ("accel_z_threshold", "cvd_threshold", "atr_squeeze_pct", "tp_ratio"):
            if k in signal_overrides:
                sp[k] = float(signal_overrides[k])
        if "sides" in signal_overrides:
            sp["sides"] = str(signal_overrides["sides"]).strip().lower()
    if tpsl_overrides:
        tp_params.update(tpsl_overrides)

    accel_lookback    = int(sp["accel_lookback"])
    accel_z_threshold = float(sp["accel_z_threshold"])
    z_period          = int(sp["z_period"]) or 50
    cvd_lookback      = int(sp["cvd_lookback"])
    cvd_threshold     = float(sp["cvd_threshold"])
    atr_squeeze_pct   = float(sp["atr_squeeze_pct"])
    atr_period        = int(sp["atr_period"]) or 14
    ema_period        = int(sp["ema_period"])
    tp_ratio          = float(sp["tp_ratio"])
    sides             = str(sp.get("sides", "both"))
    sl_pct            = float(tp_params["sl_pct"])

    allowed = {"long", "short"}
    if sides == "long":
        allowed = {"long"}
    elif sides == "short":
        allowed = {"short"}

    df = df.reset_index(drop=True)
    n = len(df)
    i = n - 1  # 마지막 완성봉 (= backtest 진입 봉 i)

    need = max(z_period + accel_lookback, cvd_lookback, atr_period, ema_period) + 2
    if n < need:
        return {**_none, "reasons": [f"[OI v2] warmup 부족 ({n} < {need})"]}

    # ── 벡터화 지표 (backtest engine 과 동일, .shift(1) 포함) ──────────────────
    oi = pd.to_numeric(df["open_interest"], errors="coerce")
    oi_prev_n = oi.shift(accel_lookback)
    oi_accel = (oi - oi_prev_n) / oi_prev_n.replace(0, np.nan) * 100.0
    _mp = max(z_period // 2, accel_lookback + 1)
    oi_z_mean = oi_accel.rolling(z_period, min_periods=_mp).mean()
    oi_z_std  = oi_accel.rolling(z_period, min_periods=_mp).std()
    oi_accel_z = ((oi_accel - oi_z_mean) / oi_z_std.replace(0, np.nan)).shift(1)

    cvd_roll = df["cvd_delta"].rolling(cvd_lookback, min_periods=cvd_lookback).sum()
    vol_roll = df["volume"].rolling(cvd_lookback, min_periods=cvd_lookback).sum()
    cvd_pct  = (cvd_roll / vol_roll.replace(0, np.nan) * 100.0).shift(1)

    prev_c = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_c).abs(),
        (df["low"]  - prev_c).abs(),
    ], axis=1).max(axis=1)
    atr_pct = (tr.rolling(atr_period, min_periods=atr_period).mean() / df["close"] * 100.0).shift(1)

    if ema_period > 0:
        ema = df["close"].ewm(span=ema_period, adjust=False).mean().shift(1)
    else:
        ema = pd.Series(np.nan, index=df.index)

    oi_z_v = float(oi_accel_z.iat[i])
    cv_v   = float(cvd_pct.iat[i])
    atr_v  = float(atr_pct.iat[i])
    ema_v  = float(ema.iat[i])

    entry_px = _f(current_price)
    if entry_px <= 0:
        return {**_none, "reasons": ["[진입] 가격 이상"]}
    if not all(math.isfinite(x) for x in (oi_z_v, cv_v, atr_v)):
        return {**_none, "reasons": ["[OI v2] 지표 NaN (warmup 부족)"]}

    # ── ATR squeeze 게이트 (횡보 레짐 확인) ───────────────────────────────────
    if atr_squeeze_pct > 0.0 and atr_v > atr_squeeze_pct:
        return {**_none, "reasons": [f"[ATR%] {atr_v:.2f}% > squeeze {atr_squeeze_pct}% — skip"]}

    signal = "none"
    sl = tp = 0.0
    reasons: List[str] = []

    if "long" in allowed and oi_z_v >= accel_z_threshold and cv_v >= cvd_threshold:
        sl = round(entry_px * (1.0 - sl_pct / 100.0), 2)
        signal = "long"
        reasons = [
            f"[OI ACCEL Z] {oi_z_v:.2f}σ >= {accel_z_threshold}σ",
            f"[CVD%] {cv_v:.2f}% >= {cvd_threshold}%",
            f"[ATR%] {atr_v:.2f}% (squeeze <= {atr_squeeze_pct}%)",
        ]
    elif "short" in allowed and oi_z_v >= accel_z_threshold and cv_v <= -cvd_threshold:
        sl = round(entry_px * (1.0 + sl_pct / 100.0), 2)
        signal = "short"
        reasons = [
            f"[OI ACCEL Z] {oi_z_v:.2f}σ >= {accel_z_threshold}σ",
            f"[CVD%] {cv_v:.2f}% <= -{cvd_threshold}%",
            f"[ATR%] {atr_v:.2f}% (squeeze <= {atr_squeeze_pct}%)",
        ]

    if signal == "none":
        return {**_none, "reasons": [
            f"[OI v2] 조건 미충족 oi_z={oi_z_v:.2f} cvd={cv_v:.2f} atr={atr_v:.2f}"
        ]}

    # ── EMA 추세 필터 ─────────────────────────────────────────────────────────
    if ema_period > 0 and math.isfinite(ema_v):
        if signal == "long" and entry_px < ema_v:
            return {**_none, "reasons": [f"[EMA] long인데 close {entry_px:.2f} < ema {ema_v:.2f} — skip"]}
        if signal == "short" and entry_px > ema_v:
            return {**_none, "reasons": [f"[EMA] short인데 close {entry_px:.2f} > ema {ema_v:.2f} — skip"]}

    if tp_ratio > 0.0 and sl > 0.0:
        dist = abs(entry_px - sl)
        tp = round(entry_px + dist * tp_ratio, 2) if signal == "long" \
            else round(entry_px - dist * tp_ratio, 2)

    return {
        "signal":     signal,
        "confidence": 1,
        "tp":         tp if signal != "none" else None,
        "sl":         sl if signal != "none" else None,
        "reasons":    reasons,
        "oi_accel_z": round(oi_z_v, 3),
        "cvd_pct":    round(cv_v, 3),
        "atr_pct":    round(atr_v, 3),
        "ema":        round(ema_v, 2) if math.isfinite(ema_v) else None,
    }
