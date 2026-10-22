import asyncio
import os
import unittest
from unittest import mock

from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOAOrderType, ProtoOATradeSide

from common import ctrader_executor

ENV = {"CTRADER_CLIENT_ID": "cid", "CTRADER_CLIENT_SECRET": "secret"}

ETH_SPEC = {"symbol_spec": {"step_volume": 10, "max_volume": 5000}}

FILL = {"avgPrice": 2452.25, "positionId": 1}


def _executor(notional_usd, responses):
    with mock.patch.dict(os.environ, ENV), \
            mock.patch.object(ctrader_executor, "get_tokens", return_value=("access", "refresh")), \
            mock.patch.object(ctrader_executor, "_ensure_reactor", return_value=mock.Mock()):
        ex = ctrader_executor.CTraderExecutor(
            account_id=49204299, env="live", symbol_id=323, units_per_lot=100, notional_usd=notional_usd,
        )
    ex._send_and_wait = mock.Mock(side_effect=responses)
    return ex


def _sent_request(ex):
    req = ex._send_and_wait.call_args.args[0]
    return req


class OpenPositionSizingTests(unittest.TestCase):
    def test_reads_broker_spec_then_rounds_up_to_step(self):
        ex = _executor(6566.6, [ETH_SPEC, FILL])
        asyncio.run(ex.open_position("ETHUSDT", "long", 2452.25))
        spec_req = ex._send_and_wait.call_args_list[0].args[0]
        self.assertEqual(type(spec_req).__name__, "ProtoOASymbolByIdReq")
        self.assertEqual(list(spec_req.symbolId), [323])
        self.assertEqual(_sent_request(ex).volume, 270)

    def test_uses_step_from_broker(self):
        ex = _executor(6566.6, [{"symbol_spec": {"step_volume": 100, "max_volume": 100000}}, FILL])
        asyncio.run(ex.open_position("ETHUSDT", "long", 2452.25))
        self.assertEqual(_sent_request(ex).volume, 300)

    def test_spec_failure_raises_before_order(self):
        ex = _executor(6566.6, [None])
        with self.assertRaisesRegex(RuntimeError, "symbol spec 조회 실패"):
            asyncio.run(ex.open_position("ETHUSDT", "long", 2452.25))
        self.assertEqual(ex._send_and_wait.call_count, 1)

    def test_above_broker_max_raises_before_order(self):
        ex = _executor(200000, [ETH_SPEC])
        with self.assertRaisesRegex(ValueError, "maxVolume=5000"):
            asyncio.run(ex.open_position("ETHUSDT", "long", 2452.25))
        self.assertEqual(ex._send_and_wait.call_count, 1)

    def test_explicit_volume_is_sent_as_is(self):
        ex = _executor(6566.6, [FILL])
        asyncio.run(ex.open_position("ETHUSDT", "long", 2452.25, volume=10))
        self.assertEqual(ex._send_and_wait.call_count, 1)
        self.assertEqual(_sent_request(ex).volume, 10)

    def test_without_notional_uses_lot_size(self):
        ex = _executor(None, [FILL])
        asyncio.run(ex.open_position("ETHUSDT", "long", 2452.25))
        self.assertEqual(_sent_request(ex).volume, 1)

    def test_order_fields(self):
        ex = _executor(6566.6, [ETH_SPEC, FILL])
        asyncio.run(ex.open_position("ETHUSDT", "short", 2452.25))
        req = _sent_request(ex)
        self.assertEqual(req.ctidTraderAccountId, 49204299)
        self.assertEqual(req.symbolId, 323)
        self.assertEqual(req.orderType, ProtoOAOrderType.MARKET)
        self.assertEqual(req.tradeSide, ProtoOATradeSide.SELL)

    def test_not_ready_sends_nothing(self):
        with mock.patch.dict(os.environ, {"CTRADER_CLIENT_ID": "", "CTRADER_CLIENT_SECRET": ""}), \
                mock.patch.object(ctrader_executor, "get_tokens", return_value=("access", "refresh")), \
                mock.patch.object(ctrader_executor, "_ensure_reactor", return_value=mock.Mock()):
            ex = ctrader_executor.CTraderExecutor(
                account_id=49204299, env="live", symbol_id=323, units_per_lot=100, notional_usd=6566.6,
            )
        ex._send_and_wait = mock.Mock()
        result = asyncio.run(ex.open_position("ETHUSDT", "long", 2452.25))
        self.assertIsNone(result)
        ex._send_and_wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
