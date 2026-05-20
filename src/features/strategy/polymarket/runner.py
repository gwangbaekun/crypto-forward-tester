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

log = logging.getLogger("polymarket.runner")

_RESOLVER_INTERVAL = 900   # 15분마다 미해소 시그널 체크


async def _resolve_signals() -> None:
    """해소 시각이 지난 미해소 시그널을 체크해 PnL 기록."""
    from db.session import get_session
    from db.models import PolymarketSignal

    while True:
        await asyncio.sleep(_RESOLVER_INTERVAL)
        try:
            await _run_resolver()
        except Exception as e:
            print(f"[Resolver] error: {e}")


async def _run_resolver() -> None:
    from db.session import get_session
    from db.models import PolymarketSignal
    from sqlalchemy import select

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

        print(f"[Resolver] checking {len(rows)} unresolved signals")

        for sig in rows:
            try:
                outcome = await _check_resolution(sig)
                if outcome is None:
                    continue
                _apply_resolution(sig, outcome)
                db.add(sig)
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
    print("[Polymarket] 전략 러너 시작 (LC + PH + BF + Resolver)")

    ws_client = CLOBWSClient()

    await asyncio.gather(
        ws_client.run(),
        lc_engine.run(ws_client),
        ph_engine.run(ws_client),
        bf_engine.run(),
        _resolve_signals(),
        return_exceptions=True,
    )
