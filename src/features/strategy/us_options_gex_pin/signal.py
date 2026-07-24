"""US Options Expiry GEX Pinning — 신호 (자체 포함, 공유 코어 없음).

메커니즘(로드맵 §부활조건부, 옵션 핀닝): 딜러가 롱감마(GEX>0)면 델타 헤지가
가격 변동을 억제해 만기 근처에서 현물이 최대 OI 행사가(pin)로 끌린다. 그래서
만기 D-N 이내 & GEX>0 & 현물이 pin 에서 충분히 떨어져 있으면 pin 방향으로 회귀
진입, 만기 종가(20:00 UTC)에 청산.

일일 해상도(하루 1스냅샷)라 Deribit 의 일중 리버설은 불가 — 이건 다른 현상(핀닝).
GEX = Σ OI·gamma·mult·S²·0.01·sign  (콜 +1 / 풋 −1). gamma 는 수집 데이터 제공값.

전부 자체 수집 us_options_chain 으로 계산. 실거래 없음 — 엣지 측정 전용.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Dict

import numpy as np
import pandas as pd


def _empty_signal(reason: str) -> Dict[str, Any]:
    return {
        "signal": "none", "action": "idle", "trigger": False,
        "entry_tf": "expiry", "confidence": 0, "reasons": [reason],
        "gex_bn": None, "gex_regime": None, "pin_strike": None,
        "distance_pct": None, "days_to_exp": None, "expiry": None,
        "entry_price": None, "exit_deadline_ts": None, "sl": None,
    }


def compute_signal(df: pd.DataFrame, now_ts: float, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    현재 시각(now_ts, unix sec) 기준 최근 스냅샷의 최근접 만기 핀닝을 평가.

    Returns:
        {"spot": float|None, "signal": {...}, "should_tick": bool}
    """
    max_days = params["max_days_to_expiry"]
    min_dist = params["min_distance_pct"]
    req_pos = params["require_gex_positive"]
    mult = params["contract_multiplier"]
    exit_h = params["exit_hour_utc"]

    now = pd.Timestamp(now_ts, unit="s", tz="UTC")
    today = now.date()

    if df is None or df.empty:
        return {"spot": None, "signal": _empty_signal("us_options_chain 비어있음 (수집 대기)"), "should_tick": False}

    # ── 최신 스냅샷 ──────────────────────────────────────────────────────────
    latest_ts = df["snapshot_ts"].max()
    snap = df[df["snapshot_ts"] == latest_ts].copy()
    spot = float(snap["underlying_price"].median())
    if not spot or math.isnan(spot):
        return {"spot": None, "signal": _empty_signal("underlying_price 없음"), "should_tick": False}

    # ── 최근접(미도래) 만기 ─────────────────────────────────────────────────
    future_exp = sorted([e for e in snap["expiry"].dropna().unique() if e >= today])
    if not future_exp:
        return {"spot": spot, "signal": _empty_signal("미도래 만기 없음"), "should_tick": False}
    E = future_exp[0]
    days_to_exp = (E - today).days
    exp_snap = snap[snap["expiry"] == E]

    # ── max-OI 행사가 (pin) : 콜+풋 OI 합 최대 ──────────────────────────────
    oi_by_strike = exp_snap.groupby("strike")["open_interest"].sum()
    if oi_by_strike.empty or oi_by_strike.max() <= 0:
        return {"spot": spot, "signal": _empty_signal(f"만기 {E} OI 없음"), "should_tick": False}
    pin_strike = float(oi_by_strike.idxmax())
    distance_pct = (spot - pin_strike) / spot * 100.0

    # ── GEX (콜+/풋−, 제공 gamma 사용) ──────────────────────────────────────
    g = exp_snap["gamma"].fillna(0.0).values
    oi = exp_snap["open_interest"].fillna(0.0).values
    sign = np.where(exp_snap["option_type"].values == "C", 1.0, -1.0)
    gex = float(np.nansum(oi * g * mult * spot ** 2 * 0.01 * sign))
    gex_regime = "GEX>0(롱감마·핀닝)" if gex > 0 else "GEX<0(숏감마·반핀닝)"

    # ── 트리거: 만기 임박 & GEX>0 & 핀에서 충분히 이탈 ───────────────────────
    trigger = (
        0 <= days_to_exp <= max_days
        and (not req_pos or gex > 0)
        and abs(distance_pct) >= min_dist
    )
    # 회귀 방향: 현물이 핀보다 높으면 하락 회귀(short), 낮으면 상승 회귀(long)
    direction = "short" if distance_pct > 0 else "long"

    exit_deadline = pd.Timestamp(dt.datetime.combine(E, dt.time(exit_h)), tz="UTC")

    action = "idle"
    signal = "none"
    reasons = []
    if trigger:
        action = "entry"
        signal = direction
        reasons = [
            f"만기 {E} (D-{days_to_exp}) 핀닝",
            f"pin(max-OI)={pin_strike:.1f}, 현물={spot:.1f} → 이격 {distance_pct:+.2f}%",
            f"{gex_regime}, GEX={gex/1e9:+.2f}$bn/1%",
            f"→ 핀 방향 {direction.upper()}, 만기 {exit_h}:00 UTC 청산",
        ]
    else:
        why = []
        if not (0 <= days_to_exp <= max_days):
            why.append(f"만기 D-{days_to_exp} (>{max_days})")
        if req_pos and not gex > 0:
            why.append("GEX≤0 (반핀닝 레짐)")
        if abs(distance_pct) < min_dist:
            why.append(f"핀 이격 {distance_pct:+.2f}% (<{min_dist}%)")
        reasons = ["트리거 미발동: " + ", ".join(why or ["조건 미충족"])]

    sig = {
        "signal": signal,
        "action": action,
        "trigger": bool(trigger),
        "entry_tf": "expiry",
        "confidence": 1 if trigger else 0,
        "reasons": reasons,
        "gex_bn": round(gex / 1e9, 3),
        "gex_regime": gex_regime,
        "pin_strike": round(pin_strike, 2),
        "distance_pct": round(distance_pct, 2),
        "days_to_exp": days_to_exp,
        "expiry": str(E),
        "entry_price": round(spot, 2),
        "exit_deadline_ts": exit_deadline.timestamp(),
        "sl": None,
    }

    # tick 발동: 최신 스냅샷이 오늘 것이면 (하루 1회 평가). 재진입은 엔진/피드가 dedupe.
    should_tick = (latest_ts.date() == today)

    return {"spot": spot, "signal": sig, "should_tick": should_tick}
