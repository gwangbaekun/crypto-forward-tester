"""
현재 CTRADER_ACCESS_TOKEN 이 인증할 수 있는 전체 계좌 목록 조회 (읽기전용).

ProtoOAGetAccountListByAccessTokenReq 로 토큰의 cTID에 묶인 모든 계좌의
ctidTraderAccountId / traderLogin / isLive 를 뽑는다.

ctrader_accounts.yaml 의 account_id 는 traderLogin 이 아니라
반드시 여기 나오는 ctidTraderAccountId 값이어야 한다.

    PYTHONPATH=src python scripts/ctrader_account_list.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _load_env(path: pathlib.Path) -> dict:
    env: dict = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def run() -> None:
    from twisted.internet import reactor
    from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAApplicationAuthReq,
        ProtoOAApplicationAuthRes,
        ProtoOAGetAccountListByAccessTokenReq,
        ProtoOAGetAccountListByAccessTokenRes,
        ProtoOAErrorRes,
    )

    env       = _load_env(ROOT / ".env")
    client_id = env.get("CTRADER_CLIENT_ID", "")
    secret    = env.get("CTRADER_CLIENT_SECRET", "")
    token     = env.get("CTRADER_ACCESS_TOKEN", "")

    if not (client_id and secret and token):
        print("❌ CTRADER_CLIENT_ID / SECRET / ACCESS_TOKEN 누락")
        return

    # 계좌 목록은 live/demo 무관하게 LIVE 호스트로 조회 (토큰 기준 전체 반환).
    client = Client(EndPoints.PROTOBUF_LIVE_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)

    def stop():
        if reactor.running:
            reactor.stop()

    def on_connected(c):
        req = ProtoOAApplicationAuthReq()
        req.clientId     = client_id
        req.clientSecret = secret
        client.send(req)

    def on_message(c, message):
        payload = Protobuf.extract(message)

        if isinstance(payload, ProtoOAErrorRes):
            print(f"❌ 에러: {payload.errorCode} — {payload.description}")
            stop()

        elif isinstance(payload, ProtoOAApplicationAuthRes):
            req = ProtoOAGetAccountListByAccessTokenReq()
            req.accessToken = token
            client.send(req)

        elif isinstance(payload, ProtoOAGetAccountListByAccessTokenRes):
            accs = list(payload.ctidTraderAccount)
            print(f"\n토큰이 인증 가능한 계좌 {len(accs)}개:")
            print(f"{'ctidTraderAccountId':<22} {'traderLogin':<16} {'isLive'}")
            print("─" * 50)
            for a in accs:
                print(f"{a.ctidTraderAccountId:<22} {a.traderLogin:<16} {'LIVE' if a.isLive else 'demo'}")
            print("─" * 50)
            print("→ ctrader_accounts.yaml 의 account_id 에는 위 ctidTraderAccountId 를 넣어야 함.")
            stop()

    client.setConnectedCallback(on_connected)
    client.setDisconnectedCallback(lambda c, r: stop())
    client.setMessageReceivedCallback(on_message)
    client.startService()
    reactor.run()


if __name__ == "__main__":
    run()
