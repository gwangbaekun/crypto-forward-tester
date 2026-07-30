"""드로다운 매수 — 원장 → 대시보드용 집계.

engine 은 **기록**만 한다. 여기서는 기록된 원장을 읽어 판단 재료로 바꾼다.
집계 전용이라 네트워크를 타지 않는다 — 원장(JSON/DB) 한 번 읽고 끝이라 대시보드가
얼마나 자주 열리든 스캔 비용(일 1회)과 무관하다.

────────────────────────────────────────────────────────────
핵심 질문: "물타기가 실제로 도움이 됐나"
────────────────────────────────────────────────────────────
같은 신호를 두 가지로 재는 것 말고는 답할 방법이 없다.

    single  1차 진입가에 1주만 사고 끝    → last / entry_price - 1
    ladder  -30/-40/-50% 에 등주수 3회    → last / avg_cost   - 1

`avg_cost` 가 체결가의 **산술평균**이고 매회 같은 주식 수를 사므로
(총평가 = n × last, 총원가 = Σp = n × avg_cost) ladder 수익률은
`last/avg_cost - 1` 로 자본가중까지 정확히 맞는다. 두 값 모두 ROI 라 직접 비교 가능하다.

단, **투입자본이 다르다**. ladder 는 Σp, single 은 p₀ 를 쓴다. ROI 가 같아도
ladder 쪽이 절대 손익은 크다. 그래서 `capital_units` 를 같이 실어보내
"수익률은 좋은데 돈이 3배 들어갔다"를 대시보드에서 감출 수 없게 한다.

`n_fills == 1` 인 에피소드는 두 전략이 동일하므로 비교 모수에서 제외한다
(포함하면 edge 가 0 인 표본이 섞여 평균이 희석된다).

────────────────────────────────────────────────────────────
backfilled 분리
────────────────────────────────────────────────────────────
원장 생성 전에 시작된 에피소드는 사후 발견이다. 섞어서 평균 내면 포워드 테스트가
아니라 그냥 백테스트가 된다. 모든 집계는 `cohort` 축으로 갈라서 낸다.
"""
from __future__ import annotations

import datetime as dt
import statistics
from typing import Any, Iterable, Optional

from features.strategy.drawdown_signal.engine import (
    FILL_LEVELS,
    LADDER_FILLS,
    PATH_BUCKETS,
    REF_WINDOW,
    RESET,
    TRIGGER,
    UNIVERSE,
    load_ledger,
)

# ── 자산군 분류 ────────────────────────────────────────────
# UNIVERSE 심볼 표기법만으로 갈린다 (yfinance 규약). 별도 매핑 테이블을 두면
# UNIVERSE 에 종목을 추가할 때 두 곳을 고쳐야 해서 조용히 어긋난다.
ASSET_CLASSES = ("crypto", "stock", "index", "commodity")

ASSET_CLASS_LABELS = {
    "crypto": "Crypto",
    "stock": "인기 종목",
    "index": "지수",
    "commodity": "원자재",
}


def asset_class(symbol: str) -> str:
    if symbol.endswith("-USD"):
        return "crypto"
    if symbol.startswith("^"):
        return "index"
    if symbol.endswith("=F"):
        return "commodity"
    return "stock"


