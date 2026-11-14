from features.strategy.common.router_factory import make_router

from .signal import CFG, KEY

router = make_router(KEY, default_tfs=CFG["entry_tf"])
