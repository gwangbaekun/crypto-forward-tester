"""
Forward test 누적 수익률: 복리(compound) + Binance Futures 수수료 반영.

- 복리: 거래 순서대로 capital *= (1 + net_pnl_pct/100), 최종 (capital - 1) * 100.
- 수수료: Binance USDⓈ-M Futures 기준 1회 왕복(진입+청산) 시 round-trip fee 차감.
  기본값 0.06% (진입 0.03% + 청산 0.03% 수준, maker/taker 평균 가정).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Binance Futures taker 수수료: 0.05% per side → 왕복 0.10% (notional 기준)
# equity 기준 = notional_fee × leverage (예: 3x → 0.30%)
BINANCE_FEE_NOTIONAL_PCT   = 0.10   # 왕복 수수료 (notional 기준)
BINANCE_DEFAULT_LEVERAGE   = 1.0    # 기본 1x (BaseForwardTest.LEVERAGE 와 연동)

# 하위 호환성용 — 기존 코드가 직접 임포트하는 경우
BINANCE_FEE_ROUND_TRIP_PCT = BINANCE_FEE_NOTIONAL_PCT


def _parse_closed_at(t: Dict[str, Any]) -> Any:
    v = t.get("closed_at")
    if v is None:
        return ""
    return v


def compound_total_pnl_with_fee(
    trades: List[Dict[str, Any]],
    fee_round_trip_pct: float = BINANCE_FEE_NOTIONAL_PCT,
    leverage: float = BINANCE_DEFAULT_LEVERAGE,
) -> Tuple[float, int, int]:
    """
    거래 목록을 복리로 누적 수익률 계산하고, 거래당 수수료를 차감한 뒤 win/loss 건수 반환.

    - trades: 각 항목에 pnl_pct, closed_at(선택) 필요. closed_at 기준 오름차순(과거→최근)이어야 복리 순서 맞음.
    - fee_round_trip_pct: 1회 왕복 수수료 % (notional 기준, 0.10% 기본).
    - leverage: 레버리지 배수. equity 기준 수수료 = fee × leverage.
      pnl_pct가 이미 equity ROI(× leverage)로 저장된 경우 leverage=1.0 사용.
    Returns:
        (total_pnl_pct, win_count, loss_count)
        total_pnl_pct: (최종 자본 - 1) * 100, 복리 + 수수료 반영.
        win_count / loss_count: 수수료 차감 후 net_pnl 기준.
    """
    if not trades:
        return 0.0, 0, 0
    # equity 기준 실효 수수료: pnl_pct 가 이미 × leverage 이므로 fee도 동일 기준
    effective_fee = fee_round_trip_pct * leverage
    # DB에서 closed_at.desc()로 가져오므로 최신순 → 과거순으로 뒤집기
    ordered = sorted(trades, key=_parse_closed_at)
    capital = 1.0
    win_count = 0
    loss_count = 0
    for t in ordered:
        raw_pnl = float(t.get("pnl_pct") or 0)
        net_pnl = raw_pnl - effective_fee
        capital *= 1.0 + (net_pnl / 100.0)
        if net_pnl > 0:
            win_count += 1
        elif net_pnl < 0:
            loss_count += 1
    total_pnl_pct = (capital - 1.0) * 100.0
    return total_pnl_pct, win_count, loss_count


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
