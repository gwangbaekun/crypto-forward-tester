"""
liq level_map 순수 연산 (캐시 없음).
매 호출마다 Binance REST에서 1h klines + OI를 fetch해 build_oi_liq_map을 돌린다.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import httpx
import pandas as pd

from common.oi_liq_map import build_oi_liq_map

_LIQ_MIN_BARS = 50
_LIQ_RETAIN_BARS = 800          # 1h 기준 retain bars
_WINDOW_PRESETS = [("3d", 72), ("2w", 336), ("1m", 720)]  # 1h 기준 봉 수

_INTERVAL_TO_SECONDS: Dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}

# entry_tf → (fetch interval, window multiplier vs 1h)
# Binance openInterestHist 지원: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
_TF_TO_INTERVAL: Dict[str, str] = {
    "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h",
}
_TF_MULT: Dict[str, int] = {
    "5m": 12, "15m": 4, "30m": 2,
    "1h": 1, "2h": 1, "4h": 1,   # 4h 이상은 1h 기준 유지 (봉 부족 방지)
}


def interval_to_seconds(interval: str) -> int:
    return _INTERVAL_TO_SECONDS.get(interval, 3600)


def _closed_bar_end_time_ms(interval: str) -> int:
    iv_ms = interval_to_seconds(interval) * 1000
    now_ms = int(time.time() * 1000)
    return (now_ms // iv_ms) * iv_ms - 1


async def _fetch_klines(
    client: httpx.AsyncClient,
    symbol: str,
    limit: int,
    interval: str = "1h",
    end_time_ms: Optional[int] = None,
) -> List[list]:
    MAX_PER_REQ = 1500
    sym = symbol.upper()
    all_bars: List[list] = []
    current_end = end_time_ms
    remaining = limit

    while remaining > 0:
        batch = min(remaining, MAX_PER_REQ)
        params: Dict[str, Any] = {"symbol": sym, "interval": interval, "limit": batch}
        if current_end is not None:
            params["endTime"] = current_end
        r = await client.get("https://fapi.binance.com/fapi/v1/klines", params=params)
        r.raise_for_status()
        data: List[list] = r.json()
        if not data:
            break
        all_bars = data + all_bars
        remaining -= len(data)
        current_end = int(data[0][0]) - 1
        if len(data) < batch:
            break

    return all_bars[-limit:] if len(all_bars) > limit else all_bars


async def _fetch_oi_hist(
    client: httpx.AsyncClient,
    symbol: str,
    need: int,
    interval: str = "1h",
    end_time_ms: Optional[int] = None,
) -> List[Dict[str, Any]]:
    chunks: List[List[Dict[str, Any]]] = []
    end_time = end_time_ms
    sym = symbol.upper()
    collected = 0

    while collected < need:
        batch_need = min(500, need - collected)
        params: Dict[str, Any] = {"symbol": sym, "period": interval, "limit": batch_need}
        if end_time is not None:
            params["endTime"] = end_time
        resp = await client.get("https://fapi.binance.com/futures/data/openInterestHist", params=params)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        chunks.append(batch)
        collected += len(batch)
        end_time = int(batch[0]["timestamp"]) - 1
        if len(batch) < batch_need:
            break

    if not chunks:
        return []
    merged: List[Dict[str, Any]] = []
    for ch in reversed(chunks):
        merged = list(ch) + merged
    by_ts: Dict[int, Dict[str, Any]] = {int(r["timestamp"]): r for r in merged}
    sorted_rows = [by_ts[k] for k in sorted(by_ts.keys())]
    return sorted_rows[-need:] if len(sorted_rows) > need else sorted_rows


def _klines_to_df(raw: List[list]) -> pd.DataFrame:
    rows = []
    for k in raw:
        vol = float(k[5])
        tb = float(k[9]) if len(k) > 9 else 0.0
        rows.append({
            "open_time_ms": int(k[0]),
            "open":  float(k[1]),
            "high":  float(k[2]),
            "low":   float(k[3]),
            "close": float(k[4]),
            "volume": vol,
            "cvd_delta": tb - max(0.0, vol - tb),
        })
    return pd.DataFrame(rows)


def _merge_oi(df_k: pd.DataFrame, oi_rows: List[Dict[str, Any]]) -> pd.DataFrame:
    if not oi_rows:
        df = df_k.copy()
        df["oi"] = float("nan")
        df["oi_delta"] = 0.0
        return df
    df_oi = pd.DataFrame(oi_rows)
    df_oi["open_time_ms"] = df_oi["timestamp"].astype("int64")
    df_oi["oi"] = df_oi["sumOpenInterest"].astype(float)
    df_oi = df_oi[["open_time_ms", "oi"]].sort_values("open_time_ms")
    df = df_k.sort_values("open_time_ms").copy()
    df = pd.merge_asof(df, df_oi, on="open_time_ms", direction="backward")
    df["oi"] = pd.to_numeric(df["oi"], errors="coerce").ffill()
    df["oi_delta"] = df["oi"].diff().fillna(0.0)
    return df


def _bars_for_map(df: pd.DataFrame) -> List[Dict[str, Any]]:
    out = []
    for _, row in df.iterrows():
        out.append({
            "time":      int(row["open_time_ms"]),
            "open":      float(row["open"]),
            "high":      float(row["high"]),
            "low":       float(row["low"]),
            "close":     float(row["close"]),
            "volume":    float(row["volume"]),
            "oi":        float(row["oi"]) if pd.notna(row.get("oi")) else 0.0,
            "oi_delta":  float(row["oi_delta"]) if pd.notna(row.get("oi_delta")) else 0.0,
            "cvd_delta": float(row["cvd_delta"]),
        })
    return out


def _zones_to_level_map(liq_map: Dict) -> List[Dict]:
    out: List[Dict] = []
    for key in ("long_liq_zones", "short_liq_zones"):
        for z in (liq_map or {}).get(key) or []:
            lo = z.get("price_low") or z.get("price")
            hi = z.get("price_high") or z.get("price")
            if lo and hi:
                mid = (float(lo) + float(hi)) / 2
                out.append({
                    "price":     round(mid, 1),
                    "rank":      z.get("rank", 0),
                    "intensity": z.get("intensity", ""),
                    "oi_weight": round(float(z.get("oi_weight", 0)), 4),
                })
    return out


def _merge_level_maps(level_maps: List[List[Dict]]) -> List[Dict]:
    merged: Dict[float, Dict] = {}
    for levels in level_maps:
        for lvl in levels:
            p = round(float(lvl.get("price", 0)), 1)
            if p <= 0:
                continue
            cur = merged.get(p)
            if cur is None or float(lvl.get("oi_weight", 0)) > float(cur.get("oi_weight", 0)):
                merged[p] = dict(lvl)
    out = list(merged.values())
    out.sort(key=lambda x: float(x.get("price", 0)))
    return out


async def compute_liq_level_map(symbol: str, entry_tf: str) -> List[Dict]:
    """
    entry_tf 해상도로 Binance REST fetch → multi-window merged level_map 반환.
    backtest 와 동일: entry_tf interval 의 klines + OI 로 build_oi_liq_map 실행.
    """
    interval = _TF_TO_INTERVAL.get(entry_tf, "1h")
    mult     = _TF_MULT.get(entry_tf, 1)
    retain   = _LIQ_RETAIN_BARS * mult
    presets  = [(k, v * mult) for k, v in _WINDOW_PRESETS]
    end_ms   = _closed_bar_end_time_ms(interval)

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            raw_k = await _fetch_klines(client, symbol, retain, interval=interval, end_time_ms=end_ms)
            if not raw_k:
                return []
            df_k = _klines_to_df(raw_k)
            oi_rows = await _fetch_oi_hist(client, symbol, len(df_k), interval=interval, end_time_ms=end_ms)
            df = _merge_oi(df_k, oi_rows)
            df = df.dropna(subset=["close"])
    except Exception as exc:
        print(f"[liq_compute] fetch 실패 ({symbol} {entry_tf}): {exc}")
        return []

    bars_all = _bars_for_map(df)
    if len(bars_all) < _LIQ_MIN_BARS:
        return []

    last_close = float(bars_all[-1]["close"])
    preset_level_maps: List[List[Dict]] = []

    for _, preset_bars in presets:
        slc = bars_all[-preset_bars:] if len(bars_all) >= preset_bars else bars_all
        if len(slc) < _LIQ_MIN_BARS:
            continue
        try:
            liq_map = build_oi_liq_map(slc, current_price=last_close, min_bars=min(_LIQ_MIN_BARS, len(slc) // 2))
            preset_level_maps.append(_zones_to_level_map(liq_map))
        except Exception as exc:
            print(f"[liq_compute] window build 실패 ({entry_tf}): {exc}")

    return _merge_level_maps(preset_level_maps)
