"""
전략 Router 팩토리.

새 전략의 router.py:

    from features.strategy.common.router_factory import make_router
    router = make_router("my_strategy", default_tfs="15m,1h,4h")

자동 생성 엔드포인트:
    GET  /quant/{strategy_key}/dashboard
    GET  /quant/{strategy_key}/realtime_state
    GET  /quant/{strategy_key}/forward_test/stats
    GET  /quant/{strategy_key}/forward_test/trades
    POST /quant/{strategy_key}/forward_test/reset_halt   (항상 포함)
    GET  /quant/{strategy_key}/signal/explain             (항상 포함)
    GET  /quant/{strategy_key}/execute/status             (binance_live: true 전략만)

커스텀 엔드포인트가 필요하면 make_router() 반환값에 추가 데코레이터 사용.
"""
from __future__ import annotations

import importlib

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse

from common.utils import render_template


def make_router(strategy_key: str, default_tfs: str = "15m,1h,4h") -> APIRouter:
    """표준 라우터 생성 (6~7 엔드포인트)."""
    prefix = f"/quant/{strategy_key}"
    router = APIRouter(prefix=prefix, tags=[strategy_key])

    # ── 표준 4개 ────────────────────────────────────────────────────────────

    @router.get("/dashboard", response_class=HTMLResponse)
    async def dashboard():
        from features.strategy.common.config_loader import is_monitoring_start_by_default
        return render_template(
            f"{strategy_key}_dashboard.html",
            monitoring_start_by_default=is_monitoring_start_by_default(strategy_key),
        )

    @router.get("/realtime_state", response_class=JSONResponse)
    async def realtime_state(
        symbol: str = Query("BTCUSDT"),
        timeframes: str = Query(default_tfs),
        strategy_tag: str | None = Query(default=None),
    ):
        # strategy_tag가 있으면 master config의 tick.module/fn + timeframes 사용
        if strategy_tag:
            try:
                from features.strategy.common.config_loader import get_master_config
                master = get_master_config() or {}
                tag_cfg = master.get(strategy_tag) or {}
                tick_cfg = tag_cfg.get("tick") or {}
                tick_module = tick_cfg.get("module")
                tick_fn    = tick_cfg.get("fn")
                if tick_module and tick_fn:
                    # master config의 timeframes 사용 (클라이언트 값 무시)
                    cfg_tfs = tag_cfg.get("timeframes") or []
                    tfs_str = ",".join(str(t) for t in cfg_tfs) if cfg_tfs else timeframes
                    mod = importlib.import_module(tick_module)
                    state_fn = getattr(mod, tick_fn)
                    result = await state_fn(symbol=symbol, tfs=tfs_str)
                    return JSONResponse(result)
            except Exception:
                pass  # fallback to default below

        mod = importlib.import_module(f"features.strategy.{strategy_key}.realtime_feed")
        # get_state (신규) 또는 get_realtime_state (기존 전략) 모두 지원
        # 버전 함수가 있을 경우 우선 사용 (예: get_state_v2)
        state_fn = None
        if strategy_tag and strategy_tag.endswith("_v2"):
            state_fn = getattr(mod, "get_state_v2", None)
        state_fn = state_fn or getattr(mod, "get_state", None) or getattr(mod, "get_realtime_state", None)
        result = await state_fn(symbol=symbol, tfs=timeframes)
        return JSONResponse(result)

    @router.get("/forward_test/stats", response_class=JSONResponse)
    async def ft_stats(
        symbol: str = Query("BTCUSDT"),
        strategy_tag: str | None = Query(
            default=None,
            description="옵션: 공용 엔진에서 사용할 strategy_tag (예: renaissance_1d)",
        ),
    ):
        try:
            ft = importlib.import_module(f"features.strategy.{strategy_key}.engine")

            # 공용 엔진 패턴 지원: get_engine_for(tag) 우선, 없으면 get_engine()
            engine = None
            if strategy_tag and hasattr(ft, "get_engine_for"):
                try:
                    engine = ft.get_engine_for(strategy_tag)
                except Exception:
                    engine = None
            if engine is None:
                engine = ft.get_engine()

            return JSONResponse(engine.get_stats(symbol=symbol))
        except Exception as e:
            # 특정 전략(예: trend_following)에서 에러가 나더라도
            # video_history 같은 상위 화면이 깨지지 않도록 안전하게 처리.
            import logging
            logging.getLogger(__name__).error(
                "ft_trades error for %s: %s", strategy_key, e, exc_info=True
            )
            return JSONResponse({"error": str(e), "trades": []}, status_code=200)

    @router.get("/forward_test/trades", response_class=JSONResponse)
    async def ft_trades(
        symbol: str = Query("BTCUSDT"),
        limit: int = Query(50),
        strategy_tag: str | None = Query(
            default=None,
            description="옵션: 공용 엔진에서 사용할 strategy_tag (예: renaissance_1d)",
        ),
        trades_dto: bool = Query(
            False,
            description="true면 공통 TradeDTO 스키마(schema_version, trades[])로 반환",
        ),
    ):
        try:
            ft = importlib.import_module(f"features.strategy.{strategy_key}.engine")

            engine = None
            if strategy_tag and hasattr(ft, "get_engine_for"):
                try:
                    engine = ft.get_engine_for(strategy_tag)
                except Exception:
                    engine = None
            if engine is None:
                engine = ft.get_engine()

            raw = engine.get_trades_from_db(symbol=symbol, limit=limit)
            if not trades_dto:
                return JSONResponse(raw)

            tag = strategy_tag or strategy_key
            from common.trade_dto import SCHEMA_VERSION, forward_rows_to_dtos

            srid = f"{tag}:{symbol}"
            dtos = forward_rows_to_dtos(raw, strategy_run_id=srid)
            return JSONResponse(
                {
                    "trades_canonical": {
                        "schema_version": SCHEMA_VERSION,
                        "strategy_run_id": srid,
                        "source": "forward_test",
                        "trades": [d.to_dict() for d in dtos],
                    }
                }
            )
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── 공통 추가 3개 ────────────────────────────────────────────────────────

    @router.post("/forward_test/sync_binance", response_class=JSONResponse)
    async def sync_binance(symbol: str = Query("BTCUSDT")):
        """Binance 실제 포지션 ↔ 모듈 상태 강제 동기화 (서버 재시작 후 복구용)."""
        try:
            ft = importlib.import_module(f"features.strategy.{strategy_key}.engine")
            result = await ft.get_engine().sync_from_binance(symbol)
            return JSONResponse(result)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @router.post("/forward_test/reset_halt", response_class=JSONResponse)
    async def reset_halt():
        """엣지 검증 실패로 중단된 상태 수동 해제."""
        try:
            ft = importlib.import_module(f"features.strategy.{strategy_key}.engine")
            ft.get_engine().reset_edge_halt()
            return JSONResponse({"success": True, "message": "거래 중단 해제 완료."})
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.get("/signal/explain", response_class=JSONResponse)
    async def signal_explain(
        symbol: str = Query("BTCUSDT"),
        tf: str = Query("15m"),
        timeframes: str = Query(default_tfs),
    ):
        """특정 TF의 신호 상세 설명 (디버깅 + 검토용)."""
        try:
            mod = importlib.import_module(f"features.strategy.{strategy_key}.realtime_feed")
            state_fn = getattr(mod, "get_state", None) or getattr(mod, "get_realtime_state", None)
            state = await state_fn(symbol=symbol, tfs=timeframes)
            by_tf = state.get("by_tf") or {}
            payload = by_tf.get(tf) or {}
            # prediction (renaissance/trend_following) 또는 signal (신규 전략)
            pred = payload.get("prediction") or payload.get("signal") or {}
            return JSONResponse({"tf": tf, "symbol": symbol, **pred})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── Binance execute/status — 항상 등록, 활성화 여부는 요청 시점에 확인 ────

    @router.get("/execute/status", response_class=JSONResponse)
    async def execute_status(symbol: str = Query("BTCUSDT")):
        """Binance 계좌 잔고 + 현재 포지션 + 거래소 현재가."""
        from features.strategy.common.config_loader import is_binance_live_enabled
        if not is_binance_live_enabled(strategy_key):
            return JSONResponse({"enabled": False, "message": "binance_live 비활성화"})
        try:
            import asyncio
            from common.binance_executor import get_executor
            ex = get_executor()
            if not ex:
                return JSONResponse(
                    {"error": "API 키 없음 — executor 비활성화"}, status_code=503
                )
            balance, position, price = await asyncio.gather(
                ex.get_usdt_balance(),
                ex.get_position(symbol),
                ex.get_market_price(symbol),
                return_exceptions=True,
            )
            balance  = balance  if isinstance(balance, float) else 0.0
            position = position if isinstance(position, dict)  else None
            price    = price    if isinstance(price, float)    else 0.0
            return JSONResponse({
                "testnet":          ex._testnet,
                "balance_usdt":     balance,
                "exchange_price":   price,
                "binance_position": position,
            })
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    return router
