"""
OI-Based Liquidation Map — Binance OI 데이터 기반 근사 (btc_backtest data/oi_liq_map.py 와 동일 로직).
"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

LEVERAGE_DISTRIBUTION = {
    2: 0.03,
    3: 0.05,
    5: 0.10,
    10: 0.20,
    20: 0.20,
    25: 0.15,
    50: 0.12,
    75: 0.07,
    100: 0.05,
    125: 0.03,
}

_BINANCE_MMR_TIERS = [
    (50_000, 0.004),
    (250_000, 0.005),
    (1_000_000, 0.010),
    (5_000_000, 0.025),
    (20_000_000, 0.050),
    (50_000_000, 0.100),
    (100_000_000, 0.125),
    (200_000_000, 0.150),
    (float("inf"), 0.250),
]


def _get_mmr(notional_usd: float = 50_000) -> float:
    for threshold, rate in _BINANCE_MMR_TIERS:
        if notional_usd <= threshold:
            return rate
    return 0.25


def _calc_liq_price(entry: float, leverage: int, side: str) -> float:
    mmr = _get_mmr()
    if side == "long":
        return entry * (1 - 1 / leverage + mmr)
    return entry * (1 + 1 / leverage - mmr)


_ZONE_HALF_WIDTH_PCT = 0.004
_CLUSTER_PCT = 0.008
_OI_DELTA_QUANTILE = 0.70
_INTENSITY_THRESHOLDS = [
    ("CRITICAL", 0.25),
    ("HIGH", 0.12),
    ("MEDIUM", 0.06),
    ("LOW", 0.0),
]
_TOP_N_ZONES = 5


def _empty(current_price: float) -> Dict[str, Any]:
    return {"long_liq_zones": [], "short_liq_zones": [], "current_price": current_price, "method": "oi_derived"}


def build_oi_liq_map(
    bars: List[Dict[str, Any]],
    current_price: float,
    min_bars: int = 50,
) -> Dict[str, Any]:
    empty = _empty(current_price)
    if not bars or len(bars) < min_bars:
        return empty

    valid = [
        b
        for b in bars
        if (b.get("oi") or 0) > 0 and b.get("oi_delta") is not None and b.get("cvd_delta") is not None
    ]
    if len(valid) < min_bars:
        return empty

    deltas = np.array([b["oi_delta"] for b in valid], dtype=float)
    threshold = float(np.quantile(deltas[deltas > 0], _OI_DELTA_QUANTILE)) if np.any(deltas > 0) else 0.0
    accumulation_bars = [b for b in valid if (b.get("oi_delta") or 0) > threshold]
    if not accumulation_bars:
        return empty

    total_oi_weight = sum(float(b["oi_delta"]) for b in accumulation_bars)
    if total_oi_weight <= 0:
        return empty

    liq_points: List[Dict[str, Any]] = []
    for b in accumulation_bars:
        entry = (
            float(b.get("high", 0) or 0) + float(b.get("low", 0) or 0) + float(b.get("close", 0) or 0)
        ) / 3.0
        if entry <= 0:
            entry = float(b.get("close") or 0)
        if entry <= 0:
            continue

        oi_wt = float(b["oi_delta"])
        cvd = float(b.get("cvd_delta") or 0)
        cvd_abs = abs(cvd)
        cvd_norm = min(cvd_abs / (float(b.get("volume") or 1) + 1e-9), 1.0)
        long_ratio = 0.5 + 0.5 * (cvd_norm if cvd > 0 else -cvd_norm)
        short_ratio = 1.0 - long_ratio

        for lev, lev_wt in LEVERAGE_DISTRIBUTION.items():
            combined_wt = oi_wt * lev_wt
            long_liq = _calc_liq_price(entry, lev, "long")
            if long_liq > 0:
                liq_points.append({"liq_price": long_liq, "weight": combined_wt * long_ratio, "side": "long"})
            short_liq = _calc_liq_price(entry, lev, "short")
            liq_points.append({"liq_price": short_liq, "weight": combined_wt * short_ratio, "side": "short"})

    long_liq_pts = [p for p in liq_points if p["side"] == "long" and p["liq_price"] < current_price]
    short_liq_pts = [p for p in liq_points if p["side"] == "short" and p["liq_price"] > current_price]

    long_liq_zones = _cluster_and_rank(long_liq_pts, current_price, total_oi_weight, top_n=_TOP_N_ZONES)
    short_liq_zones = _cluster_and_rank(short_liq_pts, current_price, total_oi_weight, top_n=_TOP_N_ZONES)

    return {
        "long_liq_zones": long_liq_zones,
        "short_liq_zones": short_liq_zones,
        "current_price": current_price,
        "method": "oi_derived",
    }


def _cluster_and_rank(
    points: List[Dict[str, Any]],
    current_price: float,
    total_oi_weight: float,
    top_n: int = 5,
) -> List[Dict[str, Any]]:
    if not points:
        return []

    points_sorted = sorted(points, key=lambda p: abs(p["liq_price"] - current_price))
    clusters: List[Dict[str, Any]] = []
    for pt in points_sorted:
        lp = float(pt["liq_price"])
        wt = float(pt["weight"])
        merged = False
        for c in clusters:
            if abs(lp - c["center"]) / c["center"] <= _CLUSTER_PCT:
                new_w = c["weight"] + wt
                c["center"] = (c["center"] * c["weight"] + lp * wt) / new_w if new_w > 0 else c["center"]
                c["weight"] += wt
                c["count"] += 1
                merged = True
                break
        if not merged:
            clusters.append({"center": lp, "weight": wt, "count": 1})

    clusters.sort(key=lambda c: c["weight"], reverse=True)
    top = clusters[:top_n]
    total_w = sum(c["weight"] for c in clusters) or 1.0

    zones = []
    for rank, c in enumerate(top, 1):
        ratio = c["weight"] / total_w
        hw = c["center"] * _ZONE_HALF_WIDTH_PCT
        intensity = _classify_intensity(ratio)
        zones.append(
            {
                "rank": rank,
                "price_low": round(c["center"] - hw, 2),
                "price_high": round(c["center"] + hw, 2),
                "intensity": intensity,
                "oi_weight": round(c["weight"], 4),
                "count": c["count"],
            }
        )
    return zones


def _classify_intensity(ratio: float) -> str:
    for name, threshold in _INTENSITY_THRESHOLDS:
        if ratio >= threshold:
            return name
    return "LOW"


def compute_direction(long_liq_zones: List[Dict], short_liq_zones: List[Dict]) -> Dict[str, Any]:
    score = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}

    def weighted_score(zones: List[Dict]) -> float:
        s = 0.0
        for i, z in enumerate(zones):
            s += score.get(z.get("intensity", "LOW"), 0) / (i + 1)
        return round(s, 3)

    down = weighted_score(long_liq_zones)
    up = weighted_score(short_liq_zones)
    if down == up:
        bias, ratio = "NEUTRAL", 1.0
    elif down > up:
        bias, ratio = "SHORT", round(down / (up + 1e-9), 3)
    else:
        bias, ratio = "LONG", round(up / (down + 1e-9), 3)
    return {"down_strength": down, "up_strength": up, "bias": bias, "ratio": ratio}
