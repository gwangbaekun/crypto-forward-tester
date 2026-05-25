"""Polymarket 실거래 주문 실행기 (V2 SDK).

py-clob-client-v2 기반. live.py 와 동일한 ClobClient 싱글턴을 공유한다.

필요 env:
  POLYMARKET_PK               — EOA 개인키 (주문 서명용)
  POLYMARKET_API_KEY          — V2 L2 API 키
  POLYMARKET_API_SECRET       — V2 시크릿
  POLYMARKET_PASSPHRASE       — V2 패스프레이즈
  POLYMARKET_WALLET_ADDRESS   — 프록시(funder) 지갑 주소
  POLYMARKET_ORDER_SIZE_USD   — (선택) 포지션당 달러, 기본 1.0
  POLYMARKET_TICK_SIZE        — (선택) 가격 tick, 기본 "0.01"
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from features.strategy.polymarket._data.live import _get_clob_client, _has_pk, _pk_valid

log = logging.getLogger("polymarket.executor")

_ORDER_SIZE_USD = float(os.environ.get("POLYMARKET_ORDER_SIZE_USD", "1.0"))
_TICK_SIZE      = os.environ.get("POLYMARKET_TICK_SIZE", "0.01")


def is_live_mode() -> bool:
    """실거래 모드 (유효 hex PK + 자격증명) 여부."""
    return _has_pk() and _pk_valid()


# ── 주문 생성 (BUY GTC limit) ───────────────────────────────────────────────

async def place_order(
    token_id: str,
    price: float,
    size_usd: float = _ORDER_SIZE_USD,
) -> dict[str, Any]:
    """GTC 지정가 매수 주문.

    size_usd 달러만큼 매수. 실제 shares = size_usd / price.

    Returns:
        {"order_id": str, "status": "matched"|"live"|"delayed"|"unmatched"|"failed",
         "raw": dict | None, "error": str | None}
    """
    if not is_live_mode():
        log.info("[executor] PK 미설정 → 주문 skip (시뮬 모드) token=%s", token_id[:12])
        return {"order_id": "", "status": "failed", "error": "POLYMARKET_PK 미설정"}

    from py_clob_client_v2 import OrderArgs, OrderType, PartialCreateOrderOptions, Side

    shares = round(size_usd / price, 4)

    def _sync() -> dict[str, Any]:
        client = _get_clob_client()
        return client.create_and_post_order(
            order_args = OrderArgs(
                token_id = token_id,
                price    = round(price, 4),
                size     = shares,
                side     = Side.BUY,
            ),
            options    = PartialCreateOrderOptions(tick_size=_TICK_SIZE),
            order_type = OrderType.GTC,
        )

    try:
        resp = await asyncio.to_thread(_sync)
        order_id = resp.get("orderID") or resp.get("orderId") or resp.get("id") or ""
        status   = resp.get("status", "live")
        log.info(
            "[executor] BUY token=%s price=%.4f shares=%.4f order_id=%s status=%s",
            token_id[:12], price, shares, order_id, status,
        )
        return {"order_id": order_id, "status": status, "raw": resp}
    except Exception as e:
        log.error("[executor] 주문 실패 token=%s err=%s", token_id[:12], e)
        return {"order_id": "", "status": "failed", "error": str(e)}


# ── 주문 취소 (미체결 GTC 회수용) ───────────────────────────────────────────

async def cancel_order(order_id: str) -> dict[str, Any]:
    """미체결 GTC 주문 취소.

    Polymarket 은 만기 시 YES/NO 토큰이 1:1 USDC 로 자동 청산되므로
    체결된 포지션을 시장가로 닫을 일은 없다. 이 함수는 오타·중복 등
    잘못 들어간 미체결 주문을 회수할 때만 사용.

    Returns:
        {"ok": bool, "raw": dict | None, "error": str | None}
    """
    if not is_live_mode():
        return {"ok": False, "error": "POLYMARKET_PK 미설정"}

    if not order_id:
        return {"ok": False, "error": "order_id 비어있음"}

    from py_clob_client_v2.clob_types import OrderPayload

    def _sync() -> dict[str, Any]:
        client = _get_clob_client()
        return client.cancel_order(OrderPayload(orderID=order_id))

    try:
        resp = await asyncio.to_thread(_sync)
        ok = bool(resp) and not resp.get("error")
        log.info("[executor] cancel order_id=%s ok=%s", order_id[:12], ok)
        return {"ok": ok, "raw": resp}
    except Exception as e:
        log.error("[executor] 취소 실패 order_id=%s err=%s", order_id[:12], e)
        return {"ok": False, "error": str(e)}
