"""Pair Hedge — 시그널 계산 (순수 함수).

YES_ask + NO_ask < max_pair_cost 일 때 수학적 무위험 수익 확정.
"""
from __future__ import annotations

from dataclasses import dataclass

from features.strategy.polymarket._data.ws_client import PriceLevel


@dataclass
class PairSignal:
    condition_id: str
    question:     str
    yes_ask:      float
    no_ask:       float
    pair_cost:    float       # yes_ask + no_ask
    profit:       float       # 1.00 - pair_cost
    profit_pct:   float       # profit / pair_cost * 100
    volume_usd:   float
    yes_token_id: str
    no_token_id:  str


def compute(
    market:    dict,
    yes_level: PriceLevel | None,
    no_level:  PriceLevel | None,
    cfg:       dict,
) -> PairSignal | None:
    """시그널 반환. 조건 미충족 시 None."""
    if not market.get("yes_token_id") or not market.get("no_token_id"):
        return None
    if market.get("volume_usd", 0) < cfg["min_volume_usd"]:
        return None

    yes_ask = _ask(yes_level)
    no_ask  = _ask(no_level)

    if yes_ask is None or no_ask is None:
        return None

    pair_cost = yes_ask + no_ask
    if pair_cost >= cfg["max_pair_cost"]:
        return None

    profit = 1.0 - pair_cost
    if profit < cfg.get("min_profit", 0.005):   # 최소 0.5% 수익
        return None

    return PairSignal(
        condition_id = market.get("condition_id", ""),
        question     = market.get("question", ""),
        yes_ask      = yes_ask,
        no_ask       = no_ask,
        pair_cost    = pair_cost,
        profit       = profit,
        profit_pct   = profit / pair_cost * 100,
        volume_usd   = market["volume_usd"],
        yes_token_id = market["yes_token_id"],
        no_token_id  = market["no_token_id"],
    )


def _ask(level: PriceLevel | None) -> float | None:
    if level is None:
        return None
    # best_ask 우선, 없으면 mid (근사치)
    if level.best_ask is not None:
        return level.best_ask
    if level.mid is not None:
        return level.mid + 0.005   # spread 절반 추정
    return None
