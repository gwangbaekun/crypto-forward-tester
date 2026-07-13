"""
PHASE 1 대안 탐색 — maker MR 폐기 후, 봇과 경쟁 안 하는 taker 방향성 각들.

일봉 기준(싸다). 신규상장 유니버스에 대해:
  A. 상장 후 구조적 하락 드리프트 (mature 구간 buy&hold, 숏 관점)
  C. 상장 스파이크 페이드 (초기 고점 대비 이후 바닥)
  D. 수명/생존 분포
전부 '큰 이동'이라 왕복비용(0.4% 1회)이 무의미한지까지 본다.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from bithumb_public import get_all_candles

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
HYPE_DAYS = 3
COST = 0.004  # 왕복 1회(taker). 몇 주짜리 이동엔 사실상 무시 수준.


def coin_daily_features(market: str) -> dict | None:
    d = get_all_candles(market, unit="days")
    if d.empty or len(d) < HYPE_DAYS + 3:
        return None
    d = d.reset_index(drop=True)
    n = len(d)
    day0_open = d["open"].iloc[0]
    hype_high = d["high"].iloc[:HYPE_DAYS].max()
    mature = d.iloc[HYPE_DAYS:]
    p_mat0 = mature["close"].iloc[0]
    p_end = d["close"].iloc[-1]

    # A. mature 구간 드리프트 (양수=상승, 음수=하락). 숏 net = -drift - 비용
    drift = p_end / p_mat0 - 1.0
    short_net = -drift - COST

    # C. 초기 고점 대비 종가 (스파이크 페이드 크기)
    from_hype_high = p_end / hype_high - 1.0

    # 첫날 시가 대비 최종 (상장 진입자 관점)
    from_day0 = p_end / day0_open - 1.0

    return {
        "market": market,
        "days": n,
        "drift_mature": drift,       # mature 시작→끝
        "short_net": short_net,      # mature 숏 1회 net(비용후)
        "from_hype_high": from_hype_high,
        "from_day0": from_day0,
    }


def main():
    listings = pd.read_csv(os.path.join(DATA_DIR, "listings.csv"))
    universe = listings[~listings["capped"]].sort_values("age_days")

    rows = []
    for m in universe["market"]:
        try:
            r = coin_daily_features(m)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {m}: {e}")
            continue
        if r:
            rows.append(r)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(DATA_DIR, "explore_daily.csv"), index=False)

    print(f"\n{'='*60}\n대안 탐색 집계 (n={len(df)} 신규상장, 일봉)\n{'='*60}")

    print("\n[A. 상장 후(mature) 드리프트 — 하방압력 가설]")
    dm = df["drift_mature"]
    print(f"  드리프트 중앙값 {dm.median():+.2%}, 평균 {dm.mean():+.2%}, 음수(하락) 비율 {(dm<0).mean()*100:.0f}%")
    sn = df["short_net"]
    print(f"  mature 숏 1회 net(비용후): 중앙값 {sn.median():+.2%}, +비율 {(sn>0).mean()*100:.0f}%, 풀링합 {sn.sum():+.2f}")

    print("\n[C. 초기 hype 고점 대비 현재]")
    fh = df["from_hype_high"]
    print(f"  중앙값 {fh.median():+.2%}, 음수 비율 {(fh<0).mean()*100:.0f}%")

    print("\n[첫날 시가 대비 현재 — 상장 매수자 관점]")
    f0 = df["from_day0"]
    print(f"  중앙값 {f0.median():+.2%}, 음수 비율 {(f0<0).mean()*100:.0f}%")

    print("\n[상위/하위 드리프트 5개]")
    s = df.sort_values("drift_mature")
    for _, r in pd.concat([s.head(5), s.tail(5)]).iterrows():
        print(f"  {r['market']:<12} days={int(r['days']):>3}  drift_mature={r['drift_mature']:+.1%}  from_hype_high={r['from_hype_high']:+.1%}")


if __name__ == "__main__":
    main()
