from __future__ import annotations

from datetime import datetime
from typing import Optional, Tuple

from db.models import CTraderToken
from db.session import get_session


def get_tokens() -> Tuple[str, str]:
    """Return (access_token, refresh_token). Empty strings when missing."""
    s = get_session()
    try:
        row: Optional[CTraderToken] = s.get(CTraderToken, "default")
        if not row:
            return "", ""
        return (row.access_token or "").strip(), (row.refresh_token or "").strip()
    finally:
        s.close()


def save_tokens(access_token: str, refresh_token: str = "") -> None:
    """Upsert tokens into DB singleton row."""
    at = (access_token or "").strip()
    rt = (refresh_token or "").strip()
    if not at:
        return

    s = get_session()
    try:
        row: Optional[CTraderToken] = s.get(CTraderToken, "default")
        if row is None:
            row = CTraderToken(
                key="default",
                access_token=at,
                refresh_token=rt or None,
                updated_at=datetime.utcnow(),
            )
            s.add(row)
        else:
            row.access_token = at
            if rt:
                row.refresh_token = rt
            row.updated_at = datetime.utcnow()
        s.commit()
    finally:
        s.close()
