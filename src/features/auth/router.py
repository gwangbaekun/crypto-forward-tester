"""
로그인 · 게스트 패스 관리 엔드포인트.

GET  /login                     로그인 화면 (admin 비밀번호 or 게스트 코드 겸용)
POST /login                     인증 → 세션 쿠키 발급
POST /logout, GET /logout       쿠키 삭제
GET  /admin/access              패스 발급/폐기 관리 화면 (admin 전용)
GET  /api/access/passes         패스 목록 (admin 전용)
POST /api/access/passes         패스 발급 → 평문 코드 1회 반환 (admin 전용)
POST /api/access/passes/{id}/revoke   패스 즉시 폐기 (admin 전용)
"""
from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from common.utils import render_template
from features.auth.config import COOKIE_NAME, get_auth_config
from features.auth.store import (
    create_pass,
    extend_pass,
    list_passes,
    revoke_pass,
    verify_code,
)
from features.auth.tokens import issue_token

router = APIRouter(tags=["auth"])


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).isoformat()


def _set_cookie(response, token: str, max_age: int, secure: bool) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def _is_https(request: Request) -> bool:
    # Railway 등 프록시 뒤에서는 x-forwarded-proto 가 실제 스킴
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    return (proto or request.url.scheme) == "https"


def _safe_next(raw: str) -> str:
    """
    로그인 후 이동할 경로 정규화.

    /login 은 인증 없이 열려 있으므로 next 는 완전한 미신뢰 입력이다.
    같은 사이트의 절대경로만 허용 — 오픈 리다이렉트와 스크립트 주입을 원천 차단.
    """
    v = (raw or "").strip()
    if not v.startswith("/") or v.startswith("//"):
        return "/"
    if any(c in v for c in ("<", ">", '"', "'", "\\", "\n", "\r", "\t")):
        return "/"
    return v


def _attach_guest_session(response, request: Request, row) -> None:
    """게스트 세션 쿠키를 응답에 붙인다 (코드 입력 / 매직링크 공통)."""
    cfg = get_auth_config()
    now = datetime.utcnow()
    remaining = int((row.expires_at - now).total_seconds())

    # 자동 연장 패스는 만료가 계속 밀리므로 쿠키를 상한(없으면 90일)까지 길게 준다.
    # 실제 유효성은 매 요청마다 DB(is_pass_active)가 판단하므로 쿠키가 길어도 안전.
    if row.sliding_hours:
        cap = row.max_expires_at or (now + timedelta(days=90))
        cookie_ttl = max(remaining, int((cap - now).total_seconds()))
    else:
        cookie_ttl = remaining

    token = issue_token(cfg.secret, role="guest", ttl_seconds=cookie_ttl, pass_id=row.id)
    _set_cookie(response, token, cookie_ttl, secure=_is_https(request))


def _require_admin(request: Request):
    """미들웨어가 이미 걸러주지만, 라우터 단에서도 방어적으로 확인."""
    if getattr(request.state, "auth_role", None) != "admin":
        return JSONResponse({"ok": False, "error": "admin only"}, status_code=403)
    return None


# ── 로그인 ───────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request, next: str = "/", e: str = ""):
    cfg = get_auth_config()
    if not cfg.enabled:
        return RedirectResponse(url="/", status_code=303)
    return HTMLResponse(render_template(
        "login.html",
        next_url=_safe_next(next),
        link_failed=(e == "1"),   # 매직 링크가 만료/무효였음
    ))


@router.post("/login")
async def login(request: Request):
    cfg = get_auth_config()
    if not cfg.enabled:
        return JSONResponse({"ok": False, "error": "auth is disabled"}, status_code=400)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)

    secret_input = str(body.get("password") or "").strip()
    if not secret_input:
        return JSONResponse({"ok": False, "error": "password is required"}, status_code=400)

    # 1) admin 비밀번호
    if hmac.compare_digest(secret_input, cfg.admin_password):
        max_age = cfg.admin_ttl_days * 86400
        token = issue_token(cfg.secret, role="admin", ttl_seconds=max_age)
        resp = JSONResponse({"ok": True, "role": "admin", "expires_in": max_age})
        _set_cookie(resp, token, max_age, secure=_is_https(request))
        return resp

    # 2) 게스트 패스코드
    row = verify_code(cfg.secret, secret_input)
    if row is not None:
        remaining = int((row.expires_at - datetime.utcnow()).total_seconds())
        if remaining <= 0:
            return JSONResponse({"ok": False, "error": "access pass expired"}, status_code=401)
        resp = JSONResponse({
            "ok": True,
            "role": "guest",
            "expires_in": remaining,
            "expires_at": _iso(row.expires_at),
            "auto_renew": bool(row.sliding_hours),
        })
        _attach_guest_session(resp, request, row)
        return resp

    return JSONResponse({"ok": False, "error": "invalid password or access code"}, status_code=401)


