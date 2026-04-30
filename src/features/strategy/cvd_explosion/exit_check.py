"""
CVD Explosion — 청산 로직 (btc_backtest).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .signal import _f
from .tpsl_resolve import MODE_MAGNET_RR, MODE_MAGNET_TP_RR, next_magnet_strictly_above, next_magnet_strictly_below

_INTENSITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]


def _find_magnet_at(level_map: List[Dict], price: float) -> Optional[Dict]:
    """price에 가장 가까운 마그넷 반환 (1.0 이내)."""
    best: Optional[Dict] = None
    best_dist = float("inf")
    for m in level_map:
        p = _f(m.get("price"))
        dist = abs(p - price)
        if dist < best_dist:
            best_dist = dist
            best = m
    return best if best_dist <= 1.0 else None


def _sl_lift_allowed(position: Dict[str, Any], tp_price: float, level_map: List[Dict]) -> bool:
    """sl_lift_mode 조건에 따라 SL을 올릴 수 있는지 판단."""
    mode = str(position.get("sl_lift_mode") or "always").strip().lower()
    if mode == "always":
        return True
    if mode == "never":
        return False
    magnet = _find_magnet_at(level_map, tp_price)
    if magnet is None:
        return True
    intensity = str(magnet.get("intensity") or "LOW").upper()
    rank = int(magnet.get("rank") or 999)
    if mode == "critical_only":
        return intensity == "CRITICAL"
    if mode == "min_intensity":
        min_i = str(position.get("sl_lift_min_intensity") or "HIGH").upper()
        min_idx = _INTENSITY_ORDER.index(min_i) if min_i in _INTENSITY_ORDER else 1
        cur_idx = _INTENSITY_ORDER.index(intensity) if intensity in _INTENSITY_ORDER else 3
        return cur_idx <= min_idx
    if mode == "rank_le":
        max_rank = int(position.get("sl_lift_rank_le") or 2)
        return rank <= max_rank
    return True


def _sl_reason(position: Dict[str, Any], sl_price: float) -> str:
    """SL 청산이 이익인지 손실인지 구분.

    래칫 이후 SL이 진입가 이상(long) / 이하(short)이면 이익 청산.
    """
    side  = position.get("side")
    entry = _f(position.get("entry_price"))
    if entry <= 0:
        return "closed_sl"
    if side == "long":
        return "closed_sl_profit" if sl_price >= entry else "closed_sl_loss"
    if side == "short":
        return "closed_sl_profit" if sl_price <= entry else "closed_sl_loss"
    return "closed_sl"


def _check_exit_m15_structure_break(
    position: Dict[str, Any],
    current_price: float,
    sig: Dict[str, Any],
) -> Optional[tuple]:
    """진입 후 15m 중요 가격대 붕괴 시 구조 손절."""
    if not bool(position.get("m15_structure_stop_enabled", False)):
        return None
    side = str(position.get("side") or "")
    px = _f(current_price)
    if px <= 0:
        return None

    buf_pct = float(position.get("m15_structure_buffer_pct") or 0.0)
    support = _f(sig.get("m15_support"))
    resistance = _f(sig.get("m15_resistance"))

    if side == "long" and support > 0:
        trigger = support * (1.0 - buf_pct / 100.0)
        if px <= trigger:
            return (
                px,
                "closed_structure_15m",
                f"15m support break: price {px:.2f} <= {trigger:.2f}",
            )
    if side == "short" and resistance > 0:
        trigger = resistance * (1.0 + buf_pct / 100.0)
        if px >= trigger:
            return (
                px,
                "closed_structure_15m",
                f"15m resistance break: price {px:.2f} >= {trigger:.2f}",
            )
    return None


def _check_exit_simple(
    position: Dict[str, Any],
    current_price: float,
    bar_high: Optional[float] = None,
    bar_low:  Optional[float] = None,
    m1_highs: Optional[Any] = None,
    m1_lows:  Optional[Any] = None,
    m1_closes: Optional[Any] = None,
) -> Optional[tuple]:
    """SL/TP 단일 구간 청산 (magnet / fixed_rr). TP 우선 (m1 resolution 있으면 먼저 터치된 쪽)."""
    side = position.get("side")
    sl   = _f(position.get("sl"))
    tp   = _f(position.get("tp"))
    bh   = bar_high if bar_high else current_price
    bl   = bar_low  if bar_low  else current_price

    if side == "long":
        tp_hit = bool(tp and bh >= tp)
        sl_hit = bool(sl and bl <= sl)
        if tp_hit and sl_hit:
            if m1_highs is not None and m1_lows is not None and len(m1_highs) > 0:
                for mh, ml in zip(m1_highs, m1_lows):
                    if sl and ml <= sl:
                        return (sl, _sl_reason(position, sl), "resolved by 1m (SL first)")
                    if tp and mh >= tp:
                        return (tp, "closed_tp1", "resolved by 1m (TP first)")
            return (tp, "closed_tp1", None)
        elif tp_hit:
            return (tp, "closed_tp1", None)
        elif sl_hit:
            return (sl, _sl_reason(position, sl), None)
    elif side == "short":
        tp_hit = bool(tp and bl <= tp)
        sl_hit = bool(sl and bh >= sl)
        if tp_hit and sl_hit:
            if m1_highs is not None and m1_lows is not None and len(m1_highs) > 0:
                for mh, ml in zip(m1_highs, m1_lows):
                    if sl and mh >= sl:
                        return (sl, _sl_reason(position, sl), "resolved by 1m (SL first)")
                    if tp and ml <= tp:
                        return (tp, "closed_tp1", "resolved by 1m (TP first)")
            return (tp, "closed_tp1", None)
        elif tp_hit:
            return (tp, "closed_tp1", None)
        elif sl_hit:
            return (sl, _sl_reason(position, sl), None)

    return None


def _check_exit_magnet_rr_single(
    position: Dict[str, Any],
    current_price: float,
    level_map: List[Dict],
    bar_high: Optional[float] = None,
    bar_low:  Optional[float] = None,
) -> Optional[tuple]:
    side      = position.get("side")
    tp        = _f(position.get("tp"))
    sl        = _f(position.get("sl"))

    bh = bar_high if bar_high else current_price
    bl = bar_low  if bar_low  else current_price

    if side == "long":
        tp_triggered = bool(tp and bh >= tp)
        sl_triggered = bool(sl and bl <= sl)

        if tp_triggered:
            sim_price = bh
            tpsl_mode = str(position.get("tpsl_mode") or "").strip().lower()
            while sim_price >= tp:
                nxt = next_magnet_strictly_above(level_map, tp)
                if nxt is None:
                    return (tp, "closed_tp", None)

                if tpsl_mode == MODE_MAGNET_TP_RR:
                    # TP advance 후 새 TP까지의 거리를 rr_ratio로 역산해 SL 재설정
                    rr = float(position.get("rr_ratio") or 0.0)
                    if rr <= 0:
                        # rr_ratio 미설정/오염 시 하드코딩 fallback 없이 현재 TP에서 종료
                        return (tp, "closed_tp", "invalid rr_ratio for magnet_tp_rr")
                    tp_hit = float(tp)
                    new_tp_dist = float(nxt) - tp_hit
                    new_sl = tp_hit - new_tp_dist / rr
                else:
                    step       = int(position.get("sl_ratchet_step", 1))
                    tp_idx     = len(position.get("tp_levels", [])) - 1
                    target_idx = tp_idx - step
                    ratchet_mode = str(position.get("sl_ratchet_mode") or "tp_sl_mid").strip().lower()
                    mid_ratio = float(position.get("sl_ratchet_mid_ratio") or 0.5)
                    mid_ratio = max(0.0, min(mid_ratio, 1.0))

                    if ratchet_mode == "tp_sl_mid":
                        tp_hit = float(tp)
                        prev_sl = float(sl)
                        new_sl = prev_sl + (tp_hit - prev_sl) * mid_ratio
                    else:
                        if target_idx < 0:
                            new_sl = float(position.get("entry_price", sl))
                        else:
                            new_sl = float(position["tp_levels"][target_idx])

                buf_pct = float(position.get("sl_ratchet_buffer_pct") or 0.0)
                if buf_pct > 0:
                    new_sl = new_sl * (1.0 - buf_pct / 100.0)

                if _sl_lift_allowed(position, float(tp), level_map):
                    position["sl"] = round(max(sl, new_sl), 2)
                    sl = _f(position["sl"])
                    if position.get("sl_levels") is not None:
                        position["sl_levels"].append(sl)
                position.setdefault("tp_levels", [tp])
                position["tp"] = round(float(nxt), 2)
                position["tp_levels"].append(position["tp"])
                position["tp_advances"] = int(position.get("tp_advances") or 0) + 1
                tp = _f(position["tp"])
                if sim_price < tp:
                    break

            # 봉 종가가 advance된 SL 아래면 같은 봉에서 SL 청산
            if sl and current_price <= sl:
                return (sl, _sl_reason(position, sl), None)

            return None

        if sl_triggered:
            return (sl, _sl_reason(position, sl), None)

        return None

    if side == "short":
        tp_triggered = bool(tp and bl <= tp)
        sl_triggered = bool(sl and bh >= sl)

        if tp_triggered:
            sim_price = bl
            tpsl_mode = str(position.get("tpsl_mode") or "").strip().lower()
            while sim_price <= tp:
                nxt = next_magnet_strictly_below(level_map, tp)
                if nxt is None:
                    return (tp, "closed_tp", None)

                if tpsl_mode == MODE_MAGNET_TP_RR:
                    rr = float(position.get("rr_ratio") or 0.0)
                    if rr <= 0:
                        return (tp, "closed_tp", "invalid rr_ratio for magnet_tp_rr")
                    tp_hit = float(tp)
                    new_tp_dist = tp_hit - float(nxt)
                    new_sl = tp_hit + new_tp_dist / rr
                else:
                    step       = int(position.get("sl_ratchet_step", 1))
                    tp_idx     = len(position.get("tp_levels", [])) - 1
                    target_idx = tp_idx - step
                    ratchet_mode = str(position.get("sl_ratchet_mode") or "tp_sl_mid").strip().lower()
                    mid_ratio = float(position.get("sl_ratchet_mid_ratio") or 0.5)
                    mid_ratio = max(0.0, min(mid_ratio, 1.0))

                    if ratchet_mode == "tp_sl_mid":
                        tp_hit = float(tp)
                        prev_sl = float(sl)
                        new_sl = prev_sl - (prev_sl - tp_hit) * mid_ratio
                    else:
                        if target_idx < 0:
                            new_sl = float(position.get("entry_price", sl))
                        else:
                            new_sl = float(position["tp_levels"][target_idx])

                buf_pct = float(position.get("sl_ratchet_buffer_pct") or 0.0)
                if buf_pct > 0:
                    new_sl = new_sl * (1.0 + buf_pct / 100.0)

                if _sl_lift_allowed(position, float(tp), level_map):
                    position["sl"] = round(min(sl, new_sl), 2)
                    sl = _f(position["sl"])
                    if position.get("sl_levels") is not None:
                        position["sl_levels"].append(sl)
                position.setdefault("tp_levels", [tp])
                position["tp"] = round(float(nxt), 2)
                position["tp_levels"].append(position["tp"])
                position["tp_advances"] = int(position.get("tp_advances") or 0) + 1
                tp = _f(position["tp"])
                if sim_price > tp:
                    break

            if sl and current_price >= sl:
                return (sl, _sl_reason(position, sl), None)

            return None

        if sl_triggered:
            return (sl, _sl_reason(position, sl), None)

        return None

    return None


def _check_exit_magnet_rr(
    position: Dict[str, Any],
    current_price: float,
    sig: Dict[str, Any],
    bar_high: Optional[float] = None,
    bar_low:  Optional[float] = None,
    m1_highs: Optional[Any] = None,
    m1_lows:  Optional[Any] = None,
    m1_closes: Optional[Any] = None,
) -> Optional[tuple]:
    level_map = list(sig.get("level_map"))
    if m1_highs is not None and m1_lows is not None and m1_closes is not None and len(m1_highs) > 0:
        for mh, ml, mc in zip(m1_highs, m1_lows, m1_closes):
            res = _check_exit_magnet_rr_single(position, mc, level_map, mh, ml)
            if res:
                return res
        return None
    else:
        return _check_exit_magnet_rr_single(position, current_price, level_map, bar_high, bar_low)


def check_exit(
    position: Dict[str, Any],
    current_price: float,
    sig: Dict[str, Any],
    bar_high: Optional[float] = None,
    bar_low:  Optional[float] = None,
    m1_highs: Optional[Any] = None,
    m1_lows:  Optional[Any] = None,
    m1_closes: Optional[Any] = None,
) -> Optional[tuple]:
    m15_exit = _check_exit_m15_structure_break(position, current_price, sig)
    if m15_exit is not None:
        return m15_exit
    if position.get("tpsl_mode") in (MODE_MAGNET_RR, MODE_MAGNET_TP_RR):
        return _check_exit_magnet_rr(position, current_price, sig,
                                      bar_high=bar_high, bar_low=bar_low,
                                      m1_highs=m1_highs, m1_lows=m1_lows, m1_closes=m1_closes)
    return _check_exit_simple(position, current_price,
                               bar_high=bar_high, bar_low=bar_low,
                               m1_highs=m1_highs, m1_lows=m1_lows, m1_closes=m1_closes)
