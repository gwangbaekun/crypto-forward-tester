from __future__ import annotations

import os
from typing import Optional

import redis

_client: Optional[redis.Redis] = None


def get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    return _client


def key(name: str) -> str:
    return f"{os.environ['REDIS_KEY_PREFIX']}{name}"
