"""
CTrader Executor — FTMO/Open API 실제 구현.

Twisted reactor 는 프로세스 전체에서 하나만 실행 가능하므로
모듈 레벨 단일 스레드에서 구동하고, 각 CTraderExecutor 는
reactor 위에 자신의 Client 를 붙이는 방식으로 동작한다.

환경변수 (전략별 yaml 오버라이드가 없을 때 기본값):
    CTRADER_CLIENT_ID
    CTRADER_CLIENT_SECRET
    CTRADER_ACCESS_TOKEN
    CTRADER_ACCOUNT_ID
    CTRADER_ENV              "demo" | "live"  (기본 demo)
    CTRADER_SYMBOL_ID
    CTRADER_LOT_SIZE         (기본 0.01)
    CTRADER_FORCE_DEMO       "true" 이면 모든 executor 비활성 (로컬 개발용)
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import os
import threading
from typing import Any, Dict, Optional
import httpx
from twisted.python.failure import Failure
from common.ctrader_token_store import get_tokens, save_tokens

_CENTILOTS_PER_LOT = 100_000
_CTRADER_TOKEN_URL = "https://openapi.ctrader.com/apps/token"


def _lots_to_volume(lots: float) -> int:
    return max(1_000, int(lots * _CENTILOTS_PER_LOT))


# ── 공유 Twisted reactor (프로세스당 1개) ────────────────────────────────────

_reactor_lock    = threading.Lock()
_reactor_started = False


def _ensure_reactor() -> Any:
    global _reactor_started
    from twisted.internet import reactor
    with _reactor_lock:
        if not _reactor_started:
            _reactor_started = True
            t = threading.Thread(
                target=lambda: reactor.run(installSignalHandlers=False),
                daemon=True,
                name="ctrader-reactor",
            )
            t.start()
    return reactor


# ── Executor ─────────────────────────────────────────────────────────────────

class CTraderExecutor:
    def __init__(
        self,
        account_id: Optional[int] = None,
        env: Optional[str] = None,
        symbol_id: Optional[int] = None,
        lot_size: Optional[float] = None,
    ) -> None:
        self._client_id     = os.environ.get("CTRADER_CLIENT_ID", "").strip()
        self._client_secret = os.environ.get("CTRADER_CLIENT_SECRET", "").strip()
        env_access_token = os.environ.get("CTRADER_ACCESS_TOKEN", "").strip()
        env_refresh_token = os.environ.get("CTRADER_REFRESH_TOKEN", "").strip()
        db_access_token, db_refresh_token = get_tokens()
        self._access_token  = env_access_token or db_access_token
        self._refresh_token = env_refresh_token or db_refresh_token
        self._account_id    = account_id or int(os.environ.get("CTRADER_ACCOUNT_ID", "0") or "0")
        self._env           = (env or os.environ.get("CTRADER_ENV", "demo")).strip().lower()
        self._symbol_id     = symbol_id or int(os.environ.get("CTRADER_SYMBOL_ID", "0") or "0")
        self._lot_size      = lot_size or float(os.environ.get("CTRADER_LOT_SIZE", "0.01") or "0.01")
        self._is_live       = self._env == "live"

        self._client: Any                              = None
        self._reactor: Any                             = None
        self._ready_event                              = threading.Event()
        self._authed                                   = False
        self._lock                                     = threading.Lock()
        self._pending: Optional[concurrent.futures.Future] = None
        self._open_position_id: Optional[int]          = None
        self._refresh_attempted                         = False

        self._reactor = _ensure_reactor()
        # reactor가 이미 실행 중이면 thread-safe하게 setup 예약
        if getattr(self._reactor, "running", False):
            self._reactor.callFromThread(self._setup_client)
        else:
            self._reactor.callWhenRunning(self._setup_client)

    def _ready(self) -> bool:
        return bool(
            self._client_id and self._client_secret
            and self._access_token and self._account_id and self._symbol_id
        )

    # ── Client 초기화 (reactor 스레드에서 실행) ──────────────────────────────

    def _setup_client(self) -> None:
        from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOAAccountAuthReq,
            ProtoOAApplicationAuthReq,
            ProtoOAAccountAuthRes,
            ProtoOAApplicationAuthRes,
            ProtoOAErrorRes,
            ProtoOAExecutionEvent,
            ProtoOAOrderErrorEvent,
        )

        host   = EndPoints.PROTOBUF_LIVE_HOST if self._is_live else EndPoints.PROTOBUF_DEMO_HOST
        client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)
        self._client = client

        def _start_client_service() -> None:
            """
            ctrader_open_api 의 startService() 는 Deferred 를 반환할 수 있는데,
            내부 connect timeout(기본 5s)이 발생하면 errback 으로 전달된다.
            이를 소비하지 않으면 'Unhandled error in Deferred' 로그가 누적된다.
            """
            try:
                d = client.startService()
                if d is not None:
                    d.addErrback(_consume_start_error)
            except Exception as exc:
                print(f"[cTrader] startService 예외 (account={self._account_id}): {exc}")

        def _consume_start_error(failure: Failure):
            msg = str(getattr(failure, "value", "") or failure)
            # 네트워크 변동/일시 timeout 은 재시도 루프에서 복구되므로 로그만 남기고 소비
            if "TimeoutError" in msg and "Deferred" in msg:
                print(f"[cTrader] 연결 타임아웃 (account={self._account_id}) — 재시도 예정")
                return None
            # 그 외 에러는 로그로 표면화
            print(f"[cTrader] startService errback (account={self._account_id}): {failure}")
            return None

        def on_connected(c):
            print(f"[cTrader] TCP connected (account={self._account_id} env={self._env})")
            req = ProtoOAApplicationAuthReq()
            req.clientId     = self._client_id
            req.clientSecret = self._client_secret
            print(f"[cTrader] AppAuth 요청 전송 (account={self._account_id})")
            client.send(req)

        def on_disconnected(c, reason):
            print(f"[cTrader] 연결 종료 (account={self._account_id}): {reason}")
            self._authed = False
            self._ready_event.clear()
            self._reactor.callLater(3.0, _start_client_service)

        def on_message(c, message):
            payload = Protobuf.extract(message)

            if isinstance(payload, ProtoOAErrorRes):
                desc = (getattr(payload, "description", "") or "").strip()
                code = str(getattr(payload, "errorCode", "") or "").strip().upper()
                denied = code == "ACCESS_DENIED" or "ACCESS_DENIED" in desc.upper()
                if denied and not self._refresh_attempted:
                    self._refresh_attempted = True
                    print(f"[cTrader] ACCESS_DENIED 감지 — 토큰 refresh 시도 (account={self._account_id})")
                    if self._refresh_access_token():
                        req = ProtoOAAccountAuthReq()
                        req.ctidTraderAccountId = self._account_id
                        req.accessToken = self._access_token
                        client.send(req)
                        return
                print(f"[cTrader] ❌ 에러: {payload.errorCode} — {payload.description}")
                self._resolve_pending(None)
                return

            if isinstance(payload, ProtoOAApplicationAuthRes):
                print(f"[cTrader] AppAuth 완료 (account={self._account_id})")
                req = ProtoOAAccountAuthReq()
                req.ctidTraderAccountId = self._account_id
                req.accessToken         = self._access_token
                print(f"[cTrader] AccountAuth 요청 전송 (account={self._account_id})")
                client.send(req)
                return

            if isinstance(payload, ProtoOAAccountAuthRes):
                self._authed = True
                self._refresh_attempted = False
                self._ready_event.set()
                print(f"[cTrader] ✅ 인증 완료 (account={self._account_id} env={self._env})")
                return

            if isinstance(payload, ProtoOAOrderErrorEvent):
                print(f"[cTrader] ❌ 주문 오류: {payload.errorCode} — {getattr(payload, 'description', '')}")
                self._resolve_pending(None)
                return

            if isinstance(payload, ProtoOAExecutionEvent):
                self._on_execution(payload)
                return

        client.setConnectedCallback(on_connected)
        client.setDisconnectedCallback(on_disconnected)
        client.setMessageReceivedCallback(on_message)
        _start_client_service()

    def _refresh_access_token(self) -> bool:
        if not (self._client_id and self._client_secret and self._refresh_token):
            print("[cTrader] refresh 불가: client/secret/refresh token 누락")
            return False

        params = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        try:
            res = httpx.get(_CTRADER_TOKEN_URL, params=params, timeout=10.0)
            if res.status_code >= 400:
                print(f"[cTrader] refresh 실패: {res.status_code} {res.text[:200]}")
                return False
            data = res.json() if res.content else {}
            new_access = (data.get("accessToken") or data.get("access_token") or "").strip()
            new_refresh = (data.get("refreshToken") or data.get("refresh_token") or "").strip()
            if not new_access:
                print(f"[cTrader] refresh 응답 이상: {data}")
                return False
            self._access_token = new_access
            os.environ["CTRADER_ACCESS_TOKEN"] = new_access
            if new_refresh:
                self._refresh_token = new_refresh
                os.environ["CTRADER_REFRESH_TOKEN"] = new_refresh
            save_tokens(self._access_token, self._refresh_token)
            print(f"[cTrader] ✅ access token refresh 완료 (account={self._account_id})")
            return True
        except Exception as e:
            print(f"[cTrader] refresh 예외: {e}")
            return False

    # ── 실행 이벤트 처리 ─────────────────────────────────────────────────────

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
            pass

        else:
            self._resolve_pending({"executionType": exec_type})

    def _resolve_pending(self, result: Any) -> None:
        with self._lock:
            f = self._pending
            self._pending = None
        if f and not f.done():
            f.set_result(result)

    def _send_and_wait(self, req: Any, timeout: float = 10.0) -> Optional[Dict]:
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

    # ── asyncio 공개 API ─────────────────────────────────────────────────────

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

    async def close_position(self, symbol: str, side: str) -> Optional[Dict]:
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


# ── per-account 인스턴스 캐시 ────────────────────────────────────────────────

_executors: Dict[int, CTraderExecutor] = {}


def get_executor(
    account_id: Optional[int] = None,
    env: Optional[str] = None,
    symbol_id: Optional[int] = None,
    lot_size: Optional[float] = None,
) -> Optional[CTraderExecutor]:
    token = os.environ.get("CTRADER_ACCESS_TOKEN", "").strip()
    if not token:
        token, _ = get_tokens()
    if not token:
        return None

    # 로컬 개발 시 cTrader 전체 비활성화
    if os.environ.get("CTRADER_FORCE_DEMO", "").strip().lower() == "true":
        return None

    _account_id = account_id or int(os.environ.get("CTRADER_ACCOUNT_ID", "0") or "0")
    _symbol_id  = symbol_id  or int(os.environ.get("CTRADER_SYMBOL_ID",  "0") or "0")
    if not _account_id or not _symbol_id:
        return None

    if _account_id not in _executors:
        _executors[_account_id] = CTraderExecutor(
            account_id=_account_id,
            env=env,
            symbol_id=_symbol_id,
            lot_size=lot_size,
        )
        print(f"[cTrader] 새 executor 생성 — account={_account_id} env={env or 'env_default'} symbol={_symbol_id}")
    return _executors[_account_id]