@router.get("/a/{code}", include_in_schema=False)
async def magic_link(request: Request, code: str):
    """
    매직 링크 — 링크 하나로 로그인 완료. 상대는 코드를 타이핑할 필요가 없다.

        https://<host>/a/XA35-7CP3-FZMR

    쿠키를 심고 즉시 "/" 로 303 리다이렉트하므로 주소창에 코드가 남지 않는다.
    Referrer-Policy: no-referrer 로 외부 링크 클릭 시 코드가 새어나가는 것도 막는다.

    URL 에 비밀값이 들어가는 구조라 브라우저 히스토리·프록시 로그에는 남을 수 있다.
    읽기 전용 + 기간제라는 전제에서 감수하는 트레이드오프.
    """
    cfg = get_auth_config()
    if not cfg.enabled:
        return RedirectResponse(url="/", status_code=303)

    row = verify_code(cfg.secret, code)
    if row is None or row.expires_at <= datetime.utcnow():
        # 만료·폐기·오타 → 수동 입력 화면으로 (사유 노출은 최소화)
        resp = RedirectResponse(url="/login?e=1", status_code=303)
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    resp = RedirectResponse(url="/", status_code=303)
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    _attach_guest_session(resp, request, row)
    return resp


@router.api_route("/logout", methods=["GET", "POST"], include_in_schema=False)
async def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


# ── 게스트 패스 관리 (admin) ─────────────────────────────────────────────────

@router.get("/admin/access", response_class=HTMLResponse, include_in_schema=False)
async def access_admin_page(request: Request):
    denied = _require_admin(request)
    if denied:
        return denied
    return HTMLResponse(render_template("access_admin.html"))


@router.get("/api/access/passes")
async def api_list_passes(request: Request):
    denied = _require_admin(request)
    if denied:
        return denied

    now = datetime.utcnow()
    items = []
    for r in list_passes(limit=200):
        if r.revoked_at is not None:
            status = "revoked"
        elif r.expires_at <= now:
            status = "expired"
        else:
            status = "active"
        items.append({
            "id": r.id,
            "label": r.label,
            "hint": r.code_hint,
            "status": status,
            "created_at": _iso(r.created_at),
            "expires_at": _iso(r.expires_at),
            "revoked_at": _iso(r.revoked_at),
            "first_used_at": _iso(r.first_used_at),
            "last_used_at": _iso(r.last_used_at),
            "use_count": r.use_count,
            "remaining_sec": max(0, int((r.expires_at - now).total_seconds())) if status == "active" else 0,
            "auto_renew": bool(r.sliding_hours),
            "sliding_hours": r.sliding_hours,
            "max_expires_at": _iso(r.max_expires_at),
            "renew_count": r.renew_count or 0,
        })
    return JSONResponse({"ok": True, "items": items, "server_time": _iso(now)})


@router.post("/api/access/passes")
async def api_create_pass(request: Request):
    denied = _require_admin(request)
    if denied:
        return denied

    cfg = get_auth_config()
    try:
        body = await request.json()
    except Exception:
        body = {}

    label = str(body.get("label") or "").strip()
    raw_hours = body.get("ttl_hours")
    if raw_hours is None or str(raw_hours).strip() == "":
        ttl_hours = cfg.guest_ttl_hours
    else:
        try:
            ttl_hours = int(raw_hours)
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "ttl_hours must be an integer"}, status_code=400)
        if not (1 <= ttl_hours <= 24 * 30):
            return JSONResponse({"ok": False, "error": "ttl_hours must be 1..720"}, status_code=400)

    sliding = bool(body.get("sliding"))
    raw_max = body.get("max_days")
    max_days: Optional[int] = None
    if sliding and raw_max not in (None, "", "0"):
        try:
            max_days = int(raw_max)
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "max_days must be an integer"}, status_code=400)
        if not (1 <= max_days <= 365):
            return JSONResponse({"ok": False, "error": "max_days must be 1..365"}, status_code=400)

    code, row = create_pass(
        cfg.secret, label=label, ttl_hours=ttl_hours, sliding=sliding, max_days=max_days,
    )
    return JSONResponse({
        "ok": True,
        "code": code,                       # 평문 노출은 이 응답이 마지막
        "id": row.id,
        "label": row.label,
        "expires_at": _iso(row.expires_at),
        "ttl_hours": ttl_hours,
        "auto_renew": sliding,
        "max_expires_at": _iso(row.max_expires_at),
    })


@router.post("/api/access/passes/{pass_id}/extend")
async def api_extend_pass(request: Request, pass_id: int):
    """같은 코드를 유지한 채 만료만 연장 — 상대에게 새 코드를 다시 보낼 필요 없음."""
    denied = _require_admin(request)
    if denied:
        return denied

    cfg = get_auth_config()
    try:
        body = await request.json()
    except Exception:
        body = {}

    raw_hours = body.get("hours")
    if raw_hours is None or str(raw_hours).strip() == "":
        hours = cfg.guest_ttl_hours
    else:
        try:
            hours = int(raw_hours)
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "hours must be an integer"}, status_code=400)
        if not (1 <= hours <= 24 * 30):
            return JSONResponse({"ok": False, "error": "hours must be 1..720"}, status_code=400)

    row = extend_pass(pass_id, hours)
    if row is None:
        return JSONResponse({"ok": False, "error": "not found or revoked"}, status_code=404)
    return JSONResponse({
        "ok": True,
        "id": row.id,
        "expires_at": _iso(row.expires_at),
        "renew_count": row.renew_count,
    })


@router.post("/api/access/passes/{pass_id}/revoke")
async def api_revoke_pass(request: Request, pass_id: int):
    denied = _require_admin(request)
    if denied:
        return denied
    ok = revoke_pass(pass_id)
    if not ok:
        return JSONResponse({"ok": False, "error": "not found or already revoked"}, status_code=404)
    return JSONResponse({"ok": True, "id": pass_id})
