"""
Forward test 누적 수익률: 복리(compound) + Binance Futures 수수료 반영.

- pnl_pct: 순수 가격 이동률 (leverage 미반영, fee 미포함).
- pnl_pct_net: 거래 저장 시 pnl_pct - BINANCE_FEE_NOTIONAL_PCT 로 미리 계산된 값.
- compound_total_pnl: pnl_pct 복리 누적 (순수 PnL).
- compound_total_pnl_net: pnl_pct_net 복리 누적 (수수료 차감 PnL).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Binance Futures taker 수수료: 0.05% per side → 왕복 0.10% (notional 기준)
BINANCE_FEE_NOTIONAL_PCT   = 0.10   # 왕복 수수료 (notional = equity, leverage 제거 후)

# 하위 호환성용 — 기존 코드가 직접 임포트하는 경우
BINANCE_FEE_ROUND_TRIP_PCT = BINANCE_FEE_NOTIONAL_PCT
BINANCE_DEFAULT_LEVERAGE   = 1.0


def _parse_closed_at(t: Dict[str, Any]) -> Any:
    v = t.get("closed_at")
    if v is None:
        return ""
    return v


def compound_total_pnl(
    trades: List[Dict[str, Any]],
) -> Tuple[float, int, int]:
    """순수 pnl_pct 복리 누적 (수수료 미포함). win/loss는 raw pnl 기준."""
    if not trades:
        return 0.0, 0, 0
    ordered = sorted(trades, key=_parse_closed_at)
    capital = 1.0
    win_count = 0
    loss_count = 0
    for t in ordered:
        raw_pnl = float(t.get("pnl_pct") or 0)
        capital *= 1.0 + (raw_pnl / 100.0)
        if raw_pnl > 0:
            win_count += 1
        elif raw_pnl < 0:
            loss_count += 1
    return (capital - 1.0) * 100.0, win_count, loss_count


def compound_total_pnl_net(
    trades: List[Dict[str, Any]],
) -> Tuple[float, int, int]:
    """
    수수료 차감 pnl_pct_net 복리 누적.
    pnl_pct_net 이 없는 구형 레코드는 pnl_pct - BINANCE_FEE_NOTIONAL_PCT 로 폴백.
    win/loss는 net pnl 기준.
    """
    if not trades:
        return 0.0, 0, 0
    ordered = sorted(trades, key=_parse_closed_at)
    capital = 1.0
    win_count = 0
    loss_count = 0
    for t in ordered:
        net_pnl = t.get("pnl_pct_net")
        if net_pnl is None:
            net_pnl = float(t.get("pnl_pct") or 0) - BINANCE_FEE_NOTIONAL_PCT
        else:
            net_pnl = float(net_pnl)
        capital *= 1.0 + (net_pnl / 100.0)
        if net_pnl > 0:
            win_count += 1
        elif net_pnl < 0:
            loss_count += 1
    return (capital - 1.0) * 100.0, win_count, loss_count


def compound_total_pnl_with_fee(
    trades: List[Dict[str, Any]],
    fee_round_trip_pct: float = BINANCE_FEE_NOTIONAL_PCT,
    leverage: float = BINANCE_DEFAULT_LEVERAGE,
) -> Tuple[float, int, int]:
    """하위 호환성 유지 — compound_total_pnl_net 으로 위임."""
    return compound_total_pnl_net(trades)


def total_pnl_including_unrealized(
    closed_total_pct: float,
    unrealized_pct: Optional[float],
) -> float:
    """
    청산 누적 수익률(복리)에 현재 포지션 미실현 수익률을 반영한 합계 수익률(%).
    (1 + closed/100) * (1 + unrealized/100) - 1 을 % 로 반환.
    """
    if unrealized_pct is None:
        return closed_total_pct
    return ((1.0 + closed_total_pct / 100.0) * (1.0 + unrealized_pct / 100.0) - 1.0) * 100.0
