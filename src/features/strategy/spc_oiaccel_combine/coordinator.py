"""spc_oiaccel_combine — 합체 실행 코디네이터.

기존 엔진 이벤트(entry/close)를 받아 전략별 notional_ratio로 단일 계좌 실주문하고
strategy='spc_oiaccel_combine' 태그로 체결을 기록한다. 신호 로직 없음.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

COMBINE_TAG = "spc_oiaccel_combine"
BINANCE_FEE_NOTIONAL_PCT = 0.065  # round-trip, backtest와 동일


def account_contribution_pct(pnl_pct: float, notional_ratio: float) -> float:
    """포지션 가격변동% → 계좌 기여%. (명목이 계좌의 notional_ratio배라서)"""
    return round(pnl_pct * notional_ratio, 4)


def combined_equity_mdd(trades: List[Dict[str, Any]], fee_pct: float = BINANCE_FEE_NOTIONAL_PCT):
    """체결 기록(시간순)으로 계좌 합산 compound%와 MDD% 계산.
    각 trade: {"pnl_pct": float, "notional_ratio": float}
    """
    eq = 100.0
    peak = 100.0
    mdd = 0.0
    for t in trades:
        contrib = account_contribution_pct(t["pnl_pct"], t["notional_ratio"]) - fee_pct * t["notional_ratio"]
        eq *= (1 + contrib / 100.0)
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak * 100.0)
    return round(eq - 100.0, 4), round(mdd, 4)


async def handle(
    strategy_tag: str,
    events: List[Dict[str, Any]],
    symbol: str,
    current_price: Optional[float],
    notional_ratio: float,
    leverage: int,
) -> None:
    """개별 엔진 events를 받아 단일 계좌에 비중 주문 + 합산 기록.
    개별 엔진의 페이퍼 기록과 별개로 strategy='spc_oiaccel_combine' 행을 남긴다.
    """
    from common.binance_executor import get_executor

    try:
        ex = get_executor()
    except Exception as e:
        print(f"[{COMBINE_TAG}] executor 없음: {e}")
        ex = None

    for ev in events:
        kind = ev.get("event")
        if kind == "entry":
            pos = ev.get("position") or {}
            side = pos.get("side")
            if not side or not current_price:
                continue
            fill_price = current_price
            if ex:
                try:
                    result = await ex.open_position(
                        symbol, side, current_price,
                        leverage=leverage, notional_ratio=notional_ratio,
                    )
                    fp = float((result or {}).get("avgPrice") or 0)
                    if fp > 0:
                        fill_price = fp
                    tp, sl = pos.get("tp"), pos.get("sl")
                    if tp or sl:
                        await ex.place_tp_sl(symbol, side, tp=tp, sl=sl)
                except Exception as e:
                    print(f"[{COMBINE_TAG}] 진입 오류: {e}")
            _persist_open(symbol, side, fill_price, pos, strategy_tag, notional_ratio)

        elif kind == "close":
            trade = ev.get("trade") or {}
            side = trade.get("side")
            if not side:
                continue
            exit_price = trade.get("exit_price") or current_price
            if ex:
                try:
                    result = await ex.close_position(symbol, side)
                    fp = float((result or {}).get("avgPrice") or 0)
                    if fp > 0:
                        exit_price = fp
                except Exception as e:
                    print(f"[{COMBINE_TAG}] 청산 오류: {e}")
            _persist_close(symbol, side, exit_price, trade, notional_ratio)


def _db():
    from db.session import get_session
    return get_session()


def _persist_open(symbol, side, entry_price, pos, strategy_tag, notional_ratio) -> Optional[int]:
    import json
    from db.models import ForwardTrade
    session = _db()
    try:
        now = datetime.utcnow()
        meta = {"notional_ratio": notional_ratio, "src_strategy": strategy_tag}
        row = ForwardTrade(
            symbol=symbol, side=side, entry_price=entry_price, opened_at=now,
            entry_state=str(pos.get("reasons", [])),
            trigger_tfs=pos.get("entry_tf", ""), confidence=pos.get("confidence", 0),
            direction_detail=f"{COMBINE_TAG}<-{strategy_tag}",
            sl_price=pos.get("sl"), tp1_price=pos.get("tp"),
            position_meta=json.dumps(meta),
            status="open", entry_source="combine", strategy=COMBINE_TAG,
        )
        session.add(row); session.commit(); session.refresh(row)
        return row.id
    except Exception as e:
        session.rollback(); print(f"[{COMBINE_TAG}] persist open err: {e}"); return None
    finally:
        session.close()


def _persist_close(symbol, side, exit_price, trade, notional_ratio) -> None:
    from db.models import ForwardTrade
    session = _db()
    try:
        row = (session.query(ForwardTrade)
               .filter(ForwardTrade.strategy == COMBINE_TAG,
                       ForwardTrade.symbol == symbol,
                       ForwardTrade.status == "open")
               .order_by(ForwardTrade.opened_at.desc()).first())
        if not row:
            return
        entry = row.entry_price or 0
        pnl = ((exit_price - entry) / entry * 100 if side == "long"
               else (entry - exit_price) / entry * 100) if entry > 0 else 0
        now = datetime.utcnow()
        row.status = trade.get("exit_reason", "closed")
        row.exit_price = exit_price
        row.pnl_pct = round(pnl, 4)
        row.pnl_pct_net = round(pnl - BINANCE_FEE_NOTIONAL_PCT, 4)
        row.closed_at = now
        row.duration_min = round((now - row.opened_at).total_seconds() / 60.0, 1)
        row.close_note = trade.get("exit_reason", "")
        session.commit()
    except Exception as e:
        session.rollback(); print(f"[{COMBINE_TAG}] persist close err: {e}")
    finally:
        session.close()
