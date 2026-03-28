# pyright: reportMissingImports=false
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from common.binance_price_ws import BinancePriceWS
from common.liq_series_cache import refresh_loop
from db.session import init_db
from features.home.router import router as home_router
from features.strategy.quant_strategies.router_registry import include_strategy_routers
from features.strategy.router import router as strategy_router


async def startup_binance_price_ws() -> None:
    try:
        BinancePriceWS().start(["btcusdt", "ethusdt"])
    except Exception as exc:
        print(f"[BinancePriceWS] startup skipped: {exc}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    await startup_binance_price_ws()
    liq_task = asyncio.create_task(refresh_loop())
    try:
        from features.strategy.quant_strategies.common.strategy_loop import run_all_strategy_loops
        strategy_task = asyncio.create_task(run_all_strategy_loops())
    except Exception as exc:
        print(f"[StrategyLoop] startup skipped: {exc}")
        strategy_task = None
    yield
    liq_task.cancel()
    if strategy_task:
        strategy_task.cancel()
    for t in [liq_task, strategy_task]:
        if t is None:
            continue
        try:
            await t
        except asyncio.CancelledError:
            pass


app = FastAPI(title="BTC Forward Test API", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


app.include_router(home_router)
app.include_router(strategy_router)
include_strategy_routers(app)  # strategies_master.yaml 기반 자동 등록

_project_root = Path(__file__).resolve().parents[2]
_static_dir = _project_root / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
