"""
CVD Explosion — TP/SL 해석기 (모드별로 분기, 이후 모드 추가 시 여기만 확장)

확장 방법
---------
1. config.yaml `tpsl.mode` 에 새 문자열 등록.
2. 이 파일에 `_resolve_<mode>(...)` 구현.
3. `resolve_tpsl()` 분기에 한 줄 추가.

모드
----
- magnet     : TP/SL 모두 liq nearest (+ 선택 sl_max_pct)
- magnet_rr  : 진입 TP/SL 은 magnet 과 동일. 청산만 engine 에서 TP advance (다음 마그넷으로 TP만 이동, SL 고정).
- fixed_rr   : TP/SL 모두 진입가 기준 고정 %
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

MODE_MAGNET = "magnet"
MODE_MAGNET_RR = "magnet_rr"
MODE_FIXED_RR = "fixed_rr"


def _f(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        x = float(v)
        return x if x == x else 0.0
    except (TypeError, ValueError):
        return 0.0


def _nearest_magnet_above(level_map: List[Dict], price: float) -> Optional[float]:
    candidates = [_f(m.get("price")) for m in level_map if _f(m.get("price")) > price]
    return min(candidates) if candidates else None


def _nearest_magnet_below(level_map: List[Dict], price: float) -> Optional[float]:
    candidates = [_f(m.get("price")) for m in level_map if 0 < _f(m.get("price")) < price]
    return max(candidates) if candidates else None


def next_magnet_strictly_above(level_map: List[Dict], ref: float) -> Optional[float]:
    """진입가/이전 TP 가 아니라 ref 보다 큰 가장 가까운 마그넷 (TP advance 용)."""
    candidates = [_f(m.get("price")) for m in level_map if _f(m.get("price")) > ref]
    return min(candidates) if candidates else None


def next_magnet_strictly_below(level_map: List[Dict], ref: float) -> Optional[float]:
    candidates = [_f(m.get("price")) for m in level_map if 0 < _f(m.get("price")) < ref]
    return max(candidates) if candidates else None


def _clamp_sl_to_max_risk(
    side: str, entry: float, tp: float, sl: float, sl_max_pct: Optional[float]
) -> Tuple[Optional[float], Optional[float]]:
    if sl_max_pct is None or float(sl_max_pct) <= 0 or entry <= 0:
        return tp, sl
    if side == "long":
        floor_sl = entry * (1.0 - float(sl_max_pct) / 100.0)
        sl = max(sl, floor_sl)
        if not (sl < entry < tp):
            return None, None
    else:
        cap_sl = entry * (1.0 + float(sl_max_pct) / 100.0)
        sl = min(sl, cap_sl)
        if not (tp < entry < sl):
            return None, None
    return tp, sl


def _resolve_magnet(
    side: str, entry: float, level_map: List[Dict], params: Dict[str, Any]
) -> Tuple[Optional[float], Optional[float]]:
    """가장 가까운 마그넷을 TP/SL로 사용하되, sl_max_pct 로 최대 손실폭을 제한함."""
    if not level_map:
        return None, None
    if side == "long":
        tp = _nearest_magnet_above(level_map, entry)
        sl = _nearest_magnet_below(level_map, entry)
    else:
        tp = _nearest_magnet_below(level_map, entry)
        sl = _nearest_magnet_above(level_map, entry)
    if tp is None or sl is None:
        return None, None

    sl_max = params.get("sl_max_pct")
    tp2, sl2 = _clamp_sl_to_max_risk(side, entry, float(tp), float(sl), sl_max)
    if tp2 is None or sl2 is None:
        return None, None

    return round(tp2, 2), round(sl2, 2)


def _resolve_magnet_rr(
    side: str, entry: float, level_map: List[Dict], params: Dict[str, Any]
) -> Tuple[Optional[float], Optional[float]]:
    """magnet_rr: 순수하게 마그넷(원천 데이터)을 초기 TP/SL로 사용하고, 이후 TP advance."""
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
