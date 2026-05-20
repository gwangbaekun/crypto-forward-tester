"""Bayesian FOMC — 시그널 계산 (순수 함수)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BayesianSignal:
    condition_id: str
    question:     str
    side:         str         # "YES" (model > market) | "NO" (model < market)
    market_prob:  float       # Polymarket 현재 YES 가격
    model_prob:   float       # 우리 모델 P(hike)
    divergence:   float       # model_prob - market_prob
    volume_usd:   float
    yes_token_id: str
    no_token_id:  str | None


def compute(
    market:      dict,
    yes_price:   float,
    model_prob:  float,
    cfg:         dict,
) -> BayesianSignal | None:
    if market.get("volume_usd", 0) < cfg["min_volume_usd"]:
        return None

    div = model_prob - yes_price
    if abs(div) < cfg["threshold"]:
        return None

    return BayesianSignal(
        condition_id = market.get("condition_id", ""),
        question     = market.get("question", ""),
        side         = "YES" if div > 0 else "NO",
        market_prob  = yes_price,
        model_prob   = model_prob,
        divergence   = div,
        volume_usd   = market["volume_usd"],
        yes_token_id = market.get("yes_token_id", ""),
        no_token_id  = market.get("no_token_id"),
    )
