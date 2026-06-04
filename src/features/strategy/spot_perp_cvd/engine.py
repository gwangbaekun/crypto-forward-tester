"""
Spot-Perp CVD Divergence — Forward Test Engine.

backtest engine.py 와 동일한 구조:
  - exit: 봉 마감 시 SL(OHLC 기준) / TP / Trailing SL / CVD 수렴 판정
  - entry: 봉 마감 tick에서만 (intrabar tick 진입 금지)
  - hold_bars: 봉 마감 시마다 +1 — min_hold_bars 구현용
  - hwm: high/low water mark — trailing stop 구현용
  - sl_block: SL 손절 후 CVD 중립 복귀 전까지 같은 방향 재진입 차단
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

    def __init__(self) -> None:
        super().__init__()
        self._sl_block_long:  bool = False
        self._sl_block_short: bool = False

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

        sc = sig.get("spot_cvd_pct")
        pc = sig.get("perp_cvd_pct")
        try:
            sc = float(sc) if sc is not None else None
            pc = float(pc) if pc is not None else None
        except (TypeError, ValueError):
            sc = pc = None

        events: List[Dict[str, Any]] = []

        # ── 1. 포지션 보유 중: hold_bars 증가 (봉 마감 tick에서만) ────────────
        if self._position is not None and not intrabar:
            self._position["hold_bars"] = int(self._position.get("hold_bars", 0)) + 1

        # ── 2. 청산 체크 ──────────────────────────────────────────────────────
        if self._position is not None:
            result = self._check_exit_signal(
                self._position, current_price, sig,
                bar_high=bar_high, bar_low=bar_low,
                intrabar=intrabar,
            )
            if result:
                exit_price, reason, close_note = result
                side = self._position["side"]
                trade = self._close(exit_price, reason, self._position, close_note)
                events.append({"event": "close", "trade": trade})
                # SL 손절 후 같은 방향 재진입 차단 (CVD 중립 복귀까지)
                if reason == "closed_sl_loss":
                    if side == "long":
                        self._sl_block_long  = True
                    else:
                        self._sl_block_short = True
                self._position = None

        # ── 3. SL 차단 해제 — CVD가 중립(0선) 통과하면 해제 ─────────────────
        if sc is not None and pc is not None:
            if self._sl_block_long  and (sc <= 0.0 or pc >= 0.0):
                self._sl_block_long  = False
            if self._sl_block_short and (sc >= 0.0 or pc <= 0.0):
                self._sl_block_short = False

        # ── 4. 진입 체크 (봉 마감 tick에서만, intrabar 진입 금지) ─────────────
        if self._position is None and not intrabar:
            direction = sig.get("signal")
            sl = _f(sig.get("sl") or 0)
            tp = sig.get("tp")   # None 허용

            # sl_block 중이면 진입 금지
            if direction == "long"  and self._sl_block_long:
                direction = None
            if direction == "short" and self._sl_block_short:
                direction = None

            if direction in ("long", "short") and sl > 0:
                pos: Dict[str, Any] = {
                    "side":          direction,
                    "entry_price":   current_price,
                    "entry_time":    time.time(),
                    "entry_tf":      sig.get("entry_tf") or state.get("entry_tf"),
                    "confidence":    sig.get("confidence", 0),
                    "tp":            float(tp) if tp is not None else None,
                    "sl":            sl,
                    "hold_bars":     0,      # 봉 마감마다 +1 (min_hold_bars 용)
                    "hwm":           current_price,  # high/low water mark (trailing 용)
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
