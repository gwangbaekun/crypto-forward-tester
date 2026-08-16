"""Gamma Wall — 원장 집계.

두 가지를 절대 섞지 않는다:
  1. **코호트** — 첫 실행이 소급 계산한 `backfilled` 와 이후 실시간 기록분.
     섞으면 포워드 테스트가 아니라 백테스트다.
  2. **대조군** — 실제 벽 밴드 vs 같은 폭의 대칭 밴드. 벽이 '폭'이 아니라
     '위치' 정보를 담아야만 이긴다. 차이가 0 이면 벽은 아무것도 아니다.

표본이 부족할 때 유의성을 주장하지 않기 위해, 필요 표본 수를 같이 낸다.
"""
from __future__ import annotations

import math

from features.strategy.us_options_gamma_wall.engine import load_ledger


def _wilson(k: int, n: int, z: float = 1.959963985) -> tuple[float, float]:
    """이항 비율의 Wilson 신뢰구간. n 이 작을 때 정규근사보다 정직하다."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _n_for_binomial(effect: float, base: float = 0.5) -> int:
    """base 대비 effect(절대 비율차)를 80% 검정력·양측 5% 로 검출할 표본 수."""
    if effect <= 0:
        return 10**9
    p1, p2 = base, min(0.999, base + effect)
    pbar = (p1 + p2) / 2
    num = (1.959963985 * math.sqrt(2 * pbar * (1 - pbar)) + 0.8416212336
           * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    return int(math.ceil(num / (effect**2)))


def _agg(recs: list[dict]) -> dict:
    done = [r for r in recs if "result" in r]
    n = len(done)
    if n == 0:
        return {"n": 0, "n_pending": len(recs)}
    res = [r["result"] for r in done]
    cont = sum(r["contained"] for r in res)
    null = sum(r["null_contained"] for r in res)
    tc = sum(r["touched_call"] for r in res)
    tp = sum(r["touched_put"] for r in res)
    rc = sum(r["respected_call"] for r in res)
    rp = sum(r["respected_put"] for r in res)
    lo, hi = _wilson(cont, n)
    edge = cont / n - null / n
    return {
        "n": n,
        "n_pending": len(recs) - n,
        "contained_pct": round(cont / n * 100, 1),
        "contained_ci": [round(lo * 100, 1), round(hi * 100, 1)],
        "null_contained_pct": round(null / n * 100, 1),
        "edge_pp": round(edge * 100, 1),
        "touch_call_pct": round(tc / n * 100, 1),
        "touch_put_pct": round(tp / n * 100, 1),
        "respect_call_pct": round(rc / tc * 100, 1) if tc else None,
        "respect_put_pct": round(rp / tp * 100, 1) if tp else None,
        "avg_band_pct": round(sum(r["band_up_pct"] + r["band_dn_pct"] for r in done) / n, 2),
        "avg_next_range_pct": round(sum(r["next_range_pct"] for r in res) / n, 2),
        "n_needed": _n_for_binomial(abs(edge)) if abs(edge) > 1e-9 else None,
        "verdict": _verdict(n, edge),
    }


def _verdict(n: int, edge: float) -> str:
    if abs(edge) < 1e-9:
        return "대조군과 동일 — 벽에 위치 정보 없음"
    need = _n_for_binomial(abs(edge))
    if n >= need:
        return "판정 가능: " + ("벽 우위" if edge > 0 else "벽 열위")
    return f"판정 불가 — 표본 {n}/{need}"


def build_stats() -> dict:
    led = load_ledger()
    recs = list(led.get("sessions", {}).values())
    live = [r for r in recs if not r.get("backfilled")]
    back = [r for r in recs if r.get("backfilled")]
    return {
        "last_run": led.get("last_run"),
        "total_sessions": len(recs),
        "cohorts": {
            "out_of_sample": _agg(live),   # 실시간 기록분 — 유일하게 포워드 테스트인 것
            "backfilled": _agg(back),      # 첫 실행 소급분 — 참고용
        },
        "note": "out_of_sample 만 포워드 테스트다. backfilled 는 백테스트이므로 합산하지 않는다.",
    }


def build_ledger_rows(limit: int = 200) -> list[dict]:
    led = load_ledger()
    rows = sorted(led.get("sessions", {}).values(), key=lambda r: r["session"], reverse=True)
    return rows[:limit]


def latest_levels() -> dict:
    led = load_ledger()
    rows = sorted(led.get("sessions", {}).values(), key=lambda r: r["session"])
    if not rows:
        return {"error": "원장 비어있음 — /run 을 먼저 실행"}
    r = rows[-1]
    return {
        "session": r["session"], "snapshot_ts": r.get("snapshot_ts"),
        "spot": r["spot"], "call_wall": r["call_wall"], "put_wall": r["put_wall"],
        "total_gex_bn": r["total_gex_bn"],
        "exec_note": "SPY 레벨. US500 집행 시 비율 환산 필요 (US500/SPY).",
    }
