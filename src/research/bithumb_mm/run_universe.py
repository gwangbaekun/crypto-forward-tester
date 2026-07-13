"""
PHASE 1 KILL GATE 판정 — 신규상장 유니버스 전체 풀링.

listings.csv 의 신규상장(capped=False) 코인들을 돌려서:
  1) mature 레짐 lag1 자기상관이 음수(평균회귀)인 코인 비율
  2) gross(무비용) 수익이 +인 코인 비율  ← 반전이 존재하는가
  3) net(비용후) 수익이 +인 코인 비율    ← 실전 가능한가  ← KILL GATE

결론: net이 유의하게 +가 아니면, 이 타임프레임/방식에선 엣지 없음.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

from analysis import analyze_coin

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def main(unit: str = "minutes/10", cost: float = 0.004, max_coins: int | None = None):
    listings = pd.read_csv(os.path.join(DATA_DIR, "listings.csv"))
    universe = listings[~listings["capped"]].sort_values("age_days")
    if max_coins:
        universe = universe.head(max_coins)

    rows = []
    for m in universe["market"]:
        try:
            r = analyze_coin(m, unit=unit, cost_roundtrip=cost)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {m}: {e}")
            continue
        if "error" in r:
            continue
        rows.append(r)
        print(f"  {m:<12} mature={r['mature_bars']:>5}  ac1={r['ac1_mature']:+.4f}  "
              f"gross={r['mr_gross']:+.3f}  net={r['mr_net']:+.3f}  wr={r['mr_win_rate']:.2f}")

    df = pd.DataFrame(rows)
    out = os.path.join(DATA_DIR, "phase1_results.csv")
    df.to_csv(out, index=False)

    print("\n" + "=" * 60)
    print(f"KILL GATE 집계  (n={len(df)} 코인, {unit}, 비용 {cost*100:.1f}% 왕복)")
    print("=" * 60)
    ac = df["ac1_mature"].dropna()
    print(f"mature lag1 자기상관: 평균 {ac.mean():+.4f}, 음수(평균회귀) 비율 {(ac<0).mean()*100:.0f}%")
    print(f"gross(무비용) +  코인 비율: {(df['mr_gross']>0).mean()*100:.0f}%  "
          f"(중앙값 {df['mr_gross'].median():+.3f})")
    print(f"net(비용후)   +  코인 비율: {(df['mr_net']>0).mean()*100:.0f}%  "
          f"(중앙값 {df['mr_net'].median():+.3f})")
    print(f"net 풀링 합계: {df['mr_net'].sum():+.3f}   gross 풀링 합계: {df['mr_gross'].sum():+.3f}")
    verdict = "통과 후보 → 정밀화 가치 있음" if (df["mr_net"] > 0).mean() > 0.55 and df["mr_net"].median() > 0 \
        else "KILL → 이 방식/TF로는 비용 넘는 엣지 없음"
    print(f"\n판정: {verdict}")


if __name__ == "__main__":
    unit = sys.argv[1] if len(sys.argv) > 1 else "minutes/10"
    main(unit=unit)
