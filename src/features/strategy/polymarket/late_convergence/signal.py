"""Late Convergence Alpha — 시그널 계산 (순수 함수).

해소 N시간 이내인데 YES/NO 가격이 아직 완전히 수렴 안 된 마켓을 탐색.
예) 6시간 후 종료, YES = 0.87 → 13% 할인된 near-certain outcome.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from features.strategy.polymarket._data.ws_client import PriceLevel


@dataclass
class LCSignal:
    condition_id:  str
    question:      str
    side:          str          # "YES" or "NO"
    entry_price:   float        # 살 수 있는 가격 (best_ask or mid)
    fair_value:    float        # 1.00 (해소 시 수령 가격)
    expected_pnl:  float        # fair_value - entry_price
    expected_roi:  float        # expected_pnl / entry_price
    hours_to_end:  float
    end_ts:        int | None
    volume_usd:    float
    yes_token_id:  str
    no_token_id:   str | None


def compute(
    market:    dict,
    yes_level: PriceLevel | None,
    no_level:  PriceLevel | None,
    cfg:       dict,
) -> LCSignal | None:
    """시그널 반환. 조건 미충족 시 None."""
    end_ts = market.get("end_ts")
    if not end_ts:
        return None

    hours_to_end = (end_ts - time.time()) / 3600
    if hours_to_end < 0 or hours_to_end > cfg["hours_before_end"]:
        return None

    if market.get("volume_usd", 0) < cfg["min_volume_usd"]:
        return None

    lo = cfg["min_convergence_price"]
    hi = cfg["max_convergence_price"]

    # YES 쪽 확인
    yes_price = _best_price(yes_level)
    if yes_price and lo <= yes_price <= hi:
        return LCSignal(
            condition_id  = market.get("condition_id", ""),
            question      = market.get("question", ""),
            side          = "YES",
            entry_price   = yes_price,
            fair_value    = 1.0,
            expected_pnl  = 1.0 - yes_price,
            expected_roi  = (1.0 - yes_price) / yes_price,
            hours_to_end  = hours_to_end,
            end_ts        = end_ts,
            volume_usd    = market["volume_usd"],
            yes_token_id  = market.get("yes_token_id", ""),
            no_token_id   = market.get("no_token_id"),
        )

    # NO 쪽 확인
    no_price = _best_price(no_level)
    if no_price and lo <= no_price <= hi:
        return LCSignal(
            condition_id  = market.get("condition_id", ""),
            question      = market.get("question", ""),
            side          = "NO",
            entry_price   = no_price,
            fair_value    = 1.0,
            expected_pnl  = 1.0 - no_price,
            expected_roi  = (1.0 - no_price) / no_price,
            hours_to_end  = hours_to_end,
            end_ts        = end_ts,
            volume_usd    = market["volume_usd"],
            yes_token_id  = market.get("yes_token_id", ""),
            no_token_id   = market.get("no_token_id"),
        )

    return None


def _best_price(level: PriceLevel | None) -> float | None:
    if level is None:
        return None
    # ask 가격이 있으면 우선 (실제 살 수 있는 가격), 없으면 mid
    return level.best_ask or level.mid or level.last_price
