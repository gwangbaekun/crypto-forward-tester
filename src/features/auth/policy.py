"""게스트에게 공개하는 화면과 읽기 API의 고정 허용 목록."""
from __future__ import annotations


GUEST_DASHBOARD_PATHS = frozenset({
    "/quant/spot_perp_cvd/dashboard",
    "/quant/oi_accel_breakout_v2/dashboard",
    "/quant/polymarket/fade/dashboard",
})

_GUEST_SHARED_DATA_PATHS = frozenset({
    "/api/market-stream",
    "/auth/ctrader/positions",
})

_GUEST_STRATEGY_DATA_PATHS = frozenset({
    "/quant/spot_perp_cvd/klines",
    "/quant/spot_perp_cvd/realtime_state",
    "/quant/spot_perp_cvd/execute/status",
    "/quant/spot_perp_cvd/forward_test/stats",
    "/quant/spot_perp_cvd/forward_test/trades",
    "/quant/oi_accel_breakout_v2/klines",
    "/quant/oi_accel_breakout_v2/realtime_state",
    "/quant/oi_accel_breakout_v2/execute/status",
    "/quant/oi_accel_breakout_v2/forward_test/stats",
    "/quant/oi_accel_breakout_v2/forward_test/trades",
})

_GUEST_FADE_DATA_PATHS = frozenset({
    "/quant/polymarket/fade/watchlist",
    "/quant/polymarket/fade/positions",
    "/quant/polymarket/fade/live-status",
    "/quant/polymarket/fade/curve",
    "/quant/polymarket/fade/market/detail",
})

_GUEST_ALLOWED_PATHS = (
    frozenset({"/", "/api/site-index"})
    | GUEST_DASHBOARD_PATHS
    | _GUEST_SHARED_DATA_PATHS
    | _GUEST_STRATEGY_DATA_PATHS
    | _GUEST_FADE_DATA_PATHS
)


def is_guest_path_allowed(path: str) -> bool:
    """쿼리스트링을 제외한 요청 경로가 게스트 허용 목록에 있는지 반환한다."""
    return path in _GUEST_ALLOWED_PATHS
