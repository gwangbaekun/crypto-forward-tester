"""Polymarket 질문 텍스트 → 세분화 섹터 (대시보드·필터 공용)."""
from __future__ import annotations

import re
from typing import NamedTuple

# 표시 순서 (대시보드 차트)
SECTOR_ORDER: list[str] = [
    "Esports_Prop",
    "Esports_Match",
    "Sports_Prop",
    "Sports_Match",
    "Politics",
    "Economics",
    "Crypto",
    "Weather",
    "Entertainment",
    "Other",
]

# 기본 제외(도박성 prop) — 대시보드 기본 필터
DEFAULT_EXCLUDE: frozenset[str] = frozenset({
    "Esports_Prop",
    "Sports_Prop",
    "Weather",
    "Entertainment",
    "Crypto",
    "Other",
})

# 진입 허용 섹터 — 엔진·rotation 화이트리스트
# Esports_Match 제거 (78.6% WR, -3.69 pnl), Esports_Prop 추가 (91.8% WR, +7.24 pnl)
ALLOWED_SECTORS: frozenset[str] = frozenset({
    "Esports_Prop",
    "Sports_Match",
    "Politics",
    "Economics",
})


class _Rule(NamedTuple):
    sector: str
    patterns: list[str]


# 위에서 아래로 우선 매칭
_RULES: list[_Rule] = [
    _Rule("Esports_Prop", [
        r"game \d+",       # "Game 3 Winner", "Game 3:" 등 — 특정 게임 단위 prop
        r"\bmap \d+",      # "Map 2 Winner", "Map 2:" 등 — 특정 맵 단위 prop
        r"\bmap handicap", # "Map Handicap: ..." — 핸디캡 prop
        r"baron nashor",
        r"\broshan\b",
        r"\binhibitor",
        r"penta\s*kill",
        r"quadra\s*kill",
        r"ultra\s*kill",
        r"\brampage\b",
        r"odd/even total kills",
        r"total kills over/under",
        r"both teams (slay|destroy|beat)",
        r"any player",
        r"first blood",
        r"first tower",
    ]),
    _Rule("Esports_Match", [
        r"\blol:",
        r"league of legends",
        r"\bdota\b",
        r"\bvalorant\b",
        r"\bcs2\b",
        r"counter-strike",
        r"\bbo[357]\b",
        r"esports",
        r"circuito desafiante",
        r"rift legends",
    ]),
    _Rule("Sports_Prop", [
        r"\bspread\s*:",
        r"game handicap",
        r"games total",
        r"\bo/u\b",
        r"over/under",
        r"\brebounds\b",
        r"\bassists\b",
        r"\bpoints o/u",
        r"triple-double",
        r"finish (in|under|top|at|within) (the )?top \d",
        r"will .{0,80} finish in the top",
        r"exact score:",
        r"odd/even",
    ]),
    _Rule("Sports_Match", [
        r"win on 20\d{2}-",
        r"end in a draw",
        r"\b(fc|sc|ac|if|bk|sk) (vs|v\.)\b",
        r"\bvs\.? .{1,40} (fc|sc|united|city|madrid|barcelona)\b",
        r"\bnba\b", r"\bnfl\b", r"\bnhl\b", r"\bmlb\b",
        r"\bsoccer\b", r"\bfootball\b", r"\bbasketball\b",
        r"world cup", r"super bowl", r"\bplayoff\b",
        r"\btournament\b", r"\bchampionship\b",
        r"\bgolf\b", r"\bpga\b", r"\bufc\b",
    ]),
    _Rule("Politics", [
        r"\belection\b", r"\bpresident\b", r"\bsenate\b", r"\bcongress\b",
        r"\bvote\b", r"\brepublican\b", r"\bdemocrat\b", r"\bgovernor\b",
        r"\btrump\b", r"\bbiden\b", r"\bharris\b", r"\bparliament\b",
        r"\breferendum\b", r"\bballot\b", r"\bprimary\b", r"\bnomination\b",
        r"truth social", r"\bmayoral\b", r"approval rating",
        r"turnout in the",
    ]),
    _Rule("Economics", [
        r"\bfed\b", r"\bfomc\b", r"\binflation\b", r"interest rate",
        r"\bgdp\b", r"\brecession\b", r"\bunemployment\b",
        r"\bcpi\b", r"\bppi\b", r"rate cut", r"rate hike", r"\bpayroll\b",
        r"\btreasury\b", r"\byield\b", r"reserve bank", r"central bank",
        r"official cash rate", r"bank of israel", r"\becb\b", r"\bboj\b",
    ]),
    _Rule("Crypto", [
        r"\bbitcoin\b", r"\bbtc\b", r"\beth\b", r"\bethereum\b",
        r"\bcrypto\b", r"\bsol\b", r"\bdoge\b", r"\bxrp\b",
        r"\bdefi\b", r"\bnft\b", r"\busdt\b", r"stablecoin",
        r"up or down",
    ]),
    _Rule("Weather", [
        r"\bweather\b", r"\brain\b", r"temperature", r"°c\b",
        r"\bcelsius\b", r"highest temp", r"\bhurricane\b",
        r"\btyphoon\b", r"\bstorm\b", r"\bsnow\b", r"\bflood\b",
    ]),
    _Rule("Entertainment", [
        r"\boscar\b", r"\bgrammy\b", r"\bemmy\b", r"\baward\b",
        r"\bmovie\b", r"\bfilm\b", r"\bactor\b", r"\bcelebrity\b",
        r"\balbum\b", r"box office",
    ]),
]

_COMPILED: list[tuple[str, list[re.Pattern[str]]]] = [
    (rule.sector, [re.compile(p, re.I) for p in rule.patterns])
    for rule in _RULES
]


def classify_sector(question: str | None) -> str:
    """질문 → 세분화 섹터명. 매칭 없으면 Other."""
    q = (question or "").strip()
    if not q:
        return "Other"
    for sector, patterns in _COMPILED:
        if any(p.search(q) for p in patterns):
            return sector
    return "Other"


def sector_label(sector: str) -> str:
    """UI 표시용."""
    return sector.replace("_", " ")


def is_gambling_prop(sector: str) -> bool:
    """고가여도 실질 도박성 prop."""
    return sector in ("Esports_Prop", "Esports_Match", "Sports_Prop")
