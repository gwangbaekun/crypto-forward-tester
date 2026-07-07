"""btc_backtest news_lag 워치리스트(included)를 forwardtest fade 워치리스트로 이관.

btc_backtest 는 read-only 로 취급 — 이 스크립트는 btc_backtest sqlite 를 SELECT 만 하고
forwardtest 쪽 DB에만 쓴다.

실행: PYTHONPATH=src python3 scripts/migrate_fade_watchlist_from_backtest.py
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

BACKTEST_DB = Path("/Users/home/Developer/T/btc_backtest/data/polymarket_asia_lag.db")


def _included_tokens() -> list[tuple[str, str]]:
    con = sqlite3.connect(BACKTEST_DB)
    try:
        return con.execute(
            "SELECT token, question FROM watchlist WHERE status='included'"
        ).fetchall()
    finally:
        con.close()


async def main() -> None:
    from features.strategy.polymarket._data.client import fetch_market_by_token
    from features.strategy.polymarket.router import _refresh_curve
    from db.session import get_session, init_db
    from db.models import PolymarketFadeWatch
    from datetime import datetime

    init_db()

    tokens = _included_tokens()
    print(f"btc_backtest 워치리스트(included) {len(tokens)}개 발견")

    ok, failed = 0, []
    for token, question in tokens:
        try:
            m = await fetch_market_by_token(token)
            if not m or not m.get("condition_id"):
                failed.append((question, "gamma 조회 실패"))
                continue

            db = get_session()
            try:
                row = db.get(PolymarketFadeWatch, m["condition_id"])
                if row is None:
                    row = PolymarketFadeWatch(condition_id=m["condition_id"])
                    db.add(row)
                row.question = m.get("question", "") or question
                row.yes_token_id = m.get("yes_token_id")
                row.no_token_id = m.get("no_token_id")
                row.volume_usd = m.get("volume_usd")
                row.start_ts = m.get("start_ts")
                row.end_ts = m.get("end_ts")
                row.status = "included"
                row.added_at = datetime.utcnow()
                db.commit()
            finally:
                db.close()

            n = await _refresh_curve(
                m["condition_id"], m.get("yes_token_id"), m.get("start_ts"), m.get("end_ts")
            )
            print(f"  OK  {question[:55]:55s} curve={n}pt")
            ok += 1
        except Exception as e:
            failed.append((question, str(e)))
            print(f"  FAIL {question[:55]:55s} {e}")

    print(f"\n완료: {ok}/{len(tokens)} 성공")
    if failed:
        print("실패 목록:")
        for q, err in failed:
            print(f"  - {q}: {err}")


if __name__ == "__main__":
    asyncio.run(main())
