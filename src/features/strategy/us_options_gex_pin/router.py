"""US Options Expiry GEX Pinning — Router (자체 포함).

표준 엔드포인트(dashboard/realtime_state/forward_test/…)는 router_factory.make_router
가 자동 생성. 옵션체인 기반이라 추가 커스텀 엔드포인트는 없다.
"""
from __future__ import annotations

from features.strategy.common.router_factory import make_router

router = make_router("us_options_gex_pin", default_tfs="1d")
