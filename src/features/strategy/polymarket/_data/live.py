"""Polymarket 실시간 지갑 조회 (V2 SDK).

py-clob-client-v2 기반. PK 가 있어야 잔액 조회가 가능 (V2 SDK 의 L2 인증이
signer 를 요구하기 때문). PK 가 없으면 잔액은 0 으로 반환되고 포지션 정보만
data-api 에서 조회한다 (시뮬레이션 모드).

필요 env (실거래):
  POLYMARKET_PK               — EOA 개인키
  POLYMARKET_API_KEY          — V2 L2 API 키
  POLYMARKET_API_SECRET       — V2 시크릿
  POLYMARKET_PASSPHRASE       — V2 패스프레이즈
  POLYMARKET_WALLET_ADDRESS   — 프록시(funder) 지갑 주소
  POLYMARKET_EOA_ADDRESS      — (선택) signature_type 자동 추정용
  POLYMARKET_SIGNATURE_TYPE   — (선택) 0=EOA / 1=POLY_PROXY / 2=GNOSIS_SAFE
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

log = logging.getLogger("polymarket.live")

_DATA_API   = "https://data-api.polymarket.com"
_CLOB_HOST  = "https://clob.polymarket.com"
_TIMEOUT    = 10.0
_USDC_SCALE = 1_000_000  # pUSD / USDC — 6 decimals

# Polygon mainnet (CLOB V2 collateral)
_PUSD_TOKEN       = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
_USDC_E_TOKEN     = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
_USDC_NATIVE_TOKEN = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
_POLYGON_RPCS = [
    u.strip()
    for u in (
        os.environ.get("POLYGON_RPC_URL", ""),
        "https://polygon-bor.publicnode.com",
        "https://rpc.ankr.com/polygon",
        "https://1rpc.io/matic",
    )
    if u.strip()
]

# 프로세스당 1개 client (PK·creds 로딩이 비싸기 때문)
_clob_client: Any = None


# ── env / signature_type 헬퍼 ──────────────────────────────────────────────

def _wallet() -> str:
    addr = os.environ.get("POLYMARKET_WALLET_ADDRESS", "").strip()
    if not addr:
        raise ValueError("POLYMARKET_WALLET_ADDRESS 환경변수 필요")
    return addr


def _resolve_signature_type() -> int:
    """0=EOA, 1=POLY_PROXY(이메일/매직), 2=GNOSIS_SAFE.

    명시적 POLYMARKET_SIGNATURE_TYPE 가 있으면 그 값. 없으면
    EOA == WALLET 이면 0, 다르면 1 (프록시 지갑) 로 추정.
    """
    raw = os.environ.get("POLYMARKET_SIGNATURE_TYPE", "").strip()
    if raw:
        try:
            v = int(raw)
            if v in (0, 1, 2, 3):
                return v
        except ValueError:
            pass
    eoa    = os.environ.get("POLYMARKET_EOA_ADDRESS", "").strip().lower()
    wallet = os.environ.get("POLYMARKET_WALLET_ADDRESS", "").strip().lower()
    return 0 if eoa and wallet and eoa == wallet else 1


# ── V2 ClobClient (실거래용 L2 인증) ────────────────────────────────────────

def _get_clob_client() -> Any:
    """py-clob-client-v2 의 ClobClient 싱글턴.

    PK 또는 자격증명 4종 중 하나라도 빠지면 ValueError.
    """
    global _clob_client
    if _clob_client is not None:
        return _clob_client

    from py_clob_client_v2 import ApiCreds, ClobClient

    pk         = os.environ.get("POLYMARKET_PK",         "").strip()
    api_key    = os.environ.get("POLYMARKET_API_KEY",    "").strip()
    secret     = os.environ.get("POLYMARKET_API_SECRET", "").strip()
    passphrase = os.environ.get("POLYMARKET_PASSPHRASE", "").strip()
    funder     = os.environ.get("POLYMARKET_WALLET_ADDRESS", "").strip()

    missing = [k for k, v in {
        "POLYMARKET_PK":             pk,
        "POLYMARKET_API_KEY":        api_key,
        "POLYMARKET_API_SECRET":     secret,
        "POLYMARKET_PASSPHRASE":     passphrase,
        "POLYMARKET_WALLET_ADDRESS": funder,
    }.items() if not v]
    if missing:
        raise ValueError(f"환경변수 누락 (실거래 모드): {', '.join(missing)}")

    sig_type = _resolve_signature_type()

    _clob_client = ClobClient(
        host           = _CLOB_HOST,
        chain_id       = 137,
        key            = pk,
        creds          = ApiCreds(api_key=api_key, api_secret=secret, api_passphrase=passphrase),
        signature_type = sig_type,
        funder         = funder,
    )
    log.info("[live] ClobClient(V2) 초기화 sig_type=%d funder=%s", sig_type, funder[:10] + "...")
    return _clob_client


def _has_pk() -> bool:
    """실거래 모드 (PK + 자격증명) 활성 여부."""
    return all(os.environ.get(k, "").strip() for k in (
        "POLYMARKET_PK",
        "POLYMARKET_API_KEY",
        "POLYMARKET_API_SECRET",
        "POLYMARKET_PASSPHRASE",
        "POLYMARKET_WALLET_ADDRESS",
    ))


def _pk_valid() -> bool:
    """MetaMask hex PK (0x + 64 hex) 여부. UUID 등 잘못된 값은 CLOB 호출 스킵."""
    pk = os.environ.get("POLYMARKET_PK", "").strip()
    if not pk:
        return False
    raw = pk[2:] if pk.startswith("0x") else pk
    return len(raw) == 64 and all(c in "0123456789abcdefABCDEF" for c in raw)


# ── 온체인 ERC-20 잔액 (Polygon) ─────────────────────────────────────────────

def _balance_of_calldata(holder: str) -> str:
    addr = holder.lower().removeprefix("0x")
    return "0x70a08231" + ("0" * 24) + addr


async def _erc20_balance(token: str, holder: str) -> float:
    """Polygon RPC eth_call — balanceOf(holder)."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [
            {"to": token, "data": _balance_of_calldata(holder)},
            "latest",
        ],
    }
    last_err: Exception | None = None
    async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
        for rpc in _POLYGON_RPCS:
            try:
                r = await cli.post(rpc, json=payload)
                r.raise_for_status()
                body = r.json()
                if body.get("error"):
                    raise RuntimeError(body["error"])
                result = body.get("result", "0x0")
                return int(result, 16) / _USDC_SCALE
            except Exception as e:
                last_err = e
                continue
    if last_err:
        log.warning("[live] Polygon RPC balanceOf 실패: %s", last_err)
    return 0.0


