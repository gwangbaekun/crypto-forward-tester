import concurrent.futures
import os
import sys
import time

sys.path.insert(0, "/app/src")


def _fetch_symbol_and_deals(account_id: int, symbol_id: int, is_live: bool,
                            client_id: str, client_secret: str, access_token: str,
                            from_ms: int, to_ms: int, timeout: float = 15.0) -> dict:
    from common.ctrader_executor import _ensure_reactor
    from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAAccountAuthReq,
        ProtoOAAccountAuthRes,
        ProtoOAApplicationAuthReq,
        ProtoOAApplicationAuthRes,
        ProtoOADealListReq,
        ProtoOADealListRes,
        ProtoOAErrorRes,
        ProtoOASymbolByIdReq,
        ProtoOASymbolByIdRes,
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
                req = ProtoOASymbolByIdReq()
                req.ctidTraderAccountId = account_id
                req.symbolId.append(symbol_id)
                d = client.send(req)
                if d is not None and hasattr(d, "addErrback"):
                    d.addErrback(lambda f: None)
            elif isinstance(payload, ProtoOASymbolByIdRes):
                if not payload.symbol:
                    _finish(error=RuntimeError(f"symbol_id={symbol_id} 응답 없음"))
                    return
                state["symbol"] = payload.symbol[0]
                req = ProtoOADealListReq()
                req.ctidTraderAccountId = account_id
                req.fromTimestamp = from_ms
                req.toTimestamp = to_ms
                d = client.send(req)
                if d is not None and hasattr(d, "addErrback"):
                    d.addErrback(lambda f: None)
            elif isinstance(payload, ProtoOADealListRes):
                _finish(value={"symbol": state["symbol"], "deals": list(payload.deal), "hasMore": payload.hasMore})

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


def main() -> None:
    from common.ctrader_token_store import get_tokens
    from features.strategy.common.config_loader import get_ctrader_config

    strategy = sys.argv[1]
    days = int(sys.argv[2])
    cfg = get_ctrader_config(strategy)
    account_id = cfg["ctrader_account_id"]
    env = cfg["ctrader_env"]
    symbol_id = cfg["ctrader_symbol_id"]
    units_per_lot = cfg["ctrader_units_per_lot"]

    client_id = os.environ["CTRADER_CLIENT_ID"].strip()
    client_secret = os.environ["CTRADER_CLIENT_SECRET"].strip()
    access_token, _ = get_tokens()
    if not access_token:
        raise RuntimeError("DB 에 cTrader access token 없음")

    to_ms = int(time.time() * 1000)
    from_ms = to_ms - days * 86_400_000

    result = _fetch_symbol_and_deals(
        account_id, symbol_id, env == "live",
        client_id, client_secret, access_token,
        from_ms, to_ms,
    )
    sym = result["symbol"]
    print(f"[deals] strategy={strategy} account={account_id} env={env} symbol_id={symbol_id} units_per_lot(yaml)={units_per_lot}")
    print(
        f"[deals] lotSize={sym.lotSize} minVolume={sym.minVolume} "
        f"stepVolume={sym.stepVolume} maxVolume={sym.maxVolume}"
    )
    print(f"[deals] count={len(result['deals'])} hasMore={result['hasMore']}")
    for deal in result["deals"]:
        print("----")
        print(deal)


main()
