"""
sweep_by_tf 빌더 — backtest 의 strategies/eth_cvd_explosion/sweep_builder.py 에서
DB 관련 함수를 제거한 forward-test 전용 버전.

build_sweep_at() 로직이 backtest 와 동일하므로 look-ahead 필터 동작이 보장됨.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

TF_TO_MINUTES: Dict[str, int] = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "12h": 720, "1d": 1440,
}


def _df_to_bars(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """DataFrame 슬라이스 → signal 함수가 기대하는 bar list 포맷."""
    bars = []
    for row in df.itertuples(index=False):
        bars.append({
            "time":      int(row.open_time_ms // 1000),
            "open":      float(row.open),
            "high":      float(row.high),
            "low":       float(row.low),
            "close":     float(row.close),
            "volume":    float(row.volume),
            "cvd_delta": float(getattr(row, "cvd_delta", 0) or 0),
        })
    return bars


def build_sweep_at(
    dfs_by_tf: Dict[str, pd.DataFrame],
    current_ts_ms: int,
    bar_limit: int = 500,
    entry_tf: Optional[str] = None,
) -> Tuple[Dict[str, Any], float]:
    """
    backtest sweep_builder.build_sweep_at() 와 완전 동일한 로직.

    Parameters
    ----------
    dfs_by_tf     : {tf: DataFrame} — open_time_ms 오름차순 정렬
    current_ts_ms : 완성된 entry_tf 봉의 open_time_ms (ms 단위)
    bar_limit     : TF별 최대 봉 수
    entry_tf      : 설정 시, 상위 TF 미완성 봉을 시간 기준으로 정확히 제거
                    (open_ms + tf_duration <= current_ts_ms + entry_tf_duration)

    Returns
    -------
    (sweep_by_tf, current_price)
    """
    as_of_ms: Optional[int] = None
    if entry_tf:
        em = TF_TO_MINUTES.get(str(entry_tf).strip())
        if em is not None:
            as_of_ms = int(current_ts_ms + em * 60 * 1000)

    sweep_by_tf: Dict[str, Any] = {}
    current_price = 0.0
    for tf, df in dfs_by_tf.items():
        if df is None or df.empty:
            sweep_by_tf[tf] = {"data": []}
            continue
        open_ms = df["open_time_ms"]
        mask = open_ms <= current_ts_ms
        if as_of_ms is not None:
            tf_min = TF_TO_MINUTES.get(tf)
            if tf_min is not None:
                tf_ms = int(tf_min * 60 * 1000)
                mask = mask & (open_ms + tf_ms <= as_of_ms)
        slice_df = df[mask].tail(bar_limit)
        bars = _df_to_bars(slice_df)
        sweep_by_tf[tf] = {"data": bars}
        if bars and current_price == 0.0:
            current_price = bars[-1]["close"]
    return sweep_by_tf, current_price
