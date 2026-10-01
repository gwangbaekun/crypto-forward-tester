import datetime as dt
import unittest

import pandas as pd

from features.collectors.retention import prepare_pending


class PendingSnapshotTests(unittest.TestCase):
    def frame(self):
        snapshot = dt.datetime(2026, 10, 2, 0, 30, tzinfo=dt.timezone.utc)
        rows = [
            ["SPY", "near", snapshot, "2026-10-01 15:59:00", dt.date(2026, 10, 2), 600.0, "C", 10.0, 0.1, 590.0],
            ["SPY", "far", snapshot, "2026-10-01 16:00:00", dt.date(2026, 12, 18), 600.0, "C", 10.0, 0.1, 590.0],
            ["SPY", "inactive", snapshot, "2026-10-02 01:00:00", dt.date(2026, 10, 2), 600.0, "C", 0.0, 0.1, 590.0],
            ["QQQ", "other", snapshot, "2026-10-02 01:00:00", dt.date(2026, 10, 2), 600.0, "C", 10.0, 0.1, 590.0],
        ]
        frame = pd.DataFrame(rows, columns=[
            "underlying", "option", "snapshot_ts", "last_trade_time", "expiry",
            "strike", "option_type", "open_interest", "gamma", "underlying_price",
        ])
        return frame

    def test_session_comes_from_eligible_trades_before_expiry_filter(self):
        frame = self.frame()
        frame.loc[0, "last_trade_time"] = "2026-09-30 16:00:00"
        session, pending = prepare_pending(frame)
        self.assertEqual(session, dt.date(2026, 10, 1))
        self.assertEqual(pending["option"].tolist(), ["near"])
        self.assertEqual(pending["session"].tolist(), [session])
        self.assertNotIn("last_trade_time", pending.columns)

    def test_no_near_expiry_does_not_retain_irrelevant_contracts(self):
        frame = self.frame()
        frame.loc[0, "expiry"] = dt.date(2026, 12, 18)
        session, pending = prepare_pending(frame)
        self.assertEqual(session, dt.date(2026, 10, 1))
        self.assertTrue(pending.empty)

    def test_unidentifiable_session_fails(self):
        frame = self.frame()
        frame["last_trade_time"] = None
        with self.assertRaises(ValueError):
            prepare_pending(frame)


if __name__ == "__main__":
    unittest.main()
