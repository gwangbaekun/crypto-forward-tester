import unittest

from features.auth.policy import GUEST_DASHBOARD_PATHS, is_guest_path_allowed


class GuestAccessPolicyTests(unittest.TestCase):
    def test_guest_index_contains_only_three_dashboards(self):
        self.assertEqual(
            GUEST_DASHBOARD_PATHS,
            frozenset({
                "/quant/spot_perp_cvd/dashboard",
                "/quant/oi_accel_breakout_v2/dashboard",
                "/quant/polymarket/fade/dashboard",
            }),
        )

    def test_guest_can_open_root_and_dashboard_data(self):
        allowed = (
            "/",
            "/api/site-index",
            "/api/market-stream",
            "/auth/ctrader/positions",
            "/quant/spot_perp_cvd/dashboard",
            "/quant/spot_perp_cvd/realtime_state",
            "/quant/spot_perp_cvd/forward_test/stats",
            "/quant/oi_accel_breakout_v2/dashboard",
            "/quant/oi_accel_breakout_v2/klines",
            "/quant/polymarket/fade/dashboard",
            "/quant/polymarket/fade/watchlist",
            "/quant/polymarket/fade/market/detail",
        )
        for path in allowed:
            with self.subTest(path=path):
                self.assertTrue(is_guest_path_allowed(path))

    def test_guest_cannot_open_other_pages_or_signal_logs(self):
        denied = (
            "/quant/eth_cvd_explosion_v2/dashboard",
            "/quant/polymarket/dashboard",
            "/quant/polymarket/analytics/dashboard",
            "/quant/spot_perp_cvd/signal/logs/view",
            "/quant/oi_accel_breakout_v2/signal/logs/view",
            "/quant/spot_perp_cvd/forward_test/trades/view",
            "/admin/access",
            "/docs",
            "/openapi.json",
        )
        for path in denied:
            with self.subTest(path=path):
                self.assertFalse(is_guest_path_allowed(path))

    def test_similar_prefixes_do_not_bypass_policy(self):
        denied = (
            "/quant/spot_perp_cvd_evil/dashboard",
            "/quant/polymarket/fadeaway/dashboard",
            "/api/market-stream/private",
        )
        for path in denied:
            with self.subTest(path=path):
                self.assertFalse(is_guest_path_allowed(path))


if __name__ == "__main__":
    unittest.main()
