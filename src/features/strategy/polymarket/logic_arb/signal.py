"""Logic-Arb — 조합 차익 시그널 (순수 함수).

예측이 아니라 **관계(무위험 구조)** 를 거래한다. 두 종류:

1) 포함관계 차익 (nested_pair) — 사다리의 핵심
   GT 사다리에서 lo < hi 이면 "BTC>lo" ⊇ "BTC>hi".
   long YES_lo + long NO_hi 의 페이오프:
       BTC ≤ lo         : YES_lo=0, NO_hi=1 → 1
       lo < BTC ≤ hi    : YES_lo=1, NO_hi=1 → 2
       BTC > hi         : YES_lo=1, NO_hi=0 → 1
   → 최소 페이오프 1 확정. cost = ask(YES_lo)+ask(NO_hi) < 1 이면 무위험 차익.
   lo==hi(동일 시장)면 YES+NO<1 (= pair_hedge). 즉 pair_hedge 의 시장 간 일반화.
   LT 사다리는 대칭: hi>lo 이면 "BTC<hi" ⊇ "BTC<lo" → long YES_hi + long NO_lo.

2) 분할 차익 (partition)
   상호배타·전수 버킷 집합의 Σ ask(YES) < 1 → 전부 매수, 정확히 하나만 1 지급.
   RANGE 버킷들이 실제로 수직선을 빈틈·겹침 없이 덮을 때만 발화 (산술 증명).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from features.strategy.polymarket.logic_arb.parse import GT, LT, RANGE, Ladder, LadderMarket


@dataclass
class Leg:
    token_id: str
    side: str          # "YES" | "NO"
    ask: float
    label: str         # 사람이 읽을 다리 설명


@dataclass
class ArbSignal:
    kind: str          # "nested_pair" | "partition"
    end_ts: int
    legs: list[Leg]
    cost: float                    # Σ ask
    guaranteed_payoff: float       # 최소 확정 지급 (nested=1, partition=1)
    profit: float                  # guaranteed_payoff - cost
    profit_pct: float              # profit / cost * 100
    volume_usd: float              # 다리 중 최소 거래량 (가장 얇은 다리 기준)
    detail: str = ""
    condition_ids: list[str] = field(default_factory=list)


# AskFn: (token_id) -> best_ask 또는 None (book 확인 후 실제 체결가). engine 이 주입.
AskFn = Callable[[str], float | None]


def _yes_tid(lm: LadderMarket) -> str:
    return lm.market["yes_token_id"]


def _no_tid(lm: LadderMarket) -> str:
    return lm.market["no_token_id"]


def _vol(lm: LadderMarket) -> float:
    return float(lm.market.get("volume_usd") or 0.0)


def nested_pairs(
    ladder: Ladder,
    ask_of: AskFn,
    fee_buffer: float,
    min_profit: float,
) -> list[ArbSignal]:
    """GT/LT 사다리 내 모든 포함관계 쌍을 검사."""
    if ladder.direction not in (GT, LT):
        return []

    # GT: lo 임계값 작을수록 상위집합(superset). LT: hi 임계값 클수록 상위집합.
    members = [m for m in ladder.members if m.spec.direction == ladder.direction]
    members.sort(key=lambda m: m.spec.lo)

    out: list[ArbSignal] = []
    n = len(members)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = members[i], members[j]      # a.lo < b.lo
            if a.spec.lo == b.spec.lo:
                continue

            if ladder.direction == GT:
                # "BTC>a.lo" ⊇ "BTC>b.lo".  long YES_a + long NO_b
                sup, sub = a, b
                leg_yes = Leg(_yes_tid(sup), "YES", 0.0, f'YES ">{_fmt(sup.spec.lo)}"')
                leg_no  = Leg(_no_tid(sub),  "NO",  0.0, f'NO  ">{_fmt(sub.spec.lo)}"')
            else:  # LT
                # "BTC<b.lo" ⊇ "BTC<a.lo" (b.lo 가 더 큼).  long YES_b + long NO_a
                sup, sub = b, a
                leg_yes = Leg(_yes_tid(sup), "YES", 0.0, f'YES "<{_fmt(sup.spec.lo)}"')
                leg_no  = Leg(_no_tid(sub),  "NO",  0.0, f'NO  "<{_fmt(sub.spec.lo)}"')

            ask_yes = ask_of(leg_yes.token_id)
            ask_no  = ask_of(leg_no.token_id)
            if ask_yes is None or ask_no is None:
                continue
            leg_yes.ask, leg_no.ask = ask_yes, ask_no

            cost = ask_yes + ask_no
            profit = 1.0 - cost - fee_buffer
            if profit < min_profit:
                continue

            out.append(ArbSignal(
                kind="nested_pair",
                end_ts=ladder.end_ts,
                legs=[leg_yes, leg_no],
                cost=cost,
                guaranteed_payoff=1.0,
                profit=profit,
                profit_pct=profit / cost * 100 if cost > 0 else 0.0,
                volume_usd=min(_vol(sup), _vol(sub)),
                detail=f"{leg_yes.label} + {leg_no.label} = {cost:.3f} (min payoff 1.00)",
                condition_ids=[sup.market.get("condition_id", ""), sub.market.get("condition_id", "")],
            ))
    return out


def partition(
    ladder: Ladder,
    ask_of: AskFn,
    fee_buffer: float,
    min_profit: float,
) -> list[ArbSignal]:
    """RANGE 버킷들이 수직선을 빈틈·겹침 없이 덮으면 Σ YES_ask < 1 검사."""
    if ladder.direction != RANGE:
        return []
    members = [m for m in ladder.members if m.spec.hi is not None]
    if len(members) < 2:
        return []

    members.sort(key=lambda m: m.spec.lo)
    # 인접 버킷이 정확히 이어붙는지 (겹침·빈틈 없음) — 산술 증명. 하나라도 어긋나면 탈락.
    for k in range(len(members) - 1):
        if members[k].spec.hi != members[k + 1].spec.lo:
            return []
    # 전수성(tail 포함 여부)은 API 로 증명 불가 → 보수적으로 "닫힌 내부 분할"만 인정하지 않고
    # 발화하지 않는다. (전수 버킷 셋이 확인되면 여기서 확장)
    return []


def _fmt(v: float) -> str:
    if v >= 1_000_000 and v % 1_000_000 == 0:
        return f"${int(v/1_000_000)}M"
    if v >= 1_000 and v % 1_000 == 0:
        return f"${int(v/1_000)}k"
    return f"${v:,.0f}"


def scan_ladder(
    ladder: Ladder,
    ask_of: AskFn,
    fee_buffer: float,
    min_profit: float,
) -> list[ArbSignal]:
    return (
        nested_pairs(ladder, ask_of, fee_buffer, min_profit)
        + partition(ladder, ask_of, fee_buffer, min_profit)
    )
