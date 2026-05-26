"""Polymarket 전용 워커 — GCP e2-micro 등 상시 VM에서 실행.

HTTP 서버 없이 run_polymarket() 만 실행.
  PYTHONPATH=src python -m polymarket_worker.main
"""
from __future__ import annotations

import asyncio
import logging
import sys

from db.session import init_db

log = logging.getLogger("polymarket_worker")


async def _run() -> None:
    init_db()
    from features.strategy.polymarket.runner import run_polymarket

    log.info("[worker] Polymarket engine starting")
    await run_polymarket()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("[worker] stopped")


if __name__ == "__main__":
    main()
