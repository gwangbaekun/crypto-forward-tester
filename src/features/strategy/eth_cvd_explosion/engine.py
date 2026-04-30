"""
ETH CVD Explosion — Forward Test Engine.

backtest_runner.py (v1) 과 완전히 동일한 구조:
  - exit 체크: 1h 봉 마감 시 1회 (bar_high/bar_low = 완성봉 OHLC)
  - 진입 체크: exit 이후, same-bar 재진입 금지, tp>0 AND sl>0 필수
  - 봉 사이 구간에서는 build_state 가 tick 자체를 호출하지 않음
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import numpy as np

from features.strategy.common.base_forward_test import (
    BaseForwardTest,
    get_engine_for as _make_engine,
    _f,
)

from .exit_check import check_exit
from .tpsl_resolve import next_magnet_strictly_above, next_magnet_strictly_below


def _init_tp_levels(tp: float, level_map: list, side: str) -> list:
    """backtest_runner 와 동일하게 tp_levels=[tp] 로 초기화."""
    return [tp]


class EthCvdExplosionForwardTest(BaseForwardTest):
    STRATEGY_TAG = "eth_cvd_explosion"

    def _extra_db_fields(self, row: Any) -> Dict:
        return {}

    def _check_exit_signal(
        self,
        position: Dict,
        current_price: float,
        sig: Dict,
        bar_high: Optional[float] = None,
        bar_low: Optional[float] = None,
        m1_highs: Optional[np.ndarray] = None,
        m1_lows: Optional[np.ndarray] = None,
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
        current_price = state.get("current_price")
        if not current_price:
            return None

        sig: Dict[str, Any] = state.get("signal") or {}

        bar_high = float(state.get("bar_high") or current_price)
        bar_low  = float(state.get("bar_low")  or current_price)

        m1_highs = state.get("m1_highs")
        m1_lows = state.get("m1_lows")
        m1_closes = state.get("m1_closes")

        events: List[Dict[str, Any]] = []

        # ── 1. 청산 체크 ─────────────────────────────────────────────────────
        just_closed = False
        if self._position is not None:
            prev_adv = int(self._position.get("tp_advances") or 0)
            result = self._check_exit_signal(
                self._position, current_price, sig,
                bar_high=bar_high, bar_low=bar_low,
                m1_highs=m1_highs, m1_lows=m1_lows, m1_closes=m1_closes,
            )
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

        # ── 2. 진입 체크 ─────────────────────────────────────────────────────
        if self._position is None and not just_closed:
            direction = sig.get("signal")
            tp = _f(sig.get("tp") or 0)
            sl = _f(sig.get("sl") or 0)

            if direction in ("long", "short") and tp > 0 and sl > 0:
                pos: Dict[str, Any] = {
                    "side":           direction,
                    "entry_price":    current_price,
                    "entry_time":     time.time(),
                    "entry_tf":       sig.get("entry_tf") or "1h",
                    "confidence":     sig.get("confidence", 0),
                    "tp":             tp,
                    "sl":             sl,
                    "tp_levels":      _init_tp_levels(tp, list(sig.get("level_map")), direction),
                    "sl_levels":      [sl],
                    "entry_state":    str(sig.get("reasons", [])),
                    "level_map":      list(sig.get("level_map")),
                    # ── Telegram 알림용 신호 상세 ──────────────────────────
                    "reasons":        list(sig.get("reasons") or []),
                    "vol_ratio":      sig.get("vol_ratio"),
                    "cvd_accel":      sig.get("cvd_accel"),
                    "cvd_higher":     sig.get("cvd_higher"),
                    "cvd_higher_tf":  sig.get("cvd_higher_tf"),
                    "tpsl_mode_label": sig.get("tpsl_mode"),
                    "m15_support":    sig.get("m15_support"),
                    "m15_resistance": sig.get("m15_resistance"),
                    "bull_score":     sig.get("bull_score"),
                    "bear_score":     sig.get("bear_score"),
                    "max_score":      sig.get("max_score"),
                }
                # position_meta — backtest_runner 와 동일 키셋
                pm = sig.get("position_meta") or {}
                for k in (
                    "tpsl_mode",
                    "tp_advances",
                    "rr_ratio",
                    "sl_ratchet_step",
                    "sl_ratchet_buffer_pct",
                    "sl_ratchet_mode",
                    "sl_ratchet_mid_ratio",
                    "sl_lift_mode",
                    "sl_lift_min_intensity",
                    "sl_lift_rank_le",
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


get_engine = _make_engine(EthCvdExplosionForwardTest)
