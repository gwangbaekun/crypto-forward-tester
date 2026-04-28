"""OI CVD Surge — Forward Test Engine."""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np

from features.strategy.common.base_forward_test import (
    BaseForwardTest,
    get_engine_for as _make_engine,
    _f,
)

from .exit_check import check_exit


class OiCvdSurgeForwardTest(BaseForwardTest):
    STRATEGY_TAG = "oi_cvd_surge"

    def _extra_db_fields(self, row: Any) -> Dict:
        return {}

    def _check_exit_signal(
        self,
        position: Dict,
        current_price: float,
        sig: Dict,
        bar_high: Optional[float] = None,
        bar_low:  Optional[float] = None,
        m1_highs: Optional[np.ndarray] = None,
        m1_lows:  Optional[np.ndarray] = None,
        m1_closes: Optional[np.ndarray] = None,
    ) -> Optional[tuple]:
        return check_exit(
            position, current_price, sig,
            bar_high=bar_high,
            bar_low=bar_low,
            m1_highs=m1_highs,
            m1_lows=m1_lows,
            m1_closes=m1_closes,
        )

    def tick(
        self,
        symbol: str,
        state: Dict[str, Any],
        report_text: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        import time as _time
        from typing import List

        current_price = state.get("current_price")
        if not current_price:
            return None

        sig: Dict[str, Any]   = state.get("signal") or {}
        bar_high = float(state.get("bar_high") or current_price)
        bar_low  = float(state.get("bar_low")  or current_price)

        events: List[Dict[str, Any]] = []

        # ── 청산 체크 ──────────────────────────────────────────────────────────
        just_closed = False
        if self._position is not None:
            result = self._check_exit_signal(
                self._position, current_price, sig,
                bar_high=bar_high, bar_low=bar_low,
            )
            if result:
                exit_price, reason, close_note = result
                trade = self._close(exit_price, reason, self._position, close_note)
                events.append({"event": "close", "trade": trade})
                self._position = None
                just_closed = True

        # ── 진입 체크 ──────────────────────────────────────────────────────────
        if self._position is None and not just_closed:
            direction = sig.get("signal")
            tp = _f(sig.get("tp") or 0)
            sl = _f(sig.get("sl") or 0)

            if direction in ("long", "short") and tp > 0 and sl > 0:
                pos: Dict[str, Any] = {
                    "side":        direction,
                    "entry_price": current_price,
                    "entry_time":  _time.time(),
                    "entry_tf":    "1h",
                    "confidence":  sig.get("confidence", 1),
                    "tp":          tp,
                    "sl":          sl,
                    "tp_levels":   [tp],
                    "sl_levels":   [sl],
                    "entry_state": str(sig.get("reasons", [])),
                    "reasons":     list(sig.get("reasons") or []),
                    "cvd_net":     sig.get("cvd_net"),
                    "oi_pct":      sig.get("oi_pct"),
                    "roll_high":   sig.get("roll_high"),
                    "roll_low":    sig.get("roll_low"),
                }
                pos["trade_id"] = self._persist_open(symbol, pos, report_text or "")
                self._position = pos
                events.append({"event": "entry", "position": pos})

        if events:
            self._trigger_recording(symbol, events)

        return {"events": events} if events else None


get_engine = _make_engine(OiCvdSurgeForwardTest)
