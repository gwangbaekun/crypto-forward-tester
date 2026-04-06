"""
CVD Explosion — Forward Test (btc_backtest engine 청산 로직 동일).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from features.strategy.quant_strategies.common.base_forward_test import (
    BaseForwardTest,
    get_engine_for as _make_engine,
    _f,
)

from .exit_check import check_exit


class CvdExplosionForwardTest(BaseForwardTest):
    STRATEGY_TAG = "cvd_explosion"
    _last_entry_candle_time: Optional[int] = None 

    def _extra_position_fields(self, sig: Dict) -> Dict:
        out: Dict[str, Any] = {}
        pm = sig.get("position_meta")
        if isinstance(pm, dict):
            out.update(pm)
        out["level_map"] = list(sig.get("level_map") or [])
        out["tpsl_mode"] = sig.get("tpsl_mode")
        tp = sig.get("tp")
        sl = sig.get("sl")
        if tp is not None and sl is not None:
            out["tp_levels"] = [_f(tp)]
            out["sl_levels"] = [_f(sl)]
        return out

    def _extra_db_fields(self, row: Any) -> Dict:
        return {}

    def _check_exit_signal(
        self, position: Dict, current_price: float, sig: Dict
    ) -> Optional[tuple]:
        return check_exit(position, current_price, sig)

    def tick(
        self,
        symbol: str,
        state: Dict[str, Any],
        report_text: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        current_price = state.get("current_price")
        if not current_price:
            return None

        sig: Dict[str, Any] = state.get("signal") or {}
        events: List[Dict[str, Any]] = []

        if self._position is not None:
            prev_adv = int(self._position.get("tp_advances") or 0)
            result = self._check_exit_signal(self._position, current_price, sig)
            if result:
                exit_price, reason, close_note = result
                trade = self._close(exit_price, reason, self._position, close_note)
                events.append({"event": "close", "trade": trade})
                self._position = None
            else:
                new_adv = int(self._position.get("tp_advances") or 0)
                if new_adv > prev_adv:
                    events.append({"event": "tp_advance", "position": dict(self._position)})

        if self._position is None:
            direction = sig.get("signal") or sig.get("direction")
            candle_time = sig.get("candle_time")
            already_entered = (candle_time is not None and candle_time == self._last_entry_candle_time)
            if direction in ("long", "short") and not already_entered:
                pos: Dict[str, Any] = {
                    "side":        direction,
                    "entry_price": current_price,
                    "entry_time":  time.time(),
                    "entry_tf":    sig.get("entry_tf") or "1h",
                    "confidence":  sig.get("confidence", 0),
                    "tp":          sig.get("tp"),
                    "sl":          sig.get("sl"),
                    "entry_state": str(sig.get("reasons", [])),
                }
                pos.update(self._extra_position_fields(sig))
                pos["trade_id"] = self._persist_open(symbol, pos, report_text or "")
                self._position = pos
                self._last_entry_candle_time = candle_time
                events.append({"event": "entry", "position": pos})

        if events:
            self._trigger_recording(symbol, events)

        return {"events": events} if events else None


get_engine = _make_engine(CvdExplosionForwardTest)