async def fetch_onchain_balances(wallet: str | None = None) -> dict[str, float]:
    """프록시 지갑의 온체인 pUSD / USDC.e / Native USDC."""
    addr = wallet or _wallet()
    pusd, usdc_e, usdc_native = await asyncio.gather(
        _erc20_balance(_PUSD_TOKEN, addr),
        _erc20_balance(_USDC_E_TOKEN, addr),
        _erc20_balance(_USDC_NATIVE_TOKEN, addr),
    )
    return {
        "pusd_onchain":   round(pusd, 4),
        "usdc_e":         round(usdc_e, 4),
        "usdc_native":    round(usdc_native, 4),
    }


# ── CLOB V2 COLLATERAL (= pUSD trading balance) ───────────────────────────────

def _clob_collateral_sync() -> tuple[float, dict[str, Any]]:
    """balance-allowance 갱신 후 COLLATERAL(pUSD) 잔액 + 원본 응답."""
    from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

    client = _get_clob_client()
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    try:
        client.update_balance_allowance(params)
    except Exception as e:
        log.debug("[live] update_balance_allowance: %s", e)
    resp = client.get_balance_allowance(params)
    try:
        bal = float(resp.get("balance", 0)) / _USDC_SCALE
    except (TypeError, ValueError):
        bal = 0.0
    return round(bal, 4), resp if isinstance(resp, dict) else {"raw": resp}


async def fetch_clob_balance() -> float:
    """CLOB 거래용 pUSD(COLLATERAL) 잔액. PK 없거나 형식 오류면 0."""
    if not _has_pk() or not _pk_valid():
        return 0.0
    try:
        bal, _ = await asyncio.to_thread(_clob_collateral_sync)
        return bal
    except Exception as e:
        log.warning("[live] CLOB 잔액 조회 실패: %s", e)
        return 0.0


async def fetch_balance_raw() -> dict[str, Any]:
    """디버그: CLOB balance-allowance 원본 + 온체인 잔액."""
    wallet = _wallet()
    out: dict[str, Any] = {
        "wallet": wallet,
        "signature_type": _resolve_signature_type(),
        "pk_valid": _pk_valid(),
        "onchain": await fetch_onchain_balances(wallet),
    }
    if _has_pk() and _pk_valid():
        try:
            clob_bal, raw = await asyncio.to_thread(_clob_collateral_sync)
            out["clob_pusd"] = clob_bal
            out["balance_allowance"] = raw
        except Exception as e:
            out["clob_error"] = str(e)
    else:
        out["clob_error"] = "POLYMARKET_PK 미설정 또는 hex PK 아님"
    return out


