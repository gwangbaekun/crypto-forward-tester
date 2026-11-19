from __future__ import annotations

from features.strategy.common.base_forward_test import BaseForwardTest, get_engine_for as _make_engine

from .signal import CFG, KEY, SIDE, stop


class QqqsevVixNas100CellophaneAForwardTest(BaseForwardTest):
    STRATEGY_TAG = KEY

    def _extra_db_fields(self, row):
        return {}

    def _check_exit_signal(self, position, current_price, sig, bar_high=None, bar_low=None, m1_highs=None,
                           m1_lows=None, m1_closes=None):
        raise RuntimeError(f"{KEY}: 청산은 tick 의 5분 봉으로만 판단한다")

    def stop_hit(self, bars):
        entry_s = self._position["entry_time"]
        lows = [b for b in bars if b[0] >= entry_s and b[3] <= self._position["sl"]]
        out = lows[0][0] if lows else None
        return out

    def tick(self, symbol, state, report_text=None):
        if state.get("intrabar"):
            raise RuntimeError(f"{KEY}: intrabar tick 을 받았다. strategies_master.yaml 의 intrabar_exit 를 확인한다")
        price = float(state["current_price"])
        sig = state["signal"]
        events = []
        stopped = False
        if self._position is not None:
            hit = self.stop_hit(state["bars"])
            if hit is not None:
                trade = self._close(self._position["sl"], "closed_sl_loss", self._position, f"bar {hit}")
                stopped = True
            elif sig["decay"]:
                trade = self._close(price, "closed_decay", self._position, str(sig["z"]))
            if hit is not None or sig["decay"]:
                events.append({"event": "close", "trade": trade})
                self._position = None
        if self._position is None and not stopped and sig["signal"] == SIDE:
            sl = stop(price)
            pos = {"side": SIDE, "entry_price": price, "entry_time": float(state["decision_s"]),
                   "entry_tf": CFG["entry_tf"], "confidence": 1, "tp": None, "sl": sl, "tp_levels": [],
                   "sl_levels": [sl], "entry_state": str(sig["z"]), "reasons": [str(sig["z"])]}
            pos["trade_id"] = self._persist_open(symbol, pos, report_text or "")
            self._position = pos
            events.append({"event": "entry", "position": pos})
        out = {"events": events} if events else None
        return out


get_engine = _make_engine(QqqsevVixNas100CellophaneAForwardTest)
