from __future__ import annotations


def check_exit(position, price):
    side, sl, tp = position["side"], position["sl"], position["tp"]
    if side == "long":
        if price <= sl:
            return sl, "closed_sl_loss", None
        if price >= tp:
            return tp, "closed_tp1", None
    else:
        if price >= sl:
            return sl, "closed_sl_loss", None
        if price <= tp:
            return tp, "closed_tp1", None
    return None
