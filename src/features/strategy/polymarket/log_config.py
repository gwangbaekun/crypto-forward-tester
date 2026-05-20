"""Polymarket 로깅 — Docker/stdout 노이즈 억제.

기본: polymarket.* → WARNING (연결·스캔·시그널 등 정상 동작은 미출력).
개발 시: POLYMARKET_LOG_LEVEL=DEBUG|INFO
"""
from __future__ import annotations

import logging
import os

_PARENT = "polymarket"


def configure_polymarket_logging() -> None:
    level_name = os.environ.get("POLYMARKET_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)
    logging.getLogger(_PARENT).setLevel(level)
