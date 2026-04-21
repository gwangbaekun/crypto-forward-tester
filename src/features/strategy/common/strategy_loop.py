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
from typing import Any, Dict, Optional


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
            "symbol":        cfg.get("symbol") or None,  # 전략별 심볼 (없으면 STRATEGY_SYMBOL 환경변수)
        }
    return registry


STRATEGY_REGISTRY: Dict[str, Dict[str, Any]] = _build_registry()


async def _strategy_loop(name: str, cfg: Dict[str, Any], symbol: str) -> None:
    """단일 전략 무한 루프.

    포지션 없음: tick_interval 마다 realtime_feed 호출 (bar close 기준 신호 체크).
    포지션 있음: exit_tick_interval 마다 BinancePriceWS 캐시 가격으로 engine.tick() 직접 호출.
                 REST 없음 — WS mark price 스트림만 사용.
    """
    base_interval = float(cfg["tick_interval"])
    fast_exit_interval = float(cfg.get("exit_tick_interval") or 1.0)
    tfs_str = ",".join(str(t) for t in cfg["timeframes"]) if cfg["timeframes"] else "15m,1h"

    while True:
        try:
            mod = importlib.import_module(cfg["module"])
            fn  = getattr(mod, cfg["fn"])

            try:
                eng_mod = importlib.import_module(f"features.strategy.{name}.engine")
                eng = eng_mod.get_engine()
                st  = eng.get_stats(symbol=symbol) or {}
                has_open_pos = bool(st.get("current_position"))
            except Exception:
                has_open_pos = False
                eng = None

            if has_open_pos and eng is not None:
                # ── 포지션 보유: WS 가격으로 exit 체크 (REST 없음) ────────────
                from common.binance_price_ws import get_cached_price
                ws_price = get_cached_price(symbol)
                if ws_price:
                    eng.tick(symbol, {
                        "current_price": ws_price,
                        "signal":        {},
                        "bar_high":      ws_price,
                        "bar_low":       ws_price,
                    })
                else:
                    print(f"[StrategyLoop:{name}] WS price stale for {symbol}, skip exit tick")
                await asyncio.sleep(max(0.5, fast_exit_interval))
                continue

            # ── 포지션 없음: bar close 기준 정상 신호 체크 ──────────────────
            await fn(symbol=symbol, tfs=tfs_str)

        except Exception as e:
            print(f"[StrategyLoop:{name}] error: {e}")
        await asyncio.sleep(base_interval)


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
