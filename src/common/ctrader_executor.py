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
    CTRADER_ENV              → strategies_master.yaml ctrader_mode 로 일원화. 직접 설정 불필요.
    CTRADER_SYMBOL_ID
    CTRADER_LOT_SIZE         (기본 0.01) — 표준 lot; API volume = lot × 100 (0.01 lot 단위)
    CTRADER_UNITS_PER_LOT    (선택) — volume 환산 배수 (기본 100)
    CTRADER_MAX_VOLUME       (선택) — 브로커 ProtoOASymbol.maxVolume 상한으로 클램프 (정수)
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import os
import threading
from collections import deque
from typing import Any, Dict, Optional
import httpx
from twisted.python.failure import Failure
from common.ctrader_token_store import get_tokens, save_tokens


def _tg(msg: str) -> None:
    """Telegram 으로 cTrader 연결 상태 알림 (블로킹 없이 별도 스레드)."""
    def _send():
        try:
            from features.notifications.alert_dispatcher import AlertDispatcher
            AlertDispatcher().send_message(msg, send_telegram=True, send_discord=False)
        except Exception as e:
            print(f"[cTrader] Telegram 알림 실패: {e}")
    threading.Thread(target=_send, daemon=True).start()

_CTRADER_TOKEN_URL = "https://openapi.ctrader.com/apps/token"
def _get_units_per_lot() -> int:
    raw = os.environ.get("CTRADER_UNITS_PER_LOT", "100").strip()
    try:
        units = int(raw)
        if units > 0:
            return units
    except ValueError:
        pass
    return 100


def _lots_to_volume(lots: float) -> int:
    vol = max(1, int(round(float(lots) * _get_units_per_lot())))
    cap = os.environ.get("CTRADER_MAX_VOLUME", "").strip()
    if cap:
        try:
            mx = int(cap)
            if mx > 0 and vol > mx:
                print(
                    f"[cTrader] 주문 부피 {vol} → maxVolume({mx})으로 클램프 "
                    f"(CTRADER_MAX_VOLUME / 브로커 한도 확인)"
                )
                vol = mx
        except ValueError:
            pass
    return vol


# ── 공유 Twisted reactor (프로세스당 1개) ────────────────────────────────────

_reactor_lock    = threading.Lock()
_reactor_started = False
_ctrader_tcp_isolation_patched = False


def _patch_ctrader_tcp_protocol_for_multi_connection() -> None:
    """
    ctrader_open_api.TcpProtocol 는 클래스 레벨 _send_queue 를 쓴다.
    live + demo 처럼 Client(=TcpProtocol) 가 2개 이상이면 큐가 섞여
    다른 연결의 프레임이 나가고, 인증/응답이 깨지거나 타임아웃 난다.
    """
    global _ctrader_tcp_isolation_patched
    if _ctrader_tcp_isolation_patched:
        return
    from ctrader_open_api import tcpProtocol as _ctp

    _orig = _ctp.TcpProtocol.connectionMade

    def connectionMade(self):  # type: ignore[no-untyped-def]
        self._send_queue = deque()
        self._lastSendMessageTime = None
        return _orig(self)

    _ctp.TcpProtocol.connectionMade = connectionMade  # type: ignore[method-assign]
    _ctrader_tcp_isolation_patched = True


