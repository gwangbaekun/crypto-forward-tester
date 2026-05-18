#!/usr/bin/env python3
"""
Value Forward Test — Daily runner

Run once:  python scripts/scan_value_forward.py [--market kospi|nasdaq|all]
Run loop:  python scripts/scan_value_forward.py --loop   # daily at 22:00 UTC
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from features.strategy.value_scan.engine import run_daily


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market",  choices=["kospi", "nasdaq", "all"], default="all")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--loop",    action="store_true", help="매일 22:00 UTC 자동 실행")
    args = parser.parse_args()

    markets = ["kospi", "nasdaq"] if args.market == "all" else [args.market]

    if not args.loop:
        result = run_daily(markets=markets, max_workers=args.workers)
        print(f"[value_fwd] {result}")
        return

    print("[value_fwd] loop 모드 — 매일 22:00 UTC 실행")
    while True:
        now    = datetime.now(UTC)
        target = now.replace(hour=22, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        sleep_secs = (target - now).total_seconds()
        print(f"[value_fwd] 다음 실행까지 {sleep_secs / 3600:.1f}h 대기 "
              f"({target.strftime('%Y-%m-%d %H:%M UTC')})")
        time.sleep(sleep_secs)
        try:
            result = run_daily(markets=markets, max_workers=args.workers)
            print(f"[value_fwd] {result}")
        except Exception as e:
            print(f"[value_fwd] 오류: {e}")


if __name__ == "__main__":
    main()
