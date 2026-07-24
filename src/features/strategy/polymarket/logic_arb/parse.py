"""Logic-Arb — BTC 가격 임계값 사다리 파서 (순수 함수).

제목 유사성으로 동치를 추정하지 않는다. 질문 텍스트에서 **방향(GT/LT/RANGE) +
해상도 basis(TERMINAL/TOUCH) + 숫자 임계값**을 산술적으로 파싱하고, 파싱에 성공한
마켓만 관계 그래프에 넣는다. 파싱 실패 = 후보 탈락 (보수적). "기계적 검증 선행"의 1차 게이트.

해상도 basis 구분이 핵심:
  TERMINAL — 종가/시점 기준 ("above/below $X on <date>").
  TOUCH    — 배리어/터치 기준 ("reach/dip to $X by <date>"): 기간 중 한 번이라도 도달.
basis 가 다르면 규칙이 달라 동치·포함이 성립하지 않으므로 절대 교차 차익 대상이 안 된다.

PoC 범위: BTC 가격 임계값 사다리 (문장 구조 단순, 관계가 산술적).
LLM 규칙 정규화(규칙 원문 대조)는 이 계층을 넘어서는 확장 지점 — 여기서는 하지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# BTC 지칭 (다른 자산 사다리와 섞이지 않도록)
_BTC_RE = re.compile(r"\b(bitcoin|btc)\b", re.I)

# 방향 x basis 키워드
_GT_TERMINAL_RE = re.compile(r"\b(above|over|exceed(?:s)?|greater than|more than|at least|higher than)\b", re.I)
_LT_TERMINAL_RE = re.compile(r"\b(below|under|less than|lower than)\b", re.I)
_GT_TOUCH_RE = re.compile(r"\b(reach(?:es)?|climb(?:s)? to|top(?:s)?)\b", re.I)
_LT_TOUCH_RE = re.compile(r"\b(dip(?:s)? to|drop(?:s)? to|fall(?:s)? to|down to)\b", re.I)
_BETWEEN_RE = re.compile(r"\bbetween\b", re.I)

# $100,000 / $100k / 100K / $1.2M 등
_MONEY_RE = re.compile(r"\$?\s?([0-9][0-9,]*\.?[0-9]*)\s?([kmKM])?")

GT = "GT"          # BTC > threshold (또는 상향 터치) 이면 YES
LT = "LT"          # BTC < threshold (또는 하향 터치) 이면 YES
RANGE = "RANGE"

TERMINAL = "TERMINAL"
TOUCH = "TOUCH"


@dataclass(frozen=True)
class ThresholdSpec:
    direction: str          # GT | LT | RANGE
    lo: float               # GT/LT: 임계값. RANGE: 하한
    hi: float | None = None # RANGE: 상한. 그 외 None
    basis: str = TERMINAL   # TERMINAL | TOUCH — 해상도 규칙 구분 (교차 금지)
    confidence: float = 1.0


def _to_value(num: str, suffix: str | None) -> float | None:
    try:
        v = float(num.replace(",", ""))
    except ValueError:
        return None
    if suffix:
        s = suffix.lower()
        if s == "k":
            v *= 1_000
        elif s == "m":
            v *= 1_000_000
    # BTC 가격 사다리 sanity: 1k~10M 범위 밖은 임계값이 아니라고 본다 (연도/개수 등 오탐 방지)
    if v < 1_000 or v > 10_000_000:
        return None
    return v


def _money_values(text: str) -> list[float]:
    out: list[float] = []
    for m in _MONEY_RE.finditer(text):
        v = _to_value(m.group(1), m.group(2))
        if v is not None:
            out.append(v)
    return out


def parse_btc_threshold(question: str) -> ThresholdSpec | None:
    """BTC 가격 임계값 파싱. 실패/모호 시 None (= 후보 탈락)."""
    if not question or not _BTC_RE.search(question):
        return None

    vals = _money_values(question)
    if not vals:
        return None

    if _BETWEEN_RE.search(question) and len(vals) >= 2:
        lo, hi = sorted(vals[:2])
        if lo == hi:
            return None
        return ThresholdSpec(RANGE, lo, hi, basis=TERMINAL, confidence=0.9)

    # (direction, basis) 를 정확히 하나만 매칭해야 확정. 충돌·부재면 탈락.
    matches: list[tuple[str, str]] = []
    if _GT_TERMINAL_RE.search(question):
        matches.append((GT, TERMINAL))
    if _LT_TERMINAL_RE.search(question):
        matches.append((LT, TERMINAL))
    if _GT_TOUCH_RE.search(question):
        matches.append((GT, TOUCH))
    if _LT_TOUCH_RE.search(question):
        matches.append((LT, TOUCH))

    if len(matches) != 1:
        return None

    direction, basis = matches[0]
    return ThresholdSpec(direction, vals[0], None, basis=basis, confidence=1.0)


# ---------------------------------------------------------------------------
# 그룹핑: 동일 해상도 시점(end_ts ±tol) + 동일 방향 + 동일 basis = 하나의 사다리
# ---------------------------------------------------------------------------

@dataclass
class LadderMarket:
    market: dict            # _data.client._normalize 결과
    spec: ThresholdSpec

    @property
    def end_ts(self) -> int | None:
        return self.market.get("end_ts")


@dataclass
class Ladder:
    direction: str          # GT | LT | RANGE
    basis: str              # TERMINAL | TOUCH
    end_ts: int             # 그룹 대표 마감시각
    slug: str | None
    members: list[LadderMarket]


def build_ladder_markets(markets: list[dict]) -> list[LadderMarket]:
    """파싱 성공한 BTC 마켓만 LadderMarket 으로."""
    out: list[LadderMarket] = []
    for m in markets:
        spec = parse_btc_threshold(m.get("question", ""))
        if spec is None:
            continue
        if m.get("end_ts") is None:
            continue
        if not m.get("yes_token_id") or not m.get("no_token_id"):
            continue
        out.append(LadderMarket(market=m, spec=spec))
    return out


def group_ladders(
    lms: list[LadderMarket],
    tol_sec: int,
    require_same_slug: bool = False,
) -> list[Ladder]:
    """동일 해상도 시점 + 동일 방향 + 동일 basis 로 사다리 그룹핑.

    end_ts 를 tol_sec 버킷으로 양자화. 방향(GT/LT/RANGE)·basis(TERMINAL/TOUCH)가
    다르면 관계 구조·해상도 규칙이 달라 별도 그룹. require_same_slug 면 slug 도 강제.
    """
    buckets: dict[tuple, list[LadderMarket]] = {}
    for lm in lms:
        assert lm.end_ts is not None
        key_ts = round(lm.end_ts / max(tol_sec, 1))
        slug = lm.market.get("slug") if require_same_slug else None
        key = (lm.spec.direction, lm.spec.basis, key_ts, slug)
        buckets.setdefault(key, []).append(lm)

    ladders: list[Ladder] = []
    for (direction, basis, _kts, slug), members in buckets.items():
        # 동일 임계값 중복 마켓 제거 (Polymarket 은 같은 질문을 2행 반환하기도)
        seen_lo: dict[float, LadderMarket] = {}
        for m in members:
            key_lo = m.spec.lo
            if key_lo not in seen_lo or _vol(m) > _vol(seen_lo[key_lo]):
                seen_lo[key_lo] = m
        uniq = list(seen_lo.values())
        if len(uniq) < 2:
            continue  # 사다리는 최소 2개 시장 필요
        rep_end = min(m.end_ts for m in uniq)  # type: ignore[type-var]
        ladders.append(Ladder(direction=direction, basis=basis, end_ts=rep_end,
                              slug=slug, members=uniq))
    return ladders


def _vol(lm: LadderMarket) -> float:
    return float(lm.market.get("volume_usd") or 0.0)
