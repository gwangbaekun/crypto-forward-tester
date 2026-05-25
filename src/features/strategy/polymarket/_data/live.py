"""Polymarket 실시간 지갑 조회.

인증 흐름 (L2 — HMAC-SHA256, py-clob-client 호환):
  POLYMARKET_API_KEY / API_SECRET / PASSPHRASE  →  GET /balance-allowance

자격증명 1회 파생:
  python scripts/derive_polymarket_creds.py   (개인키는 로컬에서만 사용)

필요 env:
  POLYMARKET_API_KEY          — L2 API 키
  POLYMARKET_API_SECRET       — L2 시크릿 (base64-url-safe)
  POLYMARKET_PASSPHRASE       — L2 패스프레이즈
  POLYMARKET_EOA_ADDRESS      — API key를 파생한 signer EOA
  POLYMARKET_WALLET_ADDRESS   — Polymarket 프록시(=funder) 지갑 주소
  POLYMARKET_SIGNATURE_TYPE   — (선택) 0=EOA / 1=POLY_PROXY(이메일·매직) / 2=GNOSIS_SAFE
                                기본: EOA != WALLET 면 1, 같으면 0
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Any

import httpx

_CLOB_BASE = "https://clob.polymarket.com"
_DATA_API  = "https://data-api.polymarket.com"
_TIMEOUT   = 10.0

# USDC 는 6-decimals — /balance-allowance 는 micro-units 문자열을 반환.
_USDC_SCALE = 1_000_000


# ── env 헬퍼 ────────────────────────────────────────────────────────────────

def _l2_creds() -> tuple[str, str, str, str]:
    """(eoa_address, api_key, secret, passphrase) — 없으면 즉시 raise."""
    eoa_address = os.environ.get("POLYMARKET_EOA_ADDRESS", "").strip()
    api_key     = os.environ.get("POLYMARKET_API_KEY",    "").strip()
    secret      = os.environ.get("POLYMARKET_API_SECRET", "").strip()
    passphrase  = os.environ.get("POLYMARKET_PASSPHRASE", "").strip()

    missing = [k for k, v in {
        "POLYMARKET_EOA_ADDRESS": eoa_address,
        "POLYMARKET_API_KEY":     api_key,
        "POLYMARKET_API_SECRET":  secret,
        "POLYMARKET_PASSPHRASE":  passphrase,
    }.items() if not v]

    if missing:
        raise ValueError(f"환경변수 누락: {', '.join(missing)}")

    return eoa_address, api_key, secret, passphrase


def _wallet() -> str:
    addr = os.environ.get("POLYMARKET_WALLET_ADDRESS", "").strip()
    if not addr:
        raise ValueError("POLYMARKET_WALLET_ADDRESS 환경변수 필요")
    return addr


def _signature_type(eoa: str) -> int:
    """0=EOA, 1=POLY_PROXY(이메일/매직), 2=GNOSIS_SAFE.

    명시적으로 POLYMARKET_SIGNATURE_TYPE 가 있으면 그 값 사용.
    아니면 EOA == WALLET 이면 0, 다르면 1 (프록시 지갑) 로 추정.
    """
    raw = os.environ.get("POLYMARKET_SIGNATURE_TYPE", "").strip()
    if raw:
        try:
            v = int(raw)
            if v in (0, 1, 2):
                return v
        except ValueError:
            pass
    wallet = os.environ.get("POLYMARKET_WALLET_ADDRESS", "").strip().lower()
    return 0 if wallet and wallet == eoa.lower() else 1


# ── L2 서명 (py-clob-client 호환 HMAC-SHA256) ───────────────────────────────

def _l2_headers(address: str, api_key: str, secret: str, passphrase: str,
                method: str, path: str, body: str = "") -> dict[str, str]:
    """py_clob_client.signing.hmac.build_hmac_signature 와 동일.

    - secret 은 base64-url-safe 디코딩 후 HMAC 키로 사용
    - message = timestamp + method + path (+ body)
    - 결과는 base64-url-safe 인코딩 문자열
    - L2 에는 POLY_NONCE 가 들어가지 않는다 (L1 전용)
    """
    timestamp = str(int(time.time()))
    message = timestamp + method + path
    if body:
        message += body.replace("'", '"')

    key = base64.urlsafe_b64decode(secret)
    digest = hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(digest).decode("utf-8")

    return {
        "POLY_ADDRESS":    address,
        "POLY_API_KEY":    api_key,
        "POLY_SIGNATURE":  signature,
        "POLY_TIMESTAMP":  timestamp,
        "POLY_PASSPHRASE": passphrase,
    }


# ── USDC 잔액 (CLOB API /balance-allowance) ─────────────────────────────────

async def fetch_clob_balance() -> float:
    """Polymarket CLOB /balance-allowance — USDC(또는 pUSD) 현금 잔액.

    HMAC 서명에는 query string 을 포함하지 않고 path 만 (`/balance-allowance`)
    사용한다 — py-clob-client 와 동일한 동작.
    """
    address, api_key, secret, passphrase = _l2_creds()
    sig_type = _signature_type(address)

    sign_path = "/balance-allowance"
    request_url = f"{_CLOB_BASE}{sign_path}"
    params = {"asset_type": "COLLATERAL", "signature_type": sig_type}

    headers = _l2_headers(address, api_key, secret, passphrase, "GET", sign_path)

    async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
        r = await cli.get(request_url, headers=headers, params=params)
        r.raise_for_status()
        data = r.json()

    raw = data.get("balance", 0)
    try:
        balance_micro = float(raw)
    except (TypeError, ValueError):
        balance_micro = 0.0

    return round(balance_micro / _USDC_SCALE, 4)


# ── 오픈 포지션 (data-api, 인증 불필요) ──────────────────────────────────────

async def fetch_open_positions() -> list[dict[str, Any]]:
    """data-api.polymarket.com/positions — 현재 오픈 포지션."""
    wallet = _wallet()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
        r = await cli.get(f"{_DATA_API}/positions", params={"user": wallet, "limit": 500})
        r.raise_for_status()
        data = r.json()
    if not isinstance(data, list):
        data = data.get("data", [])

    out = []
    for p in data:
        size       = float(p.get("size",         0))
        avg_price  = float(p.get("avgPrice",      0) or p.get("avgCost",      0) or 0)
        cur_price  = float(p.get("currentPrice",  0) or p.get("price",        0) or 0)
        cur_value  = float(p.get("currentValue",  0) or 0)
        init_val   = float(p.get("initialValue",  0) or size * avg_price)
        unrealized = cur_value - init_val if cur_value else None

        out.append({
            "condition_id":   p.get("conditionId") or p.get("condition_id"),
            "question":       (p.get("title") or p.get("question") or "")[:120],
            "outcome":        p.get("outcome") or p.get("side"),
            "size":           round(size, 4),
            "avg_price":      round(avg_price, 4),
            "current_price":  round(cur_price, 4),
            "current_value":  round(cur_value, 4),
            "unrealized_pnl": round(unrealized, 4) if unrealized is not None else None,
        })
    return out


async def fetch_portfolio_value() -> float:
    """data-api.polymarket.com/value — 오픈 포지션 총 평가액."""
    wallet = _wallet()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
        r = await cli.get(f"{_DATA_API}/value", params={"user": wallet})
        r.raise_for_status()
        data = r.json()
    if isinstance(data, list) and data:
        return round(float(data[0].get("value", 0)), 4)
    if isinstance(data, dict):
        return round(float(data.get("value", 0)), 4)
    return 0.0


# ── 통합 ─────────────────────────────────────────────────────────────────────

async def fetch_live_wallet() -> dict[str, Any]:
    """CLOB 잔액 + 포지션 평가액 + 오픈 포지션 목록 통합 반환."""
    import asyncio
    usdc_cash, portfolio_val, positions = await asyncio.gather(
        fetch_clob_balance(),
        fetch_portfolio_value(),
        fetch_open_positions(),
    )

    total = usdc_cash + portfolio_val
    return {
        "wallet_address":    _wallet(),
        "usdc_cash":         usdc_cash,
        "positions_value":   portfolio_val,
        "total":             round(total, 4),
        "open_positions":    positions,
        "open_count":        len(positions),
        "recommended_slots": max(1, int(usdc_cash)),
    }
