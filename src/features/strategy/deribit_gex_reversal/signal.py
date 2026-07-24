"""Deribit Expiry GEX Reversal — 신호 (자체 포함, 공유 코어 없음).

메커니즘(로드맵 §1): 딜러가 숏감마(GEX<0)일 때 델타 헤지가 추세를 강제해
만기 직전(02:00→07:30 UTC) 가격을 과잉 이동시킨다. 만기 08:00 UTC 옵션 소멸 →
헤지 수요 소멸 → 되돌림. 그래서 |r_pre| 상위분위 & GEX<0 이면 과잉이동의 반대로
진입하고 r_post(12:00 UTC)에 청산한다.

GEX = Σ_contracts OI·Γ_BS(K, T, σ=mark_iv, S)·S²·0.01·sign   (콜 +1 / 풋 −1)

전부 자체 수집 deribit_chain 스냅샷으로 계산. 외부 가격피드 불필요.
실거래 없음 — 엣지 측정 전용.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

YEAR = 365.0


# ─────────────────────────── GEX (Black-Scholes, r=0) ───────────────────────────
def _norm_pdf(x: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_gamma(S, K, T, sigma) -> np.ndarray:
    S = np.asarray(S, dtype=float)
    K = np.asarray(K, dtype=float)
    T = np.asarray(T, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(S / K) + 0.5 * sigma ** 2 * T) / (sigma * np.sqrt(T))
        g = _norm_pdf(d1) / (S * sigma * np.sqrt(T))
    return np.where((T > 0) & (sigma > 0), g, 0.0)


def _snapshot_gex(snap: pd.DataFrame, spot: float, asof: pd.Timestamp, expiry_hour: int) -> float:
    """한 스냅샷 안, 특정 만기 옵션들의 누적 GEX (콜+/풋−)."""
    if snap.empty:
        return float("nan")
    exp_ts = pd.Timestamp(
        dt.datetime.combine(snap["expiry"].iloc[0], dt.time(expiry_hour)), tz="UTC"
    )
    T = (exp_ts - asof).total_seconds() / (YEAR * 86400)
    if T <= 0:
        return float("nan")
    g = _bs_gamma(spot, snap["strike"].values, T, snap["mark_iv"].values / 100.0)
    sign = np.where(snap["option_type"].values == "C", 1.0, -1.0)
    gex = snap["open_interest"].values * g * spot ** 2 * 0.01 * sign
    return float(np.nansum(gex))


# ─────────────────────────── 스냅샷 선택 ───────────────────────────
def _nearest_snap(snaps: pd.Series, day: dt.date, hh: int, mm: int, tol_min: int) -> Optional[pd.Timestamp]:
    target = pd.Timestamp(dt.datetime(day.year, day.month, day.day, hh, mm), tz="UTC")
    if snaps.empty:
        return None
    diffs = (snaps - target).abs()
    if diffs.min() <= pd.Timedelta(minutes=tol_min):
        return snaps.iloc[int(diffs.values.argmin())]
    return None


def _spot_at(df: pd.DataFrame, ts: pd.Timestamp) -> float:
    return float(df.loc[df["snapshot_ts"] == ts, "underlying_price"].median())


def _daily_rpre(df: pd.DataFrame, tol_min: int) -> pd.Series:
    """창 전체 각 날짜의 02:00→07:30 로그수익 — |r_pre| in-sample 분위 산정용."""
    snaps = df["snapshot_ts"].drop_duplicates().sort_values()
    out = {}
    for d in sorted(snaps.dt.date.unique()):
        s02 = _nearest_snap(snaps, d, 2, 0, tol_min)
        s0730 = _nearest_snap(snaps, d, 7, 30, tol_min)
        if s02 is not None and s0730 is not None:
            out[d] = math.log(_spot_at(df, s0730) / _spot_at(df, s02))
    return pd.Series(out, dtype=float)


# ─────────────────────────── 만기 분류 ───────────────────────────
def _is_last_friday(d: dt.date) -> bool:
    if d.weekday() != 4:
        return False
    return (d + dt.timedelta(days=7)).month != d.month


def _classify(d: dt.date) -> str:
    if _is_last_friday(d):
        return "monthly"
    if d.weekday() == 4:
        return "weekly"
    return "daily"


# ─────────────────────────── 신호 ───────────────────────────
def _empty_signal(reason: str) -> Dict[str, Any]:
    return {
        "signal": "none", "action": "idle", "trigger": False,
        "entry_tf": "expiry", "confidence": 0, "reasons": [reason],
        "gex_bn": None, "r_pre_bp": None, "rpre_pctl": None, "atm_pct": None,
        "expiry": None, "type": None, "entry_price": None,
        "exit_deadline_ts": None, "sl": None,
    }


def compute_signal(df: pd.DataFrame, now_ts: float, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    현재 시각(now_ts, unix sec) 기준 오늘 만기(08:00 UTC) 이벤트를 평가.

    Returns:
        {"spot": float|None, "signal": {...}, "should_tick": bool}
    """
    exp_h = params["expiry_hour_utc"]
    exit_h = params["exit_hour_utc"]
    tol = params["snap_tol_min"]
    atm_band = params["atm_band"]
    pctl_trig = params["pctl_trigger"]
    req_neg = params["require_gex_negative"]
    win_h = params["entry_window_hours"]

    now = pd.Timestamp(now_ts, unit="s", tz="UTC")
    today = now.date()

    if df is None or df.empty:
        return {"spot": None, "signal": _empty_signal("deribit_chain 비어있음 (수집 대기)"), "should_tick": False}

    spot_disp = float(df.sort_values("snapshot_ts")["underlying_price"].iloc[-1])
    snaps = df["snapshot_ts"].drop_duplicates().sort_values()

    # 오늘 만기 옵션 존재?
    todays = df[df["expiry"] == today]
    if todays.empty:
        sig = _empty_signal("오늘(08:00 UTC) 만기 없음 — 대기")
        return {"spot": spot_disp, "signal": sig, "should_tick": False}

    # ── r_pre (02:00 → 07:30) ─────────────────────────────────────────────
    s02 = _nearest_snap(snaps, today, 2, 0, tol)
    s0730 = _nearest_snap(snaps, today, 7, 30, tol)
    r_pre = (
        math.log(_spot_at(df, s0730) / _spot_at(df, s02))
        if s02 is not None and s0730 is not None else float("nan")
    )

    # ── GEX + ATM 집중도 (만기 직전 07:00 스냅샷) ─────────────────────────
    s0700 = _nearest_snap(snaps, today, 7, 0, tol)
    gex_ts = s0700 or s0730 or s02
    gex = float("nan")
    atm_pct = float("nan")
    if gex_ts is not None:
        spot_g = _spot_at(df, gex_ts)
        snap = df[(df["snapshot_ts"] == gex_ts) & (df["expiry"] == today)]
        gex = _snapshot_gex(snap, spot_g, gex_ts, exp_h)
        oi_sum = float(snap["open_interest"].sum())
        atm = snap[snap["strike"].between(spot_g * (1 - atm_band), spot_g * (1 + atm_band))]
        atm_pct = float(atm["open_interest"].sum() / oi_sum * 100) if oi_sum > 0 else float("nan")

    # ── |r_pre| in-sample 분위 (대리, 90일 아님) ─────────────────────────
    rpre_dist = _daily_rpre(df, tol)
    rpre_pctl = (
        float((rpre_dist.abs() <= abs(r_pre)).mean())
        if not math.isnan(r_pre) and len(rpre_dist) > 3 else float("nan")
    )

    etype = _classify(today)

    # ── 트리거: |r_pre| 상위분위 & (옵션) GEX<0 ──────────────────────────
    trigger = (
        (not math.isnan(rpre_pctl)) and rpre_pctl >= pctl_trig
        and (not req_neg or (not math.isnan(gex) and gex < 0))
    )
    # 리버설 방향: 과잉이동의 반대
    direction = None
    if not math.isnan(r_pre) and r_pre != 0:
        direction = "short" if r_pre > 0 else "long"

    # ── 진입 창: 08:00 ~ 08:00 + entry_window_hours ──────────────────────
    entry_start = pd.Timestamp(dt.datetime.combine(today, dt.time(exp_h)), tz="UTC")
    entry_end = entry_start + pd.Timedelta(hours=win_h)
    in_entry_window = entry_start <= now < entry_end
    exit_deadline = pd.Timestamp(dt.datetime.combine(today, dt.time(exit_h)), tz="UTC")

    # 진입 가격 = 08:00 스냅샷 spot (없으면 최신)
    s0800 = _nearest_snap(snaps, today, exp_h, 0, tol)
    entry_price = _spot_at(df, s0800) if s0800 is not None else spot_disp

    action = "idle"
    signal = "none"
    reasons = []
    if in_entry_window and trigger and direction:
        action = "entry"
        signal = direction
        reasons = [
            f"만기 {today}({etype}) 08:00 UTC 리버설",
            f"r_pre={r_pre*1e4:+.0f}bp (|분위|={rpre_pctl*100:.0f}%≥{pctl_trig*100:.0f})",
            f"GEX={gex/1e9:+.2f}$bn/1% (<0=딜러 숏감마)",
            f"→ 과잉이동 반대 {direction.upper()}, 12:00 UTC 청산",
        ]
    else:
        # 표시용 사유
        if not in_entry_window:
            reasons = [f"만기 {today}({etype}) — 진입창 밖 (08:00~{exp_h+win_h}:00 UTC)"]
        elif not trigger:
            why = []
            if math.isnan(rpre_pctl) or rpre_pctl < pctl_trig:
                why.append("과잉이동 분위 미달")
            if req_neg and not (not math.isnan(gex) and gex < 0):
                why.append("GEX≥0 (딜러 롱감마)")
            reasons = ["트리거 미발동: " + ", ".join(why or ["조건 미충족"])]

    sig = {
        "signal": signal,
        "action": action,
        "trigger": bool(trigger),
        "entry_tf": "expiry",
        "confidence": 1 if trigger else 0,
        "reasons": reasons,
        "gex_bn": None if math.isnan(gex) else round(gex / 1e9, 3),
        "r_pre_bp": None if math.isnan(r_pre) else round(r_pre * 1e4, 1),
        "rpre_pctl": None if math.isnan(rpre_pctl) else round(rpre_pctl * 100, 0),
        "atm_pct": None if math.isnan(atm_pct) else round(atm_pct, 0),
        "expiry": str(today),
        "type": etype,
        "entry_price": round(entry_price, 2) if entry_price else None,
        "exit_deadline_ts": exit_deadline.timestamp(),
        "sl": None,
    }

    # tick 발동 창: 만기시각 ~ 청산시각 (그 사이 시간대에만 엔진 tick 허용)
    should_tick = exp_h <= now.hour <= exit_h

    return {"spot": spot_disp, "signal": sig, "should_tick": should_tick}
