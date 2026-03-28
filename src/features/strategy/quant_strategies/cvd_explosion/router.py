"""CVD Explosion — Router."""
from features.strategy.quant_strategies.common.router_factory import make_router

router = make_router("cvd_explosion", default_tfs="1h,4h")
