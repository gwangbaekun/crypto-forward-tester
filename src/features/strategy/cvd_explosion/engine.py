"""
CVD Explosion — Forward Test Engine.

backtest_runner.py 구조를 그대로 따름:
  1. 청산 체크 (매 tick — ws price 기준)
  2. 진입 체크 (build_state 가 새 1h 봉 마감 시에만 signal 전달, 그 외 "none")
     - tp > 0 AND sl > 0 필수 (backtest_runner 동일 조건)
     - just_closed 시 같은 tick 재진입 금지 (backtest just_closed_at 동일)
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


class CvdExplosionForwardTest(BaseForwardTest):
    STRATEGY_TAG = "cvd_explosion"

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

        # ── 1. 청산 체크 (매 tick) ──────────────────────────────────────────
        # backtest: check_exit_fn(position, current_price, sig, bar_high, bar_low)
        # forward:  bar_high/bar_low 없음 → current ws price 로 tick 단위 체크
        just_closed = False
        if self._position is not None:
            prev_adv = int(self._position.get("tp_advances") or 0)
            result = self._check_exit_signal(self._position, current_price, sig)
            if result:
                exit_price, reason, close_note = result
                trade = self._close(exit_price, reason, self._position, close_note)
                events.append({"event": "close", "trade": trade})
                self._position = None
                just_closed = True
            else:
                new_adv = int(self._position.get("tp_advances") or 0)
                if new_adv > prev_adv:
                    events.append({"event": "tp_advance", "position": dict(self._position)})

        # ── 2. 진입 체크 ────────────────────────────────────────────────────
        # backtest_runner 조건 그대로:
        #   position is None
        #   AND not just_closed  (same-bar close+entry 금지)
        #   AND direction in ("long","short")
        #   AND tp > 0 AND sl > 0
        #
        # build_state 가 새 1h 봉 마감 시에만 signal="long/short" 전달.
        # 그 외 모든 tick 은 signal="none" → 아래 조건 자연스럽게 통과 불가.
        if self._position is None and not just_closed:
            direction = sig.get("signal")
            tp = _f(sig.get("tp") or 0)
            sl = _f(sig.get("sl") or 0)

            if direction in ("long", "short") and tp > 0 and sl > 0:
                pos: Dict[str, Any] = {
                    "side":        direction,
                    "entry_price": current_price,
                    "entry_time":  time.time(),
                    "entry_tf":    sig.get("entry_tf") or "1h",
                    "confidence":  sig.get("confidence", 0),
                    "tp":          tp,
                    "sl":          sl,
                    "tp_levels":   [tp],
                    "sl_levels":   [sl],
                    "entry_state": str(sig.get("reasons", [])),
                    "level_map":   list(sig.get("level_map") or []),
                }
                # position_meta 필드 명시적 복사 — backtest_runner 와 동일 키셋
                pm = sig.get("position_meta") or {}
                for k in (
                    "tpsl_mode",
                    "tp_advances",
                    "sl_ratchet_step",
                    "sl_ratchet_buffer_pct",
                    "slippage_pct",
                    "m15_structure_stop_enabled",
                    "m15_structure_lookback_bars",
                    "m15_structure_buffer_pct",
                ):
                    if k in pm:
                        pos[k] = pm[k]

                pos["trade_id"] = self._persist_open(symbol, pos, report_text or "")
                self._position = pos
                events.append({"event": "entry", "position": pos})

        if events:
            self._trigger_recording(symbol, events)

        return {"events": events} if events else None


get_engine = _make_engine(CvdExplosionForwardTest)