def _ensure_reactor() -> Any:
    global _reactor_started
    _patch_ctrader_tcp_protocol_for_multi_connection()
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
        if not env:
            raise ValueError("ctrader_executor: env('demo'|'live') 가 없습니다. strategies_master.yaml의 ctrader_mode 를 확인하세요.")
        self._env           = env.strip().lower()
        self._symbol_id     = symbol_id or int(os.environ.get("CTRADER_SYMBOL_ID", "0") or "0")
        self._lot_size      = lot_size or float(os.environ.get("CTRADER_LOT_SIZE", "0.01") or "0.01")
        self._is_live       = self._env == "live"

        self._client: Any                              = None
        self._reactor: Any                             = None
        self._ready_event                              = threading.Event()
        self._authed                                   = False
        self._lock                                     = threading.Lock()
        self._send_lock                                = threading.Lock()  # 동시 전송 직렬화
        self._pending: Optional[concurrent.futures.Future] = None
        self._open_position_id: Optional[int]          = None
        self._refresh_attempted                         = False
        self._position_cache: Optional[Dict]           = None
        self._position_cache_ts: float                 = 0.0
        self._position_fetch_lock: Optional[asyncio.Lock] = None  # lazy init (event loop 필요)

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

        # 이전 client의 내부 TCPClient 서비스가 살아있으면 자동 재연결을 시도한다.
        # self._client를 먼저 None으로 비운 뒤 stopService를 호출해야
        # stopService가 유발하는 on_disconnected 콜백이 재진입하지 않는다.
        if self._client is not None:
            old_client = self._client
            self._client = None
            try:
                old_client.stopService()
            except Exception:
                pass

        host   = EndPoints.PROTOBUF_LIVE_HOST if self._is_live else EndPoints.PROTOBUF_DEMO_HOST
        client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)
        self._client = client

        def _consume_deferred_error(failure: Failure):
            """client.send() 가 반환한 Deferred의 에러를 소비해 'Unhandled error in Deferred' 방지."""
            msg = str(getattr(failure, "value", "") or failure)
            if "TimeoutError" in msg and "Deferred" in msg:
                return None
            print(f"[cTrader] send errback (account={self._account_id}): {failure}")
            return None

        def _safe_send(req) -> None:
            """send() 반환 Deferred에 errback을 붙여 unhandled error 방지."""
            try:
                d = client.send(req)
                if d is not None and hasattr(d, "addErrback"):
                    d.addErrback(_consume_deferred_error)
            except Exception as exc:
                print(f"[cTrader] send 예외 (account={self._account_id}): {exc}")

        def _start_client_service() -> None:
            """
            ctrader_open_api 의 startService() 는 Deferred 를 반환할 수 있는데,
            내부 connect timeout(기본 5s)이 발생하면 errback 으로 전달된다.
            이를 소비하지 않으면 'Unhandled error in Deferred' 로그가 누적된다.
            """
            try:
                d = client.startService()
                if d is not None:
                    d.addErrback(_consume_deferred_error)
            except Exception as exc:
                print(f"[cTrader] startService 예외 (account={self._account_id}): {exc}")

        def on_connected(c):
            print(f"[cTrader] TCP connected (account={self._account_id} env={self._env})")
            req = ProtoOAApplicationAuthReq()
            req.clientId     = self._client_id
            req.clientSecret = self._client_secret
            print(f"[cTrader] AppAuth 요청 전송 (account={self._account_id})")
            _safe_send(req)

        def on_disconnected(c, reason):
            # c가 현재 활성 client가 아니면 이미 교체된 구 client의 콜백이므로 무시한다.
            # stopService() 호출이 on_disconnected를 재트리거해도 여기서 차단된다.
            if c is not self._client:
                return
            msg = str(getattr(reason, "value", "") or reason)
            clean = "ConnectionDone" in msg
            print(f"[cTrader] 연결 종료 (account={self._account_id}): {reason}")
            if not clean:
                _tg(f"⚠️ <b>[cTrader {self._env}]</b> 연결 끊김\naccount: <code>{self._account_id}</code>\n{msg[:120]}")
            self._authed = False
            self._ready_event.clear()
            self._reactor.callLater(3.0, self._setup_client)

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
                        _safe_send(req)
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
                _safe_send(req)
                return

            if isinstance(payload, ProtoOAAccountAuthRes):
                self._authed = True
                self._refresh_attempted = False
                self._ready_event.set()
                print(f"[cTrader] ✅ 인증 완료 (account={self._account_id} env={self._env})")
                _tg(f"✅ <b>[cTrader {self._env}]</b> 인증 완료\naccount: <code>{self._account_id}</code>")
                return

            if isinstance(payload, ProtoOAOrderErrorEvent):
                desc = getattr(payload, "description", "")
                print(f"[cTrader] ❌ 주문 오류: {payload.errorCode} — {desc}")
                _tg(f"❌ <b>[cTrader {self._env}]</b> 주문 오류\n<code>{payload.errorCode}</code>: {desc}")
                self._resolve_pending(None)
                return

            if isinstance(payload, ProtoOAExecutionEvent):
                self._on_execution(payload)
                return

            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAReconcileRes
            if isinstance(payload, ProtoOAReconcileRes):
                from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATradeSide
                positions = []
                for pos in payload.position:
                    side = "BUY" if pos.tradeData.tradeSide == ProtoOATradeSide.BUY else "SELL"
                    positions.append({
                        "positionId": pos.positionId,
                        "symbolId": pos.tradeData.symbolId,
                        "side": side,
                        "volume": pos.tradeData.volume,
                        "price": pos.price,
                        "stopLoss": pos.stopLoss if pos.stopLoss else None,
                        "takeProfit": pos.takeProfit if pos.takeProfit else None,
                        "openTimestamp": pos.tradeData.openTimestamp,
                    })
                self._resolve_pending({"positions": positions, "account_id": self._account_id, "env": self._env})
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
            _tg(
                f"✅ <b>[cTrader {self._env}]</b> 체결\n"
                f"fill: <code>{fill_price:.4f}</code>  posId: <code>{pos_id}</code>"
            )
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

        # 동시에 여러 요청이 _pending 슬롯을 덮어쓰지 않도록 직렬화
        with self._send_lock:
            loop = concurrent.futures.Future()
            with self._lock:
                self._pending = loop

            def _send_from_thread():
                try:
                    d = self._client.send(req)
                    if d is not None and hasattr(d, "addErrback"):
                        d.addErrback(lambda f: None)
                except Exception as exc:
                    print(f"[cTrader] send 예외 (account={self._account_id}): {exc}")
            self._reactor.callFromThread(_send_from_thread)

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
            self._position_cache = None  # 진입 후 포지션 캐시 무효화
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
            self._position_cache = None  # 청산 후 포지션 캐시 무효화
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

    async def close_position_by_id(self, position_id: int, volume: Optional[int] = None) -> Optional[Dict]:
        """positionId를 직접 지정해서 청산 (메모리 상태와 무관)."""
        if not self._ready():
            return None

        def _send():
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAClosePositionReq
            req = ProtoOAClosePositionReq()
            req.ctidTraderAccountId = self._account_id
            req.positionId          = position_id
            req.volume              = volume if volume is not None else _lots_to_volume(self._lot_size)
            return self._send_and_wait(req)

        result = await self._run_in_executor(_send)
        if result:
            if self._open_position_id == position_id:
                self._open_position_id = None
            self._position_cache = None  # 강제 청산 후 포지션 캐시 무효화
            print(f"[cTrader] ✅ 강제 청산 — positionId={position_id} fill={result.get('avgPrice', 0):.4f}")
        return result

    async def get_position(self, symbol: str, cache_ttl: float = 8.0) -> Optional[Dict]:
        """오픈 포지션 조회. cache_ttl 초 이내 재요청은 캐시를 반환해 cTrader 부하를 방지.

        - 캐시 stale 상태에서 동시 요청이 몰려도 실제 ReconcileReq 는 1개만 전송.
        """
        import time as _t
        if not self._ready():
            return None

        now = _t.time()
        if self._position_cache is not None and now - self._position_cache_ts < cache_ttl:
            return self._position_cache

        # asyncio.Lock lazy init (event loop 기동 후 생성)
        if self._position_fetch_lock is None:
            self._position_fetch_lock = asyncio.Lock()

        async with self._position_fetch_lock:
            # Lock 대기 중 다른 코루틴이 이미 채워뒀으면 캐시 반환
            now = _t.time()
            if self._position_cache is not None and now - self._position_cache_ts < cache_ttl:
                return self._position_cache

            def _send():
                from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAReconcileReq
                req = ProtoOAReconcileReq()
                req.ctidTraderAccountId = self._account_id
                return self._send_and_wait(req)

            result = await self._run_in_executor(_send)
            if result is not None:
                self._position_cache = result
                self._position_cache_ts = _t.time()
            return result

    async def cancel_tp_sl(self, symbol: str) -> None:
        pass


