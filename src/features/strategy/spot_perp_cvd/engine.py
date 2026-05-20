"""
Spot-Perp CVD Divergence — Forward Test Engine.

backtest engine.py 와 동일한 구조:
  - exit: 봉 마감 시 SL(OHLC 기준) 또는 CVD 수렴 판정
  - entry: 봉 마감 tick에서만 (intrabar tick 진입 금지)
  - TP 없음 — CVD exit 전략
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from features.strategy.common.base_forward_test import (
    BaseForwardTest,
    get_engine_for as _make_engine,
    _f,
)

from .exit_check import check_exit


class SpotPerpCvdForwardTest(BaseForwardTest):
    STRATEGY_TAG = "spot_perp_cvd"

    def _extra_db_fields(self, row: Any) -> Dict:
        return {}

    def _check_exit_signal(
        self,
        position: Dict,
        current_price: float,
        sig: Dict,
        bar_high: Optional[float] = None,
        bar_low:  Optional[float] = None,
        intrabar: bool = False,
    ) -> Optional[tuple]:
        return check_exit(
            position, current_price, sig,
            bar_high=bar_high,
            bar_low=bar_low,
            intrabar=intrabar,
        )

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

        bar_high = float(state.get("bar_high") or current_price)
        bar_low  = float(state.get("bar_low")  or current_price)
        intrabar = bool(state.get("intrabar", False))

        events: List[Dict[str, Any]] = []

        # ── 1. 청산 체크 ───────────────────────────────────────────────────────
        if self._position is not None:
            result = self._check_exit_signal(
                self._position, current_price, sig,
                bar_high=bar_high, bar_low=bar_low,
                intrabar=intrabar,
            )
            if result:
                exit_price, reason, close_note = result
                trade = self._close(exit_price, reason, self._position, close_note)
                events.append({"event": "close", "trade": trade})
                self._position = None

        # ── 2. 진입 체크 (봉 마감 tick에서만, intrabar 진입 금지) ─────────────
        if self._position is None and not intrabar:
            direction = sig.get("signal")
            sl = _f(sig.get("sl") or 0)

            if direction in ("long", "short") and sl > 0:
                pos: Dict[str, Any] = {
                    "side":          direction,
                    "entry_price":   current_price,
                    "entry_time":    time.time(),
                    "entry_tf":      sig.get("entry_tf") or "1h",
                    "confidence":    sig.get("confidence", 0),
                    "tp":            None,
                    "sl":            sl,
                    "entry_state":   str(sig.get("reasons", [])),
                    "level_map":     [],
                    "reasons":       list(sig.get("reasons") or []),
                    "spot_cvd_pct":  sig.get("spot_cvd_pct"),
                    "perp_cvd_pct":  sig.get("perp_cvd_pct"),
                }
                pos["trade_id"] = self._persist_open(symbol, pos, report_text or "")
                self._position = pos
                events.append({"event": "entry", "position": pos})

        if events:
            self._trigger_recording(symbol, events)

        return {"events": events} if events else None


get_engine = _make_engine(SpotPerpCvdForwardTest)
