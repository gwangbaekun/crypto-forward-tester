"""
Spot-Perp CVD Divergence — Signal Function.

Signal:
  LONG:  spot_cvd_pct >= spot_threshold  AND  perp_cvd_pct <= -perp_threshold
         (현물 매수압력 강함 / 선물 매도압력 강함 → 가격 반등)

  SHORT: spot_cvd_pct <= -spot_threshold  AND  perp_cvd_pct >= perp_threshold
         (현물 매도압력 강함 / 선물 매수(헤지)압력 강함 → 가격 하락)

CVD%:
  rolling_sum(cvd_delta, lookback) / rolling_sum(volume, lookback) * 100
  — lookback개의 완성봉 기준 (형성 중인 봉 제외) → backtest shift(1) 동일

Exit:
  CVD 수렴 (다이버전스 해소) 또는 hard SL  (TP 없음)

진입 시점:
  봉 마감 60초 전 forming 봉 close 기준 — look-ahead bias 없음
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


def compute_signal(
    perp_df: Optional[pd.DataFrame],
    spot_df: Optional[pd.DataFrame],
    signal_overrides: Optional[Dict[str, Any]] = None,
    tpsl_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Spot-Perp CVD 다이버전스 신호 계산.

    Parameters
    ----------
    perp_df : Binance Futures klines (open_time_ms, open/high/low/close, volume, cvd_delta)
    spot_df : Binance Spot klines    (open_time_ms, volume, cvd_delta)

    Returns
    -------
    signal: "long" | "short" | "none"
    sl: float | None   (TP 없음 — CVD exit 전략)
    spot_cvd_pct, perp_cvd_pct: float  (exit_check에서 CVD 수렴 판정용)
    """
    sp = dict(get_signal_params())
    tp_p = dict(get_tpsl_params())
    if signal_overrides:
        for k, cast in [("lookback", int), ("spot_cvd_threshold", float), ("perp_cvd_threshold", float)]:
            if k in signal_overrides:
                sp[k] = cast(signal_overrides[k])
    if tpsl_overrides:
        tp_p.update(tpsl_overrides)

    lookback  = int(sp["lookback"])
    spot_thr  = float(sp["spot_cvd_threshold"])
    perp_thr  = float(sp["perp_cvd_threshold"])
    sl_pct    = float(tp_p.get("sl_pct", 2.0))

    def _no(reason: str) -> Dict[str, Any]:
        return {
            "signal": "none", "confidence": 0, "tp": None, "sl": None,
            "entry_tf": "1h", "level_map": [],
            "spot_cvd_pct": None, "perp_cvd_pct": None,
            "reasons": [reason],
        }

    if perp_df is None or perp_df.empty or len(perp_df) < 2:
        return _no("perp 데이터 없음")
    if spot_df is None or spot_df.empty:
        return _no("spot 데이터 없음")

    spot_cvd_pct = _compute_cvd_pct(spot_df, lookback)
    perp_cvd_pct = _compute_cvd_pct(perp_df, lookback)

    if not math.isfinite(spot_cvd_pct) or not math.isfinite(perp_cvd_pct):
        return _no(f"CVD% 산출 불가 — spot={spot_cvd_pct:.3g} perp={perp_cvd_pct:.3g}")

    # 진입가 = forming 봉 close (pre-entry price)
    entry = _f(perp_df.iloc[-1]["close"])
    if entry <= 0:
        return _no("forming 봉 가격 없음")

    sc = round(spot_cvd_pct, 3)
    pc = round(perp_cvd_pct, 3)
    reasons: List[str] = []
    signal = "none"
    sl = 0.0

    # LONG: 현물 강한 매수 + 선물 매도 다이버전스
    if sc >= spot_thr and pc <= -perp_thr:
        sl = round(entry * (1.0 - sl_pct / 100.0), 2)
        signal = "long"
        reasons = [
            f"[SPOT CVD] {sc:.3f}% >= {spot_thr}%",
            f"[PERP CVD] {pc:.3f}% <= -{perp_thr}%",
            f"[진입] LONG  entry={entry:.2f}  SL={sl:.2f}  (max {sl_pct}% / CVD exit)",
        ]

    # SHORT: 현물 강한 매도 + 선물 매수(헤지) 다이버전스
    elif sc <= -spot_thr and pc >= perp_thr:
        sl = round(entry * (1.0 + sl_pct / 100.0), 2)
        signal = "short"
        reasons = [
            f"[SPOT CVD] {sc:.3f}% <= -{spot_thr}%",
            f"[PERP CVD] {pc:.3f}% >= {perp_thr}%",
            f"[진입] SHORT entry={entry:.2f}  SL={sl:.2f}  (max {sl_pct}% / CVD exit)",
        ]

    else:
        reasons.append(
            f"[대기] spot_cvd={sc:.3f}% perp_cvd={pc:.3f}%  "
            f"(thr: spot±{spot_thr} / perp±{perp_thr})"
        )

    return {
        "signal":         signal,
        "confidence":     1 if signal != "none" else 0,
        "tp":             None,
        "sl":             sl if signal != "none" else None,
        "entry_tf":       "1h",
        "level_map":      [],
        "spot_cvd_pct":   sc,
        "perp_cvd_pct":   pc,
        "lookback":       lookback,
        "spot_threshold": spot_thr,
        "perp_threshold": perp_thr,
        "reasons":        reasons,
    }
