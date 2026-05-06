"""
cTrader 계정 목록 + Symbol 목록 조회 → .env 자동 저장.

ctrader-open-api 는 Twisted 기반이므로 reactor.run() 으로 구동.

사용법:
    python scripts/ctrader_list_symbols.py
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ── .env 유틸 ────────────────────────────────────────────────────────────────

ENV_PATH = ROOT / ".env"


def _load_env(path: pathlib.Path) -> dict:
    env: dict = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def _save_env(updates: dict) -> None:
    lines   = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    written: set = set()
    out: list    = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            out.append(line)
            continue
        if "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}")
                written.add(k)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in written:
            out.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(out) + "\n")
    print(f"✅ .env 저장 완료")


# ── Twisted 기반 메인 ─────────────────────────────────────────────────────────

def run() -> None:
    from twisted.internet import reactor, threads

    from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAAccountAuthReq,
        ProtoOAApplicationAuthReq,
        ProtoOAGetAccountListByAccessTokenReq,
        ProtoOASymbolsListReq,
    )
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAAccountAuthRes,
        ProtoOAApplicationAuthRes,
        ProtoOAErrorRes,
        ProtoOAGetAccountListByAccessTokenRes,
        ProtoOASymbolsListRes,
    )

    import os as _os
    env = {**_load_env(ENV_PATH), **{k: v for k, v in _os.environ.items() if k.startswith("CTRADER_")}}

    client_id     = env.get("CTRADER_CLIENT_ID", "").strip()
    client_secret = env.get("CTRADER_CLIENT_SECRET", "").strip()
    access_token  = env.get("CTRADER_ACCESS_TOKEN", "").strip()
    account_id    = int(env.get("CTRADER_ACCOUNT_ID", "0") or "0")
    ctrader_env   = env.get("CTRADER_ENV", "demo").strip().lower()
    is_live       = ctrader_env == "live"

    if not access_token:
        print("❌ CTRADER_ACCESS_TOKEN 없음. 앱 실행 후 /auth/ctrader/login 먼저.")
        return

    host = EndPoints.PROTOBUF_LIVE_HOST if is_live else EndPoints.PROTOBUF_DEMO_HOST
    port = EndPoints.PROTOBUF_PORT
    print(f"연결 중: {host}:{port}  (env={ctrader_env})")

    state: dict = {
        "account_id":   account_id,
        "account_list": [],
        "symbol_list":  [],
        "env_updates":  {},
    }

    client = Client(host, port, TcpProtocol)

    def stop():
        if reactor.running:
            reactor.stop()

    def on_connected(c):
        print("✅ TCP 연결")
        req = ProtoOAApplicationAuthReq()
        req.clientId     = client_id
        req.clientSecret = client_secret
        client.send(req)

    def on_disconnected(c, reason):
        print(f"연결 종료: {reason}")
        stop()

    def on_message(c, message):
        payload = Protobuf.extract(message)

        if isinstance(payload, ProtoOAErrorRes):
            print(f"❌ cTrader 에러: code={payload.errorCode}  desc={payload.description}")
            stop()
            return

        elif isinstance(payload, ProtoOAApplicationAuthRes):
            # App 인증 완료 → 계정 목록 요청
            req = ProtoOAGetAccountListByAccessTokenReq()
            req.accessToken = access_token
            client.send(req)

        elif isinstance(payload, ProtoOAGetAccountListByAccessTokenRes):
            accounts = list(payload.ctidTraderAccount)
            state["account_list"] = accounts

            target_live = is_live
            matching = [a for a in accounts if bool(a.isLive) == target_live]

            print(f"\n── 계정 목록 (env={ctrader_env}) {'─'*35}")
            print(f"{'#':<4} {'ctidTraderAccountId':<22} {'traderLogin':<16} {'isLive'}")
            print("-" * 55)
            for i, a in enumerate(accounts):
                label = "LIVE" if a.isLive else "demo"
                print(f"{i:<4} {a.ctidTraderAccountId:<22} {a.traderLogin:<16} {label}")

            def _pick_account():
                # 스레드에서 실행 (blocking input)
                if state["account_id"]:
                    print(f"\n→ CTRADER_ACCOUNT_ID={state['account_id']} (이미 설정됨)")
                    return state["account_id"]
                if len(matching) == 1:
                    chosen = matching[0]
                    print(f"\n→ {ctrader_env.upper()} 계정 자동 선택: {chosen.ctidTraderAccountId}  (login={chosen.traderLogin})")
                    return chosen.ctidTraderAccountId
                raw = input(f"\n#번호 입력 (0~{len(accounts)-1}): ").strip()
                try:
                    return accounts[int(raw)].ctidTraderAccountId
                except Exception:
                    print("⚠️  잘못된 입력")
                    return None

            def _after_pick(chosen_id):
                if not chosen_id:
                    stop()
                    return
                state["account_id"] = chosen_id
                if not env.get("CTRADER_ACCOUNT_ID", "").strip():
                    state["env_updates"]["CTRADER_ACCOUNT_ID"] = str(chosen_id)

                req = ProtoOAAccountAuthReq()
                req.ctidTraderAccountId = chosen_id
                req.accessToken         = access_token
                client.send(req)

            d = threads.deferToThread(_pick_account)
            d.addCallback(_after_pick)

        elif isinstance(payload, ProtoOAAccountAuthRes):
            print(f"✅ 계정 인증 완료 (accountId={state['account_id']})")
            req = ProtoOASymbolsListReq()
            req.ctidTraderAccountId    = state["account_id"]
            req.includeArchivedSymbols = False
            client.send(req)

        elif isinstance(payload, ProtoOASymbolsListRes):
            symbols = list(payload.symbol)
            state["symbol_list"] = symbols

            eth_syms = [s for s in symbols if "ETH" in s.symbolName.upper()]
            print(f"\n── ETH 관련 심볼 ({len(eth_syms)}개 / 전체 {len(symbols)}개) {'─'*20}")
            print(f"{'symbolId':<12} {'symbolName'}")
            print("-" * 35)
            target_id = None
            for s in sorted(eth_syms, key=lambda x: x.symbolName):
                print(f"{s.symbolId:<12} {s.symbolName}")
                if s.symbolName.upper() in ("ETHUSD", "ETH/USD", "ETHUSDT", "ETH/USDT"):
                    target_id = s.symbolId
            if not target_id and eth_syms:
                target_id = eth_syms[0].symbolId

            def _pick_symbol():
                current = env.get("CTRADER_SYMBOL_ID", "").strip()
                if current:
                    print(f"\n→ CTRADER_SYMBOL_ID={current} (이미 설정됨)")
                    return None
                if target_id:
                    ans = input(f"\nCTRADER_SYMBOL_ID={target_id} 으로 저장할까요? [y/N]: ").strip().lower()
                    return str(target_id) if ans == "y" else None
                raw = input("\nsymbolId 직접 입력: ").strip()
                return raw if raw.isdigit() else None

            def _after_symbol(sym_id):
                if sym_id:
                    state["env_updates"]["CTRADER_SYMBOL_ID"] = sym_id
                if state["env_updates"]:
                    _save_env(state["env_updates"])
                print("\n━━ 완료 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                print("CTRADER_LOT_SIZE=0.01 (.env에서 조정 가능)")
                print("서버 재시작하면 cTrader 주문 활성화됨")
                stop()

            d = threads.deferToThread(_pick_symbol)
            d.addCallback(_after_symbol)

    client.setConnectedCallback(on_connected)
    client.setDisconnectedCallback(on_disconnected)
    client.setMessageReceivedCallback(on_message)
    client.startService()
    reactor.run()


if __name__ == "__main__":
    run()
