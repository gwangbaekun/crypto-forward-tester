"""fade 시그널 → 오라클(오사카) buy/sell 릴레이 호출 라우트.

한국/Railway IP는 Polymarket 주문 API가 막혀 있어, 실제 주문은 오사카 리전 오라클
클라우드 VM 위의 릴레이(oracle/relay/app.py)가 대신 서명·전송한다.

ORACLE_RELAY_URL 미설정 시 실주문 없이 로컬 로그만 남긴다(시뮬). 설정 시 릴레이의
실제 응답(order_id, status, shares, usd)을 그대로 돌려준다.
"""
from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("polymarket.fade.oracle")

_RELAY_URL = os.environ.get("ORACLE_RELAY_URL", "").rstrip("/")
_RELAY_KEY = os.environ.get("ORACLE_RELAY_KEY", "")
_TIMEOUT = 30.0


def relay_configured() -> bool:
    return bool(_RELAY_URL)


async def fetch_balance() -> float | None:
    """릴레이 /balance → 가용 pUSD(거래 담보). 릴레이 미설정/실패 시 None."""
    if not _RELAY_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
            r = await cli.get(
                f"{_RELAY_URL}/balance",
                headers={"X-Relay-Key": _RELAY_KEY} if _RELAY_KEY else {},
            )
            d = r.json()
        if d.get("ok"):
            return float(d.get("pusd_cash") or 0.0)
        log.warning("[ORACLE-BAL] 실패: %s", d.get("error"))
        return None
    except Exception as e:
        log.warning("[ORACLE-BAL] 예외: %s", e)
        return None


async def place_order(
    *, side: str, action: str, condition_id: str, question: str,
    token_id: str, price: float, size_usd: float, reason: str,
    size_shares: float | None = None,
) -> dict:
    """오라클 릴레이에 buy/sell 요청.

    action: "buy" | "sell", side: "NO"(fade).
    size_shares 지정 시 그 수량으로 주문(청산=보유수량 그대로 매도).
    반환: 릴레이 응답(order_id/status/shares/usd) 또는 {"status":"logged"|"relay_failed"}.
    """
    payload = {
        "action": action, "side": side, "condition_id": condition_id,
        "question": question[:120], "token_id": token_id,
        "price": round(price, 4), "size_usd": round(size_usd, 4), "reason": reason,
    }
    if size_shares is not None:
        payload["size_shares"] = round(size_shares, 2)

    if not _RELAY_URL:
        log.info("[ORACLE-CALL] (relay 미설정, 로컬 로그만) %s", payload)
        return {"status": "logged", **payload}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
            r = await cli.post(
                f"{_RELAY_URL}/order", json=payload,
                headers={"X-Relay-Key": _RELAY_KEY} if _RELAY_KEY else {},
            )
        resp = r.json()
        log.info("[ORACLE-CALL] relayed http=%s resp=%s", r.status_code, resp)
        return resp
    except Exception as e:
        log.warning("[ORACLE-CALL] relay 실패(%s): %s", e, payload)
        return {"status": "relay_failed", "error": str(e), **payload}
