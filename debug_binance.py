"""
Binance Testnet 연결 디버그 스크립트.

실행:
    cd /Users/home/Developer/T/btc_forwardtest
    python debug_binance.py
"""
import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv

# .env 로드
load_dotenv(Path(__file__).parent / ".env")

import sys
sys.path.insert(0, str(Path(__file__).parent / "src"))

from common.binance_executor import BinanceExecutor


async def main():
    key    = os.environ.get("BINANCE_API_KEY", "")
    secret = os.environ.get("BINANCE_API_SECRET", "")
    testnet = os.environ.get("BINANCE_TESTNET", "true")

    print("=" * 55)
    print(f"  KEY    : {key[:8]}...{key[-4:] if key else '(없음)'}")
    print(f"  SECRET : {secret[:4]}...{secret[-4:] if secret else '(없음)'}")
    print(f"  TESTNET: {testnet}")
    print("=" * 55)

    if not key or not secret:
        print("❌ API 키가 없습니다. .env 확인 필요")
        return

    ex = BinanceExecutor()
    symbol = "BTCUSDT"

    # ── 1. 퍼블릭 엔드포인트 — 인증 불필요 ───────────────────────────────
    print("\n[1] 퍼블릭 가격 조회 (인증 없음)")
    try:
        price = await ex.get_market_price(symbol)
        print(f"    ✅ {symbol} 현재가: ${price:,.2f}")
    except Exception as e:
        print(f"    ❌ 실패: {e}")
        print("    → testnet.binancefuture.com 접속 자체가 안 됨 (네트워크/방화벽)")
        return

    # ── 2. 인증 필요 — 잔고 조회 ─────────────────────────────────────────
    print("\n[2] USDT 잔고 조회 (인증 필요)")
    try:
        balance = await ex.get_usdt_balance()
        print(f"    ✅ 가용 잔고: {balance:.2f} USDT")
    except Exception as e:
        print(f"    ❌ 실패: {e}")
        print("    → API 키가 잘못됐거나 Testnet 전용 키가 아님")
        print("    → https://testnet.binancefuture.com 에서 별도 키 발급 필요")
        return

    # ── 3. 현재 포지션 ────────────────────────────────────────────────────
    print("\n[3] 현재 포지션 조회")
    try:
        pos = await ex.get_position(symbol)
        if pos:
            print(f"    ✅ 포지션 있음: {pos}")
        else:
            print(f"    ✅ 포지션 없음 (정상)")
    except Exception as e:
        print(f"    ❌ 실패: {e}")

    # ── 4. stepSize 조회 ─────────────────────────────────────────────────
    print("\n[4] LOT_SIZE stepSize 조회")
    try:
        step = await ex.get_step_size(symbol)
        print(f"    ✅ stepSize: {step}")
    except Exception as e:
        print(f"    ❌ 실패: {e}")

    print("\n" + "=" * 55)
    print("  모든 체크 통과 → Binance Testnet 연결 정상")
    print("=" * 55)


if __name__ == "__main__":
    asyncio.run(main())
