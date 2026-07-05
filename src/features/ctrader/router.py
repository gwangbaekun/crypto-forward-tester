"""cTrader OAuth helper endpoints (local/Railway bootstrap)."""
from __future__ import annotations

import os
import pathlib
import secrets
from typing import Dict
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse
from common.ctrader_token_store import save_tokens

router = APIRouter(prefix="/auth/ctrader", tags=["ctrader-auth"])


def _get_or_bootstrap_executors() -> dict:
    """
    실행 중인 executor가 없으면 ctrader_accounts.yaml의 enabled 계좌를 읽어서
    executor를 직접 생성한다. positions/close API가 전략 루프 시작 전에도 동작하게 한다.
    """
    from common.ctrader_executor import get_all_executors, get_executor
    executors = get_all_executors()
    if executors:
        return executors

    try:
        from common.ctrader_account_loader import get_enabled_accounts
        for firm_key, acfg in get_enabled_accounts().items():
            account_id = acfg.get("account_id")
            env        = acfg.get("env")
            symbol_id  = acfg.get("symbol_id")
            lot_size   = acfg.get("lot_size")
            if account_id and env and symbol_id:
                ex = get_executor(
                    account_id=account_id,
                    env=env,
                    symbol_id=symbol_id,
                    lot_size=lot_size,
                    units_per_lot=acfg.get("units_per_lot"),
                )
                if ex is not None:
                    print(f"[ctrader/router] bootstrap: {firm_key} account={account_id}")
    except Exception as e:
        print(f"[ctrader/router] bootstrap executor 실패: {e}")

    return get_all_executors()


@router.get("/positions")
async def ctrader_positions():
    from common.ctrader_executor import get_all_executors
    executors = get_all_executors()

    all_positions: list = []
    for account_id, ex in executors.items():
        cached = ex.get_cached_position()
        if cached and "positions" in cached:
            all_positions.extend(cached["positions"])

    return JSONResponse({
        "positions": all_positions,
        "account_count": len(executors),
        "note": "cTrader는 주문 시에만 연결됩니다. 포지션은 최근 주문 이후 캐시만 표시됩니다.",
    })


@router.post("/positions/{position_id}/close")
async def ctrader_close_position(
    position_id: int,
    volume: int = Query(default=None, description="청산 볼륨 (units). 미입력 시 executor lot_size 기본값 사용"),
):
    executors = _get_or_bootstrap_executors()
    if not executors:
        raise HTTPException(status_code=503, detail="executor 없음 — CTRADER_ACCESS_TOKEN 등 환경변수를 확인하세요.")

    for account_id, ex in executors.items():
        result = await ex.close_position_by_id(position_id, volume=volume)
        if result is not None:
            fill = float((result or {}).get("avgPrice") or 0)
            return JSONResponse({
                "ok": True,
                "positionId": position_id,
                "account_id": account_id,
                "fill": fill if fill > 0 else None,
                "raw": result,
            })

    raise HTTPException(status_code=404, detail=f"positionId={position_id} 청산 실패 — executor가 응답하지 않거나 포지션을 찾을 수 없습니다.")


def _build_yaml_suggestion(accounts: list) -> str:
    """fetch한 계좌 목록을 ctrader_accounts.yaml 기존 항목과 비교해 추천 문구 생성.
    파일은 건드리지 않고 텍스트만 반환 — 실거래 계좌 설정이라 자동 반영은 하지 않는다.
    """
    from common.ctrader_account_loader import get_all_accounts
    existing = get_all_accounts()

    lines = []
    matched_ctids = set()
    for firm_key, cfg in existing.items():
        cur_id = int(cfg.get("account_id") or 0)
        match = next(
            (a for a in accounts if cur_id in (a["ctidTraderAccountId"], a["traderLogin"])),
            None,
        )
        if not match:
            lines.append(f"# {firm_key}: 매칭되는 계좌를 fetch 목록에서 못 찾음 (현재 account_id={cur_id})")
            continue
        matched_ctids.add(match["ctidTraderAccountId"])
        correct_id  = match["ctidTraderAccountId"]
        correct_env = "live" if match["isLive"] else "demo"
        cur_env = cfg.get("env")
        if cur_id == correct_id and cur_env == correct_env:
            lines.append(f"# {firm_key}: 이미 정확함 (account_id={correct_id}, env={correct_env})")
        else:
            lines.append(
                f"# {firm_key}: 수정 필요 → account_id: {correct_id}   env: {correct_env}"
                f"  (현재 account_id={cur_id}, env={cur_env})"
            )

    unassigned = [a for a in accounts if a["ctidTraderAccountId"] not in matched_ctids]
    for a in unassigned:
        lines.append(
            "# 미할당 계좌 — 추가하려면:\n"
            f"#   <firm_key>:\n"
            f'#     label: "<이름>"\n'
            f"#     enabled: false\n"
            f'#     env: {"live" if a["isLive"] else "demo"}\n'
            f"#     account_id: {a['ctidTraderAccountId']}   # traderLogin={a['traderLogin']}\n"
            f"#     symbol_id: 0   # TODO scripts/ctrader_list_symbols.py 로 확인\n"
            f"#     lot_size: 0.05\n"
            f"#     units_per_lot: 1"
        )
    return "\n".join(lines)


