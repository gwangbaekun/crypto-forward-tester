"""
전략별 background tick 루프 — tradingview_mcp report_job.py 와 동일 패턴.

FastAPI lifespan에서 `asyncio.create_task(run_all_strategy_loops())` 호출.
strategies_master.yaml 의 enabled 전략을 자동 포함.
새 전략 = yaml 추가만 하면 루프 자동 포함.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import time
from typing import Any, Dict, Optional

from common.liq_compute import interval_to_seconds as _interval_to_seconds

PRE_ENTRY_SECONDS = 60.0


async def _sleep_until_next_trigger(entry_tf: str, pre_entry_seconds: float = 0.0) -> None:
    """다음 트리거까지 sleep (기본=봉 마감 정각, 선진입이면 마감 pre_entry_seconds 전)."""
    iv = _interval_to_seconds(entry_tf)
    now = time.time()
    bar_close = (int(now) // iv + 1) * iv
    trigger = bar_close - max(0.0, float(pre_entry_seconds or 0.0))
    if trigger <= now:
        trigger += iv
    await asyncio.sleep(max(1.0, trigger - now))


def _build_registry() -> Dict[str, Dict[str, Any]]:
    """strategies_master.yaml → enabled 전략 레지스트리 구성."""
    try:
        from features.strategy.common.config_loader import get_master_config
        master = get_master_config()
    except Exception as e:
        print(f"[StrategyLoop] master config 로드 실패: {e}")
        return {}

    registry: Dict[str, Dict[str, Any]] = {}
    for name, cfg in master.items():
        if not isinstance(cfg, dict):
            continue
        if not cfg.get("enabled", False):
            continue
        tick = cfg.get("tick") or {}
        module = tick.get("module")
        fn = tick.get("fn")
        if not module or not fn:
            print(f"[StrategyLoop] {name}: tick.module / tick.fn 없음 → 제외")
            continue
        registry[name] = {
            "module":        module,
            "fn":            fn,
            "kwargs":        tick.get("kwargs") or {},
            "tick_interval": int(cfg.get("tick_interval") or 60),
            "timeframes":    cfg.get("timeframes") or [],
            "symbol":        cfg.get("symbol") or None,
            "entry_tf":      cfg.get("entry_tf") or "15m",
        }
    return registry


STRATEGY_REGISTRY: Dict[str, Dict[str, Any]] = _build_registry()


async def _strategy_loop(name: str, cfg: Dict[str, Any], symbol: str) -> None:
    """단일 전략 무한 루프.

    포지션 없음: entry_tf 봉 마감 시점에 realtime_feed(fn) 호출.
    포지션 있음:
      - pre-entry window(봉 마감 60초 전)에 fn() 1회 호출 → ratchet/TP advance 수행.
        dedup은 fn() 내부 _last_bar_time 로 처리 (봉당 1회 tick 보장).
      - 봉 사이에는 WS 가격으로 intrabar SL 판정.
    """
    entry_tf = cfg.get("entry_tf") or "15m"
    fast_exit_interval = float(cfg.get("exit_tick_interval") or 1.0)
    tfs_str = ",".join(str(t) for t in cfg["timeframes"]) if cfg["timeframes"] else "15m,1h"
    _stale_logged_at: float = 0.0

    while True:
        try:
            mod = importlib.import_module(cfg["module"])
            fn  = getattr(mod, cfg["fn"])

            try:
                eng_mod = importlib.import_module(f"features.strategy.{name}.engine")
                eng = eng_mod.get_engine()
                has_open_pos = bool(eng.get_position())
            except Exception:
                has_open_pos = False
                eng = None

            if has_open_pos and eng is not None:
                # ── pre-entry window: fn() 호출 → ratchet 수행 (봉당 1회) ──────
                # dedup: fn() 안에서 new_bar_detected 시에만 tick 실행 + _last_bar_time 갱신.
                # _last_bar_time 을 보고 이번 봉에 이미 fn()을 호출했는지 판단.
                from features.strategy.common.base_realtime_feed import _last_bar_time
                iv              = _interval_to_seconds(entry_tf)
                now             = time.time()
                sec_to_close    = iv - (now % iv)
                in_pre_entry    = 0 < sec_to_close <= PRE_ENTRY_SECONDS
                forming_bar_start = int(now / iv) * iv   # 현재 forming 봉 open_time (UTC sec)
                cache_key       = f"{name}:{symbol}"

                if in_pre_entry and _last_bar_time.get(cache_key, 0) < forming_bar_start:
                    await fn(symbol=symbol, tfs=tfs_str)
                    # fn() 내부에서 _last_bar_time[cache_key] = forming_bar_start 로 갱신됨.
                    # 청산됐으면 intrabar tick 스킵
                    if eng.get_position() is None:
                        await _sleep_until_next_trigger(entry_tf, pre_entry_seconds=PRE_ENTRY_SECONDS)
                        continue

                # ── 봉 사이: WS → REST fallback으로 intrabar SL tick ───────────
                from common.binance_price_ws import BinancePriceWS, get_cached_price
                BinancePriceWS().start()
                ws_price = get_cached_price(symbol)
                if ws_price is None:
                    from common.binance_service import fetch_mark_price
                    ws_price = await fetch_mark_price(symbol)
                    if ws_price is None:
                        now = time.time()
                        if now - _stale_logged_at >= 60.0:
                            print(f"[StrategyLoop:{name}] WS+REST price unavailable for {symbol}, skip exit tick")
                            _stale_logged_at = now
                        await asyncio.sleep(max(0.5, fast_exit_interval))
                        continue
                pos = eng.get_position()
                tick_result = eng.tick(symbol, {
                    "current_price": ws_price,
                    "signal":        {"level_map": pos.get("level_map")} if pos else {},
                    "bar_high":      ws_price,
                    "bar_low":       ws_price,
                    "intrabar":      True,
                })
                events = (tick_result or {}).get("events") or []
                if events:
                    from features.strategy.common.base_realtime_feed import _execute_verify_notify
                    await _execute_verify_notify(name, symbol, events, ws_price)
                await asyncio.sleep(max(0.5, fast_exit_interval))
                continue

            # ── 포지션 없음: pre-entry 트리거 시점에 fn() 호출 ─────────────────
            await fn(symbol=symbol, tfs=tfs_str)

            if eng is not None and eng.get_position() is not None:
                continue

        except Exception as e:
            print(f"[StrategyLoop:{name}] error: {e}")

        await _sleep_until_next_trigger(entry_tf, pre_entry_seconds=PRE_ENTRY_SECONDS)


async def run_all_strategy_loops() -> None:
    """
    enabled 전략 전부 독립 루프로 시작. FastAPI lifespan에서 1회 호출.
    각 전략은 자체 tick_interval로 독립 실행.
    """
    if not STRATEGY_REGISTRY:
        print("[StrategyLoop] 등록된 전략 없음 — 루프 스킵")
        return

    default_symbol = os.getenv("STRATEGY_SYMBOL", "BTCUSDT").strip().upper() or "BTCUSDT"
    await asyncio.sleep(10)  # 서버 완전 기동 대기
    print(f"[StrategyLoop] 시작: {list(STRATEGY_REGISTRY.keys())} → default_symbol={default_symbol}")
    await asyncio.gather(
        *[
            _strategy_loop(name, cfg, cfg.get("symbol") or default_symbol)
            for name, cfg in STRATEGY_REGISTRY.items()
        ],
        return_exceptions=True,
    )
