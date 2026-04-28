"""OI CVD Surge — 청산 로직 (fixed_rr 전용)."""
from __future__ import annotations

from typing import Any, Dict, Optional


def _f(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        x = float(v)
        return x if x == x else 0.0
    except (TypeError, ValueError):
        return 0.0


def _sl_reason(position: Dict[str, Any], sl_price: float) -> str:
    side  = position.get("side")
    entry = _f(position.get("entry_price"))
    if entry <= 0:
        return "closed_sl"
    if side == "long":
        return "closed_sl_profit" if sl_price >= entry else "closed_sl_loss"
    if side == "short":
        return "closed_sl_profit" if sl_price <= entry else "closed_sl_loss"
    return "closed_sl"


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
