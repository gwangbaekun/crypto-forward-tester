"""드로다운 대시보드용 in-process TTL 캐시 (value_scan `vs_cache` 와 같은 형태).

원장이 하루 1회만 바뀌므로 대시보드 폴링이 매번 DB 를 때릴 이유가 없다.
스캔 직후에는 `dd_cache.invalidate()` 로 즉시 반영한다.
"""
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


dd_cache = TTLCache()
