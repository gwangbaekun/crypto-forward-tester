"""Polymarket 전략 통합 러너.

FastAPI lifespan 에서 asyncio.create_task(run_polymarket()) 으로 실행.

담당:
  1. CLOBWSClient 생성 + WS 연결 태스크
  2. LC / PH 엔진 (WS 기반, ws_client 공유)
  3. BF 엔진 (REST 폴링, 독립)
  4. Resolver 루프 (해소된 시그널 PnL 자동 계산)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, UTC

from features.strategy.polymarket._data import client as poly_client
from features.strategy.polymarket._data.ws_client import CLOBWSClient
from features.strategy.polymarket.late_convergence import engine as lc_engine
from features.strategy.polymarket.pair_hedge       import engine as ph_engine
from features.strategy.polymarket.bayesian_fomc    import engine as bf_engine
from features.strategy.polymarket.log_config import configure_polymarket_logging
from features.strategy.polymarket._data.executor import redeem_positions, redeem_all_pending


log = logging.getLogger("polymarket.runner")

_RESOLVER_INTERVAL = 900   # 15분마다 미해소 시그널 체크


async def _resolve_signals(ws_client) -> None:
    """해소 시각이 지난 미해소 시그널을 체크해 PnL 기록."""
    while True:
        try:
            await _run_resolver(ws_client)
        except Exception as e:
            log.warning("[Resolver] loop error: %s", e)
        await asyncio.sleep(_RESOLVER_INTERVAL)


async def _run_resolver(ws_client) -> None:
    from db.session import get_session
    from db.models import PolymarketSignal
    from sqlalchemy import select
    from features.strategy.polymarket.retry_service import process_pending_jobs

    try:
        processed = await process_pending_jobs(limit=5)
        if processed:
            log.info("[Resolver] processed pending polymarket jobs=%d", processed)
    except Exception as e:
        log.warning("[Resolver] process_pending_jobs error: %s", e)

    try:
        redeemed = await redeem_all_pending()
        if redeemed:
            log.info("[Resolver] redeem_all_pending processed=%d → LC 즉시 재스캔", len(redeemed))
            asyncio.create_task(lc_engine._refresh_and_scan(ws_client))
        else:
            log.info("[Resolver] redeem_all_pending processed=0")
    except Exception as e:
        log.warning("[Resolver] redeem_all_pending error: %s", e)

    now_ts = int(datetime.now(UTC).timestamp())
    db = get_session()
    try:
        stmt = select(PolymarketSignal).where(
            PolymarketSignal.is_resolved == 0,
            PolymarketSignal.event_end_ts.isnot(None),
            PolymarketSignal.event_end_ts < now_ts,
        )
        rows = db.execute(stmt).scalars().all()
        if not rows:
            return

        log.debug("[Resolver] checking %d unresolved signals", len(rows))

        for sig in rows:
            try:
                outcome = await _check_resolution(sig)
                if outcome is None:
                    continue
                _apply_resolution(sig, outcome)
                db.add(sig)
                if sig.yes_token_id:
                    token = sig.yes_token_id if (outcome == "YES") else (sig.no_token_id or sig.yes_token_id)
                    redeem_result = await redeem_positions(token)
                    log.info("[Resolver] redeem token=%s result=%s", token[:12], redeem_result)
            except Exception as e:
                log.warning("[Resolver] signal id=%s error: %s", sig.id, e)

        db.commit()
    except Exception as e:
        db.rollback()
        log.warning("[Resolver] DB error: %s", e)
    finally:
        db.close()


async def _check_resolution(sig) -> str | None:
    """마켓 현재가로 YES/NO/UNKNOWN 판정."""
    token_id = sig.yes_token_id
    if not token_id:
        return None

    price = await poly_client.fetch_current_price(token_id)
    if price is None:
        return None

    if price > 0.98:
        return "YES"
    elif price < 0.02:
        return "NO"
    return None  # 아직 해소 안 됨


def _apply_resolution(sig, outcome: str) -> None:
    """outcome에 따라 PnL 계산 후 sig 필드 업데이트."""
    sig.actual_outcome = outcome
    sig.is_resolved = 1
    sig.resolved_at = datetime.now(UTC)

    strategy = sig.strategy or ""
    side = sig.side or ""

    if strategy == "late_convergence":
        entry = sig.yes_price if side == "YES" else sig.no_price
        if entry and entry > 0:
            won = (side == "YES" and outcome == "YES") or (side == "NO" and outcome == "NO")
            sig.actual_pnl = (1.0 - entry) / entry if won else -1.0

    elif strategy == "pair_hedge":
        # YES + NO 동시 매수, $1.00 수령
        cost = sig.pair_cost
        if cost and cost > 0:
            sig.actual_pnl = (1.0 - cost) / cost  # 항상 수익 (pair_cost < 1.00 조건)

    elif strategy == "bayesian_fomc":
        entry = sig.yes_price if side == "YES" else sig.no_price
        if entry and entry > 0:
            won = (side == "YES" and outcome == "YES") or (side == "NO" and outcome == "NO")
            sig.actual_pnl = (1.0 - entry) / entry if won else -1.0


async def run_polymarket() -> None:
    configure_polymarket_logging()
    from features.strategy.polymarket._data.executor import is_live_mode
    if not is_live_mode():
        log.warning("POLYMARKET_LIVE 비활성 — run_polymarket 전체 루프 skip")
        return
    log.debug("runner started (LC + PH + BF + Resolver)")

    ws_client = CLOBWSClient()

    await asyncio.gather(
        ws_client.run(),
        lc_engine.run(ws_client),
        ph_engine.run(ws_client),
        bf_engine.run(),
        _resolve_signals(ws_client),
        return_exceptions=True,
    )
