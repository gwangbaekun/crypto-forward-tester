"""§3 스큐 충격 — 메커니즘 검정 (전략 백테스트 아님).

검정 대상은 트레이딩 룰이 아니라 그 밑에 깔린 주장 하나다:

    "정보는 옵션에서 먼저 움직이고, 딜러의 델타 헤지가 현물을 나중에 민다."
    => Δ(25Δ Risk Reversal) 가 현물 수익률을 LEAD 한다.

룰(진입/청산/손절)을 먼저 백테스트하면 룰의 자유도가 메커니즘의 부재를 가린다.
그래서 여기서는 파라미터가 거의 없는 형태로 관계 자체만 본다.

데이터
  신호  deribit_chain 15분 스냅샷 (자체 수집, 백필 불가)
  가격  Binance 15분 봉 (무료·무한 백필 → 가격 쪽 표본 제약 없음)

방법
  1. 스냅샷마다 BS 델타를 mark_iv 로 계산 → 델타 공간에서 IV 보간
       RR25 = IV(call δ=+0.25) − IV(put δ=−0.25)
  2. 만기 계열이 바뀌는 순간의 Δ 는 버린다 (계약 교체로 인한 가짜 점프)
  3. 리드-래그 프로파일: corr(ΔRR25(t), r(t+k)), k = −4h … +8h
       메커니즘이 참이면 피크가 k>0 에 있어야 한다. k<0 이면 후행 = 기각.
  4. 겹치는 창 → Newey-West HAC t 통계량 + 정상 블록 부트스트랩 CI
  5. 관측된 효과크기로 검정력 분석: 판정에 며칠이 더 필요한가

n 이 작다는 것을 아는 상태로 돌린다. 결론이 아니라 **판정 가능 시점**을 얻는 게 목적이다.

    python scripts/research_skew_leadlag.py
"""
from __future__ import annotations

import datetime as dt
import glob
import math
import os
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import requests

CHAIN_GLOB = os.getenv(
    "DERIBIT_CHAIN_GLOB",
    os.path.expanduser("~/Developer/T/financial-data-collector/data/deribit_chain/**/*.parquet"),
)
CURRENCY = "BTC"
EXPIRY_HOUR_UTC = 8            # Deribit 만기 08:00 UTC
MIN_DTE_DAYS = 3.0             # 프론트 만기의 감마 폭발 구간을 피한다
MAX_DTE_DAYS = 21.0
TARGET_DELTA = 0.25
BAR_MIN = 15                   # 신호·가격 공통 해상도
SEED = 0


# ---------------------------------------------------------------- Black-Scholes
def _norm_cdf(x: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + np.vectorize(math.erf)(x / math.sqrt(2.0)))


def bs_delta(S: np.ndarray, K: np.ndarray, T: np.ndarray, sigma: np.ndarray, is_call: np.ndarray) -> np.ndarray:
    """r=0, q=0. Deribit 은 인버스 결제지만 스큐 정의에 쓰는 델타는 BS 로 충분하다."""
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(S / K) + 0.5 * sigma**2 * T) / (sigma * np.sqrt(T))
    nd1 = _norm_cdf(d1)
    return np.where(is_call, nd1, nd1 - 1.0)


