"""CVD Explosion — 청산 로직 (btc_backtest engine.check_exit 와 동일)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .signal import _f
from .tpsl_resolve import MODE_MAGNET_RR, next_magnet_strictly_above, next_magnet_strictly_below


def _check_exit_simple(
    position: Dict[str, Any],
    current_price: float,
) -> Optional[tuple]:
    side = position.get("side")
    sl = _f(position.get("sl"))
    tp = _f(position.get("tp"))

    if side == "long":
        if sl and current_price <= sl:
            return (sl, "closed_sl", None)
        if tp and current_price >= tp:
            return (tp, "closed_tp1", None)
    elif side == "short":
        if sl and current_price >= sl:
            return (sl, "closed_sl", None)
        if tp and current_price <= tp:
            return (tp, "closed_tp1", None)

    return None


def _check_exit_magnet_rr(
    position: Dict[str, Any],
    current_price: float,
    sig: Dict[str, Any],
) -> Optional[tuple]:
    side = position.get("side")
    level_map = list(sig.get("level_map") or [])
    tp = _f(position.get("tp"))
    sl = _f(position.get("sl"))

    if side == "long":
        if sl and current_price <= sl:
            return (sl, "closed_sl", None)
        if not tp or current_price < tp:
            return None
        while current_price >= tp:
            nxt = next_magnet_strictly_above(level_map, tp)
            if nxt is None:
                return (tp, "closed_tp", None)
            old_tp = tp
            position["sl"] = round(max(sl, old_tp), 2)
            sl = _f(position["sl"])
            if sl and current_price <= sl:
                return (sl, "closed_sl", None)
            if position.get("sl_levels") is not None:
                position["sl_levels"].append(sl)
            position.setdefault("tp_levels", [tp])
            position["tp"] = round(float(nxt), 2)
            position["tp_levels"].append(position["tp"])
            position["tp_advances"] = int(position.get("tp_advances") or 0) + 1
            tp = _f(position["tp"])
            if current_price < tp:
                return None
        return None

    if side == "short":
        if sl and current_price >= sl:
            return (sl, "closed_sl", None)
        if not tp or current_price > tp:
            return None
        while current_price <= tp:
            nxt = next_magnet_strictly_below(level_map, tp)
            if nxt is None:
                return (tp, "closed_tp", None)
            old_tp = tp
            position["sl"] = round(min(sl, old_tp), 2)
            sl = _f(position["sl"])
            if sl and current_price >= sl:
                return (sl, "closed_sl", None)
            if position.get("sl_levels") is not None:
                position["sl_levels"].append(sl)
            position.setdefault("tp_levels", [tp])
            position["tp"] = round(float(nxt), 2)
            position["tp_levels"].append(position["tp"])
            position["tp_advances"] = int(position.get("tp_advances") or 0) + 1
            tp = _f(position["tp"])
            if current_price > tp:
                return None
        return None

    return None


def check_exit(
    position: Dict[str, Any],
    current_price: float,
    sig: Dict[str, Any],
) -> Optional[tuple]:
    if position.get("tpsl_mode") == MODE_MAGNET_RR:
        return _check_exit_magnet_rr(position, current_price, sig)
    return _check_exit_simple(position, current_price)
