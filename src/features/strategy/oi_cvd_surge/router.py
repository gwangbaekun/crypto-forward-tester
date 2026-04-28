"""OI CVD Surge — Router."""
from features.strategy.common.router_factory import make_router

router = make_router("oi_cvd_surge", default_tfs="1h")
