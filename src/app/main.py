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
        from features.strategy.common.config_loader import is_ctrader_live_enabled, get_master_config
        master = get_master_config() or {}
        enabled = [k for k in master if is_ctrader_live_enabled(k)]
        if not enabled:
            return
        from common.ctrader_executor import get_executor
        executor = get_executor()
        if executor is None:
            print("[cTrader] ⚠️  Executor not initialized — check CTRADER_ACCESS_TOKEN / ACCOUNT_ID / SYMBOL_ID")
            return
        print(f"[cTrader] Connecting... (strategies: {', '.join(enabled)})")
        # 최대 15초 대기 — Twisted reactor 스레드에서 인증 완료될 때까지
        for _ in range(30):
            await asyncio.sleep(0.5)
            if executor._authed:
                print(f"[cTrader] ✅ Connected & Authenticated — account={executor._account_id} env={executor._env} symbol={executor._symbol_id}")
                return
        print("[cTrader] ⚠️  Connection timeout — will retry on first signal")
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
