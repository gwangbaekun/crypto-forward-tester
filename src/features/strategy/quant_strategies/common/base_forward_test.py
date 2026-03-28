"""
BaseForwardTest — 전략 Forward Test 추상 기반 클래스.

서브클래스에서 구현 필수:
    STRATEGY_TAG: str
    _check_exit_signal(position, current_price, sig) -> Optional[tuple]

선택적 오버라이드:
    _extra_position_fields(sig) -> dict   # 진입 시 position에 추가할 전략별 필드
    _extra_db_fields(row) -> dict         # DB 로드 시 position에 추가할 전략별 필드

사용 예:

    from features.strategy.quant_strategies.common.base_forward_test import BaseForwardTest, get_engine_for, _f

    class AtrForwardTest(BaseForwardTest):
        STRATEGY_TAG = "atr_breakout"

        def _check_exit_signal(self, position, current_price, sig):
            side = position.get("side")
            sl, tp = position.get("sl"), position.get("tp")
            if side == "long":
                if sl and current_price <= _f(sl): return (_f(sl), "closed_sl", None)
                if tp and current_price >= _f(tp): return (_f(tp), "closed_tp1", None)
            else:
                if sl and current_price >= _f(sl): return (_f(sl), "closed_sl", None)
                if tp and current_price <= _f(tp): return (_f(tp), "closed_tp1", None)
            return None

    get_engine = get_engine_for(AtrForwardTest)
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional


# ── 공용 헬퍼 ───────────────────────────────────────────────────────────────

def _f(v: Any) -> float:
    """None-safe float 변환."""
    if v is None or v == "":
        return 0.0
    try:
        x = float(v)
        return x if x == x else 0.0
    except (TypeError, ValueError):
        return 0.0


# ── 추상 기반 클래스 ─────────────────────────────────────────────────────────

class BaseForwardTest(ABC):
    """
    Forward Test Engine 공통 기반.

    보일러플레이트 (DB/tick/stats/PnL) 를 모두 여기서 처리하므로
    서브클래스는 _check_exit_signal() 만 구현하면 됨.
    """

    STRATEGY_TAG: str = ""   # 서브클래스에서 반드시 선언
    LEVERAGE:     float = 1.0  # 레버리지 (equity ROI 계산용). Binance live 전략에서 오버라이드.

    def __init__(self) -> None:
        self._position: Optional[Dict[str, Any]] = None
        self._closed_trades: List[Dict[str, Any]] = []
        try:
            self._position = self._load_open_from_db()
        except Exception as e:
            print(f"[{self.STRATEGY_TAG} FT] load_open skipped: {e}")

    # ── 서브클래스 구현 필수 ───────────────────────────────────────────────

    @abstractmethod
    def _check_exit_signal(
        self, position: Dict, current_price: float, sig: Dict
    ) -> Optional[tuple]:
        """
        청산 조건 확인.

        Returns:
            (exit_price, reason_str, close_note_or_None)  또는  None
        """

    # ── 선택적 오버라이드 ──────────────────────────────────────────────────

    def _extra_position_fields(self, sig: Dict) -> Dict:
        """진입 시 position dict에 추가할 전략별 필드. 기본: 없음."""
        return {}

    def _extra_db_fields(self, row: Any) -> Dict:
        """DB 로드 시 position dict에 추가할 전략별 필드. 기본: 없음."""
        return {}

    def reset_edge_halt(self) -> None:
        """엣지 검증 중단 해제. 기본: no-op (Renaissance 등 서브클래스에서 오버라이드)."""

    async def sync_from_binance(self, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        """
        Binance 실제 포지션 ↔ 모듈 상태 강제 동기화.

        서버 재시작 후 모듈이 포지션을 잃었을 때 수동 복구용.
        모든 전략에서 공통으로 사용 가능.

        Returns:
            action: "db_restored" | "injected" | "cleared" | "already_clean"
        """
        result: Dict[str, Any] = {
            "strategy":         self.STRATEGY_TAG,
            "symbol":           symbol,
            "before":           self._position,
            "binance_position": None,
            "action":           "none",
        }

        # ── Binance 포지션 조회 ─────────────────────────────────────
        try:
            from common.binance_executor import get_executor
            ex = get_executor()
            if not ex:
                result["error"] = "executor 없음 (API 키 미설정)"
                return result
            binance_pos = await ex.get_position(symbol)
            result["binance_position"] = binance_pos
        except Exception as e:
            result["error"] = f"Binance 조회 실패: {e}"
            return result

        pos_amt = float((binance_pos or {}).get("positionAmt", 0))

        # ── Binance에 포지션 없음 ──────────────────────────────────
        if pos_amt == 0:
            if self._position:
                self._position = None
                result["action"] = "cleared"
            else:
                result["action"] = "already_clean"
            return result

        # ── Binance에 포지션 있음 ──────────────────────────────────
        side        = "long" if pos_amt > 0 else "short"
        entry_price = float((binance_pos or {}).get("entryPrice", 0))

        # DB open 레코드 먼저 시도
        try:
            db_pos = self._load_open_from_db()
            if db_pos:
                self._position = db_pos
                result["action"] = "db_restored"
                result["after"]  = self._position
                return result
        except Exception as e:
            result["db_error"] = str(e)

        # DB에도 없으면 수동 주입 (SL/TP=None — 별도 설정 필요)
        self._position = {
            "trade_id":    None,
            "symbol":      symbol,
            "side":        side,
            "entry_price": entry_price,
            "entry_time":  time.time(),
            "entry_tf":    "manual_sync",
            "confidence":  0,
            "tp":          None,
            "sl":          None,
            "entry_state": f"binance_sync | amt={pos_amt}",
        }
        self._position.update(self._extra_position_fields({}))
        result["action"] = "injected"
        result["after"]  = self._position
        return result

    # ── DB 공통 헬퍼 ───────────────────────────────────────────────────────

    @staticmethod
    def _db_available() -> bool:
        try:
            from db.config import get_engine_url
            return bool(get_engine_url())
        except Exception:
            return False

    @staticmethod
    def _get_session():
        from db.session import get_session
        return get_session()

    def _persist_open(
        self, symbol: str, position: Dict[str, Any], report_text: str
    ) -> Optional[int]:
        if not self._db_available():
            return None
        import json as _json
        from db.models import ForwardTrade
        from datetime import datetime
        session = self._get_session()
        try:
            conf     = position.get("confidence", 0)
            sl_init  = position.get("sl")
            tp_init  = position.get("tp")
            entry_ts = position.get("entry_time", 0)
            trade = ForwardTrade(
                symbol            = symbol,
                side              = position["side"],
                entry_price       = position["entry_price"],
                opened_at         = datetime.utcfromtimestamp(entry_ts),
                entry_state       = position.get("entry_state"),
                entry_report_text = report_text,
                trigger_tfs       = position.get("entry_tf", ""),
                confidence        = conf,
                direction_detail  = f"{self.STRATEGY_TAG} conf={conf}",
                sl_price          = sl_init,
                tp1_price         = tp_init,
                tp2_price         = None,
                sl_history        = _json.dumps([{"price": sl_init, "ts": entry_ts}]) if sl_init else None,
                tp1_history       = _json.dumps([{"price": tp_init, "ts": entry_ts}]) if tp_init else None,
                status            = "open",
                entry_source      = "engine",
                strategy          = self.STRATEGY_TAG,
            )
            session.add(trade)
            session.commit()
            session.refresh(trade)
            return trade.id
        except Exception as e:
            session.rollback()
            print(f"[{self.STRATEGY_TAG} FT] persist open error: {e}")
            return None
        finally:
            session.close()

    def _persist_close(
        self,
        trade_id: int,
        exit_price: float,
        reason: str,
        pnl_pct: float,
        close_note: Optional[str] = None,
    ) -> None:
        if not self._db_available() or trade_id is None:
            return
        from db.models import ForwardTrade
        from datetime import datetime
        session = self._get_session()
        try:
            trade = session.query(ForwardTrade).filter(ForwardTrade.id == trade_id).first()
            if not trade or trade.status != "open":
                return
            now = datetime.utcnow()
            trade.status       = reason
            trade.exit_price   = exit_price
            trade.pnl_pct      = pnl_pct
            trade.closed_at    = now
            trade.duration_min = round((now - trade.opened_at).total_seconds() / 60.0, 1)
            if close_note:
                trade.close_note = close_note
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"[{self.STRATEGY_TAG} FT] persist close error: {e}")
        finally:
            session.close()

    def _load_open_from_db(self) -> Optional[Dict[str, Any]]:
        if not self._db_available():
            return None
        from db.models import ForwardTrade
        session = self._get_session()
        try:
            row = (
                session.query(ForwardTrade)
                .filter(
                    ForwardTrade.status   == "open",
                    ForwardTrade.strategy == self.STRATEGY_TAG,
                )
                .order_by(ForwardTrade.opened_at.desc())
                .first()
            )
            if not row:
                return None
            base: Dict[str, Any] = {
                "trade_id":    row.id,
                "symbol":      row.symbol,
                "side":        row.side,
                "entry_price": row.entry_price,
                "entry_time":  row.opened_at.timestamp() if row.opened_at else time.time(),
                "entry_tf":    row.trigger_tfs,
                "confidence":  row.confidence or 0,
                "sl":          row.sl_price,
                "tp":          row.tp1_price,
            }
            base.update(self._extra_db_fields(row))
            return base
        finally:
            session.close()

    def _extra_trade_row(self, row: Any) -> Dict[str, Any]:
        """전략별 추가 필드 — 필요한 전략만 오버라이드."""
        return {}

    def get_trades_from_db(
        self, symbol: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        if not self._db_available():
            return []
        from db.models import ForwardTrade
        session = self._get_session()
        try:
            q = (
                session.query(ForwardTrade)
                .filter(
                    ForwardTrade.strategy == self.STRATEGY_TAG,
                    ForwardTrade.status   != "open",
                )
            )
            if symbol:
                q = q.filter(ForwardTrade.symbol == symbol)
            q = q.order_by(ForwardTrade.opened_at.desc()).limit(max(1, min(limit, 500)))
            return [
                {
                    "id":           r.id,
                    "symbol":       r.symbol,
                    "side":         r.side,
                    "entry_price":  r.entry_price,
                    "opened_at":    r.opened_at.isoformat() if r.opened_at else None,
                    "sl_price":     r.sl_price,
                    "tp1_price":    r.tp1_price,
                    "status":       r.status,
                    "exit_price":   r.exit_price,
                    "pnl_pct":      r.pnl_pct,
                    "closed_at":    r.closed_at.isoformat() if r.closed_at else None,
                    "duration_min": r.duration_min,
                    "close_note":   r.close_note,
                    **self._extra_trade_row(r),
                }
                for r in q.all()
            ]
        finally:
            session.close()

    # ── Tick 공통 로직 ─────────────────────────────────────────────────────

    def _close(
        self,
        exit_price: float,
        reason: str,
        position: Dict[str, Any],
        close_note: Optional[str] = None,
    ) -> Dict[str, Any]:
        side  = position.get("side")
        entry = _f(position.get("entry_price"))
        price_move_pct = (
            (exit_price - entry) / entry * 100 if side == "long"
            else (entry - exit_price) / entry * 100
        )
        # equity ROI = 가격이동% × 레버리지 (기본 1x, Binance live 전략은 오버라이드)
        pnl = round(price_move_pct * self.LEVERAGE, 4)
        trade = {
            **position,
            "exit_price":  exit_price,
            "exit_reason": reason,
            "pnl_pct":     pnl,
            "close_note":  close_note,
        }
        self._closed_trades.append(trade)
        if self._db_available() and position.get("trade_id") is not None:
            self._persist_close(
                position["trade_id"], exit_price, reason, pnl, close_note
            )
        return trade

    def tick(
        self,
        symbol: str,
        state: Dict[str, Any],
        report_text: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        current_price = state.get("current_price")
        if not current_price:
            return None

        sig: Dict[str, Any] = (
            (state.get("by_tf") or {}).get("15m", {}).get("signal") or {}
        )
        events: List[Dict[str, Any]] = []

        # ── 포지션 있음: 청산 체크 ──────────────────────────────────────
        if self._position is not None:
            result = self._check_exit_signal(self._position, current_price, sig)
            if result:
                exit_price, reason, close_note = result
                trade = self._close(exit_price, reason, self._position, close_note)
                events.append({"event": "close", "trade": trade})
                self._position = None

        # ── 포지션 없음: 진입 체크 ──────────────────────────────────────
        if self._position is None:
            direction = sig.get("signal") or sig.get("direction")
            if direction in ("long", "short"):
                pos: Dict[str, Any] = {
                    "side":        direction,
                    "entry_price": current_price,
                    "entry_time":  time.time(),
                    "entry_tf":    sig.get("entry_tf") or "15m",
                    "confidence":  sig.get("confidence", 0),
                    "tp":          sig.get("tp"),
                    "sl":          sig.get("sl"),
                    "entry_state": str(sig.get("reasons", [])),
                }
                pos.update(self._extra_position_fields(sig))
                pos["trade_id"] = self._persist_open(symbol, pos, report_text or "")
                self._position = pos
                events.append({"event": "entry", "position": pos})

        if events:
            self._trigger_recording(symbol, events)

        return {"events": events} if events else None

    def _trigger_recording(self, symbol: str, events: List[Dict[str, Any]]) -> None:
        """비디오 녹화 훅 — btc_forwardtest에서는 미사용."""
        del symbol, events

    def get_position(self) -> Optional[Dict[str, Any]]:
        return self._position

    def get_stats(self, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        trades: List[Dict] = list(self._closed_trades)
        if self._db_available():
            try:
                db = self.get_trades_from_db(symbol=symbol)
                if db:
                    trades = db
            except Exception:
                pass

        from features.strategy.quant_strategies.common.pnl import (
            compound_total_pnl_with_fee,
            total_pnl_including_unrealized,
        )
        total_pnl, win_count, loss_count = compound_total_pnl_with_fee(
            trades, leverage=self.LEVERAGE
        )

        pos = self._position
        unrealized_pct = None
        if pos and (pos.get("symbol") or symbol) == symbol:
            try:
                from common.binance_price_ws import get_cached_price
                cur   = get_cached_price(symbol)
                entry = _f(pos.get("entry_price"))
                if cur and entry > 0:
                    side = pos.get("side", "long")
                    unrealized_pct = (
                        (cur - entry) / entry * 100 if side == "long"
                        else (entry - cur) / entry * 100
                    ) * self.LEVERAGE
                    pos = dict(pos)
                    pos["unrealized_pnl_pct"] = round(unrealized_pct, 4)
                    pos["current_price"]      = cur
            except Exception:
                pass

        total_pnl = total_pnl_including_unrealized(total_pnl, unrealized_pct)

        return {
            "strategy":            self.STRATEGY_TAG,
            "current_position":    pos,
            "closed_trades_count": len(trades),
            "total_pnl_pct":       round(total_pnl, 4),
            "win_count":           win_count,
            "loss_count":          loss_count,
            "win_rate":            round(win_count / len(trades) * 100, 1) if trades else 0,
            "recent_trades": (
                trades[-20:][::-1]
                if trades and isinstance(trades[0], dict)
                else []
            ),
        }


# ── 싱글톤 팩토리 ─────────────────────────────────────────────────────────

def get_engine_for(cls: type) -> Callable[[], BaseForwardTest]:
    """
    주어진 BaseForwardTest 서브클래스의 싱글톤 get_engine() 함수를 반환.

    사용 예:
        get_engine = get_engine_for(AtrForwardTest)
    """
    _instance: Optional[BaseForwardTest] = None

    def get_engine() -> BaseForwardTest:
        nonlocal _instance
        if _instance is None:
            _instance = cls()
        return _instance

    return get_engine
