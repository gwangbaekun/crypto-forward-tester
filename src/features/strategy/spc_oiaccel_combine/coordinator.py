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
