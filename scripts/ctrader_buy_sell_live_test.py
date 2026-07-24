"""
로컬 실주문 왕복 테스트 (BUY 진입 → 몇 초 대기 → SELL 청산).

목적: 원격(Railway)에서 cTrader 연동이 안 되는 게 코드 문제인지 판단하기 위해,
      로컬에서 실제로 최소 lot 주문을 넣고 체결/청산/수수료(잔고 변동)를 확인한다.

enabled 계좌(ctrader_accounts.yaml) 전부에 대해 순차 실행:
  1) 거래 전 잔고 + 심볼 commission 스펙 조회
  2) get_executor() 로 프로덕션 코드 경로 그대로 BUY 진입
  3) 3초 대기
  4) SELL 청산
  5) 거래 후 잔고 조회 → 왕복 비용(스프레드+수수료) 확인

주의: ftmo 계좌는 env=live (실계좌). 최소 lot(0.05)이라도 실제 스프레드/수수료가 발생한다.

실행:
    docker exec -e CTRADER_ALLOW_LOCAL=1 btc-forwardtest-api \
        python scripts/ctrader_buy_sell_live_test.py
"""
import asyncio
import concurrent.futures
import os
import sys

sys.path.insert(0, "/app/src")


def _fetch_trader_and_symbol(account_id: int, symbol_id: int, is_live: bool,
                              client_id: str, client_secret: str, access_token: str,
                              timeout: float = 15.0) -> dict:
    """임시 Client로 잔고 + 심볼(commission 등) 조회 (읽기전용, 프로덕션 executor와 별개)."""
    from common.ctrader_executor import _ensure_reactor
    from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAAccountAuthReq,
        ProtoOAAccountAuthRes,
        ProtoOAApplicationAuthReq,
        ProtoOAApplicationAuthRes,
        ProtoOAErrorRes,
        ProtoOASymbolByIdReq,
        ProtoOASymbolByIdRes,
        ProtoOATraderReq,
        ProtoOATraderRes,
    )

    reactor = _ensure_reactor()
    fut: concurrent.futures.Future = concurrent.futures.Future()
    state: dict = {}

    def _build():
        host = EndPoints.PROTOBUF_LIVE_HOST if is_live else EndPoints.PROTOBUF_DEMO_HOST
        client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)

        def _finish(value=None, error=None):
            if not fut.done():
                if error is not None:
                    fut.set_exception(error)
                else:
                    fut.set_result(value)
            try:
                client.stopService()
            except Exception:
                pass

        def on_connected(c):
            req = ProtoOAApplicationAuthReq()
            req.clientId = client_id
            req.clientSecret = client_secret
            d = client.send(req)
            if d is not None and hasattr(d, "addErrback"):
                d.addErrback(lambda f: None)

        def on_message(c, message):
            payload = Protobuf.extract(message)
            if isinstance(payload, ProtoOAErrorRes):
                _finish(error=RuntimeError(f"{payload.errorCode}: {payload.description}"))
            elif isinstance(payload, ProtoOAApplicationAuthRes):
                req = ProtoOAAccountAuthReq()
                req.ctidTraderAccountId = account_id
                req.accessToken = access_token
                d = client.send(req)
                if d is not None and hasattr(d, "addErrback"):
                    d.addErrback(lambda f: None)
            elif isinstance(payload, ProtoOAAccountAuthRes):
                req = ProtoOATraderReq()
                req.ctidTraderAccountId = account_id
                d = client.send(req)
                if d is not None and hasattr(d, "addErrback"):
                    d.addErrback(lambda f: None)
            elif isinstance(payload, ProtoOATraderRes):
                state["trader"] = payload.trader
                req = ProtoOASymbolByIdReq()
                req.ctidTraderAccountId = account_id
                req.symbolId.append(symbol_id)
                d = client.send(req)
                if d is not None and hasattr(d, "addErrback"):
                    d.addErrback(lambda f: None)
            elif isinstance(payload, ProtoOASymbolByIdRes):
                sym = payload.symbol[0] if payload.symbol else None
                _finish(value={"trader": state["trader"], "symbol": sym})

        def on_disconnected(c, reason):
            _finish(error=RuntimeError(f"연결 종료: {reason}"))

        client.setConnectedCallback(on_connected)
        client.setDisconnectedCallback(on_disconnected)
        client.setMessageReceivedCallback(on_message)
        try:
            d = client.startService()
            if d is not None and hasattr(d, "addErrback"):
                d.addErrback(lambda f: None)
        except Exception as exc:
            _finish(error=exc)

    if getattr(reactor, "running", False):
        reactor.callFromThread(_build)
    else:
        reactor.callWhenRunning(_build)

    return fut.result(timeout=timeout)


