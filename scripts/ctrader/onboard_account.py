import asyncio
import concurrent.futures
import os
import sys

sys.path.insert(0, "/app/src")


def _fetch_trader_and_symbol(account_id: int, symbol_id: int, is_live: bool,
                              client_id: str, client_secret: str, access_token: str,
                              timeout: float = 15.0) -> dict:
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
    balance = t.balance / (10 ** t.moneyDigits)
    return balance


async def _open_positions(ex) -> list:
    result = await ex.get_position(symbol="", cache_ttl=0.0)
    if result is None:
        raise RuntimeError("오픈 포지션 조회 실패")
    positions = result["positions"]
    return positions


async def main() -> None:
    from common.ctrader_executor import get_executor, get_executor_unavailable_reason
    from common.ctrader_token_store import get_tokens
    from features.ctrader.router import _market_price
    from features.strategy.common.config_loader import get_ctrader_config, get_master_config

    strategy = sys.argv[1]
    cfg = get_ctrader_config(strategy)
    symbol = get_master_config()[strategy]["symbol"]
    account_id = cfg["ctrader_account_id"]
    env = cfg["ctrader_env"]
    symbol_id = cfg["ctrader_symbol_id"]
    units_per_lot = cfg["ctrader_units_per_lot"]
    notional = cfg["ctrader_notional_usd"]
    print(f"[onboard] strategy={strategy} account={account_id} env={env} symbol_id={symbol_id} notional={notional}")

    client_id = os.environ["CTRADER_CLIENT_ID"].strip()
    client_secret = os.environ["CTRADER_CLIENT_SECRET"].strip()
    access_token, _ = get_tokens()
    if not access_token:
        raise RuntimeError("DB 에 cTrader access token 없음")

    reason = get_executor_unavailable_reason(account_id=account_id, symbol_id=symbol_id)
    if reason:
        raise RuntimeError(reason)
    ex = get_executor(
        account_id=account_id, env=env, symbol_id=symbol_id,
        units_per_lot=units_per_lot, notional_usd=notional,
    )

    before = await _open_positions(ex)
    if before:
        raise RuntimeError(f"진입 전 오픈 포지션 존재: {before}")

    loop = asyncio.get_event_loop()
    info_before = await loop.run_in_executor(
        None, _fetch_trader_and_symbol,
        account_id, symbol_id, env == "live",
        client_id, client_secret, access_token,
    )
    bal_before = _balance(info_before)
    sym = info_before["symbol"]
    print(
        f"[onboard] 잔고=${bal_before:,.2f} lotSize={sym.lotSize} minVolume={sym.minVolume} "
        f"stepVolume={sym.stepVolume} maxVolume={sym.maxVolume} "
        f"commission={sym.commission} commissionType={sym.commissionType} "
        f"swapLong={sym.swapLong} swapShort={sym.swapShort} swapCalculationType={sym.swapCalculationType}"
    )

    price = await _market_price(symbol)
    if price <= 0:
        raise RuntimeError(f"{symbol} 현재가 조회 실패")
    volume = int(notional / price * units_per_lot)
    print(f"[onboard] 사이징: notional={notional} price={price} volume={volume} lots={volume / sym.lotSize}")
    if volume < sym.minVolume or volume > sym.maxVolume:
        raise RuntimeError(f"volume={volume} 가 minVolume={sym.minVolume}~maxVolume={sym.maxVolume} 밖")

    open_result = await ex.open_position(symbol="", side="long", volume=sym.minVolume)
    print(f"[onboard] open_position 결과 = {open_result}")
    await asyncio.sleep(3)

    opened = await _open_positions(ex)
    if len(opened) != 1:
        raise RuntimeError(f"진입 후 오픈 포지션이 1개가 아님: {opened}")
    position = opened[0]
    print(f"[onboard] 진입 확인: {position}")

    close_result = await ex.close_position_by_id(position["positionId"], volume=position["volume"])
    print(f"[onboard] close_position_by_id 결과 = {close_result}")
    await asyncio.sleep(3)

    after = await _open_positions(ex)
    if after:
        raise RuntimeError(f"청산 후 오픈 포지션 남음: {after}")

    info_after = await loop.run_in_executor(
        None, _fetch_trader_and_symbol,
        account_id, symbol_id, env == "live",
        client_id, client_secret, access_token,
    )
    bal_after = _balance(info_after)
    print(f"[onboard] 잔고 전=${bal_before:,.2f} 후=${bal_after:,.2f} 차이=${bal_after - bal_before:+.2f}")


asyncio.run(main())
