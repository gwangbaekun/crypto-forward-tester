"""ATR Breakout — Router."""
from features.strategy.quant_strategies.common.router_factory import make_router

router = make_router("atr_breakout", default_tfs="5m,15m,1h,4h")
