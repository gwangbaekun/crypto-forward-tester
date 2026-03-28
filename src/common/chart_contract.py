"""
btc_backtest `/api/chart` 와 동일한 JSON 계약 (캔들 캐시/DB 도입 시 맞춤).

참조: `btc_backtest/src/backtest/serve_backtest.py` — `api_chart`

응답 형태:
{
  "candles": [ {"time": <unix_sec>, "open", "high", "low", "close"}, ... ],
  "volumes": [ {"time", "value", "color"}, ... ],
  "cvd_1h":  [ float, ... ]   # 봉 순서는 candles 와 동일
}

확대 1m 컨텍스트는 backtest 의 `/api/1m?ts=...` 패턴을 동일하게 두면
forward 거래 DTO 의 entry_ts/exit_ts 로 윈도우를 잡을 수 있다.
"""
from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class CandleBar(TypedDict):
    time: int
    open: float
    high: float
    low: float
    close: float


class VolumeBar(TypedDict):
    time: int
    value: float
    color: str


class ChartPayloadV1(TypedDict, total=False):
    candles: List[CandleBar]
    volumes: List[VolumeBar]
    cvd_1h: List[float]


def empty_chart_payload() -> Dict[str, Any]:
    return {"candles": [], "volumes": [], "cvd_1h": []}
