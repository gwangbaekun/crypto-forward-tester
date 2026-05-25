"""Polymarket 실거래 주문 실행기.

필요 env:
  POLYMARKET_PK               — EOA 개인키 (주문 서명용)
  POLYMARKET_WALLET_ADDRESS   — 프록시(funder) 지갑 주소
  POLYMARKET_EOA_ADDRESS      — EOA 주소 (확인용)
  POLYMARKET_ORDER_SIZE_USD   — (선택) 포지션당 달러, 기본 1.0
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("polymarket.executor")

_ORDER_SIZE_USD = float(os.environ.get("POLYMARKET_ORDER_SIZE_USD", "1.0"))

# 싱글턴 클라이언트 (프로세스당 1개)
_client: Any = None


def _get_client():
    global _client
    if _client is not None:
        return _client

    from py_clob_client.client import ClobClient

    pk      = os.environ.get("POLYMARKET_PK", "").strip()
    funder  = os.environ.get("POLYMARKET_WALLET_ADDRESS", "").strip()

    if not pk:
        raise ValueError("POLYMARKET_PK 환경변수 필요 (실거래용)")
    if not funder:
        raise ValueError("POLYMARKET_WALLET_ADDRESS 환경변수 필요")

    eoa = os.environ.get("POLYMARKET_EOA_ADDRESS", "").strip()
    # EOA ≠ funder → signature_type=1 (POLY_PROXY)
    sig_type = 0 if (eoa and eoa.lower() == funder.lower()) else 1

    _client = ClobClient(
        host           = "https://clob.polymarket.com",
        key            = pk,
        chain_id       = 137,
        funder         = funder,
        signature_type = sig_type,
    )
    creds = _client.create_or_derive_api_creds()
    _client.set_api_creds(creds)
    log.info("[Executor] ClobClient 초기화 완료 sig_type=%d", sig_type)
    return _client


async def place_order(
    token_id: str,
    price: float,
    size_usd: float = _ORDER_SIZE_USD,
) -> dict:
    """GTC 지정가 매수 주문.

    size_usd 달러만큼 매수. 실제 shares = size_usd / price.
    반환: {"order_id": str, "status": "filled"|"open"|"failed"}
    """
    import asyncio
    from py_clob_client.clob_types import OrderArgs, OrderType

    shares = round(size_usd / price, 4)

    def _sync_place():
        client = _get_client()
        order = client.create_order(OrderArgs(
            token_id = token_id,
            price    = round(price, 4),
            size     = shares,
            side     = "BUY",
        ))
        return client.post_order(order, OrderType.GTC)

    try:
        resp = await asyncio.get_event_loop().run_in_executor(None, _sync_place)
        order_id = resp.get("orderID") or resp.get("id") or ""
        status   = resp.get("status", "open")
        log.info(
            "[Executor] 주문 완료 token=%s price=%.3f shares=%.4f order_id=%s status=%s",
            token_id[:12], price, shares, order_id, status,
        )
        return {"order_id": order_id, "status": status, "raw": resp}
    except Exception as e:
        log.error("[Executor] 주문 실패 token=%s error=%s", token_id[:12], e)
        return {"order_id": "", "status": "failed", "error": str(e)}