def _days_between(start: Optional[str], end: Optional[str]) -> Optional[int]:
    if not start or not end:
        return None
    try:
        return (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
    except ValueError:
        return None


def _mean(vals: list[float]) -> Optional[float]:
    return round(statistics.fmean(vals), 2) if vals else None


def _median(vals: list[float]) -> Optional[float]:
    return round(statistics.median(vals), 2) if vals else None


def enrich(ep: dict) -> dict:
    """원장 에피소드 1건 → 대시보드가 쓰는 파생 필드까지 붙인 사본.

    원장을 건드리지 않고 사본에만 얹는다 — 파생값을 저장하면 규칙이 바뀔 때
    옛 계산이 남아 조용히 틀린다.
    """
    out = dict(ep)
    sym = ep.get("symbol", "")
    out["asset_class"] = asset_class(sym)
    out["asset_class_label"] = ASSET_CLASS_LABELS[out["asset_class"]]

    entry = ep.get("entry_price")
    avg = ep.get("avg_cost")
    last = ep.get("last_price")
    n_fills = int(ep.get("n_fills") or 0)

    # single = 1차 진입만, ladder = 실제 원장 수익률
    single_ret = round((last / entry - 1) * 100, 2) if entry and last else None
    ladder_ret = ep.get("ret_pct")
    out["single_ret_pct"] = single_ret
    out["ladder_ret_pct"] = ladder_ret
    out["ladder_edge_pp"] = (
        round(ladder_ret - single_ret, 2)
        if (single_ret is not None and ladder_ret is not None and n_fills > 1)
        else None
    )
    # 투입 자본 배수 — 등주수라 체결 횟수가 곧 배수는 아니다 (가격이 다름).
    # Σp / p₀ 가 실제 배수.
    fills = ep.get("fills") or []
    prices = [f.get("price") for f in fills if f.get("price")]
    out["capital_units"] = (
        round(sum(prices) / prices[0], 2) if prices and prices[0] else None
    )
    out["avg_cost_discount_pct"] = (
        round((avg / entry - 1) * 100, 2) if entry and avg else None
    )

    end_date = ep.get("exit_date") if ep.get("status") == "closed" else ep.get("last_date")
    out["hold_days"] = _days_between(ep.get("signal_date"), end_date)
    out["cohort"] = "backfilled" if ep.get("backfilled") else "out_of_sample"
    return out


def _cohort_stats(eps: list[dict]) -> dict[str, Any]:
    """열림/청산을 나눠 집계. 열린 것과 청산된 것을 한 평균에 넣으면 의미가 없다."""
    open_eps = [e for e in eps if e.get("status") == "open"]
    closed = [e for e in eps if e.get("status") == "closed"]

    open_rets = [e["ret_pct"] for e in open_eps if e.get("ret_pct") is not None]
    closed_rets = [e["ret_pct"] for e in closed if e.get("ret_pct") is not None]
    maes = [e["mae_pct"] for e in eps if e.get("mae_pct") is not None]
    uw = [e["underwater_days"] for e in eps if e.get("underwater_days") is not None]
    holds = [e["hold_days"] for e in closed if e.get("hold_days") is not None]
    wins = [r for r in closed_rets if r > 0]

    # 물타기 비교 — 체결 2회 이상만
    laddered = [e for e in eps if (e.get("n_fills") or 0) > 1 and e.get("ladder_edge_pp") is not None]
    edges = [e["ladder_edge_pp"] for e in laddered]

    return {
        "total": len(eps),
        "open": len(open_eps),
        "closed": len(closed),
        "open_avg_ret_pct": _mean(open_rets),
        "open_median_ret_pct": _median(open_rets),
        "open_worst_ret_pct": round(min(open_rets), 2) if open_rets else None,
        "open_best_ret_pct": round(max(open_rets), 2) if open_rets else None,
        "closed_avg_ret_pct": _mean(closed_rets),
        "closed_win_rate": round(len(wins) / len(closed_rets) * 100, 1) if closed_rets else None,
        "avg_mae_pct": _mean(maes),
        "worst_mae_pct": round(min(maes), 2) if maes else None,
        "avg_underwater_days": _mean([float(d) for d in uw]),
        "max_underwater_days": max(uw) if uw else None,
        "avg_hold_days": _mean([float(d) for d in holds]),
        "ladder": {
            "n_laddered": len(laddered),
            "avg_edge_pp": _mean(edges),
            "median_edge_pp": _median(edges),
            "helped": sum(1 for e in edges if e > 0),
            "hurt": sum(1 for e in edges if e < 0),
            "avg_single_ret_pct": _mean(
                [e["single_ret_pct"] for e in laddered if e.get("single_ret_pct") is not None]
            ),
            "avg_ladder_ret_pct": _mean(
                [e["ladder_ret_pct"] for e in laddered if e.get("ladder_ret_pct") is not None]
            ),
            "avg_capital_units": _mean(
                [e["capital_units"] for e in laddered if e.get("capital_units") is not None]
            ),
        },
    }


def _return_curve(eps: list[dict]) -> dict[str, Any]:
    """보유 기간별 중앙값 수익률 곡선 — 래더 vs 1차 진입만.

    평균이 아니라 **중앙값**이다. 표본이 수십 건이고 크립토 한두 종목이 -40% 로 끌면
    평균은 그 한 종목 얘기가 된다.

    구간별 n 을 같이 낸다. 최근 신호는 +250d 구간이 아직 없어서 뒤로 갈수록 n 이 준다 —
    이걸 숨기면 뒤쪽 점이 앞쪽과 같은 무게로 보인다. 차트에 n 을 찍는 이유.

    모수는 **에피소드 전체**다 (체결 1회짜리 포함). 실제로 이 전략을 돌렸을 때
    받게 될 곡선이라 그렇다 — 체결 2회 이상만 걸러 비교하는 건 `_cohort_stats.ladder` 쪽.
    """
    buckets = [str(b) for b in PATH_BUCKETS]
    ladder: list[Optional[float]] = []
    single: list[Optional[float]] = []
    counts: list[int] = []

    for b in buckets:
        lv = [e["path"][b]["ladder"] for e in eps if e.get("path", {}).get(b)]
        sv = [e["path"][b]["single"] for e in eps if e.get("path", {}).get(b)]
        ladder.append(_median(lv))
        single.append(_median(sv))
        counts.append(len(lv))

    return {
        "buckets": [int(b) for b in buckets],
        "labels": ["진입일"] + [f"+{b}d" for b in PATH_BUCKETS[1:]],
        "ladder": ladder,
        "single": single,
        "n": counts,
        # 경로가 아예 없는 원장(구버전에서 만든 것)과 구분 — 차트에서 안내 문구를 띄운다.
        "has_data": any(c > 0 for c in counts),
    }


def _by_asset_class(eps: list[dict]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for cls in ASSET_CLASSES:
        subset = [e for e in eps if e.get("asset_class") == cls]
        if subset:
            out[cls] = {"label": ASSET_CLASS_LABELS[cls], **_cohort_stats(subset)}
    return out


def build_stats(eps: Optional[Iterable[dict]] = None, led: Optional[dict] = None) -> dict[str, Any]:
    """원장 전체 집계 — 라우터·CLI 공용.

    `eps`/`led` 를 주면 그걸 쓰고, 없으면 원장을 읽는다 (테스트·재사용).
    """
    if eps is None or led is None:
        led, _ = load_ledger()
        eps = led.get("episodes", [])
    enriched = [enrich(e) for e in eps]

    oos = [e for e in enriched if e["cohort"] == "out_of_sample"]
    back = [e for e in enriched if e["cohort"] == "backfilled"]

    return {
        "started_at": led.get("started_at"),
        "last_run": led.get("last_run"),
        "universe_size": len(UNIVERSE),
        "rules": {
            "ref_window": REF_WINDOW,
            "trigger_pct": round(TRIGGER * 100, 1),
            "reset_pct": round(RESET * 100, 1),
            "ladder_fills": LADDER_FILLS,
            "fill_levels_pct": [round(v * 100, 1) for v in FILL_LEVELS],
        },
        # out_of_sample 이 판단의 본체. all 은 참고용이라 뒤에 둔다.
        "out_of_sample": _cohort_stats(oos),
        "backfilled": _cohort_stats(back),
        "all": _cohort_stats(enriched),
        "curve": {
            "out_of_sample": _return_curve(oos),
            "backfilled": _return_curve(back),
            "all": _return_curve(enriched),
        },
        # 코호트 3종을 모두 담는다 — 대시보드가 코호트를 바꿀 때 그대로 골라 쓰게.
        "by_asset_class": {
            "out_of_sample": _by_asset_class(oos),
            "backfilled": _by_asset_class(back),
            "all": _by_asset_class(enriched),
        },
    }


def build_episodes(status: Optional[str] = None, cohort: Optional[str] = None) -> dict[str, Any]:
    """대시보드 테이블용 에피소드 목록 (파생 필드 포함)."""
    led, _ = load_ledger()
    eps = [enrich(e) for e in led.get("episodes", [])]
    if status:
        eps = [e for e in eps if e.get("status") == status]
    if cohort:
        eps = [e for e in eps if e["cohort"] == cohort]

    open_eps = sorted(
        [e for e in eps if e.get("status") == "open"],
        key=lambda x: (x.get("ret_pct") is None, x.get("ret_pct")),
    )
    closed_eps = sorted(
        [e for e in eps if e.get("status") == "closed"],
        key=lambda x: x.get("exit_date") or "",
        reverse=True,
    )
    return {
        "started_at": led.get("started_at"),
        "last_run": led.get("last_run"),
        "open": open_eps,
        "closed": closed_eps,
    }