def _balance(info: dict) -> float:
    t = info["trader"]
    digits = t.moneyDigits or 2
    return t.balance / (10 ** digits)


async def main() -> None:
    from common.ctrader_account_loader import get_enabled_accounts
    from common.ctrader_executor import get_executor
    from common.ctrader_token_store import get_tokens

    client_id = os.environ.get("CTRADER_CLIENT_ID", "").strip()
    client_secret = os.environ.get("CTRADER_CLIENT_SECRET", "").strip()
    access_token, _ = get_tokens()
    if not access_token:
        access_token = os.environ.get("CTRADER_ACCESS_TOKEN", "").strip()

    accounts = get_enabled_accounts()
    print(f"[test] enabled 계좌 {len(accounts)}개: {list(accounts.keys())}")

    loop = asyncio.get_event_loop()

    for firm, a in accounts.items():
        print(f"\n{'='*70}")
        print(f"[test] === {firm} (account={a['account_id']} env={a['env']} "
              f"symbol={a['symbol_id']} lot={a.get('lot_size')}) ===")

        try:
            info_before = await loop.run_in_executor(
                None, _fetch_trader_and_symbol,
                a["account_id"], a["symbol_id"], a["env"] == "live",
                client_id, client_secret, access_token,
            )
        except Exception as e:
            print(f"[test] {firm}: 거래 전 잔고 조회 실패 — {e}")
            continue

        bal_before = _balance(info_before)
        sym = info_before.get("symbol")
        print(
            f"[test] {firm}: 거래전 잔고=${bal_before:,.2f}  "
            f"symbol.lotSize={getattr(sym, 'lotSize', '?')} "
            f"minVolume={getattr(sym, 'minVolume', '?')} "
            f"commission={getattr(sym, 'commission', '?')} "
            f"commissionType={getattr(sym, 'commissionType', '?')}"
        )

        ex = get_executor(
            account_id=a["account_id"], env=a["env"], symbol_id=a["symbol_id"],
            lot_size=a.get("lot_size"), units_per_lot=a.get("units_per_lot"),
        )
        if ex is None:
            print(f"[test] {firm}: executor 생성 실패 (로컬 차단? CTRADER_ALLOW_LOCAL=1 확인)")
            continue

        print(f"[test] {firm}: BUY 진입 시도 (lot={a.get('lot_size')})...")
        open_result = await ex.open_position(symbol="", side="long")
        print(f"[test] {firm}: open_position 결과 = {open_result}")

        if not open_result:
            print(f"[test] {firm}: ❌ 진입 실패 — 이 계좌는 여기서 중단 (원인은 위 [cTrader] 로그 참고)")
            continue

        print(f"[test] {firm}: 3초 대기 후 청산...")
        await asyncio.sleep(3)

        close_result = await ex.close_position(symbol="", side="long")
        print(f"[test] {firm}: close_position 결과 = {close_result}")
        if not close_result:
            print(f"[test] {firm}: ⚠️ 청산 응답 없음 — 포지션이 열려있을 수 있으니 브로커 플랫폼에서 직접 확인 필요")

        await asyncio.sleep(1)
        try:
            info_after = await loop.run_in_executor(
                None, _fetch_trader_and_symbol,
                a["account_id"], a["symbol_id"], a["env"] == "live",
                client_id, client_secret, access_token,
            )
            bal_after = _balance(info_after)
            print(
                f"[test] {firm}: 거래후 잔고=${bal_after:,.2f}  "
                f"왕복비용(스프레드+수수료)=${bal_before - bal_after:+.4f}"
            )
        except Exception as e:
            print(f"[test] {firm}: 거래 후 잔고 조회 실패 — {e}")

    print(f"\n{'='*70}")
    print("[test] 전체 종료")


asyncio.run(main())
