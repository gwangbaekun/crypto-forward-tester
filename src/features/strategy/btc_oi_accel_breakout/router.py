"""BTC OI Accel Breakout — Router."""
from features.strategy.common.router_factory import make_router

router = make_router("btc_oi_accel_breakout", default_tfs="1h,4h")
