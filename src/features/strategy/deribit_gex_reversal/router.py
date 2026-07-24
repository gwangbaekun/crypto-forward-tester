"""Deribit Expiry GEX Reversal — Router (자체 포함).

표준 엔드포인트(dashboard/realtime_state/forward_test/stats·trades/…)는
router_factory.make_router 가 자동 생성. 이 전략은 옵션체인 기반이라 추가
커스텀 엔드포인트는 없다.
"""
from __future__ import annotations

from features.strategy.common.router_factory import make_router

router = make_router("deribit_gex_reversal", default_tfs="1h")
