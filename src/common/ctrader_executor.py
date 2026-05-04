"""
CTrader Executor — FTMO/Open API 실제 구현.

ctrader-open-api 는 Twisted 기반이지만 FastAPI(asyncio) 환경에서 사용해야 하므로
Twisted reactor 를 별도 스레드에서 구동하고,
asyncio ↔ Twisted 간 브릿지는 concurrent.futures 로 처리한다.

환경변수:
    CTRADER_CLIENT_ID
    CTRADER_CLIENT_SECRET
    CTRADER_ACCESS_TOKEN
    CTRADER_ACCOUNT_ID       — ctidTraderAccountId (숫자)
    CTRADER_ENV              "demo" | "live"  (기본 demo)
    CTRADER_SYMBOL_ID        — cTrader 심볼 ID (숫자)
    CTRADER_LOT_SIZE         — 주문 볼륨 lots (기본 0.01)
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import os
import threading
import time
from typing import Any, Dict, Optional

_CENTILOTS_PER_LOT = 100_000


def _lots_to_volume(lots: float) -> int:
    return max(1_000, int(lots * _CENTILOTS_PER_LOT))


class CTraderExecutor:
    """
    cTrader Open API 주문 실행기.

    Twisted reactor 를 데몬 스레드에서 구동.
    FastAPI 쪽에서는 await executor.open_position(...) 형태로 호출.
    내부적으로 스레드 간 Future 로 응답을 동기화.
    """

    def __init__(self) -> None:
        self._client_id     = os.environ.get("CTRADER_CLIENT_ID", "").strip()
        self._client_secret = os.environ.get("CTRADER_CLIENT_SECRET", "").strip()
        self._access_token  = os.environ.get("CTRADER_ACCESS_TOKEN", "").strip()
        self._account_id    = int(os.environ.get("CTRADER_ACCOUNT_ID", "0") or "0")
        self._env           = os.environ.get("CTRADER_ENV", "demo").strip().lower()
        self._symbol_id     = int(os.environ.get("CTRADER_SYMBOL_ID", "0") or "0")
        self._lot_size      = float(os.environ.get("CTRADER_LOT_SIZE", "0.01") or "0.01")
        self._is_live       = self._env == "live"

        self._client        = None
        self._reactor       = None
        self._ready_event   = threading.Event()
        self._authed        = False
        self._lock          = threading.Lock()
        self._pending: Optional[concurrent.futures.Future] = None
        self._open_position_id: Optional[int] = None

        self._start_reactor_thread()

    def _ready(self) -> bool:
        return bool(
            self._client_id and self._client_secret
            and self._access_token and self._account_id and self._symbol_id
        )

    # ── Twisted reactor 스레드 ──────────────────────────────────────────────

    def _start_reactor_thread(self) -> None:
        t = threading.Thread(target=self._run_reactor, daemon=True, name="ctrader-reactor")
        t.start()

    def _run_reactor(self) -> None:
        from twisted.internet import reactor

        from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOAAccountAuthReq,
            ProtoOAApplicationAuthReq,
        )
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOAAccountAuthRes,
            ProtoOAApplicationAuthRes,
            ProtoOAErrorRes,
            ProtoOAExecutionEvent,
        )

        self._reactor = reactor

        host = EndPoints.PROTOBUF_LIVE_HOST if self._is_live else EndPoints.PROTOBUF_DEMO_HOST
        port = EndPoints.PROTOBUF_PORT

        client = Client(host, port, TcpProtocol)
        self._client = client

        def on_connected(c):
            req = ProtoOAApplicationAuthReq()
            req.clientId     = self._client_id
            req.clientSecret = self._client_secret
            client.send(req)

        def on_disconnected(c, reason):
            print(f"[cTrader] 연결 종료: {reason}")
            self._authed = False
            self._ready_event.clear()
            # 3초 후 재연결
            reactor.callLater(3.0, client.startService)

        def on_message(c, message):
            payload = Protobuf.extract(message)

            if isinstance(payload, ProtoOAErrorRes):
                print(f"[cTrader] ❌ 에러: {payload.errorCode} — {payload.description}")
                self._resolve_pending(None)
                return

            if isinstance(payload, ProtoOAApplicationAuthRes):
                req = ProtoOAAccountAuthReq()
                req.ctidTraderAccountId = self._account_id
                req.accessToken         = self._access_token
                client.send(req)
                return

            if isinstance(payload, ProtoOAAccountAuthRes):
                self._authed = True
                self._ready_event.set()
                print(f"[cTrader] ✅ 인증 완료 (account={self._account_id} env={self._env})")
                return

            if isinstance(payload, ProtoOAExecutionEvent):
                self._on_execution(payload)
                return

        client.setConnectedCallback(on_connected)
        client.setDisconnectedCallback(on_disconnected)
        client.setMessageReceivedCallback(on_message)
        client.startService()
        reactor.run(installSignalHandlers=False)

    def _on_execution(self, payload: Any) -> None:
        from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOAExecutionType

        exec_type = payload.executionType
        order     = payload.order if payload.HasField("order") else None
        position  = payload.position if payload.HasField("position") else None

        if exec_type in (
            ProtoOAExecutionType.ORDER_FILLED,
            ProtoOAExecutionType.ORDER_PARTIAL_FILL,
        ):
            fill_price = float(getattr(order, "executionPrice", 0) or 0)
            pos_id     = getattr(position, "positionId", None)
            if pos_id:
                self._open_position_id = pos_id
            print(f"[cTrader] ✅ 체결 — fill={fill_price:.4f} positionId={pos_id}")
            self._resolve_pending({"avgPrice": fill_price, "positionId": pos_id})

        elif exec_type == ProtoOAExecutionType.SWAP:
            pass  # 무시

        else:
            self._resolve_pending({"executionType": exec_type})

    def _resolve_pending(self, result: Any) -> None:
        with self._lock:
            f = self._pending
            self._pending = None
        if f and not f.done():
            f.set_result(result)

    def _send_and_wait(self, req: Any, timeout: float = 10.0) -> Optional[Dict]:
        """Twisted 스레드로 메시지 전송 + 응답을 Future 로 동기 수신."""
        if not self._ready_event.wait(timeout=timeout):
            print("[cTrader] 연결 대기 타임아웃")
            return None

        loop = concurrent.futures.Future()
        with self._lock:
            self._pending = loop

        self._reactor.callFromThread(self._client.send, req)

        try:
            return loop.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            print("[cTrader] 응답 타임아웃")
            with self._lock:
                self._pending = None
            return None

    # ── asyncio 공개 API ────────────────────────────────────────────────────

    async def _run_in_executor(self, fn, *args):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, fn, *args)

    async def open_position(
        self,
        symbol: str,
        side: str,
        current_price: float = 0,
        leverage: Optional[int] = None,
    ) -> Optional[Dict]:
        if not self._ready():
            return None

        def _send():
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOANewOrderReq
            from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
                ProtoOAOrderType,
                ProtoOATradeSide,
            )
            req = ProtoOANewOrderReq()
            req.ctidTraderAccountId = self._account_id
            req.symbolId            = self._symbol_id
            req.orderType           = ProtoOAOrderType.MARKET
            req.tradeSide           = ProtoOATradeSide.BUY if side == "long" else ProtoOATradeSide.SELL
            req.volume              = _lots_to_volume(self._lot_size)
            return self._send_and_wait(req)

        result = await self._run_in_executor(_send)
        if result:
            print(f"[cTrader] ✅ 진입 — side={side} lots={self._lot_size} fill={result.get('avgPrice', 0):.4f}")
        return result

    async def close_position(
        self,
        symbol: str,
        side: str,
    ) -> Optional[Dict]:
        if not self._ready():
            return None
        if not self._open_position_id:
            print("[cTrader] close_position — positionId 없음, 스킵")
            return None

        def _send():
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAClosePositionReq
            req = ProtoOAClosePositionReq()
            req.ctidTraderAccountId = self._account_id
            req.positionId          = self._open_position_id
            req.volume              = _lots_to_volume(self._lot_size)
            return self._send_and_wait(req)

        result = await self._run_in_executor(_send)
        if result:
            self._open_position_id = None
            print(f"[cTrader] ✅ 청산 — fill={result.get('avgPrice', 0):.4f}")
        return result

    async def place_tp_sl(
        self,
        symbol: str,
        side: str,
        tp: Optional[float] = None,
        sl: Optional[float] = None,
    ) -> None:
        if not self._ready() or not (tp or sl) or not self._open_position_id:
            return

        def _send():
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAAmendPositionSLTPReq
            req = ProtoOAAmendPositionSLTPReq()
            req.ctidTraderAccountId = self._account_id
            req.positionId          = self._open_position_id
            if tp:
                req.takeProfit = tp
            if sl:
                req.stopLoss = sl
            return self._send_and_wait(req)

        await self._run_in_executor(_send)
        print(f"[cTrader] ✅ TP/SL — positionId={self._open_position_id} tp={tp} sl={sl}")

    async def get_position(self, symbol: str) -> Optional[Dict]:
        if not self._ready():
            return None

        def _send():
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAReconcileReq
            req = ProtoOAReconcileReq()
            req.ctidTraderAccountId = self._account_id
            return self._send_and_wait(req)

        return await self._run_in_executor(_send)

    async def cancel_tp_sl(self, symbol: str) -> None:
        pass


# ── 싱글톤 ──────────────────────────────────────────────────────────────────

_executor: Optional[CTraderExecutor] = None


def get_executor() -> Optional[CTraderExecutor]:
    global _executor
    if _executor is None:
        token      = os.environ.get("CTRADER_ACCESS_TOKEN", "").strip()
        account_id = os.environ.get("CTRADER_ACCOUNT_ID", "").strip()
        symbol_id  = os.environ.get("CTRADER_SYMBOL_ID", "").strip()
        if not token or not account_id or not symbol_id:
            return None
        _executor = CTraderExecutor()
    return _executor
