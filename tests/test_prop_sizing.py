import unittest

from common.prop_sizing import order_volume, target_notional_usd


class TargetNotionalTests(unittest.TestCase):
    def test_balance_times_target_over_backtest(self):
        self.assertAlmostEqual(target_notional_usd(10000, 7, 10.66), 6566.6041, places=4)

    def test_follows_current_balance(self):
        self.assertAlmostEqual(target_notional_usd(9800, 7, 10.66), 6435.2720, places=4)

    def test_non_positive_balance_raises(self):
        with self.assertRaisesRegex(ValueError, "balance=0"):
            target_notional_usd(0, 7, 10.66)

    def test_non_positive_mdd_raises(self):
        with self.assertRaisesRegex(ValueError, "target_mdd=0"):
            target_notional_usd(10000, 0, 10.66)


class OrderVolumeTests(unittest.TestCase):
    def test_rounds_up_to_step(self):
        self.assertEqual(order_volume(6566.6, 2452.25, 100, 10, 5000), 270)

    def test_exact_multiple_stays(self):
        self.assertEqual(order_volume(6500, 2500, 100, 10, 5000), 260)

    def test_float_noise_does_not_add_a_step(self):
        self.assertEqual(order_volume(2.6 * 2452.25, 2452.25, 100, 10, 5000), 260)

    def test_tiny_notional_becomes_one_step(self):
        self.assertEqual(order_volume(100, 2452.25, 100, 10, 5000), 10)

    def test_above_max_volume_raises(self):
        with self.assertRaisesRegex(ValueError, "maxVolume=5000"):
            order_volume(200000, 2452.25, 100, 10, 5000)

    def test_non_positive_price_raises(self):
        with self.assertRaisesRegex(ValueError, "price=0"):
            order_volume(6566.6, 0, 100, 10, 5000)


if __name__ == "__main__":
    unittest.main()
