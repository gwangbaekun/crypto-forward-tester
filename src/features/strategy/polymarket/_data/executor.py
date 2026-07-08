"""Polymarket 실거래 주문 실행기 (V2 SDK).

py-clob-client-v2 기반. live.py 와 동일한 ClobClient 싱글턴을 공유한다.

필요 env:
  POLYMARKET_PK               — EOA 개인키 (주문 서명용)
  POLYMARKET_API_KEY          — V2 L2 API 키
  POLYMARKET_API_SECRET       — V2 시크릿
  POLYMARKET_PASSPHRASE       — V2 패스프레이즈
  POLYMARKET_WALLET_ADDRESS   — 프록시(funder) 지갑 주소
  POLYMARKET_TICK_SIZE        — (선택) 가격 tick, 기본 "0.01"

주문 사이즈: 항상 Polymarket 이 수락하는 절대 최솟값으로 시도.
  1차: shares = ceil(0.01 / price, 2dp)
  실패 시 에러에서 "minimum: N" 파싱 → N shares 로 재시도.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
from typing import Any

from features.strategy.polymarket._data.live import _get_clob_client, _has_pk, _pk_valid

log = logging.getLogger("polymarket.executor")

_TICK_SIZE    = os.environ.get("POLYMARKET_TICK_SIZE", "0.01")
_LIVE_ENABLED = os.environ.get("POLYMARKET_LIVE", "false").strip().lower() == "true"

_MIN_USD_MARGIN = 1.02

_MIN_SIZE_RE = re.compile(r"minimum:\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)


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


def _extract_minimum_size(error_text: str) -> float | None:
    """주문 실패 메시지에서 'minimum: N' 형태의 최소 shares 파싱."""
    if not error_text:
        return None
    m = _MIN_SIZE_RE.search(error_text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


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
    side: str = "BUY",
    size_shares: float | None = None,
) -> dict[str, Any]:
    """GTC 지정가 주문. side="BUY"(기본)|"SELL".

    size_shares 미지정 시 Polymarket 이 수락하는 절대 최솟값으로 시도(기존 동작).
    size_shares 지정 시 그 수량(shares)으로 주문 — fade 처럼 시드 기반 사이징에 사용.

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

    _side = Side.SELL if str(side).upper() == "SELL" else Side.BUY

    if size_shares is not None and size_shares > 0:
        shares = math.ceil(size_shares * 100) / 100   # 2dp 올림
        effective_usd = round(shares * price, 4)
    else:
        shares, effective_usd = _min_order(price)

    def _sync() -> dict[str, Any]:
        client = _get_clob_client()
        return client.create_and_post_order(
            order_args = OrderArgs(
                token_id = token_id,
                price    = round(price, 4),
                size     = shares,
                side     = _side,
            ),
            options    = PartialCreateOrderOptions(tick_size=_TICK_SIZE),
            order_type = OrderType.GTC,
        )

    try:
        resp = await asyncio.to_thread(_sync)
        order_id = resp.get("orderID") or resp.get("orderId") or resp.get("id") or ""
        status   = resp.get("status", "live")
        log.info(
            "[executor] %s token=%s price=%.4f shares=%.2f usd=$%.4f order_id=%s status=%s",
            _side, token_id[:12], price, shares, effective_usd, order_id, status,
        )
        return {"order_id": order_id, "status": status, "raw": resp, "shares": shares, "usd": effective_usd}
    except Exception as e:
        err_text = str(e)
        min_size = _extract_minimum_size(err_text)
        if min_size is not None:
            log.error(
                "[executor] 주문 실패 token=%s err=%s | parsed_min_size=%.4f requested_shares=%.2f — minimum size로 재시도",
                token_id[:12], err_text, min_size, shares,
            )
            corrected_shares = math.ceil(min_size * 100) / 100
            corrected_usd    = round(corrected_shares * price, 4)

            def _sync_retry() -> dict[str, Any]:
                client = _get_clob_client()
                return client.create_and_post_order(
                    order_args = OrderArgs(
                        token_id = token_id,
                        price    = round(price, 4),
                        size     = corrected_shares,
                        side     = _side,
                    ),
                    options    = PartialCreateOrderOptions(tick_size=_TICK_SIZE),
                    order_type = OrderType.GTC,
                )

            try:
                resp2    = await asyncio.to_thread(_sync_retry)
                order_id = resp2.get("orderID") or resp2.get("orderId") or resp2.get("id") or ""
                status   = resp2.get("status", "live")
                log.info(
                    "[executor] %s retry(min_size=%.2f) token=%s order_id=%s status=%s",
                    _side, corrected_shares, token_id[:12], order_id, status,
                )
                return {"order_id": order_id, "status": status, "raw": resp2, "shares": corrected_shares, "usd": corrected_usd}
            except Exception as e2:
                log.error("[executor] 재시도도 실패 token=%s err=%s", token_id[:12], e2)
                return {"order_id": "", "status": "failed", "error": str(e2), "parsed_min_size": min_size}
        else:
            log.error("[executor] 주문 실패 token=%s err=%s", token_id[:12], err_text)
        return {
            "order_id": "",
            "status": "failed",
            "error": err_text,
        }


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
