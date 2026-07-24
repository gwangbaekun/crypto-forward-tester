"""Deribit Expiry GEX Reversal — Forward Test Engine (자체 포함).

만기 이벤트 생명주기:
  - 진입: 08:00 UTC 진입창 tick 에서 trigger 발동 시 리버설 방향 1회
  - 청산: exit_deadline(12:00 UTC) 경과 시 (시간기반). hard SL 은 옵션.

DB/통계/PnL 보일러플레이트는 BaseForwardTest 재사용(프레임워크). GEX·신호 로직은
이 패키지에 자체 포함되어 다른 전략과 공유하지 않는다.
실거래 없음 — 엣지 측정 전용.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from features.strategy.common.base_forward_test import (
    BaseForwardTest,
    get_engine_for as _make_engine,
    _f,
)

# 재시작 복구 시 exit_deadline 유실 대비 폴백 홀딩(초): 08:00→12:00 = 4h
_FALLBACK_HOLD_SEC = 4 * 3600


class DeribitGexReversalForwardTest(BaseForwardTest):
    STRATEGY_TAG = "deribit_gex_reversal"

    def _extra_db_fields(self, row: Any) -> Dict:
        # 재시작 복구: exit_deadline 을 진입시각 + 4h 로 재구성
        entry_ts = row.opened_at.timestamp() if row.opened_at else time.time()
        return {"exit_deadline_ts": entry_ts + _FALLBACK_HOLD_SEC}

    def _check_exit_signal(
        self, position: Dict, current_price: float, sig: Dict
    ) -> Optional[tuple]:
        side = position.get("side")
        # 1) 시간기반 청산 (r_post 12:00 UTC)
        deadline = _f(position.get("exit_deadline_ts"))
        if deadline <= 0:
            deadline = _f(position.get("entry_time")) + _FALLBACK_HOLD_SEC
        if time.time() >= deadline:
            return (current_price, "closed_time", "r_post 청산 (12:00 UTC)")
        # 2) 옵션 hard SL
        sl = position.get("sl")
        if sl:
            sl = _f(sl)
            if side == "long" and current_price <= sl:
                return (sl, "closed_sl", None)
            if side == "short" and current_price >= sl:
                return (sl, "closed_sl", None)
        return None

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

        # ── 청산 체크 ──────────────────────────────────────────────────────
        if self._position is not None:
            result = self._check_exit_signal(self._position, current_price, sig)
            if result:
                exit_price, reason, close_note = result
                trade = self._close(exit_price, reason, self._position, close_note)
                events.append({"event": "close", "trade": trade})
                self._position = None

        # ── 진입 체크 (진입창 tick + trigger 시 1회) ─────────────────────────
        # 같은 tick 에서 청산이 일어났으면 재진입 금지 (만기 이벤트는 1회).
        closed_this_tick = any(e.get("event") == "close" for e in events)
        if self._position is None and not closed_this_tick and sig.get("action") == "entry":
            direction = sig.get("signal")
            if direction in ("long", "short"):
                pos: Dict[str, Any] = {
                    "side":             direction,
                    "entry_price":      current_price,
                    "entry_time":       time.time(),
                    "entry_tf":         "expiry",
                    "confidence":       sig.get("confidence", 0),
                    "tp":               None,
                    "sl":               _f(sig.get("sl")) or None,
                    "exit_deadline_ts": _f(sig.get("exit_deadline_ts")) or (time.time() + _FALLBACK_HOLD_SEC),
                    "entry_state":      str(sig.get("reasons", [])),
                    "expiry":           sig.get("expiry"),
                    "gex_bn":           sig.get("gex_bn"),
                    "r_pre_bp":         sig.get("r_pre_bp"),
                }
                pos["trade_id"] = self._persist_open(symbol, pos, report_text or "")
                self._position = pos
                events.append({"event": "entry", "position": pos})

        if events:
            self._trigger_recording(symbol, events)

        return {"events": events} if events else None


get_engine = _make_engine(DeribitGexReversalForwardTest)
