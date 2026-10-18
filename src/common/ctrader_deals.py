import asyncio
import concurrent.futures
import os
import time

MIN_GROSS_PROFIT_USD = 0.10
TOLERANCE_USD = 0.01
DEAL_WINDOW_MS = 7 * 86_400_000


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


def verify_round_trip(deals: list, position_id: int, units_per_lot: int) -> dict:
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATradeSide

    position_deals = [d for d in deals if d.positionId == position_id]
    open_deals = [d for d in position_deals if not d.HasField("closePositionDetail")]
    close_deals = [d for d in position_deals if d.HasField("closePositionDetail")]
    if len(open_deals) != 1 or len(close_deals) != 1:
        raise RuntimeError(
            f"positionId={position_id} deal 수 이상 — 진입 {len(open_deals)}개, 청산 {len(close_deals)}개"
        )
    open_deal = open_deals[0]
    close_deal = close_deals[0]
    detail = close_deal.closePositionDetail
    digits = 10 ** detail.moneyDigits

    if abs(detail.grossProfit) < round(MIN_GROSS_PROFIT_USD * digits):
        raise RuntimeError(f"가격 변동 부족 — 판정 불가 (grossProfit={detail.grossProfit / digits})")

    direction = 1 if open_deal.tradeSide == ProtoOATradeSide.BUY else -1
    price_move = (close_deal.executionPrice - open_deal.executionPrice) * direction
    gross_profit = detail.grossProfit / digits
    expected = price_move * detail.closedVolume / units_per_lot
    match = abs(round(expected * digits) - detail.grossProfit) <= round(TOLERANCE_USD * digits)
    measured = detail.closedVolume / (gross_profit / price_move)
    commission = sum(d.commission / 10 ** d.moneyDigits for d in position_deals)

    result = {
        "position_id": position_id,
        "entry_price": open_deal.executionPrice,
        "close_price": close_deal.executionPrice,
        "closed_volume": detail.closedVolume,
        "units_per_lot": units_per_lot,
        "gross_profit": gross_profit,
        "expected_gross_profit": expected,
        "units_per_lot_measured": measured,
        "match": match,
        "commission": commission,
        "swap": detail.swap / digits,
    }
    return result


async def verify_position(account_id: int, env: str, symbol_id: int, units_per_lot: int, position_id: int) -> dict:
    from common.ctrader_token_store import get_tokens

    client_id = os.environ["CTRADER_CLIENT_ID"].strip()
    client_secret = os.environ["CTRADER_CLIENT_SECRET"].strip()
    access_token, _ = get_tokens()
    if not access_token:
        raise RuntimeError("DB 에 cTrader access token 없음")

    to_ms = int(time.time() * 1000)
    from_ms = to_ms - DEAL_WINDOW_MS
    loop = asyncio.get_event_loop()
    fetched = await loop.run_in_executor(
        None, _fetch_symbol_and_deals,
        account_id, symbol_id, env == "live",
        client_id, client_secret, access_token,
        from_ms, to_ms,
    )
    if fetched["hasMore"]:
        raise RuntimeError("deal 목록 hasMore=True — 기간 안의 deal 을 전부 읽지 못함")
    result = verify_round_trip(fetched["deals"], position_id, units_per_lot)
    return result
