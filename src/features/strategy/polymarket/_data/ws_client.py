"""Polymarket CLOB WebSocket 클라이언트.

단일 연결로 다수 토큰의 실시간 호가(bid/ask)를 수신.
여러 전략이 price_book 을 공유해서 읽는다.

WebSocket: wss://ws-subscriptions-clob.polymarket.com/ws/market
Subscribe : {"assets_ids": [token_id, ...], "type": "market"}
Events    : book (스냅샷), price_change (변경분)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable

import aiohttp

WS_URL      = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
RECONNECT_S = 5.0
log = logging.getLogger("polymarket.ws")


@dataclass
class PriceLevel:
    token_id:  str
    best_bid:  float | None = None   # 내가 팔 수 있는 최고가
    best_ask:  float | None = None   # 내가 살 수 있는 최저가
    mid:       float | None = None
    last_price: float | None = None
    updated_at: float = field(default_factory=time.time)

    def refresh_mid(self) -> None:
        if self.best_bid and self.best_ask:
            self.mid = (self.best_bid + self.best_ask) / 2


# 공유 가격 장부 — 전략들이 직접 읽는다
price_book: dict[str, PriceLevel] = {}

# 콜백 목록 — 업데이트마다 호출
_callbacks: list[Callable[[str], Awaitable[None]]] = []


def register_callback(fn: Callable[[str], Awaitable[None]]) -> None:
    """token_id 를 인자로 받는 async 콜백 등록."""
    _callbacks.append(fn)


def _parse_book(msg: dict) -> None:
    token_id = msg.get("asset_id")
    if not token_id:
        return

    buys  = msg.get("buys", [])
    sells = msg.get("sells", [])

    level = price_book.setdefault(token_id, PriceLevel(token_id=token_id))

    if buys:
        level.best_bid = max(float(b["price"]) for b in buys if b.get("price"))
    if sells:
        level.best_ask = min(float(s["price"]) for s in sells if s.get("price"))

    level.refresh_mid()
    level.updated_at = time.time()


def _parse_price_change(msg: dict) -> list[str]:
    """변경된 token_id 목록 반환."""
    changed: list[str] = []
    for change in msg.get("changes", []):
        token_id = change.get("asset_id")
        price    = change.get("price")
        if not token_id or not price:
            continue

        level = price_book.setdefault(token_id, PriceLevel(token_id=token_id))
        side  = (change.get("side") or "").upper()
        p     = float(price)

        if side in ("BUY", "BID"):
            level.best_bid = p
        elif side in ("SELL", "ASK"):
            level.best_ask = p
        else:
            level.last_price = p

        level.refresh_mid()
        level.updated_at = time.time()
        changed.append(token_id)
    return changed


async def _fire_callbacks(token_ids: list[str]) -> None:
    for tid in token_ids:
        for cb in _callbacks:
            try:
                await cb(tid)
            except Exception as e:
                log.debug("callback error %s: %s", tid[:16], e)


class CLOBWSClient:
    """CLOB WebSocket 관리자. runner.py 에서 생성 후 run() 태스크를 띄운다."""

    def __init__(self) -> None:
        self._token_ids: set[str] = set()
        self._running   = False

    def add_tokens(self, *token_ids: str) -> None:
        self._token_ids.update(t for t in token_ids if t)

    async def run(self) -> None:
        self._running = True
        while self._running:
            if not self._token_ids:
                await asyncio.sleep(10)
                continue
            try:
                await self._connect()
            except Exception as e:
                log.warning("WS disconnected (%s), reconnecting in %.0fs", e, RECONNECT_S)
                await asyncio.sleep(RECONNECT_S)

    async def _connect(self) -> None:
        tokens = list(self._token_ids)
        log.info("WS connecting — %d tokens", len(tokens))

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(WS_URL, heartbeat=30) as ws:
                await ws.send_json({"assets_ids": tokens, "type": "market"})
                log.info("WS subscribed")

                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._handle(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                        break

    async def _handle(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        event = data.get("event_type", "")
        if event == "book":
            _parse_book(data)
            tid = data.get("asset_id")
            if tid:
                await _fire_callbacks([tid])
        elif event == "price_change":
            changed = _parse_price_change(data)
            if changed:
                await _fire_callbacks(changed)

    def stop(self) -> None:
        self._running = False
