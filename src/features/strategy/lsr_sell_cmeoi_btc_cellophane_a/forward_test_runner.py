from __future__ import annotations

import asyncio
import time

from features.strategy.btc_cellophane_data import hub
from features.strategy.btc_cellophane_data.buffer import BAR, NotReady
from features.strategy.common.base_realtime_feed import _fire_and_forget, _last_bar_time, _signal_cache, _tick_and_notify

from .engine import get_engine
from .signal import CFG, KEY, SHEETS, compute_signal

PRE_ENTRY_SECONDS = 60.0
AFTER_CLOSE_SECONDS = 5.0
WAIT_SECONDS = 60.0
RETRY_SECONDS = 3.0

hub.register(CFG["symbol"], [s for side in SHEETS for s in SHEETS[side]])


async def buffer_at(end_s):
    while True:
        try:
            buf = await hub.refresh(end_s)
            return buf
        except NotReady:
            if time.time() > end_s + WAIT_SECONDS:
                raise
            await asyncio.sleep(RETRY_SECONDS)


async def decide_at(symbol, end_s):
    await asyncio.sleep(max(0.0, end_s + AFTER_CLOSE_SECONDS - time.time()))
    buf = await buffer_at(end_s)
    sig = compute_signal(buf)
    state = {"symbol": symbol, "current_price": None, "signal": sig, "by_tf": {CFG["entry_tf"]: {"signal": sig}},
             "entry_tf": CFG["entry_tf"], "decision_s": end_s}
    _signal_cache[f"{KEY}:{symbol}"] = {"state": state, "ts": time.time()}
    await _tick_and_notify(KEY, symbol, None, state)


async def get_state(symbol="BTCUSDT", tfs="5m", ws_only=False):
    cache_key = f"{KEY}:{symbol}"
    now = time.time()
    bar_start = int(now // BAR) * BAR
    sec_to_close = bar_start + BAR - now
    fresh = cache_key not in _last_bar_time or _last_bar_time[cache_key] < bar_start
    if 0 < sec_to_close <= PRE_ENTRY_SECONDS and fresh:
        _last_bar_time[cache_key] = bar_start
        if get_engine().get_position() is None:
            await decide_at(symbol, bar_start + BAR)
        else:
            _fire_and_forget(decide_at(symbol, bar_start + BAR))
    state = _signal_cache[cache_key]["state"] if cache_key in _signal_cache else {}
    return state
