"""
PHASE 1 KILL GATE: 신규상장 코인이 "감쇠 후 평균회귀 레짐"에 들어가고,
그 평균회귀가 거래비용을 넘는가?

핵심 규율
  - look-ahead 금지: 신호는 t 시점까지의 trailing 통계로 계산, 체결은 t+1 봉 시가.
  - 비용 정직하게: 왕복 비용모델(수수료+스프레드+슬리피지)을 반드시 차감.
  - 실패해도 그대로 보고. 결과가 과도하게 좋으면 최우선 의심.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from bithumb_public import get_all_candles


# ---------------------------------------------------------------- 데이터 로드
def load_coin(market: str, unit: str = "minutes/10") -> pd.DataFrame:
    df = get_all_candles(market, unit=unit)
    if df.empty:
        return df
    df = df.copy()
    df["ret"] = np.log(df["close"]).diff()
    return df.dropna(subset=["ret"]).reset_index(drop=True)


# ------------------------------------------------------------ 레짐 분할(감쇠)
def split_regime(df: pd.DataFrame, hype_days: float = 3.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    가설: 상장 초기 관심(hype)이 죽은 뒤가 '봇 단독 후보' 레짐.
    시간기반 분할 — 상장 후 첫 `hype_days` 일 = hype, 이후 = mature.
    (유저 가설 #4 "하루 이상 보고 판별"에 맞춰 기본 3일.)
    """
    t0 = df["open_time"].iloc[0]
    cutoff = t0 + pd.Timedelta(days=hype_days)
    hype = df[df["open_time"] < cutoff]
    mature = df[df["open_time"] >= cutoff]
    return hype, mature


# ------------------------------------------------------- 평균회귀 통계(존재증명)
def autocorr_stats(ret: pd.Series, max_lag: int = 3) -> dict:
    ret = ret.dropna()
    out = {"n": len(ret)}
    for lag in range(1, max_lag + 1):
        if len(ret) > lag + 5:
            out[f"ac{lag}"] = float(ret.autocorr(lag=lag))
        else:
            out[f"ac{lag}"] = np.nan
    return out


# --------------------------------------------- z-score 평균회귀 백테스트(비용차감)
def mr_backtest(
    df: pd.DataFrame,
    window: int = 24,
    z_enter: float = 1.5,
    cost_roundtrip: float = 0.004,  # 0.4% 왕복(수수료+스프레드+슬리피지 보수적)
) -> dict:
    """
    z = (close - trailing_mean)/trailing_std, 전부 shift(1)로 look-ahead 제거.
    z<-z_enter → 롱, z>=0 에서 청산. z>+z_enter → 숏, z<=0 에서 청산.
    체결은 다음 봉 시가(open) 기준. 각 왕복마다 cost_roundtrip 차감.
    """
    d = df.reset_index(drop=True).copy()
    mean = d["close"].rolling(window).mean().shift(1)
    std = d["close"].rolling(window).std().shift(1)
    z = (d["close"].shift(1) - mean) / std  # t 시점 신호는 t-1 종가까지만 사용

    pos = 0
    entry_px = 0.0
    trades: list[float] = []
    for i in range(len(d)):
        if np.isnan(z.iloc[i]):
            continue
        px_open = d["open"].iloc[i]  # 이 봉 시가에서 체결
        if pos == 0:
            if z.iloc[i] < -z_enter:
                pos, entry_px = 1, px_open
            elif z.iloc[i] > z_enter:
                pos, entry_px = -1, px_open
        elif pos == 1 and z.iloc[i] >= 0:
            trades.append((px_open - entry_px) / entry_px - cost_roundtrip)
            pos = 0
        elif pos == -1 and z.iloc[i] <= 0:
            trades.append((entry_px - px_open) / entry_px - cost_roundtrip)
            pos = 0

    t = np.array(trades)          # 비용 차감 후(net)
    g = t + cost_roundtrip        # 비용 전(gross): 반전이 애초에 존재하는가
    if len(t) == 0:
        return {"trades": 0, "gross_ret": 0.0, "net_ret": 0.0,
                "win_rate": np.nan, "net_avg": np.nan, "pf": np.nan}
    gw, gl = t[t > 0].sum(), -t[t < 0].sum()
    return {
        "trades": len(t),
        "gross_ret": float(g.sum()),        # 무비용 합산(엣지 존재 여부)
        "net_ret": float(t.sum()),          # 비용 차감 후(실전 가능 여부)
        "win_rate": float((t > 0).mean()),
        "net_avg": float(t.mean()),
        "pf": float(gw / gl) if gl > 0 else np.inf,
    }


def analyze_coin(market: str, unit: str = "minutes/10", cost_roundtrip: float = 0.004) -> dict:
    df = load_coin(market, unit=unit)
    if df.empty or len(df) < 60:
        return {"market": market, "error": "insufficient data", "bars": len(df)}
    hype, mature = split_regime(df)
    ac_h = autocorr_stats(hype["ret"])
    ac_m = autocorr_stats(mature["ret"])
    bt = mr_backtest(mature, cost_roundtrip=cost_roundtrip)
    return {
        "market": market,
        "bars": len(df),
        "mature_bars": len(mature),
        "ac1_hype": ac_h.get("ac1"),
        "ac1_mature": ac_m.get("ac1"),   # 음수 = 평균회귀
        "mr_trades": bt["trades"],
        "mr_gross": bt["gross_ret"],     # 무비용 (엣지 존재?)
        "mr_net": bt["net_ret"],         # 비용후 (실전 가능?)
        "mr_win_rate": bt["win_rate"],
        "mr_pf": bt["pf"],
    }


if __name__ == "__main__":
    import sys

    mkt = sys.argv[1] if len(sys.argv) > 1 else "KRW-OPG"
    print(f"=== 단일 종목 검증: {mkt} (10분봉) ===")
    df = load_coin(mkt)
    print(f"총 봉 수: {len(df)}, 기간: {df['open_time'].iloc[0]} ~ {df['open_time'].iloc[-1]}")
    hype, mature = split_regime(df)
    print(f"hype {len(hype)}봉 / mature {len(mature)}봉")
    print(f"lag1 자기상관  hype={autocorr_stats(hype['ret']).get('ac1'):+.4f}  "
          f"mature={autocorr_stats(mature['ret']).get('ac1'):+.4f}  (음수=평균회귀)")
    print("MR 백테스트(비용 0.4% 왕복, mature 구간):")
    print("  ", mr_backtest(mature))
