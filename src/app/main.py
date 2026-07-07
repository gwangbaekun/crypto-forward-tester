# pyright: reportMissingImports=false
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
from features.strategy.router_registry import include_strategy_routers
from features.strategy.router import router as strategy_router
from features.strategy.polymarket.router import router as polymarket_router
from features.strategy.common.master_dashboard_router import router as master_dashboard_router


async def startup_binance_price_ws() -> None:
    try:
        symbols = ["btcusdt", "ethusdt"]
        try:
            from features.strategy.common.config_loader import get_master_config
            for cfg in (get_master_config() or {}).values():
                if not isinstance(cfg, dict) or not cfg.get("enabled"):
                    continue
                sym = str(cfg.get("symbol") or "").strip().lower()
                if sym.endswith("usdt"):
                    symbols.append(sym)
        except Exception:
            pass
        BinancePriceWS().start(list(dict.fromkeys(symbols)))
    except Exception as exc:
        print(f"[BinancePriceWS] startup skipped: {exc}")


async def startup_ctrader() -> None:
    print("[cTrader] lazy connect 모드 — 주문(체결) 시에만 연결. startup 사전 연결 없음.")
    asyncio.create_task(_ctrader_token_healthcheck())


async def _ctrader_token_healthcheck() -> None:
    import os
    try:
        from common.ctrader_token_store import get_tokens
        from common.ctrader_executor import fetch_account_list_by_token
        from common.ctrader_account_loader import get_enabled_accounts

        client_id     = os.environ.get("CTRADER_CLIENT_ID", "").strip()
        client_secret = os.environ.get("CTRADER_CLIENT_SECRET", "").strip()
        db_at, _db_rt = get_tokens()
        env_at = os.environ.get("CTRADER_ACCESS_TOKEN", "").strip()
        access_token = db_at or env_at
        source = "DB" if db_at else ("env" if env_at else "none")

        if not (client_id and client_secret and access_token):
            print(f"[cTrader][healthcheck] ⚠️ 자격증명 누락 — token source={source}")
            return

        loop = asyncio.get_event_loop()
        try:
            accounts = await loop.run_in_executor(
                None, fetch_account_list_by_token, client_id, client_secret, access_token, 15.0,
            )
        except Exception as e:
            print(f"[cTrader][healthcheck] ❌ access token 무효 (source={source}): {e}")
            print("[cTrader][healthcheck]    → 실주문 시 executor가 refresh 시도. 계속 실패하면 재-OAuth 필요.")
            return

        granted = {a["ctidTraderAccountId"] for a in accounts}
        print(f"[cTrader][healthcheck] ✅ access token 유효 (source={source}) — grant 계좌 {len(accounts)}개")
        for a in accounts:
            print(f"[cTrader][healthcheck]    ctid={a['ctidTraderAccountId']} login={a['traderLogin']} {'LIVE' if a['isLive'] else 'demo'}")

        for firm, cfg in get_enabled_accounts().items():
            acc_id = int(cfg.get("account_id") or 0)
            mark = "✅ grant됨" if acc_id in granted else "❌ 토큰이 grant 안 함 — 인증 실패 예정 (재-OAuth 필요)"
            print(f"[cTrader][healthcheck]    enabled '{firm}' account={acc_id} → {mark}")
    except Exception as e:
        print(f"[cTrader][healthcheck] 예외: {e}")


_VALUE_SCAN_POLL_SEC = 600  # 10분마다 catch-up 확인 (하루 1회 보장)


async def _run_market_scan_if_due(market: str) -> bool:
    from features.strategy.value_scan.engine import get_scan_status, run_daily
    from features.strategy.value_scan.scan_schedule import should_run_catchup

    if not should_run_catchup(market):
        return False
    status = await get_scan_status()
    if status.get("running"):
        return False
    print(f"[ValueScan] catch-up scan starting: {market.upper()}")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: run_daily(markets=[market]))
    print(f"[ValueScan] catch-up scan done: {market.upper()}")
    return True


async def _value_scan_scheduler() -> None:
    """장 마감 시각 이후, 시장별 거래일 기준 하루 1회 스캔."""
    from features.strategy.value_scan.scan_schedule import build_schedule_status

    await asyncio.sleep(15)
    for market in ("kospi", "nasdaq"):
        try:
            await _run_market_scan_if_due(market)
        except Exception as exc:
            print(f"[ValueScan] startup catch-up {market} failed: {exc}")

    while True:
        try:
            st = build_schedule_status(running=False)
            pending = [m for m, due in st.get("catchup_pending", {}).items() if due]
            if pending:
                print(f"[ValueScan] catch-up pending: {', '.join(pending)}")
            for market in ("kospi", "nasdaq"):
                try:
                    await _run_market_scan_if_due(market)
                except Exception as exc:
                    print(f"[ValueScan] catch-up {market} error: {exc}")
        except Exception as exc:
            print(f"[ValueScan] scheduler tick error: {exc}")
        await asyncio.sleep(_VALUE_SCAN_POLL_SEC)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    from features.strategy.polymarket.fade.watchlist_seed import seed_watchlist_from_config
    seed_watchlist_from_config()  # watchlist.yaml → DB 비파괴적 upsert (local/Railway 동기화)
    await startup_binance_price_ws()
    await startup_ctrader()
    try:
        from features.strategy.common.strategy_loop import run_all_strategy_loops
        strategy_task = asyncio.create_task(run_all_strategy_loops())
    except Exception as exc:
        print(f"[StrategyLoop] startup skipped: {exc}")
        strategy_task = None

    try:
        # 항상 시작 — run_polymarket 가 내부에서 게이트(enabled 전략 있거나 LIVE면 가동).
        from features.strategy.polymarket.runner import run_polymarket
        polymarket_task = asyncio.create_task(run_polymarket())
    except Exception as exc:
        print(f"[Polymarket] startup skipped: {exc}")
        polymarket_task = None

    scan_task = asyncio.create_task(_value_scan_scheduler())
    yield
    scan_task.cancel()
    try:
        await scan_task
    except asyncio.CancelledError:
        pass
    if polymarket_task:
        polymarket_task.cancel()
        try:
            await polymarket_task
        except asyncio.CancelledError:
            pass
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


app.include_router(master_dashboard_router)
app.include_router(ctrader_auth_router)
app.include_router(strategy_router)
app.include_router(polymarket_router)
include_strategy_routers(app)  # strategies_master.yaml 기반 자동 등록

_project_root = Path(__file__).resolve().parents[2]
_static_dir = _project_root / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
