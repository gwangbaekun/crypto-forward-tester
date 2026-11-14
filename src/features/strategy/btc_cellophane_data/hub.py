from __future__ import annotations

import asyncio

from .buffer import Buffer
from .config_loader import sizes

STATE = {"sheets": [], "buffer": None, "end_s": None, "symbol": None}
LOCK = asyncio.Lock()


def register(symbol, sheets):
    if STATE["buffer"] is not None:
        raise RuntimeError("버퍼를 만든 뒤에는 장을 등록할 수 없다")
    if STATE["symbol"] not in (None, symbol):
        raise ValueError(f"종목이 다르다: {STATE['symbol']} / {symbol}")
    STATE["symbol"] = symbol
    STATE["sheets"] += sheets


async def refresh(end_s):
    async with LOCK:
        if STATE["buffer"] is None:
            STATE["buffer"] = Buffer(STATE["symbol"], sizes(STATE["sheets"]))
        if STATE["end_s"] != end_s:
            await asyncio.to_thread(STATE["buffer"].refresh, end_s)
            STATE["end_s"] = end_s
    out = STATE["buffer"]
    return out
