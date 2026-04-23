"""
Binance Futures price cache via WebSocket.
Subscribes to markPrice stream(s) and exposes get_price(symbol) to minimize REST calls.
"""
import asyncio
import errno
import json
import os
import random
import socket
import time
from typing import Any, Dict, List, Optional

import websockets
from websockets.exceptions import ConnectionClosed

# Default symbols to subscribe (lowercase)
DEFAULT_SYMBOLS = ["btcusdt", "ethusdt"]
# Cache TTL: if WS has no update for this many seconds, get_price may fall back to None/REST
STALE_SECONDS = 30
# 수신 침묵 감지: 이 시간 동안 ws.recv()가 없으면 heartbeat 검사
_RECV_TIMEOUT_SEC = float(os.getenv("BINANCE_WS_RECV_TIMEOUT_SEC", "20"))
# Abrupt TCP drop (no close frame) / flaky DNS 시 재연결 스팸 방지
_MIN_RECONNECT_SEC = float(os.getenv("BINANCE_WS_MIN_RECONNECT_SEC", "3.0"))
_DNS_RECONNECT_FLOOR = float(os.getenv("BINANCE_WS_DNS_RECONNECT_FLOOR", "5.0"))


class BinancePriceWS:
    """Singleton: one WS connection (combined stream), in-memory price cache."""

    _instance: Optional["BinancePriceWS"] = None

    def __new__(cls) -> "BinancePriceWS":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._prices: Dict[str, float] = {}
        self._updated_at: Dict[str, float] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._symbols = list(DEFAULT_SYMBOLS)
        self._reconnect_delay = 1.0
        self._dns_streak = 0
        self._last_rx_monotonic = 0.0

    def start(self, symbols: Optional[list] = None) -> None:
        """Start the WebSocket listener in the background."""
        if self._running:
            return
        if symbols:
            normalized = []
            for s in symbols:
                t = (s or "").lower().strip().replace("usdt", "")
                normalized.append((t or "btc") + "usdt")
            self._symbols = list(dict.fromkeys(normalized))
        self._running = True
        self._task = asyncio.create_task(self._run_forever())
        print(f"[BinancePriceWS] started for {self._symbols}")

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        print("[BinancePriceWS] stopped")

    def get_price(self, symbol: str) -> Optional[float]:
        """Return cached price for symbol (e.g. BTCUSDT). Returns None if stale or missing."""
        key = symbol.lower().replace("usdt", "") + "usdt"
        if key not in self._prices:
            return None
        if (time.time() - self._updated_at.get(key, 0)) > STALE_SECONDS:
            return None
        return self._prices[key]

    def get_price_or_none_sync(self, symbol: str) -> Optional[float]:
        """Alias for get_price (sync API for callers that cannot await)."""
        return self.get_price(symbol)

    def get_symbols(self) -> List[str]:
        return list(self._symbols)

    def get_display_snapshot(self) -> Dict[str, Any]:
        """
        UI/WebSocket용: 구독 심볼별 마지막 가격·수신 시각(age).
        stale 여부는 표시용(캐시 TTL 초과해도 마지막 값은 유지).
        """
        now = time.time()
        rows: Dict[str, Any] = {}
        for sym in self._symbols:
            price = self._prices.get(sym)
            ts = self._updated_at.get(sym)
            age = (now - ts) if ts is not None else None
            stale = age is None or age > STALE_SECONDS
            rows[sym] = {
                "price": price,
                "updated_at_unix": ts,
                "age_sec": round(age, 3) if age is not None else None,
                "stale": stale,
            }
        return {
            "kind": "mark_prices",
            "server_ts": now,
            "symbols": rows,
        }

    def _is_dns_oserror(self, e: BaseException) -> bool:
        if isinstance(e, socket.gaierror):
            return True
        if isinstance(e, OSError):
            en = getattr(e, "errno", None)
            # Linux/mac: -2 Name or service not known, -5 No address; Windows는 코드 다를 수 있음
            if en in (errno.EAI_NONAME, errno.EAI_AGAIN, errno.EAI_FAIL, -2, -5):
                return True
        return False

    async def _run_forever(self) -> None:
        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except ConnectionClosed as e:
                # 서버/중간망이 TCP만 끊고 close frame 없이 끊는 경우가 많음 → 짧은 간격 재시도 금지
                self._dns_streak = 0
                jitter = random.uniform(0.0, 0.75)
                delay = max(_MIN_RECONNECT_SEC, self._reconnect_delay) + jitter
                delay = min(delay, 60.0)
                code = getattr(e, "code", None)
                print(
                    f"[BinancePriceWS] connection closed (code={code}), reconnect in {delay:.1f}s"
                )
                await asyncio.sleep(delay)
                self._reconnect_delay = min(max(self._reconnect_delay * 1.5, _MIN_RECONNECT_SEC), 30.0)
            except OSError as e:
                if self._is_dns_oserror(e):
                    self._dns_streak += 1
                    base = max(self._reconnect_delay, _DNS_RECONNECT_FLOOR)
                    delay = min(base * (1.2 ** min(self._dns_streak, 8)), 60.0)
                    jitter = random.uniform(0.0, 1.0)
                    print(f"[BinancePriceWS] dns error: {e}, reconnect in {delay + jitter:.1f}s")
                    await asyncio.sleep(delay + jitter)
                    self._reconnect_delay = min(self._reconnect_delay * 2, 30.0)
                else:
                    self._dns_streak = 0
                    jitter = random.uniform(0.0, 0.5)
                    delay = min(self._reconnect_delay + jitter, 30.0)
                    print(f"[BinancePriceWS] error: {e}, reconnect in {delay:.1f}s")
                    await asyncio.sleep(delay)
                    self._reconnect_delay = min(self._reconnect_delay * 2, 30.0)
            except Exception as e:
                self._dns_streak = 0
                msg = str(e).lower()
                # websockets: "no close frame received or sent" 등
                floor = _MIN_RECONNECT_SEC if "close frame" in msg or "connection reset" in msg else 0.0
                jitter = random.uniform(0.0, 0.5)
                delay = max(floor, self._reconnect_delay) + jitter
                delay = min(delay, 60.0)
                print(f"[BinancePriceWS] error: {e}, reconnect in {delay:.1f}s")
                await asyncio.sleep(delay)
                self._reconnect_delay = min(max(self._reconnect_delay * 2, _MIN_RECONNECT_SEC), 30.0)

    def _streams_query(self) -> str:
        return "/".join(f"{s}@markPrice" for s in self._symbols)

    async def _connect_and_listen(self) -> None:
        stream = self._streams_query()
        url = f"wss://fstream.binance.com/stream?streams={stream}"
        async with websockets.connect(
            url,
            # NAT/방화벽 idle 끊김 완화 + ping 응답 지연 허용 (과도한 1011 방지)
            ping_interval=30,
            ping_timeout=120,
            close_timeout=10,
            open_timeout=30,
            max_size=2**22,
        ) as ws:
            self._reconnect_delay = 1.0
            self._dns_streak = 0
            self._last_rx_monotonic = time.monotonic()
            print(f"[BinancePriceWS] connected to {url}")

            while self._running:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT_SEC)
                except asyncio.TimeoutError:
                    # 소켓이 조용히 멈춘 half-open 상태 방지: 최근 가격 갱신이 stale면 재연결
                    newest_ts = max(self._updated_at.values()) if self._updated_at else 0.0
                    age = time.time() - newest_ts if newest_ts else float("inf")
                    if age > STALE_SECONDS:
                        print(
                            f"[BinancePriceWS] recv timeout and cache stale ({age:.1f}s) → reconnect"
                        )
                        raise OSError("ws recv timeout with stale cache")
                    continue

                self._last_rx_monotonic = time.monotonic()
                try:
                    msg = json.loads(raw)
                    stream_name = msg.get("stream", "")
                    data = msg.get("data") or msg
                    # stream_name e.g. "btcusdt@markPrice"
                    sym = (stream_name.split("@")[0] or "").lower()
                    if not sym:
                        continue
                    p_str = data.get("p")
                    if p_str is not None:
                        try:
                            self._prices[sym] = float(p_str)
                            self._updated_at[sym] = time.time()
                        except (TypeError, ValueError):
                            pass
                except Exception:
                    continue


def get_cached_price(symbol: str) -> Optional[float]:
    """Convenience: return cached price from singleton. None if WS not updated recently."""
    return BinancePriceWS().get_price(symbol)
