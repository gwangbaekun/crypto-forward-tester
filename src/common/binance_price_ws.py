"""
Mark price cache via Bybit Futures WebSocket (v5 public/linear).
Public API is identical to the previous Binance implementation so no callers change.
"""
import asyncio
import errno
import json
import os
import random
import socket
import time
from typing import Any, Dict, List, Optional

import aiohttp

_EAI_CODES: frozenset = frozenset(
    c for c in (
        getattr(errno, "EAI_NONAME", None),
        getattr(errno, "EAI_AGAIN", None),
        getattr(errno, "EAI_FAIL", None),
        -2, -5,
    )
    if c is not None
)

DEFAULT_SYMBOLS = ["btcusdt", "ethusdt"]
STALE_SECONDS = 30
_RECV_TIMEOUT_SEC = float(os.getenv("BYBIT_WS_RECV_TIMEOUT_SEC", "20"))
_MIN_RECONNECT_SEC = float(os.getenv("BYBIT_WS_MIN_RECONNECT_SEC", "3.0"))
_DNS_RECONNECT_FLOOR = float(os.getenv("BYBIT_WS_DNS_RECONNECT_FLOOR", "5.0"))

_BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"


class _SymbolWS:
    """Single-symbol Bybit WebSocket worker."""

    def __init__(self, symbol: str, prices: Dict[str, float], updated_at: Dict[str, float]) -> None:
        self._symbol = symbol                     # lowercase: btcusdt
        self._topic = f"tickers.{symbol.upper()}" # Bybit topic: tickers.BTCUSDT
        self._prices = prices
        self._updated_at = updated_at
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._reconnect_delay = 1.0
        self._dns_streak = 0

    def start(self) -> None:
        if self._running and self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run_forever())

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    def _is_dns_error(self, e: BaseException) -> bool:
        if isinstance(e, socket.gaierror):
            return True
        if isinstance(e, OSError) and getattr(e, "errno", None) in _EAI_CODES:
            return True
        return False

    async def _run_forever(self) -> None:
        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except OSError as e:
                if self._is_dns_error(e):
                    self._dns_streak += 1
                    base = max(self._reconnect_delay, _DNS_RECONNECT_FLOOR)
                    delay = min(base * (1.2 ** min(self._dns_streak, 8)), 60.0) + random.uniform(0.0, 1.0)
                    print(f"[BybitPriceWS:{self._symbol}] dns error: {e}, reconnect in {delay:.1f}s")
                    await asyncio.sleep(delay)
                    self._reconnect_delay = min(self._reconnect_delay * 2, 30.0)
                else:
                    self._dns_streak = 0
                    delay = min(max(_MIN_RECONNECT_SEC, self._reconnect_delay) + random.uniform(0.0, 0.5), 30.0)
                    print(f"[BybitPriceWS:{self._symbol}] error: {e}, reconnect in {delay:.1f}s")
                    await asyncio.sleep(delay)
                    self._reconnect_delay = min(self._reconnect_delay * 1.5, 30.0)
            except Exception as e:
                self._dns_streak = 0
                delay = min(max(_MIN_RECONNECT_SEC, self._reconnect_delay) + random.uniform(0.0, 0.5), 60.0)
                print(f"[BybitPriceWS:{self._symbol}] error: {e}, reconnect in {delay:.1f}s")
                await asyncio.sleep(delay)
                self._reconnect_delay = min(max(self._reconnect_delay * 1.5, _MIN_RECONNECT_SEC), 30.0)

    async def _connect_and_listen(self) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                _BYBIT_WS_URL,
                heartbeat=20,
                max_msg_size=2**22,
            ) as ws:
                # subscribe to this symbol's ticker
                await ws.send_str(json.dumps({"op": "subscribe", "args": [self._topic]}))

                _connect_wall = time.time()
                _received_first = False
                print(f"[BybitPriceWS:{self._symbol}] connected")

                async for msg in ws:
                    if not self._running:
                        break

                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                        except Exception:
                            continue

                        # application-level ping from Bybit
                        if data.get("op") == "ping":
                            await ws.send_str(json.dumps({"op": "pong"}))
                            continue

                        if data.get("topic") != self._topic:
                            continue

                        mark_str = data.get("data", {}).get("markPrice")
                        if mark_str is not None:
                            try:
                                self._prices[self._symbol] = float(mark_str)
                                self._updated_at[self._symbol] = time.time()
                                if not _received_first:
                                    _received_first = True
                                    self._reconnect_delay = 1.0
                                    self._dns_streak = 0
                            except (TypeError, ValueError):
                                pass

                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        raise Exception(f"ws error: {ws.exception()}")

                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                        break

                now = time.time()
                if not _received_first and (now - _connect_wall) > _RECV_TIMEOUT_SEC * 2:
                    raise OSError("ws closed with no data received")


class BinancePriceWS:
    """
    Singleton mark price cache — backed by Bybit Futures WS.
    Class name kept for backward compatibility with all callers.
    """

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
        self._symbols: List[str] = []
        self._workers: Dict[str, _SymbolWS] = {}

    def start(self, symbols: Optional[list] = None) -> None:
        if symbols:
            normalized = []
            for s in symbols:
                t = (s or "").lower().strip().replace("usdt", "")
                normalized.append((t or "btc") + "usdt")
            new_symbols = list(dict.fromkeys(normalized))
        else:
            new_symbols = list(DEFAULT_SYMBOLS)

        self._running = True

        for sym in list(self._workers):
            if sym not in new_symbols:
                self._workers.pop(sym).stop()

        added = []
        for sym in new_symbols:
            if sym not in self._workers:
                w = _SymbolWS(sym, self._prices, self._updated_at)
                self._workers[sym] = w
                w.start()
                added.append(sym)

        self._symbols = new_symbols
        if added:
            print(f"[BybitPriceWS] started for {added}")

    def stop(self) -> None:
        self._running = False
        for w in self._workers.values():
            w.stop()
        self._workers.clear()
        print("[BybitPriceWS] stopped")

    def get_price(self, symbol: str) -> Optional[float]:
        key = symbol.lower().replace("usdt", "") + "usdt"
        if key not in self._prices:
            return None
        if (time.time() - self._updated_at.get(key, 0)) > STALE_SECONDS:
            return None
        return self._prices[key]

    def get_price_or_none_sync(self, symbol: str) -> Optional[float]:
        return self.get_price(symbol)

    def get_symbols(self) -> List[str]:
        return list(self._symbols)

    def get_display_snapshot(self) -> Dict[str, Any]:
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


def get_cached_price(symbol: str) -> Optional[float]:
    return BinancePriceWS().get_price(symbol)
