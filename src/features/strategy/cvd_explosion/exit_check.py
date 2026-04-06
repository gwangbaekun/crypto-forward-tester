"""
CVD Explosion — 청산 로직 (btc_backtest).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .signal import _f
from .tpsl_resolve import MODE_MAGNET_RR, next_magnet_strictly_above, next_magnet_strictly_below


def _check_exit_m15_structure_break(
    position: Dict[str, Any],
    current_price: float,
    sig: Dict[str, Any],
) -> Optional[tuple]:
    """진입 후 15m 중요 가격대 붕괴 시 구조 손절."""
    if not bool(position.get("m15_structure_stop_enabled", True)):
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
) -> Optional[tuple]:
    """SL/TP 단일 구간 청산 (magnet / fixed_rr)."""
    side = position.get("side")
    sl   = _f(position.get("sl"))
    tp   = _f(position.get("tp"))
    bh   = bar_high if bar_high else current_price
    bl   = bar_low  if bar_low  else current_price

    if side == "long":
        tp_hit = bool(tp and bh >= tp)
        sl_hit = bool(sl and bl <= sl)
        if tp_hit:                          # TP 우선
            return (tp, "closed_tp1", None)
        if sl_hit:
            return (sl, "closed_sl", None)
    elif side == "short":
        tp_hit = bool(tp and bl <= tp)
        sl_hit = bool(sl and bh >= sl)
        if tp_hit:
            return (tp, "closed_tp1", None)
        if sl_hit:
            return (sl, "closed_sl", None)

    return None


def _check_exit_magnet_rr(
    position: Dict[str, Any],
    current_price: float,
    sig: Dict[str, Any],
    bar_high: Optional[float] = None,
    bar_low:  Optional[float] = None,
) -> Optional[tuple]:
    """
    TP/SL 은 진입 시 magnet 과 동일(단, max loss 제한). 
    TP 터치 시 다음 마그넷이 있으면 TP 를 advance 하고, 
    SL 은 바싹 붙이지 않고 1단계 뒤처져 따라감 (첫 TP 도달 시 본절, 이후엔 직전 TP).
    """
    side      = position.get("side")
    level_map = list(sig.get("level_map") or [])
    tp        = _f(position.get("tp"))
    sl        = _f(position.get("sl"))

    bh = bar_high if bar_high else current_price
    bl = bar_low  if bar_low  else current_price

    if side == "long":
        tp_triggered = bool(tp and bh >= tp)
        sl_triggered = bool(sl and bl <= sl)

        # 보수적 백테스트: 같은 봉에서 SL과 TP를 모두 건드린 경우 SL을 먼저 건드렸다고 가정 (최악의 시나리오)
        if sl_triggered:
            return (sl, "closed_sl", None)

        if tp_triggered:
            sim_price = bh
            while sim_price >= tp:
                nxt = next_magnet_strictly_above(level_map, tp)
                if nxt is None:
                    return (tp, "closed_tp", None)
                
                # 1단계 뒤처진 SL 트레일링 (버퍼 역할)
                step = int(position.get("sl_ratchet_step", 1))
                current_tp_idx = len(position.get("tp_levels", [])) - 1
                target_idx = current_tp_idx - step
                
                if target_idx < 0:
                    # step수만큼 뒤의 TP가 없으면(예: 첫 TP 돌파 시 1-step) 진입가(본절)로 이동
                    new_sl = float(position.get("entry_price", sl))
                else:
                    new_sl = float(position["tp_levels"][target_idx])
                
                # 버퍼 퍼센트 적용 (마이크로 윅 방어)
                buf_pct = float(position.get("sl_ratchet_buffer_pct") or 0.0)
                if buf_pct > 0:
                    new_sl = new_sl * (1.0 - buf_pct / 100.0)
                
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
            
            # 루프 종료 후, 해당 봉의 종가(current_price)가 새로 올린 SL보다 낮게 끝났다면 꼬리 달고 내려온 것으로 간주하여 청산
            if sl and current_price <= sl:
                return (sl, "closed_sl", None)
            
            return None

        return None

    if side == "short":
        tp_triggered = bool(tp and bl <= tp)
        sl_triggered = bool(sl and bh >= sl)

        if sl_triggered:
            return (sl, "closed_sl", None)

        if tp_triggered:
            sim_price = bl
            while sim_price <= tp:
                nxt = next_magnet_strictly_below(level_map, tp)
                if nxt is None:
                    return (tp, "closed_tp", None)
                
                # 1단계 뒤처진 SL 트레일링 (버퍼 역할)
                step = int(position.get("sl_ratchet_step", 1))
                current_tp_idx = len(position.get("tp_levels", [])) - 1
                target_idx = current_tp_idx - step
                
                if target_idx < 0:
                    new_sl = float(position.get("entry_price", sl))
                else:
                    new_sl = float(position["tp_levels"][target_idx])
                
                buf_pct = float(position.get("sl_ratchet_buffer_pct") or 0.0)
                if buf_pct > 0:
                    new_sl = new_sl * (1.0 + buf_pct / 100.0)
                
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
                return (sl, "closed_sl", None)
            
            return None

        return None

    return None


def check_exit(
    position: Dict[str, Any],
    current_price: float,
    sig: Dict[str, Any],
    bar_high: Optional[float] = None,
    bar_low:  Optional[float] = None,
) -> Optional[tuple]:
    m15_exit = _check_exit_m15_structure_break(position, current_price, sig)
    if m15_exit is not None:
        return m15_exit
    if position.get("tpsl_mode") == MODE_MAGNET_RR:
        return _check_exit_magnet_rr(position, current_price, sig,
                                      bar_high=bar_high, bar_low=bar_low)
    return _check_exit_simple(position, current_price,
                               bar_high=bar_high, bar_low=bar_low)