# ---------------------------------------------------------------- signal series
def snapshot_metrics(path: str) -> dict | None:
    """스냅샷 하나 → {ts, spot, expiry, rr25, atm_iv}. 조건 미달이면 None."""
    cols = ["currency", "strike", "option_type", "mark_iv", "underlying_price", "expiry", "snapshot_ts"]
    df = pq.read_table(path, columns=cols).to_pandas()
    df = df[(df.currency == CURRENCY) & (df.mark_iv > 0)]
    if df.empty:
        return None

    ts = pd.Timestamp(df.snapshot_ts.iloc[0]).tz_convert("UTC")
    S = float(df.underlying_price.iloc[0])
    if not np.isfinite(S) or S <= 0:
        return None

    exp_ts = pd.to_datetime(df.expiry).dt.tz_localize("UTC") + pd.Timedelta(hours=EXPIRY_HOUR_UTC)
    dte = (exp_ts - ts).dt.total_seconds() / 86400.0
    df = df.assign(dte=dte.values)
    band = df[(df.dte >= MIN_DTE_DAYS) & (df.dte <= MAX_DTE_DAYS)]
    if band.empty:
        return None

    # 만기 계열을 하나로 고정: 밴드 안에서 가장 가까운 만기.
    target_exp = band.loc[band.dte.idxmin(), "expiry"]
    g = band[band.expiry == target_exp]
    T = float(g.dte.iloc[0]) / 365.0
    sigma = g.mark_iv.to_numpy() / 100.0
    delta = bs_delta(S, g.strike.to_numpy(dtype=float), T, sigma, (g.option_type == "C").to_numpy())

    calls = pd.DataFrame({"d": delta, "iv": sigma})[(g.option_type == "C").to_numpy()]
    puts = pd.DataFrame({"d": delta, "iv": sigma})[(g.option_type == "P").to_numpy()]
    calls = calls[(calls.d > 0.02) & (calls.d < 0.70)].sort_values("d")
    puts = puts[(puts.d < -0.02) & (puts.d > -0.70)].sort_values("d")
    # 보간이 외삽으로 새지 않도록 목표 델타를 양쪽 관측이 감싸야 한다.
    if len(calls) < 3 or len(puts) < 3:
        return None
    if not (calls.d.min() <= TARGET_DELTA <= calls.d.max()):
        return None
    if not (puts.d.min() <= -TARGET_DELTA <= puts.d.max()):
        return None

    iv_c = float(np.interp(TARGET_DELTA, calls.d, calls.iv))
    iv_p = float(np.interp(-TARGET_DELTA, puts.d, puts.iv))
    atm = float(np.interp(0.5, calls.d, calls.iv)) if calls.d.max() >= 0.5 else float(calls.iv.iloc[-1])
    return {"ts": ts, "spot": S, "expiry": str(target_exp), "rr25": iv_c - iv_p, "atm_iv": atm, "dte": T * 365.0}


def build_signal(paths: list[str]) -> pd.DataFrame:
    rows = [m for m in (snapshot_metrics(p) for p in paths) if m is not None]
    if not rows:
        raise RuntimeError("스냅샷에서 유효한 스큐를 하나도 만들지 못했다 — 데이터 경로/필드 확인")
    df = pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
    df["ts"] = df.ts.dt.floor(f"{BAR_MIN}min")
    return df.drop_duplicates("ts", keep="last").set_index("ts")


# ---------------------------------------------------------------- price series
def fetch_btc(start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    out, cur = [], start
    while cur < end:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": f"{BAR_MIN}m",
                    "startTime": int(cur.timestamp() * 1000), "limit": 1000},
            timeout=30,
        )
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
def newey_west_t(x: np.ndarray, y: np.ndarray, lags: int) -> tuple[float, float]:
    """단순회귀 y = a + b·x 의 (b, HAC t). 겹치는 창의 자기상관을 보정한다."""
    X = np.column_stack([np.ones_like(x), x])
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
    se = math.sqrt(max(cov[1, 1], 1e-30))
    return float(beta[1]), float(beta[1] / se)


def block_bootstrap_ci(x: np.ndarray, y: np.ndarray, block: int, n_boot: int = 2000) -> tuple[float, float]:
    """정상 블록 부트스트랩으로 상관계수 95% CI. 시계열 의존을 보존한다."""
    rng = np.random.default_rng(SEED)
    n = len(x)
    n_blocks = int(np.ceil(n / block))
    stats = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, n, n_blocks)
        idx = np.concatenate([np.arange(s, s + block) % n for s in starts])[:n]
        xi, yi = x[idx], y[idx]
        sx, sy = xi.std(), yi.std()
        stats[b] = 0.0 if sx < 1e-12 or sy < 1e-12 else float(np.corrcoef(xi, yi)[0, 1])
    return float(np.percentile(stats, 2.5)), float(np.percentile(stats, 97.5))


