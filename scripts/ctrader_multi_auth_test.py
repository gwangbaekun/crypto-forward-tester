"""
멀티 계정 동시 접속 검증 (읽기전용, 실주문 없음).

ctrader_accounts.yaml 의 enabled 계좌 전체를 실제 CTraderExecutor 로
동시에 붙여서:
  1) 토큰 1개로 모든 계좌가 인증되는지 (_authed)
  2) TcpProtocol 클래스 레벨 큐가 섞여 응답이 깨지지 않는지 (각자 reconcile 성공)
를 확인한다.

    PYTHONPATH=src python scripts/ctrader_multi_auth_test.py
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _load_env(path: pathlib.Path) -> None:
    """실행 전 .env 를 os.environ 에 주입 (앱 lifespan 밖에서 도는 스크립트라 필요)."""
    import os
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


async def main() -> None:
    import os
    # 로컬 standalone 실행: 원격 DB(host.docker.internal) 대신 임시 sqlite 사용.
    # 토큰은 .env의 CTRADER_ACCESS_TOKEN 을 쓰므로 DB엔 없어도 됨(get_tokens→빈값).
    os.environ["DATABASE_URL"] = f"sqlite:///{ROOT}/data/local_test.db"
    _load_env(ROOT / ".env")

    from db.session import init_db
    init_db()

    from common.ctrader_account_loader import get_enabled_accounts
    from common.ctrader_executor import get_executor

    accounts = get_enabled_accounts()
    if not accounts:
        print("❌ enabled 계좌 없음 — ctrader_accounts.yaml 확인")
        return

    print(f"■ enabled 계좌 {len(accounts)}개 동시 접속 시도:")
    for k, a in accounts.items():
        print(f"   - {k}: account={a['account_id']} env={a['env']} symbol={a['symbol_id']}")

    execs = {}
    for firm_key, a in accounts.items():
        ex = get_executor(
            account_id=a["account_id"],
            env=a["env"],
            symbol_id=a["symbol_id"],
            lot_size=a.get("lot_size"),
            units_per_lot=a.get("units_per_lot"),
        )
        if ex is None:
            print(f"❌ {firm_key}: executor 생성 불가 (토큰/계좌/심볼 확인)")
            continue
        execs[firm_key] = ex

    # 두 연결이 동시에 인증되기를 대기 (최대 30초)
    print("\n■ 인증 대기...")
    for _ in range(60):
        await asyncio.sleep(0.5)
        if all(ex._authed for ex in execs.values()):
            break

    print("\n■ 인증 결과:")
    all_ok = True
    for firm_key, ex in execs.items():
        status = "✅ 인증됨" if ex._authed else "❌ 미인증(타임아웃/ACCESS_DENIED)"
        if not ex._authed:
            all_ok = False
        print(f"   - {firm_key} (account={ex._account_id}): {status}")

    if not all_ok:
        print("\n토큰 1개로 두 계좌가 동시 인증되지 않음. 로그에서 ACCESS_DENIED 확인.")
        return

    # 각자 포지션 조회 (읽기전용) — 큐 섞임 없이 각 계좌 응답이 정확히 오는지 검증
    print("\n■ 각 계좌 포지션 조회 (읽기전용):")
    results = await asyncio.gather(
        *[ex.get_position(symbol="") for ex in execs.values()],
        return_exceptions=True,
    )
    for (firm_key, ex), res in zip(execs.items(), results):
        if isinstance(res, Exception):
            print(f"   - {firm_key}: ❌ 예외 {res}")
        elif res is None:
            print(f"   - {firm_key}: ⚠️ 응답 없음(타임아웃)")
        else:
            acct = res.get("account_id")
            match = "✅ 계좌일치" if acct == ex._account_id else f"❌ 계좌불일치! 응답 account={acct}"
            print(f"   - {firm_key}: 포지션 {len(res.get('positions', []))}개  {match}")

    print("\n■ 동시 접속 검증 완료.")


if __name__ == "__main__":
    asyncio.run(main())
