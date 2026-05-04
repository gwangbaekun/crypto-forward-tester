"""
cTrader 계좌 잔고 + 레버리지 + 최대 포지션 크기 조회.

python scripts/ctrader_account_info.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _load_env(path: pathlib.Path) -> dict:
    env: dict = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def run() -> None:
    from twisted.internet import reactor

    from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAAccountAuthReq,
        ProtoOAApplicationAuthReq,
        ProtoOASymbolByIdReq,
        ProtoOATraderReq,
    )
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAAccountAuthRes,
        ProtoOAApplicationAuthRes,
        ProtoOAErrorRes,
        ProtoOASymbolByIdRes,
        ProtoOATraderRes,
    )

    env        = _load_env(ROOT / ".env")
    client_id  = env.get("CTRADER_CLIENT_ID", "")
    secret     = env.get("CTRADER_CLIENT_SECRET", "")
    token      = env.get("CTRADER_ACCESS_TOKEN", "")
    account_id = int(env.get("CTRADER_ACCOUNT_ID", "0") or "0")
    sym_id     = int(env.get("CTRADER_SYMBOL_ID", "0") or "0")
    is_live    = env.get("CTRADER_ENV", "demo") == "live"

    host = EndPoints.PROTOBUF_LIVE_HOST if is_live else EndPoints.PROTOBUF_DEMO_HOST
    client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)
    state: dict = {}

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
            req = ProtoOAAccountAuthReq()
            req.ctidTraderAccountId = account_id
            req.accessToken         = token
            client.send(req)

        elif isinstance(payload, ProtoOAAccountAuthRes):
            req = ProtoOATraderReq()
            req.ctidTraderAccountId = account_id
            client.send(req)

        elif isinstance(payload, ProtoOATraderRes):
            # 트레이더 정보 저장 후 심볼 상세 조회
            state["trader"] = payload.trader
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASymbolByIdReq
            req = ProtoOASymbolByIdReq()
            req.ctidTraderAccountId = account_id
            req.symbolId.append(sym_id)
            client.send(req)

        elif isinstance(payload, ProtoOASymbolByIdRes):
            from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOACommissionType
            t         = state["trader"]
            sym       = payload.symbol[0] if payload.symbol else None

            digits    = t.moneyDigits or 2
            divisor   = 10 ** digits
            balance   = t.balance / divisor
            leverage  = (t.leverageInCents or 0) / 100
            lot_size  = float(env.get("CTRADER_LOT_SIZE", "0.01") or "0.01")
            eth_price = 2500.0  # 근사값

            # 수수료 계산
            commission_raw  = (sym.commission or 0) if sym else 0
            commission_type = (sym.commissionType if sym else 0)
            lot_size_units  = (sym.lotSize or 100) if sym else 100  # 1 lot = 몇 단위

            # commissionType: 0=USD_PER_MILLION_USD, 1=USD_PER_LOT, 2=PERCENTAGE, 3=QUOTE_CCY_PER_LOT
            if commission_type == 1:  # per lot (양방향 × 2)
                commission_per_trade = (commission_raw / 100) * lot_size * 2
            elif commission_type == 3:  # quote ccy per lot
                commission_per_trade = (commission_raw / 100) * lot_size * 2
            elif commission_type == 0:  # per million USD notional
                notional = lot_size * lot_size_units * eth_price
                commission_per_trade = (commission_raw / 1_000_000) * notional * 2
            elif commission_type == 2:  # percentage
                notional = lot_size * lot_size_units * eth_price
                commission_per_trade = notional * (commission_raw / 100 / 100) * 2
            else:
                commission_per_trade = 0

            notional_per_trade = lot_size * lot_size_units * eth_price
            margin_per_trade   = notional_per_trade / leverage if leverage else notional_per_trade

            # 전략 파라미터
            risk_pct   = 1.0
            sl_pct     = 0.5
            rr         = 3.5
            risk_usd   = balance * risk_pct / 100
            sl_usd     = eth_price * sl_pct / 100
            opt_lot    = risk_usd / (sl_usd * lot_size_units)
            win_gross  = opt_lot * lot_size_units * sl_usd * rr
            loss_gross = risk_usd
            win_net    = win_gross - commission_per_trade
            loss_net   = loss_gross + commission_per_trade
            real_rr    = win_net / loss_net

            print(f"\n{'━'*54}")
            print(f"  계좌 정보  (accountId={account_id})")
            print(f"{'━'*54}")
            print(f"  잔고          : ${balance:,.2f}")
            print(f"  레버리지      : 1:{leverage:.0f}")
            print(f"  brokerName    : {t.brokerName}")
            print(f"  traderLogin   : {t.traderLogin}")
            print(f"{'━'*54}")
            print(f"\n  수수료 (commissionType={commission_type} raw={commission_raw}):")
            print(f"  거래당 수수료  : ${commission_per_trade:.4f}  (진입+청산)")
            print(f"{'━'*54}")
            print(f"\n  최적 LOT SIZE (risk {risk_pct}%, SL {sl_pct}%, ETH≈${eth_price:,.0f}):")
            print(f"  권장 lot       : {opt_lot:.4f} lot  ({opt_lot * lot_size_units:.1f} ETH)")
            print(f"  노셔널         : ${opt_lot * lot_size_units * eth_price:,.2f}")
            print(f"  증거금         : ${opt_lot * lot_size_units * eth_price / leverage:,.2f}")
            print(f"{'━'*54}")
            print(f"\n  수수료 반영 손익 (lot={opt_lot:.4f}):")
            print(f"  1승 (RR {rr}) : +${win_net:,.2f}  (수수료 ${commission_per_trade:.2f} 차감)")
            print(f"  1패          : -${loss_net:,.2f}  (수수료 ${commission_per_trade:.2f} 포함)")
            print(f"  실질 RR      : {real_rr:.2f}")
            print(f"{'━'*54}\n")
            stop()

    client.setConnectedCallback(on_connected)
    client.setDisconnectedCallback(lambda c, r: stop())
    client.setMessageReceivedCallback(on_message)
    client.startService()
    reactor.run()


if __name__ == "__main__":
    run()
