"""access_passes 테이블 CRUD — 게스트 패스 발급/검증/폐기."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from db.models import AccessPass
from db.session import get_session

from features.auth.tokens import generate_code, hash_code


def create_pass(
    secret: str,
    label: str,
    ttl_hours: int,
    sliding: bool = False,
    max_days: Optional[int] = None,
) -> Tuple[str, AccessPass]:
    """
    새 패스 발급. 평문 코드는 여기서만 반환되고 이후 복구 불가.

    sliding=True 면 접속할 때마다 만료가 now+ttl_hours 로 밀린다 (자동 연장).
    max_days 는 자동 연장의 절대 상한 (None = 상한 없음).

    Returns: (평문 코드, AccessPass 행 스냅샷)
    """
    if ttl_hours <= 0:
        raise ValueError(f"ttl_hours must be positive: {ttl_hours}")
    if max_days is not None and max_days <= 0:
        raise ValueError(f"max_days must be positive: {max_days}")

    code = generate_code()
    now = datetime.utcnow()
    row = AccessPass(
        code_hash=hash_code(secret, code),
        code_hint=code[:4],
        label=(label or "").strip()[:120],
        created_at=now,
        expires_at=now + timedelta(hours=ttl_hours),
        use_count=0,
        sliding_hours=ttl_hours if sliding else None,
        max_expires_at=(now + timedelta(days=max_days)) if (sliding and max_days) else None,
        renew_count=0,
    )
    s = get_session()
    try:
        s.add(row)
        s.commit()
        s.refresh(row)
        s.expunge(row)
    finally:
        s.close()
    return code, row


def verify_code(secret: str, code: str) -> Optional[AccessPass]:
    """코드가 유효(존재·미폐기·미만료)하면 행을 반환. 사용 기록도 갱신."""
    code = (code or "").strip().upper()
    if not code:
        return None

    s = get_session()
    try:
        row = s.query(AccessPass).filter(AccessPass.code_hash == hash_code(secret, code)).one_or_none()
        if row is None:
            return None
        now = datetime.utcnow()
        if row.revoked_at is not None or row.expires_at <= now:
            return None
        if row.first_used_at is None:
            row.first_used_at = now
        row.last_used_at = now
        row.use_count = (row.use_count or 0) + 1
        _slide(row, now)  # 자동 연장 패스면 로그인 시점에 만료를 민다
        s.commit()
        s.refresh(row)
        s.expunge(row)
        return row
    finally:
        s.close()


def get_pass(pass_id: int) -> Optional[AccessPass]:
    s = get_session()
    try:
        row = s.get(AccessPass, pass_id)
        if row is not None:
            s.expunge(row)
        return row
    finally:
        s.close()


# 자동 연장 시 DB write 를 매 요청마다 하지 않기 위한 최소 간격(초).
# 이 값보다 적게 밀리는 갱신은 건너뛴다.
_SLIDE_WRITE_THRESHOLD_SEC = 60


def _slide(row: AccessPass, now: datetime) -> bool:
    """
    자동 연장 대상이면 expires_at 을 now+sliding_hours 로 민다.
    max_expires_at 상한을 넘지 않는다. 실제로 값을 바꿨으면 True.
    """
    if not row.sliding_hours:
        return False
    target = now + timedelta(hours=row.sliding_hours)
    if row.max_expires_at is not None and target > row.max_expires_at:
        target = row.max_expires_at
    if (target - row.expires_at).total_seconds() < _SLIDE_WRITE_THRESHOLD_SEC:
        return False
    row.expires_at = target
    row.renew_count = (row.renew_count or 0) + 1
    return True


def is_pass_active(pass_id: int) -> bool:
    """
    세션 쿠키가 가리키는 패스가 지금도 유효한가 (폐기·만료 즉시 반영).
    자동 연장 패스라면 이 시점에 만료를 뒤로 민다.
    """
    now = datetime.utcnow()
    s = get_session()
    try:
        row = s.get(AccessPass, pass_id)
        if row is None or row.revoked_at is not None or row.expires_at <= now:
            return False
        if _slide(row, now):
            row.last_used_at = now
            s.commit()
        return True
    finally:
        s.close()


def extend_pass(pass_id: int, hours: int) -> Optional[AccessPass]:
    """
    같은 코드를 유지한 채 만료만 뒤로 민다 (재발급 불필요).
    이미 만료된 패스도 지금 시점 기준으로 되살린다. 폐기된 패스는 대상 아님.
    """
    if hours <= 0:
        raise ValueError(f"hours must be positive: {hours}")

    now = datetime.utcnow()
    s = get_session()
    try:
        row = s.get(AccessPass, pass_id)
        if row is None or row.revoked_at is not None:
            return None
        base = row.expires_at if row.expires_at > now else now
        row.expires_at = base + timedelta(hours=hours)
        row.renew_count = (row.renew_count or 0) + 1
        # 수동 연장이 자동 상한에 막히지 않도록 상한도 함께 밀어준다
        if row.max_expires_at is not None and row.max_expires_at < row.expires_at:
            row.max_expires_at = row.expires_at
        s.commit()
        s.refresh(row)
        s.expunge(row)
        return row
    finally:
        s.close()


def revoke_pass(pass_id: int) -> bool:
    s = get_session()
    try:
        row = s.get(AccessPass, pass_id)
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = datetime.utcnow()
        s.commit()
        return True
    finally:
        s.close()


def list_passes(limit: int = 100) -> List[AccessPass]:
    s = get_session()
    try:
        rows = (
            s.query(AccessPass)
            .order_by(AccessPass.created_at.desc())
            .limit(limit)
            .all()
        )
        for r in rows:
            s.expunge(r)
        return rows
    finally:
        s.close()


def purge_expired(older_than_days: int = 7) -> int:
    """만료 후 N일 지난 행 삭제. 반환: 삭제 건수."""
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
    s = get_session()
    try:
        n = s.query(AccessPass).filter(AccessPass.expires_at < cutoff).delete()
        s.commit()
        return int(n)
    finally:
        s.close()
