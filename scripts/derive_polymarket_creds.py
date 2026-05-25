"""Polymarket L2 API 자격증명 1회 파생 스크립트 (V2 SDK).

사용법:
    pip install py-clob-client-v2
    python scripts/derive_polymarket_creds.py

개인키는 이 컴퓨터에서만 사용되며 어디에도 저장되지 않습니다.
출력된 4줄을 .env / Railway 등에 추가하세요.

전용 트레이딩 PK 발급 절차:
    1. MetaMask 새 계정 생성 → 새 EOA + PK
    2. Polymarket 사이트 → Connect Wallet 으로 새 EOA 로그인
       → 자동으로 새 프록시 지갑 부여
    3. 새 프록시에 USDC.e 또는 pUSD 입금
    4. 이 스크립트 실행 → 새 api_key/secret/passphrase 발급
    5. .env 의 POLYMARKET_* 6개 값 모두 교체
       (POLYMARKET_PK 도 새 PK 로 추가)
"""
from __future__ import annotations

import getpass
import re
import sys


def main():
    print("Polymarket V2 L2 자격증명 파생 도구")
    print("개인키는 이 터미널에서만 사용되며 저장되지 않습니다.\n")

    try:
        from py_clob_client_v2 import ClobClient
    except ImportError:
        print("[ERROR] py-clob-client-v2 가 없습니다. 먼저 설치하세요:")
        print("  pip install py-clob-client-v2")
        sys.exit(1)

    pk = getpass.getpass("MetaMask 개인키 입력 (입력 내용 화면에 표시 안 됨): ").strip()

    if not pk:
        print("[ERROR] 개인키를 입력해주세요.")
        sys.exit(1)

    pk = re.sub(r"\s+", "", pk)
    if not pk.startswith("0x"):
        pk = "0x" + pk

    if len(pk) != 66:
        print(f"[ERROR] 개인키 길이가 잘못됐습니다 ({len(pk)}자). 64자리 hex 키여야 합니다.")
        sys.exit(1)

    print("\n Polymarket CLOB V2 에 연결 중...")
    try:
        client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=137,
            key=pk,
        )
        creds = client.create_or_derive_api_key()

    except Exception as e:
        print(f"\n[ERROR] 자격증명 파생 실패: {e}")
        sys.exit(1)

    from eth_account import Account
    eoa = Account.from_key(pk).address

    print("\n" + "=" * 60)
    print(" 아래 값들을 .env / Railway 환경변수에 추가하세요")
    print(" (개인키 자체는 POLYMARKET_PK 로 직접 입력하세요)")
    print("=" * 60)
    print(f"POLYMARKET_EOA_ADDRESS={eoa}")
    print(f"POLYMARKET_API_KEY={creds.api_key}")
    print(f'POLYMARKET_API_SECRET="{creds.api_secret}"')
    print(f"POLYMARKET_PASSPHRASE={creds.api_passphrase}")
    print("# POLYMARKET_PK=<위에서 입력한 PK>            ← 실거래에 필수")
    print("# POLYMARKET_WALLET_ADDRESS=<프록시 지갑 주소>  ← Polymarket 프로필에서 복사")
    print("=" * 60)


if __name__ == "__main__":
    main()
