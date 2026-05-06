# pyright: reportMissingImports=false
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# access log 에서 폴링성 엔드포인트 필터링 (Railway 로그 절약)
_MUTE_PATHS = {"/health", "/execute/status"}

class _AccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(p in msg for p in _MUTE_PATHS)

logging.getLogger("uvicorn.access").addFilter(_AccessLogFilter())

from common.binance_price_ws import BinancePriceWS
from db.session import init_db
from features.ctrader.router import router as ctrader_auth_router
from features.home.router import router as home_router
from features.strategy.router_registry import include_strategy_routers
from features.strategy.router import router as strategy_router


async def startup_binance_price_ws() -> None:
    try:
        BinancePriceWS().start(["btcusdt", "ethusdt"])
    except Exception as exc:
        print(f"[BinancePriceWS] startup skipped: {exc}")


async def startup_ctrader() -> None:
    try:
        from features.strategy.common.config_loader import (
            is_ctrader_live_enabled, get_master_config, get_ctrader_config,
        )
        from common import ctrader_executor as ctrader_exec

        master  = get_master_config() or {}
        enabled = [k for k in master if is_ctrader_live_enabled(k)]
        if not enabled:
            return

        executors: dict = {}
        for strategy_key in enabled:
            cfg      = get_ctrader_config(strategy_key)
            executor = ctrader_exec.get_executor(
                account_id=cfg.get("ctrader_account_id"),
                env=cfg.get("ctrader_env"),
                symbol_id=cfg.get("ctrader_symbol_id"),
                lot_size=cfg.get("ctrader_lot_size"),
            )
            if executor is None:
                reason_fn = getattr(ctrader_exec, "get_executor_unavailable_reason", None)
                if callable(reason_fn):
                    reason = reason_fn(
                        account_id=cfg.get("ctrader_account_id"),
                        symbol_id=cfg.get("ctrader_symbol_id"),
                    ) or "unknown"
                else:
                    reason = "executor 생성 불가(ctrader_executor 버전 불일치)"
                print(f"[cTrader] ⚠️  {strategy_key} — executor 없음 ({reason})")
            else:
                executors[executor._account_id] = executor

        if not executors:
            return

        print(f"[cTrader] Connecting... (strategies: {', '.join(enabled)})")
        for _ in range(60):
            await asyncio.sleep(0.5)
            if all(e._authed for e in executors.values()):
                for e in executors.values():
                    print(f"[cTrader] ✅ Authenticated — account={e._account_id} env={e._env} symbol={e._symbol_id}")
                return
        # 타임아웃 시 인증된 것만 보고
        for e in executors.values():
            status = "✅" if e._authed else "⚠️ timeout"
            print(f"[cTrader] {status} — account={e._account_id} env={e._env}")
    except Exception as exc:
        print(f"[cTrader] startup error: {exc}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    await startup_binance_price_ws()
    await startup_ctrader()
    try:
        from features.strategy.common.strategy_loop import run_all_strategy_loops
        strategy_task = asyncio.create_task(run_all_strategy_loops())
    except Exception as exc:
        print(f"[StrategyLoop] startup skipped: {exc}")
        strategy_task = None
    yield
    if strategy_task:
        strategy_task.cancel()
        try:
            await strategy_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="BTC Forward Test API", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


app.include_router(home_router)
app.include_router(ctrader_auth_router)
app.include_router(strategy_router)
include_strategy_routers(app)  # strategies_master.yaml 기반 자동 등록

_project_root = Path(__file__).resolve().parents[2]
_static_dir = _project_root / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
