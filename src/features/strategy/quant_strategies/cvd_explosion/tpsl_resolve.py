"""
CVD Explosion — TP/SL 해석기 (btc_backtest 와 동일)

변경 이력:
- intensity-aware 존 선택: oi_liq_map.py 의 rank/intensity 분류를 TP/SL 선택에 반영.
  CRITICAL > HIGH > MEDIUM > LOW 순으로 우선 선택, 같은 intensity 내에서는 가격 근접도.
- fixed_rr 자동 폴백: level_map 이 비거나 양측 magnet 을 찾지 못한 경우 fixed_rr 로 fallback.
  "언제 어느 상황이든 TP/SL 은 있다"는 설계 원칙 보장.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

MODE_MAGNET = "magnet"
MODE_MAGNET_RR = "magnet_rr"
MODE_FIXED_RR = "fixed_rr"

_INTENSITY_PRIORITY: Dict[str, int] = {
    "CRITICAL": 0,
    "HIGH":     1,
    "MEDIUM":   2,
    "LOW":      3,
}


def _f(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        x = float(v)
        return x if x == x else 0.0
    except (TypeError, ValueError):
        return 0.0


# ── 가격 기반 헬퍼 (exit_check.py 의 TP advance 에서 사용 — 변경 없음) ──────────

def next_magnet_strictly_above(level_map: List[Dict], ref: float) -> Optional[float]:
    candidates = [_f(m.get("price")) for m in level_map if _f(m.get("price")) > ref]
    return min(candidates) if candidates else None


def next_magnet_strictly_below(level_map: List[Dict], ref: float) -> Optional[float]:
    candidates = [_f(m.get("price")) for m in level_map if 0 < _f(m.get("price")) < ref]
    return max(candidates) if candidates else None


# ── intensity-aware 존 선택 (초기 TP/SL 결정에 사용) ─────────────────────────

def _best_magnet_above(level_map: List[Dict], price: float) -> Optional[float]:
    """
    price 위의 존 중 가장 중요한 것을 반환.
    우선순위: intensity(CRITICAL → LOW) → 가격 근접도(가까운 것 우선).
    """
    candidates = [m for m in level_map if _f(m.get("price")) > price]
    if not candidates:
        return None
    candidates.sort(key=lambda m: (
        _INTENSITY_PRIORITY.get(m.get("intensity") or "", 4),
        _f(m.get("price")) - price,
    ))
    return _f(candidates[0].get("price"))


def _best_magnet_below(level_map: List[Dict], price: float) -> Optional[float]:
    """
    price 아래의 존 중 가장 중요한 것을 반환.
    우선순위: intensity(CRITICAL → LOW) → 가격 근접도(가까운 것 우선).
    """
    candidates = [m for m in level_map if 0 < _f(m.get("price")) < price]
    if not candidates:
        return None
    candidates.sort(key=lambda m: (
        _INTENSITY_PRIORITY.get(m.get("intensity") or "", 4),
        price - _f(m.get("price")),
    ))
    return _f(candidates[0].get("price"))


def _clamp_sl_to_max_risk(
    side: str, entry: float, tp: float, sl: float, sl_max_pct: Optional[float]
) -> Tuple[Optional[float], Optional[float]]:
    if sl_max_pct is None or entry <= 0:
        return tp, sl
    if side == "long":
        floor_sl = entry * (1.0 - sl_max_pct / 100.0)
        sl = max(sl, floor_sl)
        if not (sl < entry < tp):
            return None, None
    else:
        cap_sl = entry * (1.0 + sl_max_pct / 100.0)
        sl = min(sl, cap_sl)
        if not (tp < entry < sl):
            return None, None
    return tp, sl


def _resolve_magnet(
    side: str, entry: float, level_map: List[Dict], params: Dict[str, Any]
) -> Tuple[Optional[float], Optional[float]]:
    """
    intensity-aware magnet 선택.
    level_map 이 비거나 magnet 을 찾지 못하면 fixed_rr 로 자동 폴백.
    """
    if not level_map:
        return _resolve_fixed_rr(side, entry, level_map, params)

    if side == "long":
        tp = _best_magnet_above(level_map, entry)
        sl = _best_magnet_below(level_map, entry)
    else:
        tp = _best_magnet_below(level_map, entry)
        sl = _best_magnet_above(level_map, entry)

    if tp is None or sl is None:
        # 한쪽 방향에 magnet 이 없는 엣지 케이스 → fixed_rr 폴백
        return _resolve_fixed_rr(side, entry, level_map, params)

    tp_f, sl_f = float(tp), float(sl)
    sl_max = params.get("sl_max_pct")
    tp2, sl2 = _clamp_sl_to_max_risk(side, entry, tp_f, sl_f, sl_max)
    if tp2 is None or sl2 is None:
        # sl_max_pct 클램핑 실패 → 클램핑 없이 원본 magnet 값 사용
        if (side == "long" and sl_f < entry < tp_f) or (side == "short" and tp_f < entry < sl_f):
            return round(tp_f, 2), round(sl_f, 2)
        return _resolve_fixed_rr(side, entry, level_map, params)

    return round(tp2, 2), round(sl2, 2)


def _resolve_magnet_rr(
    side: str, entry: float, level_map: List[Dict], params: Dict[str, Any]
) -> Tuple[Optional[float], Optional[float]]:
    return _resolve_magnet(side, entry, level_map, params)


def _resolve_fixed_rr(
    side: str, entry: float, _level_map: List[Dict], params: Dict[str, Any]
) -> Tuple[Optional[float], Optional[float]]:
    rr = float(params.get("rr_ratio") or 1.5)
    risk_pct = float(params.get("risk_pct") or 1.0)
    if entry <= 0 or rr <= 0 or risk_pct <= 0:
        return None, None
    risk = entry * (risk_pct / 100.0)
    reward = risk * rr
    if side == "long":
        sl = entry - risk
        tp = entry + reward
    else:
        sl = entry + risk
        tp = entry - reward
    if side == "long" and not (sl < entry < tp):
        return None, None
    if side == "short" and not (tp < entry < sl):
        return None, None
    return round(tp, 2), round(sl, 2)


def resolve_tpsl(
    side: str,
    entry: float,
    level_map: List[Dict],
    params: Dict[str, Any],
) -> Tuple[Optional[float], Optional[float]]:
    mode = str(params.get("mode") or MODE_MAGNET).strip().lower()
    if mode == MODE_FIXED_RR:
        return _resolve_fixed_rr(side, entry, level_map, params)
    if mode == MODE_MAGNET_RR:
        return _resolve_magnet_rr(side, entry, level_map, params)
    return _resolve_magnet(side, entry, level_map, params)


def tpsl_mode_label(params: Dict[str, Any]) -> str:
    return str(params.get("mode") or MODE_MAGNET).strip().lower()
