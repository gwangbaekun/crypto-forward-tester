"""오라클 클라우드(오사카) buy/sell 릴레이 — 실주문 실행형.

한국 IP는 Polymarket 주문 API가 막혀 있어, 실제 주문 서명/전송을 이 서비스가
오사카 리전 VM에서 대신 처리한다. fade 엔진이 스파이크를 감지하면 oracle_client
를 통해 이 릴레이의 /order 를 호출하고, 릴레이는 executor.place_order
(py-clob-client-v2) 로 실제 주문을 넣는다.

주문 로직은 기존 코드를 그대로 재사용:
  features.strategy.polymarket._data.executor.place_order / redeem_positions
  features.strategy.polymarket._data.live.fetch_cash_balances

안전장치:
  RELAY_API_KEY        — 설정 시 X-Relay-Key 헤더 검증.
  POLYMARKET_LIVE      — executor 가 내부에서 재확인(true 아니면 주문 skip).

실행:
  uvicorn app:app --host 0.0.0.0 --port 9090
env 파일(/etc/btc-forwardtest.env)의 POLYMARKET_* 자격증명을 os.environ 으로 로드.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("oracle_relay")


# ── env 로딩 (/etc/btc-forwardtest.env → os.environ) ─────────────────────────

def _load_env_file(path: str) -> None:
    p = Path(path)
    if not p.exists():
        log.warning("env 파일 없음: %s (컨테이너 env 로만 동작)", path)
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_file(os.environ.get("RELAY_ENV_FILE", "/etc/btc-forwardtest.env"))

_API_KEY = os.environ.get("RELAY_API_KEY", "")

# executor/live 는 env 로딩 후 import (모듈 로드 시 env 를 읽는 코드 대비)
from features.strategy.polymarket._data.executor import place_order, redeem_positions  # noqa: E402
from features.strategy.polymarket._data.live import fetch_cash_balances  # noqa: E402
from features.strategy.polymarket._data.executor import is_live_mode  # noqa: E402

app = FastAPI(title="polymarket-oracle-relay")


class OrderRequest(BaseModel):
    action: str          # "buy" | "sell"
    side: str            # "NO" | "YES" (로그/추적용, 실제 방향은 token_id + action 이 결정)
    condition_id: str
    question: str = ""
    token_id: str = ""   # 실제 주문 대상 clob token id (fade=NO 토큰)
    price: float         # 주문 지정가 (해당 토큰 기준, 0~1)
    size_usd: float      # 명목 주문 금액 (shares = size_usd / price)
    size_shares: float | None = None  # 지정 시 이 수량 그대로(청산=보유수량 매도)
    reason: str = ""


def _check_key(x_relay_key: str) -> None:
    if _API_KEY and x_relay_key != _API_KEY:
        raise HTTPException(status_code=401, detail="invalid relay key")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "mode": "live" if is_live_mode() else "sim(POLYMARKET_LIVE!=true)",
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/balance")
async def balance(x_relay_key: str = Header(default="")) -> dict:
    _check_key(x_relay_key)
    try:
        bal = await fetch_cash_balances()
        return {"ok": True, **bal}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/order")
async def order(req: OrderRequest, x_relay_key: str = Header(default="")) -> dict:
    _check_key(x_relay_key)

    if not req.token_id:
        raise HTTPException(status_code=400, detail="token_id 필요 (실주문 대상)")
    if req.price <= 0 or req.price >= 1:
        raise HTTPException(status_code=400, detail=f"price 범위 오류: {req.price}")

    # size_shares 지정(청산=보유수량 매도)이면 그대로, 아니면 size_usd/price.
    if req.size_shares is not None and req.size_shares > 0:
        shares = round(req.size_shares, 2)
    else:
        # 내림(floor) — 요청 명목가(size_usd)를 넘지 않게 보수적으로. 하드캡 없음.
        shares = math.floor(req.size_usd / req.price * 100) / 100

    log.info(
        "[RELAY-ORDER] action=%s side=%s token=%s price=%.4f shares=%.2f (size_usd req %.2f) reason=%s | %s",
        req.action, req.side, req.token_id[:16], req.price, shares, req.size_usd,
        req.reason, req.question[:80],
    )

    result = await place_order(
        token_id=req.token_id,
        price=req.price,
        side="BUY" if req.action == "buy" else "SELL",
        size_shares=shares,
    )
    log.info("[RELAY-ORDER] result=%s", result)
    return {"received_at": datetime.now(timezone.utc).isoformat(), **result}


@app.post("/redeem")
async def redeem(token_id: str, x_relay_key: str = Header(default="")) -> dict:
    """만기 해소된 포지션 gasless redeem (fade 타임아웃/미청산 잔여용)."""
    _check_key(x_relay_key)
    result = await redeem_positions(token_id)
    log.info("[RELAY-REDEEM] token=%s result=%s", token_id[:16], result)
    return result
