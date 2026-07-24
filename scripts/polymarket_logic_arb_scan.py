#!/usr/bin/env python3
"""Logic-Arb — 단독 스캔 CLI (runner 없이 1회 스캔).

BTC 가격 임계값 사다리에서 조합 차익(포함관계/분할)을 탐지한다.
config.yaml 의 enabled 와 무관하게 실행되며 DB 저장은 하지 않고 콘솔 출력만 한다.

사용:
    PYTHONPATH=src python scripts/polymarket_logic_arb_scan.py
    python scripts/polymarket_logic_arb_scan.py --groups   # 파싱된 사다리 그룹만 출력
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from features.strategy.polymarket._data.client import fetch_active_events_by_keyword, fetch_book
from features.strategy.polymarket.logic_arb import engine as la
from features.strategy.polymarket.logic_arb import parse as la_parse
from features.strategy.polymarket.logic_arb import signal as la_signal


async def _run(show_groups: bool, min_vol: float, tol: int) -> None:
    la._cfg = la._load_cfg()
    fee_buffer = la._cfg.get("fee_buffer", 0.01)
    min_profit = la._cfg.get("min_profit", 0.005)
    min_size = la._cfg.get("min_ask_size", 20)
    keywords = la._cfg.get("keywords", ["bitcoin", "btc"])

    markets = await fetch_active_events_by_keyword(keywords, min_volume=min_vol)
    lms = la_parse.build_ladder_markets(markets)
    ladders = la_parse.group_ladders(lms, tol_sec=tol,
                                     require_same_slug=la._cfg.get("require_same_slug", False))

    print(f"수집 {len(markets)} BTC 마켓 → 파싱 {len(lms)} → 사다리 {len(ladders)}개\n")

    if show_groups:
        for g in ladders:
            print(f"[{g.direction}] end_ts={g.end_ts} slug={g.slug} ({len(g.members)}개)")
            for lm in sorted(g.members, key=lambda m: m.spec.lo):
                print(f"    {la_signal._fmt(lm.spec.lo)}  "
                      f"yes={lm.market.get('yes_price')} no={lm.market.get('no_price')}"
                      f"  vol=${lm.market.get('volume_usd'):,.0f}  {lm.market.get('question','')[:60]}")
            print()
        return

    # ws 미가동 → _ask_of 는 마켓 스냅샷 가격으로 프리스크린
    la._token_index = {}
    for g in ladders:
        for lm in g.members:
            la._token_index[lm.market["yes_token_id"]] = (lm.market, "YES")
            la._token_index[lm.market["no_token_id"]] = (lm.market, "NO")

    found = 0
    for g in ladders:
        for sig in la_signal.scan_ladder(g, la._ask_of, fee_buffer, min_profit):
            # 프리스크린 통과 → REST book 재확인
            ok = True
            real_cost = 0.0
            for leg in sig.legs:
                book = await fetch_book(leg.token_id)
                if not book or book.get("best_ask") is None:
                    ok = False
                    break
                if float(book.get("ask_size") or 0) < min_size:
                    ok = False
                    break
                leg.ask = float(book["best_ask"])
                real_cost += leg.ask
            if not ok:
                continue
            profit = sig.guaranteed_payoff - real_cost - fee_buffer
            if profit < min_profit:
                continue
            found += 1
            print(f"✅ {sig.kind.upper()}  cost={real_cost:.3f}  "
                  f"profit=+{profit/real_cost*100:.2f}%  end_ts={sig.end_ts}")
            print(f"   {sig.detail}")
            for leg in sig.legs:
                print(f"     {leg.side:3} ask={leg.ask:.3f}  {leg.label}  ({leg.token_id[:16]}…)")
            print()

    print(f"확정 차익 {found}건 (book 재확인 + size≥{min_size} 통과)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", action="store_true", help="파싱된 사다리 그룹만 출력")
    ap.add_argument("--min-volume", type=float, default=None)
    ap.add_argument("--tol", type=int, default=None, help="end_ts 허용오차 초")
    args = ap.parse_args()

    cfg = la._load_cfg()
    min_vol = args.min_volume if args.min_volume is not None else cfg.get("min_volume_usd", 5000)
    tol = args.tol if args.tol is not None else cfg.get("end_ts_tolerance_sec", 3600)
    asyncio.run(_run(args.groups, min_vol, tol))


if __name__ == "__main__":
    main()
