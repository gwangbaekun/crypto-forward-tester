"""
PHASE 1-a: 빗썸 KRW 마켓 전체의 상장일 스캔 → 신규상장 유니버스 확보.

각 마켓의 일봉을 count=200으로 1회 요청 → 가장 오래된 봉 = (근사) 상장일.
  - 봉 수 < 200  → 상장 200일 이내. first_candle 이 실제 상장일에 근접.
  - 봉 수 == 200 → 상장 200일 초과(오래된 코인). first_candle 은 하한일 뿐.

결과를 data/listings.csv 로 캐시. 이후 분석은 이 캐시를 재사용.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd

from bithumb_public import get_krw_markets, get_candles

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CACHE = os.path.join(DATA_DIR, "listings.csv")


def scan() -> pd.DataFrame:
    mkts = get_krw_markets()
    rows = []
    now = datetime.now(timezone.utc)
    for i, m in enumerate(mkts["market"]):
        try:
            c = get_candles(m, unit="days", count=200)
        except Exception as e:  # noqa: BLE001 — 스캔 중 한 종목 실패는 건너뜀
            print(f"  ! {m} 실패: {e}")
            continue
        if c.empty:
            continue
        first = c["open_time"].iloc[0]
        n = len(c)
        rows.append(
            {
                "market": m,
                "first_candle_utc": first,
                "n_day_candles": n,
                "age_days": (now - first.to_pydatetime()).days,
                "capped": n >= 200,  # True면 상장일은 하한(더 오래됨)
            }
        )
        if (i + 1) % 50 == 0:
            print(f"  ...{i + 1}/{len(mkts)} 스캔")
    df = pd.DataFrame(rows).sort_values("first_candle_utc", ascending=False)
    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(CACHE, index=False)
    return df.reset_index(drop=True)


if __name__ == "__main__":
    df = scan()
    print(f"\n총 {len(df)}개 마켓 스캔 완료 → {CACHE}")

    recent = df[~df["capped"]].copy()
    print(f"\n상장 200일 이내(신규상장 후보): {len(recent)}개")
    print("\n[가장 최근 상장 15개]")
    print(
        recent.head(15)[["market", "first_candle_utc", "age_days"]].to_string(index=False)
    )

    # 상장 최근성 분포 (신규상장 빈도 = 전략 breadth의 근거)
    print("\n[상장 연령 분포]")
    for lo, hi in [(0, 30), (30, 90), (90, 180), (180, 365)]:
        n = len(df[(df["age_days"] >= lo) & (df["age_days"] < hi)])
        print(f"  {lo:>3}~{hi:<3}일 전 상장: {n}개")
