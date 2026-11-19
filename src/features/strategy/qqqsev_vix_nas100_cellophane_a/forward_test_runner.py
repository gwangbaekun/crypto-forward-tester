from __future__ import annotations

import asyncio
import time

from features.strategy.common.base_realtime_feed import (_execute_verify_notify, _fire_and_forget, _last_bar_time,
                                                         _signal_cache)
from features.strategy.common.signal_logger import log_signal_snapshot
from features.strategy.nas100_cellophane_data import ctrader_feed
from features.strategy.nas100_cellophane_data.buffer import Buffer

from .engine import get_engine
from .signal import CFG, KEY, SHEETS, compute_signal

BAR = ctrader_feed.BAR
LOOKBACK = 4 * 86400
PRE_ENTRY_SECONDS = 60.0
AFTER_CLOSE_SECONDS = 5.0
WAIT_SECONDS = 60.0
RETRY_SECONDS = 5.0
PRICE = CFG["price"]
BUFFER = Buffer(SHEETS)


def price_bars(end_s):
    out = ctrader_feed.bars(int(PRICE["ctid"]), bool(PRICE["is_live"]), PRICE["symbol_name"], end_s - LOOKBACK, end_s)
    return out


async def bars_at(end_s):
    while True:
        bars = await asyncio.to_thread(price_bars, end_s)
        if bars and bars[-1][0] == end_s - BAR:
            return bars
        if time.time() > end_s + WAIT_SECONDS:
            last = bars[-1][0] if bars else None
            print(f"[{KEY}] {end_s} 에 끝나는 봉이 없다 (마지막 봉 시작 {last}). 장이 닫힌 것으로 보고 판단하지 않는다")
            return None
        await asyncio.sleep(RETRY_SECONDS)


async def decide_at(symbol, end_s):
    await asyncio.sleep(max(0.0, end_s + AFTER_CLOSE_SECONDS - time.time()))
    bars = await bars_at(end_s)
    if bars is None:
        return
    await asyncio.to_thread(BUFFER.refresh, end_s)
    prev_s = bars[-2][0] + BAR
    sig = compute_signal(BUFFER, prev_s, end_s)
    price = bars[-1][4]
    state = {"symbol": symbol, "current_price": price, "signal": sig, "bars": bars, "decision_s": end_s,
             "by_tf": {CFG["entry_tf"]: {"signal": sig}}, "entry_tf": CFG["entry_tf"]}
    view = {k: v for k, v in state.items() if k != "bars"}
    _signal_cache[f"{KEY}:{symbol}"] = {"state": view, "ts": time.time()}
    _fire_and_forget(log_signal_snapshot(KEY, symbol, view))
    result = get_engine().tick(symbol, state)
    if result:
        await _execute_verify_notify(KEY, symbol, result["events"], price)


async def get_state(symbol="NAS100", tfs="5m", ws_only=False):
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
