"""
1h 캔들 + OI + 테이커 불균형(backtest `liq_cache_builder`와 동일 입력)을 Binance REST로 수집해
Redis(또는 메모리)에 `window=400` 대비 2배인 `retain_bars=800`만 저장.

backtest `serve_liq_cache` / `liq_cache_builder` 기준:
- interval 1h, window=400, min_bars=50
- 저장 상한: max(window) * 2 = 800봉
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
import pandas as pd

from common.oi_liq_map import build_oi_liq_map, compute_direction

_WINDOW_PRESETS: List[tuple] = [("3d", 72), ("2w", 336), ("1m", 720)]

# backtest DEFAULT_WINDOW / scripts 기본과 동일
LIQ_WINDOW = int(os.getenv("LIQ_WINDOW", "400"))
# 최대 룩백(window)의 2배만 Redis에 유지
LIQ_RETAIN_BARS = int(os.getenv("LIQ_RETAIN_BARS", str(LIQ_WINDOW * 2)))
LIQ_MIN_BARS = int(os.getenv("LIQ_MIN_BARS", "50"))
LIQ_REFRESH_SEC = float(os.getenv("LIQ_REFRESH_SEC", "120"))
# 캐시 cold start 시 첫 API 요청에서 Binance REST로 즉시 채움
LIQ_ON_DEMAND_FETCH = os.getenv("LIQ_ON_DEMAND_FETCH", "true").lower() in ("1", "true", "yes", "on")
REDIS_URL = os.getenv("REDIS_URL", "").strip()
REDIS_KEY_PREFIX = os.getenv("REDIS_KEY_PREFIX", "forwardtest:v1")
REDIS_TTL_SEC = int(os.getenv("REDIS_TTL_SEC", "7200"))

_memory_payload: Dict[str, Dict[str, Any]] = {}
_memory_lock = asyncio.Lock()
_redis_client: Any = None
_redis_disabled_until: float = 0.0
_sym_fetch_locks: Dict[str, asyncio.Lock] = {}
_sym_fetch_locks_guard = asyncio.Lock()


def _redis_key(symbol: str, interval: str = "1h") -> str:
    sym = symbol.upper().replace(" ", "")
    sfx = "" if interval == "1h" else f":{interval}"
    return f"{REDIS_KEY_PREFIX}:liq:{sym}{sfx}"


async def _get_redis():
    global _redis_client
    if not REDIS_URL:
        return None
    if time.time() < _redis_disabled_until:
        return None
    if not REDIS_URL:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        import redis.asyncio as redis  # type: ignore

        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        return _redis_client
    except Exception as exc:
        print(f"[liq_series_cache] redis unavailable: {exc}")
        return None


def _disable_redis_temporarily(exc: Exception, *, action: str) -> None:
    """
    Redis 연결 실패 시 일정 시간 재시도를 멈추고 메모리 캐시로 폴백한다.
    Redis가 내려간 환경에서 요청마다 에러 로그가 반복되는 것을 방지한다.
    """
    global _redis_client, _redis_disabled_until
    _redis_client = None
    backoff_sec = 60.0
    _redis_disabled_until = time.time() + backoff_sec
    print(f"[liq_series_cache] redis {action} failed: {exc} (fallback to memory for {int(backoff_sec)}s)")


async def _cache_write(symbol: str, payload: Dict[str, Any], interval: str = "1h") -> None:
    r = await _get_redis()
    if r is not None:
        try:
            await r.set(_redis_key(symbol, interval), json.dumps(payload, ensure_ascii=False), ex=REDIS_TTL_SEC)
            return
        except Exception as exc:
            _disable_redis_temporarily(exc, action="set")
    async with _memory_lock:
        _memory_payload[_redis_key(symbol, interval)] = payload


async def get_cached_chart_payload(symbol: str, interval: str = "1h") -> Optional[Dict[str, Any]]:
    sym = symbol.upper().strip() or "BTCUSDT"
    r = await _get_redis()
    if r is not None:
        try:
            raw = await r.get(_redis_key(sym, interval))
            if raw:
                return json.loads(raw)
        except Exception as exc:
            _disable_redis_temporarily(exc, action="get")
    async with _memory_lock:
        return _memory_payload.get(_redis_key(sym, interval))


async def _lock_for_symbol(sym: str, interval: str = "1h") -> asyncio.Lock:
    key = f"{sym}:{interval}"
    async with _sym_fetch_locks_guard:
        if key not in _sym_fetch_locks:
            _sym_fetch_locks[key] = asyncio.Lock()
        return _sym_fetch_locks[key]


async def get_chart_payload_or_fetch(symbol: str, interval: str = "1h") -> Optional[Dict[str, Any]]:
    """
    캐시 히트 시 그대로 반환. cold start(캐시 없음)이면 Binance REST로 빌드 후 캐시에 쓰고 반환.
    동일 심볼 동시 요청은 락으로 한 번만 fetch.
    """
    sym = symbol.upper().strip() or "BTCUSDT"
    if not LIQ_ON_DEMAND_FETCH:
        return await get_cached_chart_payload(sym, interval)

    hit = await get_cached_chart_payload(sym, interval)
    if hit:
        return hit

    lock = await _lock_for_symbol(sym, interval)
    async with lock:
        hit2 = await get_cached_chart_payload(sym, interval)
        if hit2:
            return hit2
        await refresh_symbol(sym, interval)

    out = await get_cached_chart_payload(sym, interval)
    if out and isinstance(out.get("meta"), dict):
        meta = dict(out["meta"])
        meta["cold_fetch"] = True
        return {**out, "meta": meta}
    return out


def build_strategy_liq_snapshot(payload: Dict[str, Any], *, include_series: bool) -> Dict[str, Any]:
    """
    Quant 전략용 고정 스키마 JSON. 청산 구간은 backtest `oi_liq_map.build_oi_liq_map` 결과와 동일 구조.
    """
    sym = payload.get("symbol")
    meta = dict(payload.get("meta") or {})
    meta["algorithm"] = "oi_liq_map_v1"
    meta["reference"] = "btc_backtest/data/oi_liq_map.py — build_oi_liq_map (동일 클러스터/랭킹)"
    meta["inputs_note"] = "1h bars + OI hist + 테이커 불균형(클로즈 봉, klines 기반)"

    if payload.get("error"):
        return {
            "schema_version": "1",
            "ok": False,
            "symbol": sym,
            "error": payload["error"],
            "meta": meta,
        }

    liq = payload.get("liq_latest") or {}
    m = liq.get("map") or {}
    out: Dict[str, Any] = {
        "schema_version": "1",
        "ok": True,
        "symbol": sym,
        "meta": meta,
        "current_price": m.get("current_price"),
        "method": m.get("method"),
        "direction": liq.get("direction"),
        "zones": {
            "long_liq_below_price": m.get("long_liq_zones", []),
            "short_liq_above_price": m.get("short_liq_zones", []),
        },
    }
    if include_series:
        out["series_1h"] = payload.get("chart")
    return out


def _interval_to_seconds(interval: str) -> int:
    """interval 문자열 → 초 단위."""
    _map = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "4h": 14400, "1d": 86400}
    return _map.get(interval, 3600)


def _closed_bar_end_time_ms(interval: str) -> int:
    """현재 시각 기준 마지막 closed 봉의 close_ms (현재 형성 중인 봉 제외)."""
    iv_ms = _interval_to_seconds(interval) * 1000
    now_ms = int(time.time() * 1000)
    current_bar_open_ms = (now_ms // iv_ms) * iv_ms
    return current_bar_open_ms - 1  # 현재 봉 open 직전 = 이전 봉 close


def _next_trigger_time(interval: str, now: float, advance: int = 60) -> float:
    """봉 close `advance`초 전 trigger 시각 반환 (항상 now 보다 미래).

    advance=60 → :14/:29/:44/:59  (진입 + pre-liq)
    advance=0  → :00/:15/:30/:45  (bar close liq)
    """
    iv_sec = _interval_to_seconds(interval)
    bar_close = (int(now) // iv_sec + 1) * iv_sec
    trigger = bar_close - advance
    if trigger <= now:
        trigger += iv_sec
    return trigger


async def fetch_klines(
    client: httpx.AsyncClient,
    symbol: str,
    limit: int,
    interval: str = "1h",
    end_time_ms: Optional[int] = None,
) -> List[list]:
    """limit > 1500 이면 endTime 기반 역순 페이지네이션으로 이어붙임."""
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
        all_bars = data + all_bars          # 오래된 데이터를 앞에 붙임
        remaining -= len(data)
        current_end = int(data[0][0]) - 1  # 첫 봉 open_time - 1ms → 다음 페이지 상한
        if len(data) < batch:
            break

    return all_bars[-limit:] if len(all_bars) > limit else all_bars


async def fetch_oi_hist(
    client: httpx.AsyncClient,
    symbol: str,
    need: int,
    interval: str = "1h",
    end_time_ms: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """openInterestHist, 최대 500개 단위로 과거까지 이어붙임 (시간 오름차순)."""
    chunks: List[List[Dict[str, Any]]] = []
    end_time: Optional[int] = end_time_ms  # 첫 요청 상한 (None이면 최신)
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
    by_ts: Dict[int, Dict[str, Any]] = {}
    for row in merged:
        by_ts[int(row["timestamp"])] = row
    sorted_rows = [by_ts[k] for k in sorted(by_ts.keys())]
    return sorted_rows[-need:] if len(sorted_rows) > need else sorted_rows


# backward-compat aliases
async def fetch_klines_1h(client: httpx.AsyncClient, symbol: str, limit: int) -> List[list]:
    return await fetch_klines(client, symbol, limit, interval="1h")

async def fetch_oi_hist_1h(client: httpx.AsyncClient, symbol: str, need: int) -> List[Dict[str, Any]]:
    return await fetch_oi_hist(client, symbol, need, interval="1h")


def _klines_to_df(raw: List[list]) -> pd.DataFrame:
    rows = []
    for k in raw:
        ot = int(k[0])
        o, h, lo, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
        vol = float(k[5])
        tb = float(k[9]) if len(k) > 9 else 0.0
        sell = max(0.0, vol - tb)
        taker_delta = tb - sell
        rows.append(
            {
                "open_time_ms": ot,
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": vol,
                "cvd_delta": taker_delta,
            }
        )
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
    out: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        out.append(
            {
                "time": int(row["open_time_ms"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "oi": float(row["oi"]) if pd.notna(row.get("oi")) else 0.0,
                "oi_delta": float(row["oi_delta"]) if pd.notna(row.get("oi_delta")) else 0.0,
                "cvd_delta": float(row["cvd_delta"]),
            }
        )
    return out


def _zones_to_level_map(liq_map: Dict) -> List[Dict]:
    """long_liq_zones + short_liq_zones → flat level_map (backtest engine.py 동일)."""
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
    """가격 기준 중복 제거(oi_weight 큰 항목 우선) — backtest engine.py _merge_level_maps 동일."""
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


async def build_payload_for_symbol(
    symbol: str,
    interval: str = "1h",
    exclude_current_bar: bool = True,
) -> Dict[str, Any]:
    sym = symbol.upper().strip() or "BTCUSDT"
    # 15m 은 1h 대비 4배 봉 → window preset도 4배
    _interval_mult = {"15m": 4, "30m": 2, "5m": 12}.get(interval, 1)
    _window_presets = [(k, v * _interval_mult) for k, v in _WINDOW_PRESETS]
    retain = max(LIQ_RETAIN_BARS * _interval_mult, (LIQ_WINDOW + LIQ_MIN_BARS) * _interval_mult)
    # 현재 형성 중인 봉 제외 — backtest(closed bars only)와 동일 조건
    end_ms = _closed_bar_end_time_ms(interval) if exclude_current_bar else None
    t0 = time.time()
    async with httpx.AsyncClient(timeout=45.0) as client:
        raw_k = await fetch_klines(client, sym, retain, interval=interval, end_time_ms=end_ms)
        if not raw_k:
            return {
                "symbol": sym,
                "error": "no_klines",
                "meta": {"updated_at": None, "retain_bars": retain, "window": LIQ_WINDOW, "interval": interval},
            }
        df_k = _klines_to_df(raw_k)
        need_oi = len(df_k)
        oi_rows = await fetch_oi_hist(client, sym, max(need_oi, LIQ_WINDOW + 10), interval=interval, end_time_ms=end_ms)
        df = _merge_oi(df_k, oi_rows)
        df = df.dropna(subset=["close"])
        if len(df) > retain:
            df = df.iloc[-retain:].reset_index(drop=True)

    bars_all = _bars_for_map(df)
    if len(bars_all) < LIQ_MIN_BARS:
        return {
            "symbol": sym,
            "error": "insufficient_bars",
            "meta": {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "retain_bars": retain,
                "window": LIQ_WINDOW,
                "bars": len(bars_all),
            },
        }

    last_close = float(bars_all[-1]["close"])

    # 3d/2w/1m 3개 윈도우로 각각 liq map 계산 (backtest 프리셋 방식과 동일)
    # 1h 봉 기준: 3d=72봉, 2w=336봉, 1m=720봉
    _WINDOW_BARS = {"3d": 72, "2w": 336, "1m": 720}
    liq_windows: Dict[str, Any] = {}
    for wkey, wsize in _WINDOW_BARS.items():
        wslice = bars_all[-wsize:] if len(bars_all) >= wsize else bars_all
        if len(wslice) < LIQ_MIN_BARS:
            continue
        liq_windows[wkey] = build_oi_liq_map(wslice, current_price=last_close, min_bars=LIQ_MIN_BARS)

    # backward-compat: 단일 map 은 1m 윈도우 기준 (없으면 가장 긴 것)
    liq_single = (
        liq_windows.get("1m")
        or liq_windows.get("2w")
        or liq_windows.get("3d")
        or {}
    )
    direction = compute_direction(liq_single.get("long_liq_zones", []), liq_single.get("short_liq_zones", []))

    # 3개 window(3d/2w/1m) 각각 빌드 → 병합 (backtest engine.py와 동일 로직)
    preset_level_maps: List[List[Dict]] = []
    by_window: Dict[str, Any] = {}
    for preset_key, preset_bars in _window_presets:
        if len(bars_all) < LIQ_MIN_BARS:
            continue
        slc = bars_all[-preset_bars:] if len(bars_all) >= preset_bars else bars_all
        try:
            preset_liq = build_oi_liq_map(slc, current_price=last_close, min_bars=min(LIQ_MIN_BARS, len(slc) // 2))
            lvl_map = _zones_to_level_map(preset_liq)
            preset_level_maps.append(lvl_map)
            by_window[preset_key] = {"bars": len(slc), "level_map": lvl_map}
        except Exception as exc:
            print(f"[liq_series_cache] preset {preset_key} build failed: {exc}")

    merged_level_map = _merge_level_maps(preset_level_maps)

    t_ms = [b["time"] for b in bars_all]
    chart = {
        "t_ms": t_ms,
        "close": [float(b["close"]) for b in bars_all],
        "oi": [float(b["oi"]) if b.get("oi") is not None else None for b in bars_all],
        "oi_delta": [float(b["oi_delta"]) for b in bars_all],
        "cvd_delta": [float(b["cvd_delta"]) for b in bars_all],
    }

    meta = {
        "window": LIQ_WINDOW,
        "retain_bars": retain,
        "min_bars": LIQ_MIN_BARS,
        "bars": len(bars_all),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "build_ms": round((time.time() - t0) * 1000, 1),
        "redis": bool(REDIS_URL),
    }

    return {
        "symbol": sym,
        "meta": meta,
        "chart": chart,
        "liq_latest": {
            "map": liq_single,        # backward-compat 단일 스냅샷
            "direction": direction,
            "windows": liq_windows,   # 3d/2w/1m 개별 liq map
        },
        "liq_multi_window": {
            "merged": merged_level_map,
            "by_window": by_window,
        },
    }


async def refresh_symbol(symbol: str, interval: str = "1h") -> None:
    try:
        payload = await build_payload_for_symbol(symbol, interval=interval)
        await _cache_write(symbol.upper(), payload, interval=interval)
    except Exception:
        pass


def _liq_symbols() -> List[str]:
    return [s.strip().upper() for s in os.getenv("LIQ_SYMBOLS", "BTCUSDT,ETHUSDT").split(",") if s.strip()]


def _liq_refresh_targets() -> list[tuple[str, str]]:
    """strategies_master.yaml 에서 enabled 전략의 (symbol, liq_interval) 목록 반환."""
    import pathlib
    import yaml as _yaml
    _master_path = pathlib.Path(__file__).resolve().parents[1] / "features" / "strategy" / "common" / "strategies_master.yaml"
    try:
        cfg = _yaml.safe_load(_master_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return [("ETHUSDT", "1h"), ("BTCUSDT", "1h")]
    seen: set[tuple[str, str]] = set()
    targets: list[tuple[str, str]] = []
    for strat_cfg in cfg.values():
        if not isinstance(strat_cfg, dict) or not strat_cfg.get("enabled"):
            continue
        sym = str(strat_cfg.get("symbol") or "ETHUSDT").upper()
        iv  = str(strat_cfg.get("liq_interval") or "1h")
        if (sym, iv) not in seen:
            seen.add((sym, iv))
            targets.append((sym, iv))
    return targets or [("ETHUSDT", "1h"), ("BTCUSDT", "1h")]


async def refresh_loop() -> None:
    """
    각 (symbol, interval) 별로 두 타이밍에 liq cache를 재빌드한다.

    advance=60 (:14/:29/:44/:59):
        봉 close 1분 전. 현재 형성 중인 봉 제외(exclude_current_bar=True).
        진입 signal check 직전에 cache를 준비해 슬리피지를 줄인다.

    advance=0 (:00/:15/:30/:45):
        봉 close 직후. 방금 닫힌 봉 포함한 최신 liq 빌드.
        다음 사이클(:14)을 위해 최신 상태 유지.
    """
    _ADVANCES = [60, 0]  # 두 타이밍
    _next: Dict[tuple, float] = {}  # key: (sym, iv, advance)
    while True:
        targets = _liq_refresh_targets()
        now = time.time()
        # 신규 target 초기화
        for sym, iv in targets:
            for adv in _ADVANCES:
                key = (sym, iv, adv)
                if key not in _next:
                    _next[key] = _next_trigger_time(iv, now, advance=adv)
        # trigger 도달한 항목 refresh
        active_keys = {(s, i) for s, i in targets}
        for sym, iv in list(active_keys):
            for adv in _ADVANCES:
                key = (sym, iv, adv)
                if now >= _next.get(key, float("inf")):
                    await refresh_symbol(sym, interval=iv)
                    _next[key] = _next_trigger_time(iv, time.time(), advance=adv)
        # 다음 trigger까지 sleep
        all_keys = [(s, i, a) for s, i in active_keys for a in _ADVANCES]
        upcoming = [_next[k] for k in all_keys if k in _next]
        sleep_sec = max(2.0, min(upcoming) - time.time()) if upcoming else 15.0
        await asyncio.sleep(sleep_sec)
