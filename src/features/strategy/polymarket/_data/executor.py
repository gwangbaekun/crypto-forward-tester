"""Polymarket 실거래 주문 실행기 (V2 SDK).

py-clob-client-v2 기반. live.py 와 동일한 ClobClient 싱글턴을 공유한다.

필요 env:
  POLYMARKET_PK               — EOA 개인키 (주문 서명용)
  POLYMARKET_API_KEY          — V2 L2 API 키
  POLYMARKET_API_SECRET       — V2 시크릿
  POLYMARKET_PASSPHRASE       — V2 패스프레이즈
  POLYMARKET_WALLET_ADDRESS   — 프록시(funder) 지갑 주소
  POLYMARKET_ORDER_SIZE_USD   — (선택) 포지션당 달러 상한, 기본 0 (최솟값 자동)
  POLYMARKET_TICK_SIZE        — (선택) 가격 tick, 기본 "0.01"

Polymarket V2 최소 주문 규칙:
  - shares × price ≥ $1 (marketable BUY 거절 방지, 2% 마진 적용 → ≥ $1.02)
  shares 는 소수 2자리 올림 처리.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from typing import Any

from features.strategy.polymarket._data.live import _get_clob_client, _has_pk, _pk_valid

log = logging.getLogger("polymarket.executor")

_ORDER_SIZE_USD = float(os.environ.get("POLYMARKET_ORDER_SIZE_USD", "0"))
_TICK_SIZE      = os.environ.get("POLYMARKET_TICK_SIZE", "0.01")

# POLYMARKET_LIVE=true 일 때만 실제 주문 전송.
# 로컬에서는 .env 에 이 값을 넣지 않으면 자동으로 시뮬 모드.
_LIVE_ENABLED = os.environ.get("POLYMARKET_LIVE", "false").strip().lower() == "true"

# Polymarket V2 최소 제약
# shares × price ≥ $1.02 (2% 마진) 만족하는 최솟값으로 자동 계산
_MIN_USD_MARGIN = 1.02  # rounding 여유 2 %


def _min_order(price: float) -> tuple[float, float]:
    """V2 정책을 만족하는 최소 (shares, usd).

    shares × price ≥ $1.02 를 만족하는 최솟값.
    shares 는 소수 2자리 올림 처리.
    """
    if price <= 0:
        raise ValueError(f"price 가 0 이하: {price}")
    shares_raw = _MIN_USD_MARGIN / price
    # 소수 2자리 올림 (ceil at 2dp)
    shares = math.ceil(shares_raw * 100) / 100
    return shares, round(shares * price, 4)


def is_live_mode() -> bool:
    """실거래 모드 여부.

    POLYMARKET_LIVE=true AND 유효 hex PK + 자격증명 모두 있어야 true.
    로컬에서는 POLYMARKET_LIVE 를 설정하지 않으면 항상 false.
    """
    return _LIVE_ENABLED and _has_pk() and _pk_valid()


# ── 주문 생성 (BUY GTC limit) ───────────────────────────────────────────────

async def place_order(
    token_id: str,
    price: float,
    size_usd: float = _ORDER_SIZE_USD,
    max_usd: float = 0.0,
) -> dict[str, Any]:
    """GTC 지정가 매수 주문.

    size_usd > 0 이면 그 금액 기준 shares 계산, V2 최솟값 자동 보정.
    size_usd == 0 이면 V2 최솟값 그대로.
    max_usd > 0 이면 최솟값이 max_usd 를 초과하는 마켓은 skip (status="skipped").

    Returns:
        {"order_id": str, "status": "matched"|"live"|"delayed"|"unmatched"|"failed"|"skipped",
         "raw": dict | None, "error": str | None}
    """
    if not _LIVE_ENABLED:
        log.info("[executor] POLYMARKET_LIVE!=true → 주문 skip (시뮬 모드) token=%s", token_id[:12])
        return {"order_id": "", "status": "skipped", "error": "POLYMARKET_LIVE 비활성"}
    if not (_has_pk() and _pk_valid()):
        log.info("[executor] PK 미설정 → 주문 skip (시뮬 모드) token=%s", token_id[:12])
        return {"order_id": "", "status": "failed", "error": "POLYMARKET_PK 미설정"}

    from py_clob_client_v2 import OrderArgs, OrderType, PartialCreateOrderOptions, Side

    min_shares, min_usd = _min_order(price)

    if max_usd > 0 and min_usd > max_usd:
        log.info(
            "[executor] skip (min $%.2f > max $%.2f) token=%s price=%.4f",
            min_usd, max_usd, token_id[:12], price,
        )
        return {"order_id": "", "status": "skipped", "error": f"min_cost ${min_usd:.2f} > max ${max_usd:.2f}"}

    if size_usd > 0:
        requested_shares = size_usd / price
        shares = math.ceil(max(requested_shares, min_shares) * 100) / 100
    else:
        shares = min_shares

    effective_usd = round(shares * price, 4)
    if size_usd > 0 and effective_usd > size_usd * 1.5:
        log.warning(
            "[executor] 최소사이즈 보정: 요청 $%.2f → 실제 $%.4f (shares=%.2f price=%.4f)",
            size_usd, effective_usd, shares, price,
        )

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
            "[executor] BUY token=%s price=%.4f shares=%.2f usd=$%.4f order_id=%s status=%s",
            token_id[:12], price, shares, effective_usd, order_id, status,
        )
        return {"order_id": order_id, "status": status, "raw": resp, "shares": shares, "usd": effective_usd}
    except Exception as e:
        log.error("[executor] 주문 실패 token=%s err=%s", token_id[:12], e)
        return {"order_id": "", "status": "failed", "error": str(e)}


# ── CLOB gasless 포지션 청산 (MATIC 불필요) ─────────────────────────────────

async def redeem_positions(token_id: str) -> dict[str, Any]:
    """CLOB update_balance_allowance(CONDITIONAL) — gasless redeem.

    Polymarket V2 relayer가 처리. MATIC 가스비 불필요.
    POLYMARKET_LIVE=true + 유효 PK 없으면 skip.
    """
    if not is_live_mode():
        log.info("[executor] POLYMARKET_LIVE!=true → redeem skip token_id=%s", token_id[:12])
        return {"ok": False, "error": "POLYMARKET_LIVE 비활성"}

    from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

    def _sync() -> dict[str, Any]:
        client = _get_clob_client()
        params = BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id)
        resp = client.update_balance_allowance(params)
        return {"ok": True, "raw": resp}

    try:
        result = await asyncio.to_thread(_sync)
        log.info("[executor] redeem token_id=%s result=%s", token_id[:12], result)
        return result
    except Exception as e:
        log.error("[executor] redeem 예외 token_id=%s err=%s", token_id[:12], e)
        return {"ok": False, "error": str(e)}


# ── Pending Redemption 전체 일괄 청산 ───────────────────────────────────────

async def redeem_all_pending() -> list[dict[str, Any]]:
    """data-api에서 redeemable 포지션 전체 조회 후 즉시 일괄 redeem.

    챌린지 기간 무시 — 포지션 회수를 최우선.
    시뮬 모드(POLYMARKET_LIVE!=true)면 로그만 출력하고 skip.
    """
    from features.strategy.polymarket._data.live import fetch_redeemable_positions

    positions = await fetch_redeemable_positions()
    if not positions:
        log.info("[executor] redeem_all_pending: 청산 대상 없음")
        return []

    log.info("[executor] redeem_all_pending: %d개 처리 시작", len(positions))
    results = []
    for pos in positions:
        token_id = pos["token_id"]
        result = await redeem_positions(token_id)
        entry = {"token_id": token_id, "question": pos["question"], **result}
        results.append(entry)
        log.info("[executor] redeemed %s → %s", pos["question"][:40], result)
    return results


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
