"""
전략 라우터 자동 등록 — strategies_master.yaml 주도.

main.py에서:
    from features.strategy.quant_strategies.router_registry import include_strategy_routers
    include_strategy_routers(app)

새 전략 추가 = strategies_master.yaml + 전략 패키지 추가만 하면 자동 등록.
"""
from __future__ import annotations

import importlib
import logging

from fastapi import FastAPI

from features.strategy.quant_strategies.common.config_loader import get_master_config

logger = logging.getLogger(__name__)


def include_strategy_routers(app: FastAPI) -> None:
    """enabled 전략 패키지에서 router 를 자동 import해 app에 등록."""
    master = get_master_config()
    for strategy_key, cfg in master.items():
        if not isinstance(cfg, dict):
            continue
        if not cfg.get("enabled", False):
            continue
        base = cfg.get("base_strategy") or strategy_key
        try:
            mod = importlib.import_module(
                f"features.strategy.quant_strategies.{base}.router"
            )
            router = getattr(mod, "router", None)
            if router is None:
                logger.warning("[RouterRegistry] %s: router 없음 — 스킵", base)
                continue
            # 중복 등록 방지 (base_strategy 공유 시)
            prefix = f"/quant/{base}"
            already = any(
                getattr(r, "prefix", None) == prefix for r in app.routes
            )
            if already:
                logger.debug("[RouterRegistry] %s: 이미 등록됨 — 스킵", base)
                continue
            app.include_router(router)
            logger.info("[RouterRegistry] ✅ %s 라우터 등록", base)
        except Exception as exc:
            logger.error("[RouterRegistry] %s 등록 실패: %s", base, exc)
