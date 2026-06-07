"""순수 사이징/합산 로직 검증 — pytest 불필요, 직접 실행.
Run: python -m features.strategy.spc_oiaccel_combine._verify_sizing
"""
from features.strategy.spc_oiaccel_combine.coordinator import (
    account_contribution_pct,
    combined_equity_mdd,
)


def main() -> None:
    # 1) 계좌 기여% = pnl_pct × notional_ratio
    assert account_contribution_pct(4.40, 0.5) == 2.2, "oi TP 기여"
    assert account_contribution_pct(-1.60, 0.5) == -0.8, "oi SL 기여"
    assert account_contribution_pct(-3.0, 0.75) == -2.25, "spc SL 기여"

    # 2) 합산 equity/MDD — 두 손실 연속이면 복리로 누적
    trades = [
        {"pnl_pct": -1.60, "notional_ratio": 0.5},   # -0.8%
        {"pnl_pct": -3.0,  "notional_ratio": 0.75},  # -2.25%
        {"pnl_pct": 4.40,  "notional_ratio": 0.5},   # +2.2%
    ]
    comp, mdd = combined_equity_mdd(trades, fee_pct=0.0)
    # eq: 100 → 99.2 → 96.968 → 99.10...; peak 100 → mdd ≈ 3.03%
    assert abs(mdd - 3.03) < 0.05, f"MDD 기대 ~3.03, 실제 {mdd}"
    assert comp < 0, f"3트레이드 누적 음수 기대, 실제 {comp}"

    print("sizing verify: ALL PASS")


if __name__ == "__main__":
    main()
