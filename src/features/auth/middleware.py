"""
전역 접근 게이트.

- 미인증        → HTML 요청은 /login 리다이렉트, API 요청은 401 JSON
- role=guest    → GET/HEAD/OPTIONS 만 허용. 그 외 메서드는 403 (읽기 전용)
                  + 매 요청마다 DB 에서 패스 유효성 재확인 (폐기/만료 즉시 반영)
- role=admin    → 제한 없음
- AUTH_ENABLED=false → 미들웨어가 아예 통과 (로컬 개발)
"""
from __future__ import annotations

from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from features.auth.config import COOKIE_NAME, get_auth_config
from features.auth.store import is_pass_active
from features.auth.tokens import verify_token

# 인증 없이 열어두는 경로 (정확 일치)
_PUBLIC_EXACT = {"/health", "/login", "/logout", "/favicon.ico"}

# 인증 없이 열어두는 경로 (prefix)
#   /a/  = 매직 링크 (URL 자체가 자격증명이라 게이트 앞에 있어야 한다)
#   "/api/…", "/admin/…" 는 앞 3글자가 "/ap", "/ad" 라 여기 걸리지 않는다
_PUBLIC_PREFIX = ("/static/", "/a/")

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _is_public(path: str) -> bool:
    return path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIX)


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def _deny(request: Request, status: int, message: str) -> Response:
    if status == 401 and _wants_html(request):
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(url=f"/login?next={quote(target, safe='')}", status_code=303)
    return JSONResponse({"ok": False, "error": message}, status_code=status)


class AccessGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cfg = get_auth_config()
        if not cfg.enabled:
            request.state.auth_role = "admin"
            return await call_next(request)

        if _is_public(request.url.path):
            return await call_next(request)

        token = verify_token(cfg.secret, request.cookies.get(COOKIE_NAME, ""))
        if token is None:
            return _deny(request, 401, "authentication required")

        if token.role == "guest":
            if not is_pass_active(token.pass_id):
                return _deny(request, 401, "access pass expired or revoked")
            if request.method.upper() not in _READ_METHODS:
                return _deny(request, 403, "read-only access: this action requires admin")

        request.state.auth_role = token.role
        request.state.auth_pass_id = token.pass_id
        return await call_next(request)