_TOKEN_BASE = "https://openapi.ctrader.com/apps/token"
_AUTH_BASE  = "https://id.ctrader.com/my/settings/openapi/grantingaccess/"
_state_store: Dict[str, bool] = {}

_ENV_PATH = pathlib.Path(__file__).parents[3] / ".env"


def _required_env() -> tuple[str, str, str]:
    client_id     = os.getenv("CTRADER_CLIENT_ID", "").strip()
    client_secret = os.getenv("CTRADER_CLIENT_SECRET", "").strip()
    redirect_uri  = os.getenv("CTRADER_REDIRECT_URI", "").strip()
    if not client_id or not client_secret or not redirect_uri:
        raise HTTPException(
            status_code=500,
            detail="Missing CTRADER_CLIENT_ID / CTRADER_CLIENT_SECRET / CTRADER_REDIRECT_URI",
        )
    return client_id, client_secret, redirect_uri


def _update_env(updates: dict) -> None:
    lines   = _ENV_PATH.read_text().splitlines() if _ENV_PATH.exists() else []
    written = set()
    out     = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        if "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}")
                written.add(k)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in written:
            out.append(f"{k}={v}")
    _ENV_PATH.write_text("\n".join(out) + "\n")


# ── OAuth 흐름 ───────────────────────────────────────────────────────────────

@router.get("/login")
async def ctrader_login(scope: str = Query("trading", pattern="^(trading|accounts)$")):
    """Redirect to cTrader consent page."""
    client_id, _, redirect_uri = _required_env()
    state = secrets.token_urlsafe(24)
    _state_store[state] = True
    query = urlencode({
        "client_id":    client_id,
        "redirect_uri": redirect_uri,
        "scope":        scope,
        "product":      "web",
        "state":        state,
    })
    return RedirectResponse(url=f"{_AUTH_BASE}?{query}", status_code=302)


@router.get("/callback")
async def ctrader_callback(
    code:  str = Query(...),
    state: str = Query(default=""),
):
    """Exchange code → token. 결과를 env에 바로 저장."""
    client_id, client_secret, redirect_uri = _required_env()
    if not state or not _state_store.pop(state, None):
        raise HTTPException(status_code=400, detail="Invalid or missing OAuth state")

    params = {
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  redirect_uri,
        "client_id":     client_id,
        "client_secret": client_secret,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.get(_TOKEN_BASE, params=params)
    if res.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {res.text[:400]}")

    token = res.json() if res.content else {}
    access_token  = token.get("accessToken") or token.get("access_token", "")
    refresh_token = token.get("refreshToken") or token.get("refresh_token", "")

    # .env 자동 저장
    if access_token:
        save_tokens(access_token, refresh_token)
        _update_env({
            "CTRADER_ACCESS_TOKEN":  access_token,
            "CTRADER_REFRESH_TOKEN": refresh_token,
        })

    response = {
        "ok":      True,
        "message": "Token saved to DB(.env도 동기화). 필요시 Railway Variables에도 동일 값 반영.",
        "token":   token,
        "env_example": {
            "CTRADER_ACCESS_TOKEN":  access_token,
            "CTRADER_REFRESH_TOKEN": refresh_token,
        },
    }

    # 이 토큰이 인증 가능한 전체 계좌 목록 + ctrader_accounts.yaml 추천 문구.
    # 파일은 자동으로 안 바꾸고 텍스트만 응답에 실어준다 — 실거래 계좌라 사람이 확인 후 복붙.
    if access_token:
        try:
            import asyncio
            from common.ctrader_executor import fetch_account_list_by_token
            accounts = await asyncio.get_event_loop().run_in_executor(
                None, fetch_account_list_by_token, client_id, client_secret, access_token, 15.0,
            )
            response["fetched_accounts"]  = accounts
            response["yaml_suggestion"]   = _build_yaml_suggestion(accounts)
        except Exception as e:
            response["account_fetch_error"] = str(e)

    return JSONResponse(response)


# ── 토큰 갱신 ────────────────────────────────────────────────────────────────

@router.get("/refresh")
async def ctrader_refresh(refresh_token: str = Query(default="")):
    """Refresh access token. 갱신된 토큰 자동 .env 저장."""
    client_id, client_secret, _ = _required_env()
    rt = (refresh_token or os.getenv("CTRADER_REFRESH_TOKEN", "")).strip()
    if not rt:
        raise HTTPException(status_code=400, detail="Missing refresh token")

    params = {
        "grant_type":    "refresh_token",
        "refresh_token": rt,
        "client_id":     client_id,
        "client_secret": client_secret,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.get(_TOKEN_BASE, params=params)
    if res.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Token refresh failed: {res.text[:400]}")

    token         = res.json() if res.content else {}
    access_token  = token.get("accessToken") or token.get("access_token", "")
    refresh_token = token.get("refreshToken") or token.get("refresh_token", "")

    if access_token:
        save_tokens(access_token, refresh_token)
        _update_env({
            "CTRADER_ACCESS_TOKEN":  access_token,
            "CTRADER_REFRESH_TOKEN": refresh_token,
        })

    return {"ok": True, "token": token}
