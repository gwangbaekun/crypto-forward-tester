"""Polymarket L2 API 자격증명 1회 파생 스크립트.

사용법:
    pip install py-clob-client
    python scripts/derive_polymarket_creds.py

개인키는 이 컴퓨터에서만 사용되며 어디에도 저장되지 않습니다.
출력된 3개 값만 Railway / .env 에 넣으세요.
"""
import asyncio
import getpass
import re
import sys


def main():
    print("Polymarket L2 자격증명 파생 도구")
    print("개인키는 이 터미널에서만 사용되며 저장되지 않습니다.\n")

    try:
        from py_clob_client.client import ClobClient
    except ImportError:
        print("[ERROR] py-clob-client가 없습니다. 먼저 설치하세요:")
        print("  pip install py-clob-client")
        sys.exit(1)

    pk = getpass.getpass("MetaMask 개인키 입력 (입력 내용 화면에 표시 안 됨): ").strip()

    if not pk:
        print("[ERROR] 개인키를 입력해주세요.")
        sys.exit(1)

    # 공백·보이지 않는 문자 제거
    pk = re.sub(r'\s+', '', pk)
    if not pk.startswith("0x"):
        pk = "0x" + pk

    if len(pk) != 66:
        print(f"[ERROR] 개인키 길이가 잘못됐습니다 ({len(pk)}자). 64자리 hex 키여야 합니다.")
        sys.exit(1)

    print("\n Polymarket CLOB API에 연결 중...")
    try:
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=pk,
            chain_id=137,  # Polygon
        )
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)

    except Exception as e:
        print(f"\n[ERROR] 자격증명 파생 실패: {e}")
        sys.exit(1)

    from eth_account import Account
    eoa = Account.from_key(pk).address

    print("\n" + "=" * 60)
    print(" 아래 4줄을 Railway 환경변수 / .env 에 추가하세요")
    print(" (개인키는 여기 없습니다 — 이 값들만 저장하면 됩니다)")
    print("=" * 60)
    print(f"POLYMARKET_EOA_ADDRESS={eoa}")
    print(f"POLYMARKET_API_KEY={creds.api_key}")
    print(f'POLYMARKET_API_SECRET="{creds.api_secret}"')
    print(f"POLYMARKET_PASSPHRASE={creds.api_passphrase}")
    print("=" * 60)


if __name__ == "__main__":
    main()
