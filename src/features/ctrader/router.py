"""cTrader OAuth helper endpoints (local/Railway bootstrap)."""
from __future__ import annotations

import os
import secrets
from typing import Dict
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse

router = APIRouter(prefix="/auth/ctrader", tags=["ctrader-auth"])

_TOKEN_BASE = "https://openapi.ctrader.com/apps/token"
_AUTH_BASE = "https://id.ctrader.com/my/settings/openapi/grantingaccess/"
_state_store: Dict[str, bool] = {}


def _required_env() -> tuple[str, str, str]:
    client_id = os.getenv("CTRADER_CLIENT_ID", "").strip()
    client_secret = os.getenv("CTRADER_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("CTRADER_REDIRECT_URI", "").strip()
    if not client_id or not client_secret or not redirect_uri:
        raise HTTPException(
            status_code=500,
            detail=(
                "Missing CTRADER_CLIENT_ID / CTRADER_CLIENT_SECRET / CTRADER_REDIRECT_URI"
            ),
        )
    return client_id, client_secret, redirect_uri


@router.get("/login")
async def ctrader_login(scope: str = Query("trading", pattern="^(trading|accounts)$")):
    """Redirect user to official cTrader consent page."""
    client_id, _, redirect_uri = _required_env()
    state = secrets.token_urlsafe(24)
    _state_store[state] = True
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "product": "web",
            "state": state,
        }
    )
    return RedirectResponse(url=f"{_AUTH_BASE}?{query}", status_code=302)


@router.get("/callback")
async def ctrader_callback(
    code: str = Query(...),
    state: str = Query(default=""),
):
    """
    Exchange authorization code for access/refresh token.
    Returns token payload so user can copy into env securely.
    """
    client_id, client_secret, redirect_uri = _required_env()
    if not state or not _state_store.pop(state, None):
        raise HTTPException(status_code=400, detail="Invalid or missing OAuth state")

    params = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.get(_TOKEN_BASE, params=params)
    if res.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {res.text[:400]}")

    token = res.json() if res.content else {}
    return JSONResponse(
        {
            "ok": True,
            "message": "Copy accessToken/refreshToken into env",
            "token": token,
            "env_example": {
                "CTRADER_ACCESS_TOKEN": token.get("accessToken", ""),
                "CTRADER_REFRESH_TOKEN": token.get("refreshToken", ""),
            },
        }
    )


@router.get("/refresh")
async def ctrader_refresh(refresh_token: str = Query(default="")):
    """Refresh access token from query or CTRADER_REFRESH_TOKEN env."""
    client_id, client_secret, _ = _required_env()
    rt = (refresh_token or os.getenv("CTRADER_REFRESH_TOKEN", "")).strip()
    if not rt:
        raise HTTPException(status_code=400, detail="Missing refresh token")

    params = {
        "grant_type": "refresh_token",
        "refresh_token": rt,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.get(_TOKEN_BASE, params=params)
    if res.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Token refresh failed: {res.text[:400]}")
    token = res.json() if res.content else {}
    return {"ok": True, "token": token}
