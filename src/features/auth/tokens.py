"""
서명 세션 토큰 (stdlib 만 사용 — 새 의존성 없음).

포맷:  base64url(payload_json) "." base64url(hmac_sha256(secret, payload_b64))

payload:
  r   role  — "admin" | "guest"
  exp 만료 unix ts (초)
  iat 발급 unix ts (초)
  pid guest 일 때 access_passes.id, admin 이면 0

exp 는 토큰 자체에 박혀 있고, guest 는 매 요청마다 DB 의 pass 상태(만료·폐기)도 재확인한다.
따라서 발급 후 admin 이 폐기하면 쿠키가 남아 있어도 즉시 차단된다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Optional

_ROLES = ("admin", "guest")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(secret: str, payload_b64: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256)
    return _b64e(mac.digest())


@dataclass(frozen=True)
class SessionToken:
    role: str
    pass_id: int
    expires_at: int
    issued_at: int


def issue_token(secret: str, role: str, ttl_seconds: int, pass_id: int = 0) -> str:
    if role not in _ROLES:
        raise ValueError(f"unknown role: {role!r}")
    if ttl_seconds <= 0:
        raise ValueError(f"ttl_seconds must be positive: {ttl_seconds}")
    now = int(time.time())
    payload = {"r": role, "exp": now + ttl_seconds, "iat": now, "pid": int(pass_id)}
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{payload_b64}.{_sign(secret, payload_b64)}"


def verify_token(secret: str, token: str) -> Optional[SessionToken]:
    """서명·만료가 유효하면 SessionToken, 아니면 None."""
    if not token or token.count(".") != 1:
        return None
    payload_b64, sig = token.split(".", 1)
    if not hmac.compare_digest(sig, _sign(secret, payload_b64)):
        return None
    try:
        payload = json.loads(_b64d(payload_b64))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    role = payload.get("r")
    exp = payload.get("exp")
    iat = payload.get("iat")
    pid = payload.get("pid")
    if role not in _ROLES or not isinstance(exp, int) or not isinstance(iat, int):
        return None
    if not isinstance(pid, int):
        return None
    if exp <= int(time.time()):
        return None
    return SessionToken(role=role, pass_id=pid, expires_at=exp, issued_at=iat)


def hash_code(secret: str, code: str) -> str:
    """게스트 패스코드 → 저장용 해시. 코드는 고엔트로피 랜덤이라 HMAC-SHA256 로 충분."""
    return hmac.new(secret.encode("utf-8"), code.strip().encode("utf-8"), hashlib.sha256).hexdigest()


def generate_code() -> str:
    """사람이 옮겨 적을 수 있는 형식: XXXX-XXXX-XXXX (혼동 문자 제외, 32^12 = 60bit)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # I, O, 0, 1 제외
    chunks = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)]
    return "-".join(chunks)
