import asyncio
import os
import unittest
from unittest import mock

from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOADeal, ProtoOATradeSide

from common import ctrader_deals

POSITION_ID = 15313997


def _deal(position_id, side, price, commission, close=None):
    deal = ProtoOADeal()
    deal.positionId = position_id
    deal.tradeSide = side
    deal.executionPrice = price
    deal.volume = 10
    deal.filledVolume = 10
    deal.commission = commission
    deal.moneyDigits = 2
    if close is not None:
        detail = deal.closePositionDetail
        detail.entryPrice = close["entry"]
        detail.grossProfit = close["gross"]
        detail.swap = close["swap"]
        detail.commission = commission
        detail.balance = 0
        detail.closedVolume = 10
        detail.moneyDigits = 2
    return deal


def _round_trip(open_side, entry, exit_price, gross, swap=0):
    close_side = ProtoOATradeSide.SELL if open_side == ProtoOATradeSide.BUY else ProtoOATradeSide.BUY
    deals = [
        _deal(POSITION_ID, open_side, entry, -8),
        _deal(POSITION_ID, close_side, exit_price, -8, close={"entry": entry, "gross": gross, "swap": swap}),
    ]
    return deals


class VerifyRoundTripTests(unittest.TestCase):
    def test_long_match(self):
        deals = _round_trip(ProtoOATradeSide.BUY, 2400.75, 2410.75, 100)
        result = ctrader_deals.verify_round_trip(deals, POSITION_ID, 100)
        self.assertTrue(result["match"])
        self.assertEqual(result["gross_profit"], 1.0)
        self.assertEqual(result["expected_gross_profit"], 1.0)
        self.assertEqual(result["units_per_lot_measured"], 100.0)
        self.assertEqual(result["commission"], -0.16)
        self.assertEqual(result["swap"], 0.0)

    def test_short_match(self):
        deals = _round_trip(ProtoOATradeSide.SELL, 2410.75, 2400.75, 100)
        result = ctrader_deals.verify_round_trip(deals, POSITION_ID, 100)
        self.assertTrue(result["match"])
        self.assertEqual(result["units_per_lot_measured"], 100.0)

    def test_one_cent_off_still_matches(self):
        deals = _round_trip(ProtoOATradeSide.BUY, 2400.75, 2410.75, 101)
        result = ctrader_deals.verify_round_trip(deals, POSITION_ID, 100)
        self.assertTrue(result["match"])

    def test_two_cents_off_is_mismatch(self):
        deals = _round_trip(ProtoOATradeSide.BUY, 2400.75, 2410.75, 102)
        result = ctrader_deals.verify_round_trip(deals, POSITION_ID, 100)
        self.assertFalse(result["match"])

    def test_wrong_units_per_lot_is_mismatch_with_measured_value(self):
        deals = _round_trip(ProtoOATradeSide.BUY, 2400.75, 2410.75, 1000)
        result = ctrader_deals.verify_round_trip(deals, POSITION_ID, 100)
        self.assertFalse(result["match"])
        self.assertEqual(result["units_per_lot_measured"], 10.0)

    def test_small_gross_profit_raises(self):
        deals = _round_trip(ProtoOATradeSide.BUY, 2400.75, 2401.25, 5)
        with self.assertRaisesRegex(RuntimeError, "판정 불가"):
            ctrader_deals.verify_round_trip(deals, POSITION_ID, 100)

    def test_missing_close_deal_raises(self):
        deals = [_deal(POSITION_ID, ProtoOATradeSide.BUY, 2400.75, -8)]
        with self.assertRaisesRegex(RuntimeError, "진입 1개, 청산 0개"):
            ctrader_deals.verify_round_trip(deals, POSITION_ID, 100)

    def test_other_positions_are_ignored(self):
        deals = _round_trip(ProtoOATradeSide.BUY, 2400.75, 2410.75, 100)
        deals.append(_deal(999, ProtoOATradeSide.BUY, 2000.0, -50))
        result = ctrader_deals.verify_round_trip(deals, POSITION_ID, 100)
        self.assertTrue(result["match"])
        self.assertEqual(result["commission"], -0.16)


class VerifyPositionTests(unittest.TestCase):
    def _run(self, fetched):
        self.fetch = mock.Mock(return_value=fetched)
        env = {"CTRADER_CLIENT_ID": "cid", "CTRADER_CLIENT_SECRET": "secret"}
        with mock.patch.dict(os.environ, env), \
                mock.patch("common.ctrader_token_store.get_tokens", return_value=("access", "refresh")), \
                mock.patch.object(ctrader_deals, "_fetch_symbol_and_deals", self.fetch):
            result = asyncio.run(ctrader_deals.verify_position(
                account_id=49204299, env="live", symbol_id=323, units_per_lot=100, position_id=POSITION_ID,
            ))
        return result

    def test_fetches_deals_for_account_and_verifies(self):
        deals = _round_trip(ProtoOATradeSide.BUY, 2400.75, 2410.75, 100)
        result = self._run({"symbol": None, "deals": deals, "hasMore": False})
        args = self.fetch.call_args.args
        self.assertEqual(args[:6], (49204299, 323, True, "cid", "secret", "access"))
        self.assertEqual(args[7] - args[6], ctrader_deals.DEAL_WINDOW_MS)
        self.assertTrue(result["match"])
        self.assertEqual(result["position_id"], POSITION_ID)

    def test_has_more_raises(self):
        deals = _round_trip(ProtoOATradeSide.BUY, 2400.75, 2410.75, 100)
        with self.assertRaisesRegex(RuntimeError, "hasMore"):
            self._run({"symbol": None, "deals": deals, "hasMore": True})


if __name__ == "__main__":
    unittest.main()
