"""
ATR Breakout — Forward Test (v1 measured move).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from features.strategy.quant_strategies.common.base_forward_test import (
    BaseForwardTest,
    get_engine_for as _make_engine,
    _f,
)


class AtrForwardTest(BaseForwardTest):
    STRATEGY_TAG = "atr_breakout"

    def _extra_position_fields(self, sig: Dict) -> Dict:
        return {
            "box_high": sig.get("box_high"),
            "box_low": sig.get("box_low"),
        }

    def _extra_db_fields(self, row: Any) -> Dict:
        return {"box_high": None, "box_low": None}

    def _check_exit_signal(
        self, position: Dict, current_price: float, sig: Dict
    ) -> Optional[tuple]:
        side = position.get("side")
        sl = position.get("sl")
        tp = position.get("tp")
        box_high = _f(position.get("box_high") or sig.get("box_high") or 0)
        box_low = _f(position.get("box_low") or sig.get("box_low") or 0)

        if side == "long":
            if sl and current_price <= _f(sl):
                return (_f(sl), "closed_sl", None)
            if tp and current_price >= _f(tp):
                return (_f(tp), "closed_tp1", None)
            if box_low > 0 and current_price < box_low:
                return (
                    current_price,
                    "closed_false_breakout",
                    f"price {current_price:.1f} < box_low {box_low:.1f}",
                )
        else:
            if sl and current_price >= _f(sl):
                return (_f(sl), "closed_sl", None)
            if tp and current_price <= _f(tp):
                return (_f(tp), "closed_tp1", None)
            if box_high > 0 and current_price > box_high:
                return (
                    current_price,
                    "closed_false_breakout",
                    f"price {current_price:.1f} > box_high {box_high:.1f}",
                )
        return None


get_engine = _make_engine(AtrForwardTest)
