"""
Historical sweep_by_tf builder — the adapter layer between btc_backtest DB and signal functions.

This is the key interface that makes strategy signal functions reusable across projects.
In btc_forwardtest:  KlineBundleHub → sweep_by_tf → compute_signal(...)
In btc_backtest:     sweep_builder  → sweep_by_tf → compute_signal(...)  ← same signal.py!

The sweep_by_tf format:
  {
    "15m": {"data": [{"time": int, "open": f, "high": f, "low": f, "close": f,
                      "volume": f, "cvd_delta": f}, ...]},
    "1h":  {"data": [...]},
    ...
  }
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

TF_TO_MINUTES: Dict[str, int] = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "12h": 720, "1d": 1440,
}

# DB interval column values
TF_TO_INTERVAL: Dict[str, str] = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720", "1d": "D",
}


def tf_to_interval(tf: str) -> str:
    """'15m' → '15', '1h' → '60', '4h' → '240' (matches DB interval column)."""
    val = TF_TO_INTERVAL.get(tf)
    if val is None:
        raise ValueError(f"Unknown TF: {tf!r}. Supported: {list(TF_TO_INTERVAL)}")
    return val


def load_merged_df(session: Any, symbol: str, tf: str, limit: int = 2000) -> pd.DataFrame:
    """
    Load candles + taker_volumes joined by open_time_ms from the DB session.

    Returns a DataFrame with columns:
        open_time_ms, open, high, low, close, volume, cvd_delta
    sorted ascending by open_time_ms.
    """
    interval = tf_to_interval(tf)
    sql = """
        SELECT
            c.open_time_ms,
            c.open,
            c.high,
            c.low,
            c.close,
            c.volume,
            COALESCE(tv.cvd_delta, 0.0) AS cvd_delta
        FROM candles c
        LEFT JOIN taker_volumes tv
            ON  tv.exchange      = c.exchange
            AND tv.symbol        = c.symbol
            AND tv.interval      = c.interval
            AND tv.open_time_ms  = c.open_time_ms
        WHERE c.exchange = 'binance'
          AND c.symbol   = :symbol
          AND c.interval = :interval
        ORDER BY c.open_time_ms DESC
        LIMIT :limit
    """
    from sqlalchemy import text
    rows = session.execute(
        text(sql), {"symbol": symbol, "interval": interval, "limit": limit}
    ).fetchall()
    if not rows:
        return pd.DataFrame(
            columns=["open_time_ms", "open", "high", "low", "close", "volume", "cvd_delta"]
        )
    df = pd.DataFrame(rows, columns=["open_time_ms", "open", "high", "low", "close", "volume", "cvd_delta"])
    return df.sort_values("open_time_ms").reset_index(drop=True)


def load_dfs_by_tf(
    session: Any,
    symbol: str,
    tfs: List[str],
    limit: int = 2000,
) -> Dict[str, pd.DataFrame]:
    """Load all required TFs in one call. Returns {tf: df}."""
    return {tf: load_merged_df(session, symbol, tf, limit) for tf in tfs}


def get_max_open_time_ms(session: Any, symbol: str, tf: str) -> Optional[int]:
    """Latest candle open_time_ms for symbol/TF, or None if empty."""
    from sqlalchemy import text
    interval = tf_to_interval(tf)
    sql = text(
        """
        SELECT MAX(c.open_time_ms)
        FROM candles c
        WHERE c.exchange = 'binance'
          AND c.symbol   = :symbol
          AND c.interval = :interval
        """
    )
    r = session.execute(sql, {"symbol": symbol, "interval": interval}).scalar()
    return int(r) if r is not None else None


def load_merged_df_range(
    session: Any,
    symbol: str,
    tf: str,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    """
    Load candles in [start_ms, end_ms] inclusive, ascending by open_time_ms.
    """
    from sqlalchemy import text
    interval = tf_to_interval(tf)
    sql = """
        SELECT
            c.open_time_ms,
            c.open,
            c.high,
            c.low,
            c.close,
            c.volume,
            COALESCE(tv.cvd_delta, 0.0) AS cvd_delta
        FROM candles c
        LEFT JOIN taker_volumes tv
            ON  tv.exchange      = c.exchange
            AND tv.symbol        = c.symbol
            AND tv.interval      = c.interval
            AND tv.open_time_ms  = c.open_time_ms
        WHERE c.exchange = 'binance'
          AND c.symbol   = :symbol
          AND c.interval = :interval
          AND c.open_time_ms >= :start_ms
          AND c.open_time_ms <= :end_ms
        ORDER BY c.open_time_ms ASC
    """
    rows = session.execute(
        text(sql),
        {"symbol": symbol, "interval": interval, "start_ms": start_ms, "end_ms": end_ms},
    ).fetchall()
    if not rows:
        return pd.DataFrame(
            columns=["open_time_ms", "open", "high", "low", "close", "volume", "cvd_delta"]
        )
    df = pd.DataFrame(rows, columns=["open_time_ms", "open", "high", "low", "close", "volume", "cvd_delta"])
    return df.reset_index(drop=True)


def load_dfs_by_tf_range(
    session: Any,
    symbol: str,
    tfs: List[str],
    start_ms: int,
    end_ms: int,
) -> Dict[str, pd.DataFrame]:
    """Load all TFs over the same wall-clock window (per-TF bars whose open_time is in range)."""
    return {tf: load_merged_df_range(session, symbol, tf, start_ms, end_ms) for tf in tfs}


def load_oi_df_range(
    session: Any,
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    """Load OI data from market_metrics for a date range (5-min granularity)."""
    from sqlalchemy import text
    sql = """
        SELECT open_time_ms, open_interest
        FROM market_metrics
        WHERE exchange = 'binance'
          AND symbol   = :symbol
          AND open_time_ms >= :start_ms
          AND open_time_ms <= :end_ms
        ORDER BY open_time_ms ASC
    """
    rows = session.execute(
        text(sql),
        {"symbol": symbol, "start_ms": start_ms, "end_ms": end_ms},
    ).fetchall()
    if not rows:
        return pd.DataFrame(columns=["open_time_ms", "open_interest"])
    return pd.DataFrame(rows, columns=["open_time_ms", "open_interest"]).reset_index(drop=True)


def lookback_ms(lookback_years: float) -> int:
    """Approximate calendar span in ms (365.25 d/y)."""
    return int(365.25 * lookback_years * 24 * 3600 * 1000)


def _df_to_bars(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Convert a DataFrame slice to the bar list format expected by signal functions."""
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
    Build a sweep_by_tf snapshot at a given timestamp (no-lookahead).

    Parameters
    ----------
    dfs_by_tf     : {tf: full_df} — pre-loaded, sorted ascending by open_time_ms
    current_ts_ms : 진입(기준) 봉의 open_time_ms. 아직 시작 안 한 봉(open > 이 값)은 제외.
    bar_limit     : max bars per TF passed to signal functions
    entry_tf      : 설정 시, 각 TF마다 "진입 봉이 막 닫힌 시각" 이전에 **완전히 종가 확정된**
                    봉만 포함 (상위 TF 미완성 봉의 미래 구간 CVD/OHLC 제거).

    Returns
    -------
    (sweep_by_tf, current_price)
    current_price = close of the last bar in the first TF key of dfs_by_tf (호출부에서 entry close를 쓰는 경우가 많음)
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
