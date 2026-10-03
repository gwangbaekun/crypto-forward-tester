from __future__ import annotations

from typing import Optional

from common.redis.client import get_client, key


def _position_key(account_id: int) -> str:
    return key(f"ctrader:open_position_id:{account_id}")


def get_position_id(account_id: int) -> Optional[int]:
    raw = get_client().get(_position_key(account_id))
    if raw is None:
        return None
    position_id = int(raw)
    return position_id


def save_position_id(account_id: int, position_id: int) -> None:
    get_client().set(_position_key(account_id), position_id)


def delete_position_id(account_id: int) -> None:
    get_client().delete(_position_key(account_id))
