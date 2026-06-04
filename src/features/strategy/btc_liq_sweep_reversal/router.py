"""BTC Liq Sweep Reversal — Router."""
from features.strategy.common.router_factory import make_router

router = make_router("btc_liq_sweep_reversal", default_tfs="1h")