# ── per-account 인스턴스 캐시 ────────────────────────────────────────────────

_executors: Dict[int, CTraderExecutor] = {}


def get_all_executors() -> Dict[int, "CTraderExecutor"]:
    """생성된 모든 executor 반환 (positions API 등에서 사용)."""
    return _executors


def get_executor_unavailable_reason(
    account_id: Optional[int] = None,
    symbol_id: Optional[int] = None,
) -> Optional[str]:
    """executor 생성이 불가한 이유를 반환. 생성 가능하면 None."""
    token = os.environ.get("CTRADER_ACCESS_TOKEN", "").strip()
    if not token:
        token, _ = get_tokens()
    if not token:
        return "CTRADER_ACCESS_TOKEN 누락"

    _account_id = account_id or int(os.environ.get("CTRADER_ACCOUNT_ID", "0") or "0")
    _symbol_id = symbol_id or int(os.environ.get("CTRADER_SYMBOL_ID", "0") or "0")
    if not _account_id:
        return "ctrader_account_id 미설정"
    if not _symbol_id:
        return "ctrader_symbol_id 미설정"
    return None


def get_executor(
    account_id: Optional[int] = None,
    env: Optional[str] = None,
    symbol_id: Optional[int] = None,
    lot_size: Optional[float] = None,
) -> Optional[CTraderExecutor]:
    if get_executor_unavailable_reason(account_id=account_id, symbol_id=symbol_id):
        return None

    _account_id = account_id or int(os.environ.get("CTRADER_ACCOUNT_ID", "0") or "0")
    _symbol_id  = symbol_id  or int(os.environ.get("CTRADER_SYMBOL_ID",  "0") or "0")

    if _account_id not in _executors:
        _executors[_account_id] = CTraderExecutor(
            account_id=_account_id,
            env=env,
            symbol_id=_symbol_id,
            lot_size=lot_size,
        )
        print(f"[cTrader] 새 executor 생성 — account={_account_id} env={env or 'env_default'} symbol={_symbol_id}")
    return _executors[_account_id]
