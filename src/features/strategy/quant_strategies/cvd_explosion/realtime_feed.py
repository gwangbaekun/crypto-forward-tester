"""CVD Explosion — Realtime Feed (btc_backtest signal 동일)."""
from __future__ import annotations

from typing import Any, Dict

from features.strategy.quant_strategies.common.base_realtime_feed import build_state


async def get_state(symbol: str = "BTCUSDT", tfs: str = "1h,4h"):
    from features.strategy.quant_strategies.common.config_loader import get_master_config

    from .config_loader import get_timeframes
    from .signal import compute_signal

    def extra_bundle_args(_bundle: Any) -> Dict[str, Any]:
        master = (get_master_config() or {}).get("cvd_explosion") or {}
        tfm = get_timeframes()
        return {
            "entry_tf": master.get("entry_tf") or tfm["entry_tf"],
            "higher_tf": master.get("higher_tf") or tfm["higher_tf"],
        }

    return await build_state(
        "cvd_explosion",
        symbol,
        tfs,
        compute_signal,
        extra_bundle_args=extra_bundle_args,
    )
