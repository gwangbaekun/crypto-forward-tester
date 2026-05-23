"""Simple in-process TTL cache for value_scan endpoints."""
from __future__ import annotations

import time
from typing import Any, Callable


class TTLCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, ttl: float, fn: Callable[[], Any]) -> Any:
        now = time.monotonic()
        if key in self._store:
            ts, val = self._store[key]
            if now - ts < ttl:
                return val
        val = fn()
        self._store[key] = (now, val)
        return val

    def invalidate(self, *keys: str) -> None:
        if keys:
            for k in keys:
                self._store.pop(k, None)
        else:
            self._store.clear()


vs_cache = TTLCache()