def n_for_power(rho: float, power: float = 0.80, alpha: float = 0.05) -> int:
    """|ρ| 를 양측 alpha 에서 power 로 검출하는 데 필요한 독립 관측 수 (Fisher z)."""
    if abs(rho) < 1e-6:
        return 10**9
    z = 0.5 * math.log((1 + abs(rho)) / (1 - abs(rho)))
    za, zb = 1.959963985, 0.8416212336  # alpha=0.05 양측, power=0.80
    return int(math.ceil((za + zb) ** 2 / z**2 + 3))


# ---------------------------------------------------------------- main
def main() -> int:
    paths = sorted(glob.glob(CHAIN_GLOB, recursive=True))
    if not paths:
        print(f"체인 스냅샷을 찾지 못했다: {CHAIN_GLOB}", file=sys.stderr)
        return 1
    print(f"스냅샷 {len(paths)}개 로드 중 …")
    sig = build_signal(paths)
    print(f"유효 스큐 관측 {len(sig)}개  |  {sig.index.min()} → {sig.index.max()}")
    print(f"만기 계열 전환 {sig.expiry.ne(sig.expiry.shift()).sum() - 1}회  |  평균 DTE {sig.dte.mean():.1f}d")

    px = fetch_btc(sig.index.min() - pd.Timedelta(hours=6), sig.index.max() + pd.Timedelta(hours=12))
    print(f"BTC 15분봉 {len(px)}개  |  {px.index.min()} → {px.index.max()}")

    d = sig.join(px.rename("px"), how="inner").dropna(subset=["px"])
    step = 60 // BAR_MIN  # 1시간 = 4봉

    # 신호: 같은 만기 계열 안에서만 Δ를 취한다 (계약 교체 점프 제거)
    d["d_rr"] = d.rr25.diff(step)
    same = d.expiry == d.expiry.shift(step)
    d.loc[~same, "d_rr"] = np.nan
    d["r_past"] = np.log(d.px / d.px.shift(step))
    dropped = int((~same).sum())

    lr = np.log(d.px)
    horizons = [1, 2, 4, 8]
    for h in horizons:
        d[f"fwd{h}"] = lr.shift(-h * step) - lr

    d = d.dropna(subset=["d_rr", "r_past"])
    print(f"만기 전환으로 버린 Δ {dropped}개  |  분석 관측 {len(d)}개\n")

    # ---- 1) 리드-래그 프로파일 : 피크가 k>0 이어야 메커니즘이 성립 ----------
    print("=" * 74)
    print("[1] 리드-래그 프로파일   corr( ΔRR25(t) , r(t→t+k) )")
    print("    k<0 은 과거 수익률 = 스큐가 후행한다는 뜻 → 메커니즘 기각")
    print("=" * 74)
    print(f"{'k (시간)':>10} {'corr':>9} {'n':>6}")
    prof = {}
    for k in [-4, -2, -1, 1, 2, 4, 8]:
        col = lr.shift(-k * step) - lr if k > 0 else lr - lr.shift(-k * step)
        s = pd.concat([d.d_rr, col.rename("r")], axis=1).dropna()
        if len(s) < 20:
            continue
        c = float(np.corrcoef(s.d_rr, s.r)[0, 1])
        prof[k] = c
        mark = "  ←피크" if abs(c) == max(abs(v) for v in prof.values()) else ""
        print(f"{k:>10} {c:>9.3f} {len(s):>6}{mark}")
    peak_k = max(prof, key=lambda k: abs(prof[k]))
    print(f"\n    피크 k = {peak_k:+d}h  →  " + ("스큐가 선행" if peak_k > 0 else "스큐가 후행 (기각 신호)"))

    # ---- 2) 전방 수익률 회귀 (HAC) -----------------------------------------
    print("\n" + "=" * 74)
    print("[2] 전방 수익률 회귀   r_fwd = a + b·ΔRR25     (Newey-West HAC)")
    print("=" * 74)
    print(f"{'지평':>6} {'n':>6} {'corr':>8} {'부트 95% CI':>20} {'HAC t':>8} {'부호일치':>9}")
    results = {}
    for h in horizons:
        s = d[["d_rr", f"fwd{h}"]].dropna()
        if len(s) < 30:
            continue
        x, y = s.d_rr.to_numpy(), s[f"fwd{h}"].to_numpy()
        c = float(np.corrcoef(x, y)[0, 1])
        lo, hi = block_bootstrap_ci(x, y, block=h * step)
        _, t = newey_west_t(x, y, lags=h * step)
        agree = float(np.mean(np.sign(x) == np.sign(y)))
        results[h] = (c, lo, hi, t, len(s))
        print(f"{h:>5}h {len(s):>6} {c:>8.3f}  [{lo:>7.3f},{hi:>7.3f}] {t:>8.2f} {agree*100:>8.1f}%")

    # ---- 3) 게이트: 현물이 아직 안 움직였을 때만 ---------------------------
    print("\n" + "=" * 74)
    print("[3] 게이트 적용   |r_past(1h)| < 0.3%  (현물 선반영 제거)")
    print("=" * 74)
    g = d[d.r_past.abs() < 0.003]
    print(f"    게이트 통과 {len(g)} / {len(d)} ({len(g)/len(d)*100:.0f}%)")
    print(f"{'지평':>6} {'n':>6} {'corr':>8} {'부호일치':>9}")
    for h in horizons:
        s = g[["d_rr", f"fwd{h}"]].dropna()
        if len(s) < 20:
            continue
        c = float(np.corrcoef(s.d_rr, s[f"fwd{h}"])[0, 1])
        agree = float(np.mean(np.sign(s.d_rr) == np.sign(s[f"fwd{h}"])))
        print(f"{h:>5}h {len(s):>6} {c:>8.3f} {agree*100:>8.1f}%")

    # ---- 4) 검정력 : 며칠이 더 필요한가 ------------------------------------
    print("\n" + "=" * 74)
    print("[4] 검정력 분석   — 지금 판정이 가능한가")
    print("=" * 74)
    days = (d.index.max() - d.index.min()).total_seconds() / 86400.0
    for h in horizons:
        if h not in results:
            continue
        c, lo, hi, t, n = results[h]
        indep = max(int(days * 24 / h), 1)          # 겹치지 않는 관측 수
        need = n_for_power(abs(c))
        verdict = "판정 가능" if indep >= need else f"부족 → 약 {math.ceil(need*h/24)}일 필요"
        print(f"  {h}h: 독립 관측 {indep}개, |ρ|={abs(c):.3f} 검출에 {need}개 필요 → {verdict}")
    print(f"\n  현재 표본 기간 {days:.1f}일. 지평 4개를 동시에 봤으므로 다중검정 보정 전이다")
    print("  (Bonferroni 적용 시 유의 임계 t 는 |t| > 2.50 수준).")

    print("\n" + "=" * 74)
    print("판정 규칙 (로드맵 §3 반증 조건)")
    print("=" * 74)
    print("  기각  : 리드-래그 피크가 k<0  또는  4h |ρ|<0.05 가 n≥30 에서 확인")
    print("  유지  : 피크가 k>0 이고 부트스트랩 CI 가 0 을 포함하지 않음")
    print("  보류  : 그 외 — 표본을 더 쌓고 재검정")
    return 0


if __name__ == "__main__":
    sys.exit(main())
