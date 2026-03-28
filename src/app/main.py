# pyright: reportMissingImports=false
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from common.binance_price_ws import BinancePriceWS
from common.liq_series_cache import refresh_loop
from features.home.router import router as home_router
from features.strategy.router import router as strategy_router


async def startup_binance_price_ws() -> None:
    try:
        BinancePriceWS().start(["btcusdt", "ethusdt"])
    except Exception as exc:
        print(f"[BinancePriceWS] startup skipped: {exc}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await startup_binance_price_ws()
    liq_task = asyncio.create_task(refresh_loop())
    yield
    liq_task.cancel()
    try:
        await liq_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="BTC Forward Test API", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


app.include_router(home_router)
app.include_router(strategy_router)

_project_root = Path(__file__).resolve().parents[2]
_static_dir = _project_root / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
