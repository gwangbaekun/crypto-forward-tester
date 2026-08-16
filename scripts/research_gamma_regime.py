"""§5 감마 레짐 — 메커니즘 검정 (부호 규약 결정 포함).

검정하는 주장:

    "딜러가 롱감마(GEX>0)면 델타 헤지가 변동성을 억제하고,
     숏감마(GEX<0)면 증폭한다."
    => GEX 가 높을수록 이후 실현변동성이 낮아야 한다 (계수 부호가 음).

이 계수의 **부호가 곧 부호 규약의 답**이다. 규약 A(콜 +, 풋 −)로 계산했을 때
계수가 음이면 A 가 맞고, 양이면 규약이 뒤집힌 것이다. 이론으로 정하지 않고
데이터로 고른다 — 규약 B 는 A 의 부호 반전이므로 따로 돌릴 필요가 없다.

⚠️ 필수 통제: **변동성 군집**.
   최근 변동성이 높으면 다음 구간 변동성도 높다. GEX 는 감마를 통해 IV 와,
   IV 는 실현변동성과 엮여 있어서, 통제 없이 회귀하면 "GEX 가 변동성을
   예측한다"는 결과가 그냥 어제 변동성의 대리 효과로 나온다.
   그래서 모든 회귀에 과거 실현변동성을 함께 넣는다.

데이터
  BTC  deribit_chain 15분 스냅 × Binance 15분봉  → n 이 큰 쪽. 주 검정
  SPY  us_options_chain 세션 스냅 × us_etf 일봉  → n=10. 참고용, 판정 불가

    python scripts/research_gamma_regime.py
"""
from __future__ import annotations

import glob
import math
import os
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import requests

FDC = os.path.expanduser("~/Developer/T/financial-data-collector/data")
DERIBIT_GLOB = os.getenv("DERIBIT_CHAIN_GLOB", f"{FDC}/deribit_chain/**/*.parquet")
USOPT_GLOB = os.getenv("US_OPTIONS_GLOB", f"{FDC}/us_options_chain/date=*/*.parquet")
US_ETF_GLOB = os.getenv("US_ETF_GLOB", f"{FDC}/us_etf/**/*.parquet")
BAR_MIN = 15
SEED = 0


# ---------------------------------------------------------------- Black-Scholes
def _npdf(x: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * x**2) / math.sqrt(2.0 * math.pi)


def bs_gamma(S: float, K: np.ndarray, T: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    """r=0, q=0. dΔ/dS — 콜/풋 동일."""
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(S / K) + 0.5 * sigma**2 * T) / (sigma * np.sqrt(T))
        g = _npdf(d1) / (S * sigma * np.sqrt(T))
    return np.nan_to_num(g, nan=0.0, posinf=0.0, neginf=0.0)


# ---------------------------------------------------------------- BTC / Deribit
def deribit_gex(path: str) -> dict | None:
    cols = ["currency", "strike", "option_type", "mark_iv", "open_interest",
            "underlying_price", "expiry", "snapshot_ts"]
    df = pq.read_table(path, columns=cols).to_pandas()
    df = df[(df.currency == "BTC") & (df.mark_iv > 0) & (df.open_interest > 0)]
    if df.empty:
        return None
    ts = pd.Timestamp(df.snapshot_ts.iloc[0]).tz_convert("UTC")
    S = float(df.underlying_price.iloc[0])
    if not np.isfinite(S) or S <= 0:
        return None

    exp_ts = pd.to_datetime(df.expiry).dt.tz_localize("UTC") + pd.Timedelta(hours=8)
    T = ((exp_ts - ts).dt.total_seconds() / 86400.0 / 365.0).to_numpy()
    ok = T > 1e-4
    df, T = df[ok], T[ok]
    if df.empty:
        return None

    K = df.strike.to_numpy(dtype=float)
    sigma = df.mark_iv.to_numpy() / 100.0
    oi = df.open_interest.to_numpy(dtype=float)
    sign = np.where(df.option_type.to_numpy() == "C", 1.0, -1.0)   # 규약 A
    gamma = bs_gamma(S, K, T, sigma)

    # Deribit 은 인버스(계약=1 BTC). 1% 이동당 딜러 감마 노출.
    gex = gamma * oi * S * S * 0.01 * sign
    # 행사가별 순 GEX 로 감마 플립 추정
    per_k = pd.Series(gex).groupby(K).sum().sort_index()
    pos = per_k[per_k.index > S]
    flip = float(pos.index[0]) if len(pos) and (per_k.values > 0).any() else np.nan

    return {"ts": ts, "spot": S, "gex": float(gex.sum()),
            "gex_near": float(gex[T <= 14 / 365].sum()),
            "oi_total": float(oi.sum()), "flip": flip}


