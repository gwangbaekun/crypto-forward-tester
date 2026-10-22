import unittest

from common.utils import render_template


class SpotPerpCvdDashboardCtraderTests(unittest.TestCase):
    def setUp(self):
        self.html = render_template(
            "spot_perp_cvd_dashboard.html",
            monitoring_start_by_default=True,
            default_symbol="ETHUSDT",
        )

    def test_strategy_is_spot_perp_cvd(self):
        self.assertIn("var STRATEGY = 'spot_perp_cvd';", self.html)

    def test_manual_order_buttons_exist(self):
        for button_id in ("ct-buy", "ct-sell", "ct-close", "ct-tbuy", "ct-tsell"):
            self.assertIn(f'id="{button_id}"', self.html)

    def test_min_test_buttons_send_min_volume(self):
        self.assertIn("ctOrder('long', 10)", self.html)
        self.assertIn("ctOrder('short', 10)", self.html)
        self.assertIn(">BUY vol 10<", self.html)
        self.assertIn(">SELL vol 10<", self.html)

    def test_order_calls_use_strategy(self):
        self.assertIn("'/auth/ctrader/order/status?strategy=' + STRATEGY", self.html)
        self.assertIn("'/auth/ctrader/order?strategy=' + STRATEGY", self.html)
        self.assertIn("'/auth/ctrader/order/close?strategy=' + STRATEGY", self.html)

    def test_result_line_wraps_inside_card(self):
        self.assertIn('<div id="ct-result" style="font-size:0.75rem;margin-top:8px;color:var(--text-secondary);overflow-wrap:anywhere;">', self.html)

    def test_close_result_shows_check(self):
        self.assertIn("(d.check.match ? '✅ ' : '❌ ') + '청산 완료 ' + JSON.stringify(d.check)", self.html)


if __name__ == "__main__":
    unittest.main()
