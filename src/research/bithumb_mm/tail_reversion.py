"""
PHASE 1 생존 가설: 큰 하방 이탈만 롱으로 잡는다 (봇의 기계적 스냅백).

세 벽 통과:
  - 비용: 잔물결(0.19%) 말고 '큰 이탈'만 → 반전폭이 왕복비 넘김
  - 자기모순 회피: taker로 진입(maker 대기열 경쟁 안 함)
  - 수단: 롱 전용 → 현물에서 가능(숏 불필요)

look-ahead 금지: z는 t-1까지 trailing으로 계산, 체결은 t 봉 시가.
캔들은 data/candles_10m/ 에 캐시(재요청 회피).
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from bithumb_public import get_all_candles

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CACHE_DIR = os.path.join(DATA_DIR, "candles_10m")
HYPE_DAYS = 3


def load_cached(market: str) -> pd.DataFrame:
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = os.path.join(CACHE_DIR, f"{market}.parquet")
    if os.path.exists(p):
        return pd.read_parquet(p)
    df = get_all_candles(market, unit="minutes/10")
    if not df.empty:
        df.to_parquet(p)
    return df


def mature(df: pd.DataFrame) -> pd.DataFrame:
    t0 = df["open_time"].iloc[0]
    return df[df["open_time"] >= t0 + pd.Timedelta(days=HYPE_DAYS)].reset_index(drop=True)


def long_only_tail(
    df: pd.DataFrame,
    window: int = 48,
    z_enter: float = 3.0,     # 큰 하방 이탈만
    z_exit: float = 0.5,      # 밴드 근처 복귀 시 청산
    max_hold: int = 24,       # 시간청산(반전 실패 방어)
    cost_roundtrip: float = 0.004,
) -> dict:
    d = df.reset_index(drop=True)
    if len(d) < window + 5:
        return {"trades": 0}
    mean = d["close"].rolling(window).mean().shift(1)
    std = d["close"].rolling(window).std().shift(1)
    z = (d["close"].shift(1) - mean) / std

    trades: list[float] = []
    holds: list[int] = []
    pos = 0
    entry_px = 0.0
    held = 0
    for i in range(len(d)):
        if np.isnan(z.iloc[i]):
            continue
        px = d["open"].iloc[i]
        if pos == 0:
            if z.iloc[i] < -z_enter:            # 큰 하방 이탈 → 롱
                pos, entry_px, held = 1, px, 0
        else:
            held += 1
            if z.iloc[i] >= -z_exit or held >= max_hold:
                trades.append((px - entry_px) / entry_px - cost_roundtrip)
                holds.append(held)
                pos = 0
    t = np.array(trades)
    if len(t) == 0:
        return {"trades": 0}
    gw, gl = t[t > 0].sum(), -t[t < 0].sum()
    return {
        "trades": len(t),
        "net_avg": float(t.mean()),          # 트레이드당 net(비용후) — >0 이어야 삶
        "net_sum": float(t.sum()),
        "win_rate": float((t > 0).mean()),
        "pf": float(gw / gl) if gl > 0 else np.inf,
        "avg_hold": float(np.mean(holds)),
    }


def main(z_enter: float = 3.0):
    listings = pd.read_csv(os.path.join(DATA_DIR, "listings.csv"))
    universe = listings[~listings["capped"]].sort_values("age_days")

    rows = []
    for m in universe["market"]:
        try:
            df = load_cached(m)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {m}: {e}")
            continue
        if df.empty:
            continue
        r = long_only_tail(mature(df), z_enter=z_enter)
        if r.get("trades", 0) == 0:
            continue
        r["market"] = m
        rows.append(r)
        print(f"  {m:<12} tr={r['trades']:>3}  net_avg={r['net_avg']:+.3%}  "
              f"wr={r['win_rate']:.2f}  pf={r['pf']:.2f}  hold={r['avg_hold']:.0f}")

    df = pd.DataFrame(rows)
    print(f"\n{'='*60}\n롱전용 큰이탈 반전 (z_enter={z_enter}, 비용 0.4%, n={len(df)})\n{'='*60}")
    if df.empty:
        print("트레이드 발생 코인 없음"); return
    tot = df["trades"].sum()
    print(f"총 트레이드 {tot}, 코인당 평균 {tot/len(df):.0f}")
    print(f"트레이드당 net_avg: 중앙값 {df['net_avg'].median():+.3%}, +코인비율 {(df['net_avg']>0).mean()*100:.0f}%")
    print(f"승률 중앙값 {df['win_rate'].median():.2f}, PF 중앙값 {df['pf'].median():.2f}")
    print(f"net_sum 풀링 {df['net_sum'].sum():+.2f}")
    verdict = "PASS 후보 → 정밀화 가치" if df["net_avg"].median() > 0 and (df["net_avg"] > 0).mean() > 0.55 \
        else "kill → 큰이탈도 비용 못 넘음"
    print(f"판정: {verdict}")


if __name__ == "__main__":
    import sys
    z = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
    main(z_enter=z)
