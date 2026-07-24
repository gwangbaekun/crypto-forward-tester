"""
cTrader 심볼 목록 조회 → ctrader_accounts.yaml 자동 저장.

사용법:
    python scripts/ctrader_list_symbols.py <firm_key>

    firm_key: ctrader_accounts.yaml 의 accounts 키 (예: ftmo, funded_next, funding_pips ...)
    생략하면 accounts 목록을 보여주고 선택하게 함.

예:
    python scripts/ctrader_list_symbols.py ftmo
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

ENV_PATH      = ROOT / ".env"
ACCOUNTS_PATH = ROOT / "src" / "common" / "ctrader_accounts.yaml"


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


def _load_accounts() -> dict:
    import yaml
    if not ACCOUNTS_PATH.exists():
        return {}
    return yaml.safe_load(ACCOUNTS_PATH.read_text(encoding="utf-8")) or {}


def _save_symbol_id(firm_key: str, symbol_id: int) -> None:
    """ctrader_accounts.yaml 의 firm_key.symbol_id 를 업데이트."""
    text   = ACCOUNTS_PATH.read_text(encoding="utf-8")
    lines  = text.splitlines()
    in_firm = False
    depth   = 0
    out     = []
    for line in lines:
        stripped = line.lstrip()
        # firm 블록 진입 감지 (2-space indent key)
        if line.startswith("  ") and not line.startswith("   ") and stripped.startswith(f"{firm_key}:"):
            in_firm = True
            depth   = 0
            out.append(line)
            continue
        if in_firm:
            # firm 내부 들여쓰기 (4 spaces)
            if line.startswith("    ") and stripped.startswith("symbol_id:"):
                out.append(f"    symbol_id: {symbol_id}")
                continue
            # 다음 firm 블록 또는 최상위 키이면 종료
            if line.startswith("  ") and not line.startswith("    ") and stripped and not stripped.startswith("#"):
                in_firm = False
        out.append(line)
    ACCOUNTS_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"✅ ctrader_accounts.yaml 저장: {firm_key}.symbol_id = {symbol_id}")


def _pick_firm(accounts: dict) -> tuple[str, dict] | None:
    """CLI 인자 또는 interactive 선택으로 firm 반환."""
    keys = list(accounts.get("accounts", {}).keys())
    if not keys:
        print("❌ ctrader_accounts.yaml 에 accounts 항목이 없습니다.")
        return None

    # CLI 인자
    if len(sys.argv) > 1:
        firm_key = sys.argv[1].strip()
        if firm_key not in accounts.get("accounts", {}):
            print(f"❌ '{firm_key}' 는 ctrader_accounts.yaml 에 없습니다.")
            print(f"   사용 가능: {', '.join(keys)}")
            return None
        return firm_key, accounts["accounts"][firm_key]

    # interactive
    print("\n── ctrader_accounts.yaml firm 목록 ──────────────")
    for i, k in enumerate(keys):
        acfg  = accounts["accounts"][k]
        state = "✅" if acfg.get("enabled") else "  "
        print(f"  {i}  {state}  {k}  (account_id={acfg.get('account_id', '?')})")
    raw = input("\n#번호 또는 firm key 입력: ").strip()
    try:
        firm_key = keys[int(raw)] if raw.isdigit() else raw
    except (IndexError, ValueError):
        print("⚠️  잘못된 입력")
        return None
    if firm_key not in accounts.get("accounts", {}):
        print(f"❌ '{firm_key}' 없음")
        return None
    return firm_key, accounts["accounts"][firm_key]


def run() -> None:
    import os as _os

    from twisted.internet import reactor, threads
    from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAAccountAuthReq,
        ProtoOAApplicationAuthReq,
        ProtoOAGetAccountListByAccessTokenReq,
        ProtoOASymbolsListReq,
        ProtoOAAccountAuthRes,
        ProtoOAApplicationAuthRes,
        ProtoOAErrorRes,
        ProtoOAGetAccountListByAccessTokenRes,
        ProtoOASymbolsListRes,
    )

    # ── 자격증명 (.env 에서만, 계좌별 값은 accounts.yaml에서)
    dot_env       = {**_load_env(ENV_PATH), **{k: v for k, v in _os.environ.items() if k.startswith("CTRADER_")}}
    client_id     = dot_env.get("CTRADER_CLIENT_ID", "").strip()
    client_secret = dot_env.get("CTRADER_CLIENT_SECRET", "").strip()
    access_token  = dot_env.get("CTRADER_ACCESS_TOKEN", "").strip()

    if not access_token:
        print("❌ CTRADER_ACCESS_TOKEN 없음. 앱 실행 후 /auth/ctrader/login 먼저.")
        return

    # ── firm 선택
    accounts = _load_accounts()
    pick = _pick_firm(accounts)
    if not pick:
        return
    firm_key, acfg = pick

    target_account_id = int(acfg.get("account_id") or 0)
    is_live           = str(acfg.get("env", "demo")).strip().lower() == "live"
    env_label         = "live" if is_live else "demo"

    print(f"\n[{firm_key}] account_id={target_account_id}  env={env_label}")

    host = EndPoints.PROTOBUF_LIVE_HOST if is_live else EndPoints.PROTOBUF_DEMO_HOST
    port = EndPoints.PROTOBUF_PORT
    print(f"연결 중: {host}:{port}")

    state: dict = {"account_id": target_account_id}
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
        stop()

    def on_message(c, message):
        payload = Protobuf.extract(message)

        if isinstance(payload, ProtoOAErrorRes):
            print(f"❌ 에러: {payload.errorCode} — {payload.description}")
            stop()

        elif isinstance(payload, ProtoOAApplicationAuthRes):
            # account_id가 명시됐으면 바로 인증, 없으면 목록 먼저 조회
            if state["account_id"]:
                req = ProtoOAAccountAuthReq()
                req.ctidTraderAccountId = state["account_id"]
                req.accessToken         = access_token
                client.send(req)
            else:
                req = ProtoOAGetAccountListByAccessTokenReq()
                req.accessToken = access_token
                client.send(req)

        elif isinstance(payload, ProtoOAGetAccountListByAccessTokenRes):
            accs = list(payload.ctidTraderAccount)
            matching = [a for a in accs if bool(a.isLive) == is_live]
            print(f"\n── 계정 목록 ({'─'*40})")
            print(f"{'#':<4} {'ctidTraderAccountId':<22} {'traderLogin':<16} {'isLive'}")
            print("-" * 55)
            for i, a in enumerate(accs):
                print(f"{i:<4} {a.ctidTraderAccountId:<22} {a.traderLogin:<16} {'LIVE' if a.isLive else 'demo'}")

            def _pick():
                if len(matching) == 1:
                    chosen = matching[0]
                    print(f"\n→ 자동 선택: {chosen.ctidTraderAccountId}  (login={chosen.traderLogin})")
                    return chosen.ctidTraderAccountId
                raw = input(f"\n#번호 입력 (0~{len(accs)-1}): ").strip()
                try:
                    return accs[int(raw)].ctidTraderAccountId
                except Exception:
                    return None

            def _after_pick(chosen_id):
                if not chosen_id:
                    stop()
                    return
                state["account_id"] = chosen_id
                req = ProtoOAAccountAuthReq()
                req.ctidTraderAccountId = chosen_id
                req.accessToken         = access_token
                client.send(req)

            threads.deferToThread(_pick).addCallback(_after_pick)

        elif isinstance(payload, ProtoOAAccountAuthRes):
            print(f"✅ 계정 인증 완료 (accountId={state['account_id']})")
            req = ProtoOASymbolsListReq()
            req.ctidTraderAccountId    = state["account_id"]
            req.includeArchivedSymbols = False
            client.send(req)

        elif isinstance(payload, ProtoOASymbolsListRes):
            symbols   = list(payload.symbol)
            eth_syms  = [s for s in symbols if "ETH" in s.symbolName.upper()]
            btc_syms  = [s for s in symbols if "BTC" in s.symbolName.upper()]
            highlight = eth_syms + [s for s in btc_syms if s not in eth_syms]

            print(f"\n── ETH/BTC 심볼 ({len(highlight)}개 / 전체 {len(symbols)}개) {'─'*20}")
            print(f"{'symbolId':<12} {'symbolName'}")
            print("-" * 35)
            target_id = None
            for s in sorted(highlight, key=lambda x: x.symbolName):
                print(f"{s.symbolId:<12} {s.symbolName}")
                if s.symbolName.upper() in ("ETHUSD", "ETH/USD", "ETHUSDT", "ETH/USDT"):
                    target_id = s.symbolId
            if not target_id and eth_syms:
                target_id = sorted(eth_syms, key=lambda x: x.symbolName)[0].symbolId

            current_sym = int(acfg.get("symbol_id") or 0)

            def _pick_symbol():
                nonlocal target_id
                if current_sym and current_sym != 0:
                    print(f"\n→ 현재 yaml 값: symbol_id={current_sym}")
                    ans = input("덮어쓸까요? [y/N]: ").strip().lower()
                    if ans != "y":
                        return None
                if target_id:
                    ans = input(f"\nsymbol_id={target_id} 으로 저장할까요? [y/N]: ").strip().lower()
                    if ans == "y":
                        return target_id
                raw = input("symbolId 직접 입력 (스킵: Enter): ").strip()
                return int(raw) if raw.isdigit() else None

            def _after_symbol(sym_id):
                if sym_id:
                    _save_symbol_id(firm_key, sym_id)
                    print(f"\n다음 단계: ctrader_accounts.yaml 에서 {firm_key}.enabled: true 로 변경")
                else:
                    print("\n저장 스킵")
                stop()

            threads.deferToThread(_pick_symbol).addCallback(_after_symbol)

    client.setConnectedCallback(on_connected)
    client.setDisconnectedCallback(on_disconnected)
    client.setMessageReceivedCallback(on_message)
    client.startService()
    reactor.run()


if __name__ == "__main__":
    run()
