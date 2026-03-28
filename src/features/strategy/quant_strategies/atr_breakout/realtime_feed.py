"""ATR Breakout — Realtime Feed (v1)."""
from features.strategy.quant_strategies.common.base_realtime_feed import build_state


async def get_state(symbol: str = "BTCUSDT", tfs: str = "5m,15m,1h,4h"):
    from .signal import compute_signal

    return await build_state("atr_breakout", symbol, tfs, compute_signal)