async def fetch_cash_balances() -> dict[str, float]:
    """대시보드용 현금 잔액: CLOB pUSD + 온체인 breakdown."""
    wallet = _wallet()
    onchain, clob_pusd = await asyncio.gather(
        fetch_onchain_balances(wallet),
        fetch_clob_balance(),
    )
    pusd_onchain = onchain["pusd_onchain"]
    # UI 메인 숫자: CLOB 거래 잔액 우선, 0이면 온체인 pUSD (입금만 된 경우)
    pusd_cash = clob_pusd if clob_pusd > 0 else pusd_onchain
    return {
        **onchain,
        "clob_pusd":    clob_pusd,
        "pusd_cash":    round(pusd_cash, 4),
        "usdc_cash":    round(pusd_cash, 4),  # 하위 호환
    }


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

    import time as _time
    now_ts = _time.time()

    out = []
    for p in data:
        size       = float(p.get("size",         0))
        avg_price  = float(p.get("avgPrice",      0) or p.get("avgCost",      0) or 0)
        cur_price  = float(p.get("currentPrice",  0) or p.get("price",        0) or 0)
        cur_value  = float(p.get("currentValue",  0) or 0)
        init_val   = float(p.get("initialValue",  0) or size * avg_price)
        unrealized = cur_value - init_val if cur_value else None

        end_date_raw = (
            p.get("endDate") or p.get("endTime") or p.get("expiresAt")
            or p.get("expirationDate") or p.get("end_date_iso")
        )
        end_ts: int | None = None
        hours_left: float | None = None
        if end_date_raw:
            try:
                from datetime import datetime, UTC
                iso = str(end_date_raw).rstrip("Z").split(".")[0]
                end_ts = int(datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp())
                hours_left = round((end_ts - now_ts) / 3600, 2)
            except Exception:
                pass

        out.append({
            "condition_id":   p.get("conditionId") or p.get("condition_id"),
            "question":       (p.get("title") or p.get("question") or "")[:120],
            "outcome":        p.get("outcome") or p.get("side"),
            "size":           round(size, 4),
            "avg_price":      round(avg_price, 4),
            "current_price":  round(cur_price, 4),
            "current_value":  round(cur_value, 4),
            "unrealized_pnl": round(unrealized, 4) if unrealized is not None else None,
            "end_ts":         end_ts,
            "hours_left":     hours_left,
        })
    return out


async def fetch_redeemable_positions() -> list[dict[str, Any]]:
    """Pending Redemption 상태 포지션 목록.

    data-api ?redeemable=true 필터 사용. asset(token_id) 포함해 반환.
    """
    wallet = _wallet()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
        r = await cli.get(
            f"{_DATA_API}/positions",
            params={"user": wallet, "redeemable": "true", "limit": 500},
        )
        r.raise_for_status()
        data = r.json()
    if not isinstance(data, list):
        data = data.get("data", [])

    out = []
    for p in data:
        token_id = p.get("asset")
        size     = float(p.get("size", 0))
        if not token_id or size <= 0:
            continue
        if not p.get("redeemable"):
            continue
        out.append({
            "token_id":     token_id,
            "condition_id": p.get("conditionId"),
            "question":     (p.get("title") or "")[:80],
            "size":         round(size, 4),
            "cur_price":    float(p.get("curPrice", 0) or 0),
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
    """CLOB pUSD + 온체인 잔액 + 포지션 평가액 통합 반환."""
    cash, portfolio_val, positions = await asyncio.gather(
        fetch_cash_balances(),
        fetch_portfolio_value(),
        fetch_open_positions(),
    )

    pusd_cash = cash["pusd_cash"]
    total = pusd_cash + portfolio_val
    return {
        "wallet_address":    _wallet(),
        "pusd_cash":         pusd_cash,
        "clob_pusd":         cash["clob_pusd"],
        "pusd_onchain":      cash["pusd_onchain"],
        "usdc_e":            cash["usdc_e"],
        "usdc_native":       cash["usdc_native"],
        "usdc_cash":         pusd_cash,
        "positions_value":   portfolio_val,
        "total":             round(total, 4),
        "open_positions":    positions,
        "open_count":        len(positions),
        "recommended_slots": max(1, int(pusd_cash)),
        "live_mode":         _has_pk() and _pk_valid(),
    }
