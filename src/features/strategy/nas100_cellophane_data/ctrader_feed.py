from __future__ import annotations

import concurrent.futures
import os

BAR = 300
CHUNK = 86400
PRICE_SCALE = 1e5
TOKEN_ERRORS = {"CH_ACCESS_TOKEN_INVALID", "OA_AUTH_TOKEN_EXPIRED", "ACCESS_DENIED"}


class TokenError(RuntimeError):
    pass


def chunks(from_s, to_s):
    out = [(a, min(a + CHUNK, to_s)) for a in range(from_s, to_s, CHUNK)]
    return out


def decode(bar):
    low = bar.low / PRICE_SCALE
    out = (bar.utcTimestampInMinutes * 60, low + bar.deltaOpen / PRICE_SCALE, low + bar.deltaHigh / PRICE_SCALE, low,
           low + bar.deltaClose / PRICE_SCALE)
    return out


def fetch(account_id, is_live, symbol_name, from_s, to_s, access_token, timeout):
    from common.ctrader_executor import _ensure_reactor
    from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAAccountAuthReq, ProtoOAAccountAuthRes, ProtoOAApplicationAuthReq, ProtoOAApplicationAuthRes,
        ProtoOAErrorRes, ProtoOAGetTrendbarsReq, ProtoOAGetTrendbarsRes, ProtoOASymbolsListReq, ProtoOASymbolsListRes)
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrendbarPeriod

    reactor = _ensure_reactor()
    fut = concurrent.futures.Future()
    todo = chunks(from_s, to_s)
    state = {"symbol_id": None, "bars": {}}

    def build():
        host = EndPoints.PROTOBUF_LIVE_HOST if is_live else EndPoints.PROTOBUF_DEMO_HOST
        client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)

        def finish(value=None, error=None):
            if not fut.done():
                fut.set_exception(error) if error is not None else fut.set_result(value)
            client.stopService()

        def send(req):
            d = client.send(req)
            d.addErrback(lambda f: finish(error=RuntimeError(f"요청 실패: {f}")))

        def next_chunk():
            if not todo:
                finish(value=[state["bars"][t] for t in sorted(state["bars"])])
                return
            a, b = todo.pop(0)
            req = ProtoOAGetTrendbarsReq(ctidTraderAccountId=account_id, symbolId=state["symbol_id"],
                                         period=ProtoOATrendbarPeriod.Value("M5"), fromTimestamp=a * 1000,
                                         toTimestamp=b * 1000)
            send(req)

        def on_connected(c):
            send(ProtoOAApplicationAuthReq(clientId=os.environ["CTRADER_CLIENT_ID"],
                                           clientSecret=os.environ["CTRADER_CLIENT_SECRET"]))

        def on_message(c, message):
            payload = Protobuf.extract(message)
            if isinstance(payload, ProtoOAErrorRes):
                kind = TokenError if payload.errorCode in TOKEN_ERRORS else RuntimeError
                finish(error=kind(f"{payload.errorCode}: {payload.description}"))
            elif isinstance(payload, ProtoOAApplicationAuthRes):
                send(ProtoOAAccountAuthReq(ctidTraderAccountId=account_id, accessToken=access_token))
            elif isinstance(payload, ProtoOAAccountAuthRes):
                send(ProtoOASymbolsListReq(ctidTraderAccountId=account_id))
            elif isinstance(payload, ProtoOASymbolsListRes):
                hits = [s.symbolId for s in payload.symbol if s.symbolName == symbol_name]
                if len(hits) != 1:
                    near = sorted(s.symbolName for s in payload.symbol if "100" in s.symbolName)
                    finish(error=ValueError(f"{symbol_name} 가 계좌 {account_id} 에 {len(hits)}개 있다. 비슷한 이름: {near}"))
                    return
                state["symbol_id"] = hits[0]
                next_chunk()
            elif isinstance(payload, ProtoOAGetTrendbarsRes):
                for bar in payload.trendbar:
                    row = decode(bar)
                    state["bars"][row[0]] = row
                next_chunk()

        def on_disconnected(c, reason):
            finish(error=RuntimeError(f"연결 종료: {reason}"))

        client.setConnectedCallback(on_connected)
        client.setDisconnectedCallback(on_disconnected)
        client.setMessageReceivedCallback(on_message)
        client.startService()

    reactor.callFromThread(build) if getattr(reactor, "running", False) else reactor.callWhenRunning(build)
    out = fut.result(timeout=timeout)
    return out


def bars(account_id, is_live, symbol_name, from_s, to_s, timeout=60.0):
    from common.ctrader_executor import refresh_shared_tokens
    from common.ctrader_token_store import get_tokens

    if from_s % BAR or to_s % BAR:
        raise ValueError(f"구간 {from_s} ~ {to_s} 가 5분 경계가 아니다")
    access_token, _ = get_tokens()
    if not access_token:
        raise TokenError("DB 에 access token 이 없다")
    try:
        out = fetch(account_id, is_live, symbol_name, from_s, to_s, access_token, timeout)
    except TokenError:
        access_token = refresh_shared_tokens(os.environ["CTRADER_CLIENT_ID"], os.environ["CTRADER_CLIENT_SECRET"])
        out = fetch(account_id, is_live, symbol_name, from_s, to_s, access_token, timeout)
    return out