def fetch_btc(start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    out, cur = [], start
    while cur < end:
        r = requests.get("https://api.binance.com/api/v3/klines",
                         params={"symbol": "BTCUSDT", "interval": f"{BAR_MIN}m",
                                 "startTime": int(cur.timestamp() * 1000), "limit": 1000},
                         timeout=30)
        r.raise_for_status()
        k = r.json()
        if not k:
            break
        out += k
        cur = pd.Timestamp(k[-1][0], unit="ms", tz="UTC") + pd.Timedelta(minutes=BAR_MIN)
    px = pd.DataFrame(out)[[0, 4]]
    px.columns = ["ts", "close"]
    px["ts"] = pd.to_datetime(px.ts, unit="ms", utc=True)
    return px.drop_duplicates("ts").set_index("ts").close.astype(float)


# ---------------------------------------------------------------- statistics
def hac_ols(y: np.ndarray, X: np.ndarray, lags: int) -> tuple[np.ndarray, np.ndarray]:
    """(계수, HAC t). X 는 상수항 포함."""
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    XtX_inv = np.linalg.inv(X.T @ X)
    S = (resid[:, None] * X).T @ (resid[:, None] * X)
    for L in range(1, lags + 1):
        w = 1.0 - L / (lags + 1.0)
        u = resid[L:, None] * X[L:]
        v = resid[:-L, None] * X[:-L]
        G = u.T @ v
        S += w * (G + G.T)
    cov = XtX_inv @ S @ XtX_inv
    se = np.sqrt(np.maximum(np.diag(cov), 1e-30))
    return beta, beta / se


def boot_mean_diff(a: np.ndarray, b: np.ndarray, n_boot: int = 5000) -> tuple[float, float]:
    rng = np.random.default_rng(SEED)
    d = np.array([rng.choice(a, len(a), True).mean() - rng.choice(b, len(b), True).mean()
                  for _ in range(n_boot)])
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def n_for_power(rho: float) -> int:
    if abs(rho) < 1e-6:
        return 10**9
    z = 0.5 * math.log((1 + abs(rho)) / (1 - abs(rho)))
    return int(math.ceil((1.959963985 + 0.8416212336) ** 2 / z**2 + 3))


# ---------------------------------------------------------------- BTC test
def run_btc() -> None:
    paths = sorted(glob.glob(DERIBIT_GLOB, recursive=True))
    print(f"[BTC] deribit 스냅 {len(paths)}개 처리 중 …")
    rows = [r for r in (deribit_gex(p) for p in paths) if r is not None]
    g = pd.DataFrame(rows).sort_values("ts")
    g["ts"] = g.ts.dt.floor(f"{BAR_MIN}min")
    g = g.drop_duplicates("ts", keep="last").set_index("ts")
    print(f"[BTC] 유효 GEX 관측 {len(g)}  |  {g.index.min()} → {g.index.max()}")

    px = fetch_btc(g.index.min() - pd.Timedelta(days=2), g.index.max() + pd.Timedelta(days=2))
    lr = np.log(px).diff()
    step = 60 // BAR_MIN

    d = g.join(px.rename("px"), how="inner").dropna(subset=["px"])
    # OI 규모로 정규화 — GEX 자체는 OI 총량과 함께 커진다
    d["gex_n"] = d.gex / d.oi_total
    d["gex_z"] = (d.gex_n - d.gex_n.expanding(100).mean()) / d.gex_n.expanding(100).std()

    # 실현변동성: 구간 내 15분 로그수익률의 제곱합 제곱근
    def rv(h: int, fwd: bool) -> pd.Series:
        sq = lr.pow(2)
        r = sq.rolling(h * step).sum()
        return np.sqrt(r.shift(-h * step) if fwd else r).reindex(d.index)

    print(f"\n  GEX 부호 분포: 양(+) {(d.gex > 0).mean()*100:.0f}%  음(−) {(d.gex < 0).mean()*100:.0f}%")
    print(f"  GEX/OI 중앙값 {d.gex_n.median():.2f}  |  spot > flip 비율 "
          f"{(d.spot > d.flip).mean()*100:.0f}%")

    print("\n" + "=" * 78)
    print("[BTC-1] 전방 실현변동성 회귀   RV_fwd ~ GEX + RV_past      (Newey-West HAC)")
    print("        메커니즘이 참이면 GEX 계수의 부호는 음(−)")
    print("=" * 78)
    print(f"{'지평':>5} {'n':>6} {'b(GEX)':>10} {'t(GEX)':>8} {'t(RV_past)':>11} {'단순 corr':>10}")
    verdicts = {}
    for h in [4, 8, 24]:
        s = pd.DataFrame({"y": rv(h, True), "g": d.gex_z, "p": rv(h, False)}).dropna()
        if len(s) < 50:
            continue
        X = np.column_stack([np.ones(len(s)), s.g.to_numpy(), s.p.to_numpy()])
        beta, t = hac_ols(s.y.to_numpy(), X, lags=h * step)
        c = float(np.corrcoef(s.g, s.y)[0, 1])
        verdicts[h] = (beta[1], t[1], c, len(s))
        print(f"{h:>4}h {len(s):>6} {beta[1]:>10.2e} {t[1]:>8.2f} {t[2]:>11.2f} {c:>10.3f}")

    print("\n" + "=" * 78)
    print("[BTC-2] 레짐 분할   GEX>0 (롱감마) vs GEX<0 (숏감마) 의 전방 실현변동성")
    print("=" * 78)
    for h in [4, 8, 24]:
        s = pd.DataFrame({"y": rv(h, True), "g": d.gex}).dropna()
        a, b = s[s.g > 0].y.to_numpy(), s[s.g < 0].y.to_numpy()
        if len(a) < 20 or len(b) < 20:
            print(f"  {h}h: 한쪽 레짐 표본 부족 (양 {len(a)}, 음 {len(b)}) — 판정 불가")
            continue
        lo, hi = boot_mean_diff(a, b)
        sig = "" if lo <= 0 <= hi else "  ← CI 가 0 제외"
        print(f"  {h}h: 롱감마 {a.mean()*100:.2f}%  숏감마 {b.mean()*100:.2f}%  "
              f"차이 95%CI [{lo*100:+.3f},{hi*100:+.3f}]%{sig}")

    print("\n" + "=" * 78)
    print("[BTC-3] 검정력")
    print("=" * 78)
    days = (d.index.max() - d.index.min()).total_seconds() / 86400
    for h, (b, t, c, n) in verdicts.items():
        indep = max(int(days * 24 / h), 1)
        need = n_for_power(abs(c))
        print(f"  {h}h: 독립 관측 {indep}개 / |ρ|={abs(c):.3f} 검출에 {need}개 필요 → "
              + ("판정 가능" if indep >= need else f"부족 (약 {math.ceil(need*h/24)}일)"))


# ---------------------------------------------------------------- SPY test
def run_spy() -> None:
    print("\n\n" + "#" * 78)
    print("[SPY] 세션별 GEX vs 익일 실현 레인지")
    print("#" * 78)
    rows = []
    for part in sorted(glob.glob(os.path.dirname(USOPT_GLOB))):
        sess = os.path.basename(part).split("=")[1]
        files = sorted(glob.glob(part + "/*.parquet"))
        if not files:
            continue
        latest = None  # 세션 내 마지막 스냅 = OI 가 가장 신선한 것
        for f in files:
            try:
                t = pq.read_table(f, columns=["underlying", "snapshot_ts", "expiry", "strike",
                                              "option_type", "open_interest", "gamma",
                                              "underlying_price"]).to_pandas()
            except Exception:
                continue
            t = t[t.underlying == "SPY"]
            if t.empty:
                continue
            ts = pd.Timestamp(t.snapshot_ts.iloc[0])
            if latest is None or ts > latest[0]:
                latest = (ts, t)
        if latest is None:
            continue
        ts, t = latest
        S = float(t.underlying_price.iloc[0])
        t = t[(t.open_interest > 0) & (t.gamma > 0)]
        exp = pd.to_datetime(t.expiry)
        near = t[(exp - pd.Timestamp(sess)).dt.days.between(0, 30)]
        if near.empty:
            continue
        sign = np.where(near.option_type == "C", 1.0, -1.0)
        gex = float((near.gamma * near.open_interest * 100 * S * S * 0.01 * sign).sum())
        rows.append({"session": pd.Timestamp(sess), "gex": gex, "spot": S,
                     "oi": float(near.open_interest.sum())})
    gx = pd.DataFrame(rows).sort_values("session").set_index("session")

    etf = pd.concat([pq.read_table(f).to_pandas() for f in sorted(glob.glob(US_ETF_GLOB, recursive=True))])
    etf = etf[etf.symbol == "SPY"].drop_duplicates("date")
    etf["date"] = pd.to_datetime(etf.date)
    etf = etf.sort_values("date").set_index("date")
    etf["range"] = (etf.high - etf.low) / etf.close

    d = gx.join(etf[["range"]], how="inner")
    d["range_next"] = etf["range"].shift(-1).reindex(d.index)
    d["gex_n"] = d.gex / d.oi
    d = d.dropna(subset=["range_next"])
    print(f"\n  세션 {len(d)}개  |  {d.index.min().date()} → {d.index.max().date()}")
    print(f"  GEX 부호: 양 {(d.gex>0).sum()}, 음 {(d.gex<0).sum()}")
    print(f"\n{'세션':>12} {'GEX($bn)':>11} {'당일레인지':>10} {'익일레인지':>10}")
    for i, r in d.iterrows():
        print(f"{str(i.date()):>12} {r.gex/1e9:>11.1f} {r['range']*100:>9.2f}% {r.range_next*100:>9.2f}%")
    if len(d) >= 4:
        c = float(np.corrcoef(d.gex_n, d.range_next)[0, 1])
        cp = float(np.corrcoef(d.gex_n, d["range"])[0, 1])
        print(f"\n  corr(GEX, 익일 레인지) = {c:+.3f}   (n={len(d)})")
        print(f"  corr(GEX, 당일 레인지) = {cp:+.3f}   ← 동시점, 인과 아님")
        print(f"  |ρ|={abs(c):.3f} 를 검출하려면 세션 {n_for_power(abs(c))}개 필요 "
              f"→ 현재 {len(d)}개. **판정 불가**")


if __name__ == "__main__":
    run_btc()
    run_spy()
    sys.exit(0)
