"""
Auth 설정 — 전부 env 기반, 누락/오설정이면 즉시 예외.

env:
  AUTH_ENABLED       필수. "true" | "false" (그 외 값은 예외)
  ADMIN_PASSWORD     AUTH_ENABLED=true 일 때 필수. admin 로그인 비밀번호
  AUTH_SECRET        AUTH_ENABLED=true 일 때 필수. 쿠키 서명·코드 해시용 (32자 이상)
  AUTH_GUEST_TTL_HOURS    선택, 기본 24. 게스트 패스 유효시간
  AUTH_ADMIN_TTL_DAYS     선택, 기본 30. admin 세션 쿠키 유효기간
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

COOKIE_NAME = "ft_session"


class AuthConfigError(RuntimeError):
    """env 설정이 없거나 잘못됨 — 기동 시점에 터뜨린다."""


def _require_bool(name: str) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        raise AuthConfigError(
            f"{name} 가 설정되지 않았습니다. .env 에 {name}=true 또는 false 를 명시하세요."
        )
    val = raw.strip().lower()
    if val in ("true", "1", "yes"):
        return True
    if val in ("false", "0", "no"):
        return False
    raise AuthConfigError(f"{name} 값이 잘못됐습니다: {raw!r} (true/false 만 허용)")


def _require_str(name: str, min_len: int) -> str:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        raise AuthConfigError(
            f"AUTH_ENABLED=true 인데 {name} 가 비어 있습니다. .env 에 설정하세요."
        )
    if len(raw) < min_len:
        raise AuthConfigError(f"{name} 가 너무 짧습니다 ({len(raw)}자). 최소 {min_len}자.")
    return raw


def _optional_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except ValueError as exc:
        raise AuthConfigError(f"{name} 는 정수여야 합니다: {raw!r}") from exc
    if val <= 0:
        raise AuthConfigError(f"{name} 는 1 이상이어야 합니다: {val}")
    return val


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool
    admin_password: str
    secret: str
    guest_ttl_hours: int
    admin_ttl_days: int


@lru_cache(maxsize=1)
def get_auth_config() -> AuthConfig:
    enabled = _require_bool("AUTH_ENABLED")
    if not enabled:
        return AuthConfig(False, "", "", 24, 30)
    return AuthConfig(
        enabled=True,
        admin_password=_require_str("ADMIN_PASSWORD", min_len=8),
        secret=_require_str("AUTH_SECRET", min_len=32),
        guest_ttl_hours=_optional_int("AUTH_GUEST_TTL_HOURS", 24),
        admin_ttl_days=_optional_int("AUTH_ADMIN_TTL_DAYS", 30),
    )
