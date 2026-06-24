"""Latency Snipe — 시그널 계산 (순수 함수).

전략 B (sell-before-settle): 종료임박 저유동 마켓에서 한쪽이 결정됐는데(favorite ask
가 높지만 완전수렴 전), 정산 전에 더 높은 bid 로 되팔 수 있는 창을 탐색.

late_convergence 와 차이:
  - LC = "확률"에 베팅, 만기보유. 음의 스큐.
  - LS = "결정된 결과"에 베팅 + 정산 전 매도로 회전. depth(되팔 bid)가 존재해야 성립.

순수 함수라 입력(book 스냅샷)만으로 판정. 주문/DB 는 engine 담당.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LSSignal:
    condition_id: str
    question:     str
    side:         str          # 매수할 favorite 측 "YES"|"NO"
    entry_ask:    float        # 매수 가능가 (best_ask)
    ask_size_usd: float        # 그 가격 depth ($)
    exit_bid:     float | None # 정산 전 되팔 수 있는 가격 (best_bid)
    bid_size_usd: float        # 되팔 수 있는 depth ($)
    spread:       float        # entry_ask - exit_bid (작을수록 회전손실 적음)
    sellable:     bool         # exit_bid≥exit_min_bid AND 양측 size≥min_size_usd
    hours_to_end: float
    end_ts:       int | None
    volume_usd:   float
    yes_token_id: str
    no_token_id:  str | None


def _leg(book: dict | None, cfg: dict) -> tuple[float, float, float | None, float] | None:
    """book → (entry_ask, ask_size_usd, exit_bid, bid_size_usd). favorite 아니면 None."""
    if not book or book.get("best_ask") is None:
        return None
    ask = book["best_ask"]
    if not (cfg["entry_min_ask"] <= ask <= cfg["entry_max_ask"]):
        return None  # favorite·미수렴 구간 아님
    ask_usd = book.get("ask_size", 0.0) * ask
    bid = book.get("best_bid")
    bid_usd = book.get("bid_size", 0.0) * (bid or 0.0)
    return ask, ask_usd, bid, bid_usd


def compute(
    market:   dict,
    yes_book: dict | None,
    no_book:  dict | None,
    cfg:      dict,
) -> LSSignal | None:
    """조건 충족 시 LSSignal, 아니면 None. YES 우선, 없으면 NO."""
    min_size = cfg["min_size_usd"]
    exit_min = cfg["exit_min_bid"]

    for side, book, tok in (("YES", yes_book, market.get("yes_token_id")),
                            ("NO",  no_book,  market.get("no_token_id"))):
        leg = _leg(book, cfg)
        if leg is None:
            continue
        ask, ask_usd, bid, bid_usd = leg
        sellable = (bid is not None and bid >= exit_min
                    and ask_usd >= min_size and bid_usd >= min_size)
        return LSSignal(
            condition_id = market.get("condition_id", ""),
            question     = market.get("question", ""),
            side         = side,
            entry_ask    = ask,
            ask_size_usd = ask_usd,
            exit_bid     = bid,
            bid_size_usd = bid_usd,
            spread       = ask - (bid or 0.0),
            sellable     = sellable,
            hours_to_end = market.get("hours_to_end", 0.0),
            end_ts       = market.get("end_ts"),
            volume_usd   = market.get("volume_usd", 0.0),
            yes_token_id = market.get("yes_token_id", ""),
            no_token_id  = market.get("no_token_id"),
        )
    return None
