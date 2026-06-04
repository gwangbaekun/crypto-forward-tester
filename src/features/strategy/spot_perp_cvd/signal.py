"""
Spot-Perp CVD Divergence — Signal Function.

mode: divergence (default)
  LONG:  spot_cvd_pct >= spot_threshold  AND  perp_cvd_pct <= -perp_threshold
  SHORT: spot_cvd_pct <= -spot_threshold  AND  perp_cvd_pct >= perp_threshold

mode: spread  (Spot leads Perp)
  LONG:  (spot_cvd% - perp_cvd%) >= +spread_threshold  AND  spot_cvd% >= spot_threshold
  SHORT: (spot_cvd% - perp_cvd%) <= -spread_threshold  AND  spot_cvd% <= -spot_threshold

mode: combined   (방향 반대 + spread 합산)
mode: composite  (Normalized score: sc/spot_thr + (-pc)/perp_thr >= combo_thr)

mode: zscore  (Spread z-score — TF 자동 정규화)
  LONG:  spread_z >= z_threshold  AND  spot > 0  AND  perp < 0
  SHORT: spread_z <= -z_threshold AND  spot < 0  AND  perp > 0

CVD%:
  rolling_sum(cvd_delta, lookback) / rolling_sum(volume, lookback) * 100
  — lookback개의 완성봉 기준 (forming 봉 제외) → backtest shift(1) 동일

Regime Filters (0 = disabled):
  atr_min_pct     : ATR% ≥ 값인 봉에만 진입 (변동성 필터)
  ema_period      : EMA 추세 방향 일치 시에만 진입
  volume_ratio_min: 현재 거래량 / 롤링 평균 ≥ 값
  adx_min         : ADX ≥ 값인 봉에만 진입 (추세 강도)
  cvd_mom_lookback: Spot CVD%가 N봉 전보다 클 때만 (다이버전스 성장 중)
  invert_signal   : True → LONG↔SHORT 반전 (perp leads 가설)

Exit:
  CVD 수렴 (다이버전스 해소) 또는 hard SL · TP · Trailing SL

진입 시점:
  봉 마감 60초 전 forming 봉 close 기준 — look-ahead bias 없음
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .config_loader import get_signal_params, get_timeframes, get_tpsl_params


def _f(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        x = float(v)
        return x if not math.isnan(x) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _compute_cvd_pct(df: pd.DataFrame, lookback: int) -> float:
    """
    마지막 lookback개 완성봉(forming 봉 제외)의 rolling CVD%.

    backtest .shift(1) 과 동일: df[-lookback-1:-1] 구간 사용.
    """
    if df is None or df.empty or len(df) < lookback + 1:
        return float("nan")
    sl = df.iloc[-(lookback + 1):-1]
    cvd_sum = float(sl["cvd_delta"].sum())
    vol_sum = float(sl["volume"].sum())
    if vol_sum <= 0:
        return float("nan")
    return cvd_sum / vol_sum * 100.0


def _compute_atr_pct(df: pd.DataFrame, period: int) -> float:
    """ATR% = ATR / close × 100 (마지막 완성봉 기준)."""
    period = period or 14
    need = period + 2
    if df is None or len(df) < need:
        return float("nan")
    sl = df.iloc[-(need + 1):-1].copy().reset_index(drop=True)
    prev_c = sl["close"].shift(1)
    tr = pd.concat([
        sl["high"] - sl["low"],
        (sl["high"] - prev_c).abs(),
        (sl["low"]  - prev_c).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period, min_periods=period).mean().iloc[-1]
    close = float(sl["close"].iloc[-1])
    if not math.isfinite(atr) or close <= 0:
        return float("nan")
    return atr / close * 100.0


def _compute_ema(df: pd.DataFrame, period: int) -> float:
    """EMA of close (마지막 완성봉 기준)."""
    if df is None or len(df) < period + 2:
        return float("nan")
    closes = df["close"].iloc[-(period * 3 + 1):-1]
    ema = closes.ewm(span=period, adjust=False).mean().iloc[-1]
    return float(ema) if math.isfinite(ema) else float("nan")


def _compute_adx(df: pd.DataFrame, period: int) -> float:
    """Wilder ADX (마지막 완성봉 기준)."""
    period = period or 14
    need = period * 3 + 2
    if df is None or len(df) < need:
        return float("nan")
    sl = df.iloc[-(need + 1):-1].copy().reset_index(drop=True)
    prev_h = sl["high"].shift(1)
    prev_l = sl["low"].shift(1)
    prev_c = sl["close"].shift(1)
    up_move   = sl["high"] - prev_h
    down_move = prev_l - sl["low"]
    dm_plus  = np.where((up_move > down_move) & (up_move > 0),   up_move,   0.0)
    dm_minus = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = pd.concat([
        sl["high"] - sl["low"],
        (sl["high"] - prev_c).abs(),
        (sl["low"]  - prev_c).abs(),
    ], axis=1).max(axis=1)
    alpha = 1.0 / period
    atr_s  = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    dmp_s  = pd.Series(dm_plus,  index=sl.index).ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    dmm_s  = pd.Series(dm_minus, index=sl.index).ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    di_plus  = 100.0 * dmp_s / atr_s.replace(0, float("nan"))
    di_minus = 100.0 * dmm_s / atr_s.replace(0, float("nan"))
    dx = 100.0 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, float("nan"))
    adx = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean().iloc[-1]
    return float(adx) if math.isfinite(adx) else float("nan")


def _compute_vol_ratio(df: pd.DataFrame, vol_lb: int) -> float:
    """현재 봉 거래량 / 롤링 평균 거래량 (마지막 완성봉 기준)."""
    if df is None or len(df) < vol_lb + 2:
        return float("nan")
    sl = df.iloc[-(vol_lb + 2):-1]
    avg = float(sl["volume"].iloc[:-1].mean())
    cur = float(sl["volume"].iloc[-1])
    if avg <= 0:
        return float("nan")
    return cur / avg


def _compute_spread_z(
    perp_df: pd.DataFrame,
    spot_df: pd.DataFrame,
    lookback: int,
    z_period: int,
) -> float:
    """
    Spread z-score = (spread - rolling_mean) / rolling_std
    spread = spot_cvd% - perp_cvd% 를 z_period개 봉에 걸쳐 정규화.
    TF 무관 자동 정규화 — 1h / 15m 동일한 threshold 사용 가능.
    """
    z_period = z_period or lookback * 3
    need = z_period + lookback + 2
    if len(perp_df) < need or len(spot_df) < need:
        return float("nan")

    spreads: List[float] = []
    for i in range(z_period):
        # 각 시점의 CVD% 계산 (슬라이딩 윈도우)
        p_end = len(perp_df) - 1 - i
        s_end = len(spot_df) - 1 - i
        if p_end < lookback + 1 or s_end < lookback + 1:
            break
        p_sl = perp_df.iloc[p_end - lookback: p_end]
        s_sl = spot_df.iloc[s_end - lookback: s_end]
        p_cvd = p_sl["cvd_delta"].sum() / (p_sl["volume"].sum() or float("nan")) * 100.0
        s_cvd = s_sl["cvd_delta"].sum() / (s_sl["volume"].sum() or float("nan")) * 100.0
        if math.isfinite(p_cvd) and math.isfinite(s_cvd):
            spreads.append(s_cvd - p_cvd)

    if len(spreads) < max(z_period // 2, lookback):
        return float("nan")

    arr = np.array(spreads)
    mean_s = float(np.mean(arr))
    std_s  = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
    if std_s <= 0:
        return float("nan")
    return (spreads[0] - mean_s) / std_s  # spreads[0] = 현재 봉


def compute_signal(
    perp_df: Optional[pd.DataFrame],
    spot_df: Optional[pd.DataFrame],
    signal_overrides: Optional[Dict[str, Any]] = None,
    tpsl_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Spot-Perp CVD 신호 계산 (모든 모드 + 레짐 필터 포함).

    Parameters
    ----------
    perp_df : Binance Futures klines (open_time_ms, open/high/low/close, volume, cvd_delta)
    spot_df : Binance Spot klines    (open_time_ms, volume, cvd_delta)

    Returns
    -------
    signal: "long" | "short" | "none"
    sl: float | None
    spot_cvd_pct, perp_cvd_pct: float  (exit_check CVD 수렴 판정용)
    """
    sp   = dict(get_signal_params())
    tp_p = dict(get_tpsl_params())
    entry_tf = get_timeframes()["entry_tf"]

    # ── overrides 적용 ────────────────────────────────────────────────────────
    if signal_overrides:
        for k, cast in [
            ("lookback", int), ("spot_cvd_threshold", float), ("perp_cvd_threshold", float),
            ("spread_threshold", float), ("combined_threshold", float),
            ("short_perp_threshold", float),
            ("atr_period", int), ("atr_min_pct", float),
            ("ema_period", int), ("volume_ratio_min", float), ("vol_lookback", int),
            ("adx_min", float), ("adx_period", int),
            ("cvd_mom_lookback", int), ("z_threshold", float), ("z_period", int),
        ]:
            if k in signal_overrides:
                sp[k] = cast(signal_overrides[k])
        if "mode" in signal_overrides:
            sp["mode"] = str(signal_overrides["mode"]).strip().lower()
        if "invert_signal" in signal_overrides:
            sp["invert_signal"] = bool(signal_overrides["invert_signal"])
    if tpsl_overrides:
        tp_p.update(tpsl_overrides)

    # ── 파라미터 ──────────────────────────────────────────────────────────────
    lookback             = int(sp["lookback"])
    spot_cvd_threshold   = float(sp["spot_cvd_threshold"])
    perp_cvd_threshold   = float(sp["perp_cvd_threshold"])
    spread_threshold     = float(sp.get("spread_threshold", 5.0))
    combined_threshold   = float(sp.get("combined_threshold", 4.0))
    mode                 = str(sp.get("mode", "divergence")).strip().lower()
    long_spot_threshold  = float(sp.get("long_spot_threshold", spot_cvd_threshold))
    long_perp_threshold  = float(sp.get("long_perp_threshold", perp_cvd_threshold))
    short_spot_threshold = float(sp.get("short_spot_threshold", spot_cvd_threshold))
    short_perp_threshold = float(sp.get("short_perp_threshold", perp_cvd_threshold))
    sl_pct               = float(tp_p.get("sl_pct", 2.0))
    tp_ratio             = float(tp_p.get("tp_ratio", 0.0))
    # 레짐 필터
    atr_period           = int(sp.get("atr_period", 14)) or 14
    atr_min_pct          = float(sp.get("atr_min_pct", 0.0))
    ema_period           = int(sp.get("ema_period", 0))
    volume_ratio_min     = float(sp.get("volume_ratio_min", 0.0))
    vol_lb               = int(sp.get("vol_lookback", 0)) or lookback
    adx_min              = float(sp.get("adx_min", 0.0))
    adx_period_v         = int(sp.get("adx_period", 14)) or 14
    cvd_mom_lookback     = int(sp.get("cvd_mom_lookback", 0))
    invert_signal        = bool(sp.get("invert_signal", False))
    z_threshold          = float(sp.get("z_threshold", 1.5))
    z_period             = int(sp.get("z_period", 0))

    def _no(reason: str) -> Dict[str, Any]:
        return {
            "signal": "none", "confidence": 0, "tp": None, "sl": None,
            "entry_tf": entry_tf, "level_map": [],
            "spot_cvd_pct": None, "perp_cvd_pct": None,
            "reasons": [reason],
        }

    if perp_df is None or perp_df.empty or len(perp_df) < 2:
        return _no("perp 데이터 없음")
    if spot_df is None or spot_df.empty:
        return _no("spot 데이터 없음")

    # ── CVD% 계산 ─────────────────────────────────────────────────────────────
    spot_cvd_pct = _compute_cvd_pct(spot_df, lookback)
    perp_cvd_pct = _compute_cvd_pct(perp_df, lookback)

    if not math.isfinite(spot_cvd_pct) or not math.isfinite(perp_cvd_pct):
        return _no(f"CVD% 산출 불가 — spot={spot_cvd_pct:.3g} perp={perp_cvd_pct:.3g}")

    # 진입가 = forming 봉 close
    entry = _f(perp_df.iloc[-1]["close"])
    if entry <= 0:
        return _no("forming 봉 가격 없음")

    sc = round(spot_cvd_pct, 3)
    pc = round(perp_cvd_pct, 3)
    spread = sc - pc

    # ── ATR 레짐 필터 ─────────────────────────────────────────────────────────
    if atr_min_pct > 0.0:
        atr_v = _compute_atr_pct(perp_df, atr_period)
        if not math.isfinite(atr_v) or atr_v < atr_min_pct:
            return _no(f"ATR 필터 미충족 atr={atr_v:.3f}% < {atr_min_pct}%")

    # ── 거래량 비율 필터 ──────────────────────────────────────────────────────
    if volume_ratio_min > 0.0:
        vr = _compute_vol_ratio(perp_df, vol_lb)
        if not math.isfinite(vr) or vr < volume_ratio_min:
            return _no(f"Volume 필터 미충족 vol_ratio={vr:.2f} < {volume_ratio_min}")

    # ── ADX 필터 ─────────────────────────────────────────────────────────────
    if adx_min > 0.0:
        adx_v = _compute_adx(perp_df, adx_period_v)
        if not math.isfinite(adx_v) or adx_v < adx_min:
            return _no(f"ADX 필터 미충족 adx={adx_v:.1f} < {adx_min}")

    reasons: List[str] = []
    signal = "none"
    sl = 0.0

    # ── 신호 생성 (모드별) ────────────────────────────────────────────────────

    if mode == "spread":
        # ── Spot leads Perp ─────────────────────────────────────────────────
        if spread <= -spread_threshold and sc <= -spot_cvd_threshold:
            sl = round(entry * (1.0 + sl_pct / 100.0), 2)
            signal = "short"
            reasons = [
                f"[SPREAD] {sc:.3f}% - {pc:.3f}% = {spread:.3f}% <= -{spread_threshold}% [spot leads down]",
                f"[SPOT CVD] {sc:.3f}% <= -{spot_cvd_threshold}%",
                f"[진입] SHORT  entry={entry:.2f}  SL={sl:.2f}  (max {sl_pct}% / CVD exit)",
            ]
        elif spread >= spread_threshold and sc >= spot_cvd_threshold:
            sl = round(entry * (1.0 - sl_pct / 100.0), 2)
            signal = "long"
            reasons = [
                f"[SPREAD] {sc:.3f}% - {pc:.3f}% = {spread:.3f}% >= +{spread_threshold}% [spot leads up]",
                f"[SPOT CVD] {sc:.3f}% >= +{spot_cvd_threshold}%",
                f"[진입] LONG  entry={entry:.2f}  SL={sl:.2f}  (max {sl_pct}% / CVD exit)",
            ]

    elif mode == "combined":
        if sc > 0 and pc < 0 and spread >= combined_threshold:
            sl = round(entry * (1.0 - sl_pct / 100.0), 2)
            signal = "long"
            reasons = [
                f"[COMBINED] spread={spread:.3f}% >= +{combined_threshold}%",
                f"[진입] LONG  entry={entry:.2f}  SL={sl:.2f}",
            ]
        elif sc < 0 and pc > 0 and spread <= -combined_threshold:
            sl = round(entry * (1.0 + sl_pct / 100.0), 2)
            signal = "short"
            reasons = [
                f"[COMBINED] spread={spread:.3f}% <= -{combined_threshold}%",
                f"[진입] SHORT  entry={entry:.2f}  SL={sl:.2f}",
            ]

    elif mode == "composite":
        long_score  = (sc / spot_cvd_threshold) + (-pc / perp_cvd_threshold) if spot_cvd_threshold and perp_cvd_threshold else 0.0
        short_score = (-sc / spot_cvd_threshold) + (pc / perp_cvd_threshold) if spot_cvd_threshold and perp_cvd_threshold else 0.0
        if long_score >= combined_threshold:
            sl = round(entry * (1.0 - sl_pct / 100.0), 2)
            signal = "long"
            reasons = [f"[COMPOSITE] score={long_score:.2f} >= {combined_threshold}", f"[진입] LONG  SL={sl:.2f}"]
        elif short_score >= combined_threshold:
            sl = round(entry * (1.0 + sl_pct / 100.0), 2)
            signal = "short"
            reasons = [f"[COMPOSITE] score={short_score:.2f} >= {combined_threshold}", f"[진입] SHORT  SL={sl:.2f}"]

    elif mode == "zscore":
        # ── Spread Z-score (TF 자동 정규화) ─────────────────────────────────
        sz = _compute_spread_z(perp_df, spot_df, lookback, z_period)
        if math.isfinite(sz):
            if sz >= z_threshold and sc > 0 and pc < 0:
                sl = round(entry * (1.0 - sl_pct / 100.0), 2)
                signal = "long"
                reasons = [
                    f"[ZSCORE] spread_z={sz:.2f} >= +{z_threshold:.2f}σ",
                    f"[CVD] spot={sc:.3f}%  perp={pc:.3f}%  spread={spread:.3f}%",
                    f"[진입] LONG  entry={entry:.2f}  SL={sl:.2f}",
                ]
            elif sz <= -z_threshold and sc < 0 and pc > 0:
                sl = round(entry * (1.0 + sl_pct / 100.0), 2)
                signal = "short"
                reasons = [
                    f"[ZSCORE] spread_z={sz:.2f} <= -{z_threshold:.2f}σ",
                    f"[CVD] spot={sc:.3f}%  perp={pc:.3f}%  spread={spread:.3f}%",
                    f"[진입] SHORT  entry={entry:.2f}  SL={sl:.2f}",
                ]

    else:
        # ── divergence (default) ─────────────────────────────────────────────
        if sc >= long_spot_threshold and pc <= -long_perp_threshold:
            sl = round(entry * (1.0 - sl_pct / 100.0), 2)
            signal = "long"
            reasons = [
                f"[SPOT CVD] {sc:.3f}% >= {long_spot_threshold}%",
                f"[PERP CVD] {pc:.3f}% <= -{long_perp_threshold}%",
                f"[진입] LONG  entry={entry:.2f}  SL={sl:.2f}  (max {sl_pct}% / CVD exit)",
            ]
        elif sc <= -short_spot_threshold and pc >= short_perp_threshold:
            sl = round(entry * (1.0 + sl_pct / 100.0), 2)
            signal = "short"
            reasons = [
                f"[SPOT CVD] {sc:.3f}% <= -{short_spot_threshold}%",
                f"[PERP CVD] {pc:.3f}% >= {short_perp_threshold}%",
                f"[진입] SHORT  entry={entry:.2f}  SL={sl:.2f}  (max {sl_pct}% / CVD exit)",
            ]

    if signal == "none":
        reasons.append(
            f"[대기] spot_cvd={sc:.3f}% perp_cvd={pc:.3f}%  spread={spread:.3f}%  "
            f"(mode={mode})"
        )
        return {
            "signal": "none", "confidence": 0, "tp": None, "sl": None,
            "entry_tf": entry_tf, "level_map": [],
            "spot_cvd_pct": sc, "perp_cvd_pct": pc,
            "reasons": reasons,
        }

    # ── 신호 반전 (invert_signal=True) ───────────────────────────────────────
    if invert_signal:
        signal = "short" if signal == "long" else "long"
        if signal == "short":
            sl = round(entry * (1.0 + sl_pct / 100.0), 2)
        else:
            sl = round(entry * (1.0 - sl_pct / 100.0), 2)

    # ── EMA 추세 필터 ─────────────────────────────────────────────────────────
    if ema_period > 0:
        ema_v = _compute_ema(perp_df, ema_period)
        if math.isfinite(ema_v):
            if signal == "long"  and entry < ema_v:
                return _no(f"EMA 필터 미충족 (LONG) price={entry:.2f} < EMA{ema_period}={ema_v:.2f}")
            if signal == "short" and entry > ema_v:
                return _no(f"EMA 필터 미충족 (SHORT) price={entry:.2f} > EMA{ema_period}={ema_v:.2f}")

    # ── CVD 모멘텀 필터 ───────────────────────────────────────────────────────
    # Spot CVD%가 N봉 전보다 커야 다이버전스가 아직 성장 중
    if cvd_mom_lookback > 0 and len(spot_df) >= lookback + cvd_mom_lookback + 2:
        sc_prev = _compute_cvd_pct(
            spot_df.iloc[:-(cvd_mom_lookback)],
            lookback,
        )
        pc_prev = _compute_cvd_pct(
            perp_df.iloc[:-(cvd_mom_lookback)],
            lookback,
        )
        if math.isfinite(sc_prev) and math.isfinite(pc_prev):
            if signal == "long"  and not (sc > sc_prev or pc < pc_prev):
                return _no(f"CVD 모멘텀 필터 미충족 (LONG) sc={sc:.3f} sc_prev={sc_prev:.3f}")
            if signal == "short" and not (sc < sc_prev or pc > pc_prev):
                return _no(f"CVD 모멘텀 필터 미충족 (SHORT) sc={sc:.3f} sc_prev={sc_prev:.3f}")

    # ── TP 계산 ───────────────────────────────────────────────────────────────
    tp = None
    if tp_ratio > 0.0 and sl > 0:
        dist = abs(entry - sl)
        if signal == "long":
            tp = round(entry + dist * tp_ratio, 2)
        else:
            tp = round(entry - dist * tp_ratio, 2)

    return {
        "signal":         signal,
        "confidence":     1,
        "tp":             tp,
        "sl":             sl,
        "entry_tf":       entry_tf,
        "level_map":      [],
        "spot_cvd_pct":   sc,
        "perp_cvd_pct":   pc,
        "lookback":       lookback,
        "spot_threshold": spot_cvd_threshold,
        "perp_threshold": perp_cvd_threshold,
        "reasons":        reasons,
    }
