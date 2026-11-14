from __future__ import annotations

import time

from features.strategy.common.base_forward_test import BaseForwardTest, get_engine_for as _make_engine

from .exit_check import check_exit
from .signal import CFG, KEY, OPPOSITE, stops


class LsrSellCmeoiBtcCellophaneAForwardTest(BaseForwardTest):
    STRATEGY_TAG = KEY

    def _extra_db_fields(self, row):
        return {}

    def _check_exit_signal(self, position, current_price, sig, bar_high=None, bar_low=None, m1_highs=None,
                           m1_lows=None, m1_closes=None):
        out = check_exit(position, current_price)
        return out

    def tick(self, symbol, state, report_text=None):
        price = float(state["current_price"])
        sig = state["signal"]
        direction = sig["signal"] if "signal" in sig else None
        events = []
        stopped = False
        if self._position is not None:
            result = check_exit(self._position, price)
            if result is None and direction == OPPOSITE[self._position["side"]]:
                result = (price, "closed_flip", "opposite signal")
            if result is not None:
                exit_price, reason, note = result
                trade = self._close(exit_price, reason, self._position, note)
                events.append({"event": "close", "trade": trade})
                self._position = None
                stopped = reason != "closed_flip"
        if self._position is None and not stopped and direction in OPPOSITE:
            sl, tp = stops(direction, price)
            pos = {"side": direction, "entry_price": price, "entry_time": time.time(), "entry_tf": CFG["entry_tf"],
                   "confidence": 1, "tp": tp, "sl": sl, "tp_levels": [tp], "sl_levels": [sl],
                   "entry_state": str(sig["z"]), "reasons": [str(sig["z"])]}
            pos["trade_id"] = self._persist_open(symbol, pos, report_text or "")
            self._position = pos
            events.append({"event": "entry", "position": pos})
        if events:
            self._trigger_recording(symbol, events)
        out = {"events": events} if events else None
        return out


get_engine = _make_engine(LsrSellCmeoiBtcCellophaneAForwardTest)
