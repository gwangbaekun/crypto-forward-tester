import asyncio
import contextlib
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from common import ctrader_onboard
from features.ctrader.router import router

CONFIG = {
    "ctrader_account_id": 49204299,
    "ctrader_env": "live",
    "ctrader_symbol_id": 323,
    "ctrader_units_per_lot": 100,
    "ctrader_notional_usd": 5000.0,
}

POSITION = {"positionId": 777, "volume": 10}


def _symbol(min_volume, max_volume):
    symbol = SimpleNamespace(
        lotSize=100, minVolume=min_volume, stepVolume=10, maxVolume=max_volume,
        commission=0, commissionType=0, swapLong=0, swapShort=0, swapCalculationType=0,
    )
    return symbol


def _info(balance, symbol):
    info = {"trader": SimpleNamespace(balance=balance, moneyDigits=2), "symbol": symbol}
    return info


BASE = {
    "config": CONFIG,
    "snapshots": [[], [POSITION], []],
    "symbol": _symbol(10, 100000),
    "price": 2500.0,
    "tokens": ("access", "refresh"),
    "reason": None,
}


class FakeExecutor:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.opened = []
        self.closed = []

    async def get_position(self, symbol, cache_ttl):
        positions = self.snapshots.pop(0)
        result = {"positions": positions}
        return result

    async def open_position(self, symbol, side, volume):
        self.opened.append({"symbol": symbol, "side": side, "volume": volume})
        result = {"ok": True}
        return result

    async def close_position_by_id(self, position_id, volume):
        self.closed.append({"position_id": position_id, "volume": volume})
        result = {"ok": True}
        return result


class OnboardAccountTests(unittest.TestCase):
    def _run(self, scenario):
        self.executor = FakeExecutor(scenario["snapshots"])
        self.get_executor = mock.Mock(return_value=self.executor)
        infos = [_info(1000000, scenario["symbol"]), _info(999950, scenario["symbol"])]
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {"CTRADER_CLIENT_ID": "cid", "CTRADER_CLIENT_SECRET": "secret"}))
            stack.enter_context(mock.patch("features.strategy.common.config_loader.get_ctrader_config", return_value=scenario["config"]))
            stack.enter_context(mock.patch("features.strategy.common.config_loader.get_master_config", return_value={"spot_perp_cvd": {"symbol": "ETHUSDT"}}))
            stack.enter_context(mock.patch("common.ctrader_token_store.get_tokens", return_value=scenario["tokens"]))
            stack.enter_context(mock.patch("common.ctrader_executor.get_executor_unavailable_reason", return_value=scenario["reason"]))
            stack.enter_context(mock.patch("common.ctrader_executor.get_executor", self.get_executor))
            stack.enter_context(mock.patch("features.ctrader.router._market_price", mock.AsyncMock(return_value=scenario["price"])))
            stack.enter_context(mock.patch.object(ctrader_onboard, "_fetch_trader_and_symbol", side_effect=infos))
            stack.enter_context(mock.patch("asyncio.sleep", mock.AsyncMock()))
            self.result = asyncio.run(ctrader_onboard.run_onboard("spot_perp_cvd"))

    def test_round_trip_uses_strategy_config_and_min_volume(self):
        self._run(BASE)
        self.get_executor.assert_called_once_with(
            account_id=49204299, env="live", symbol_id=323, units_per_lot=100, notional_usd=5000.0,
        )
        self.assertEqual(self.executor.opened, [{"symbol": "", "side": "long", "volume": 10}])
        self.assertEqual(self.executor.closed, [{"position_id": 777, "volume": 10}])
        self.assertEqual(self.result["volume"], 200)
        self.assertEqual(self.result["lots"], 2.0)
        self.assertEqual(self.result["symbol_spec"]["lotSize"], 100)
        self.assertEqual(self.result["position"], POSITION)
        self.assertEqual(self.result["balance_before"], 10000.0)
        self.assertEqual(self.result["balance_after"], 9999.5)

    def test_unavailable_reason_raises_before_executor(self):
        with self.assertRaisesRegex(RuntimeError, "local connect blocked"):
            self._run(dict(BASE, reason="local connect blocked"))
        self.get_executor.assert_not_called()

    def test_missing_access_token_raises_before_executor(self):
        with self.assertRaisesRegex(RuntimeError, "access token 없음"):
            self._run(dict(BASE, tokens=("", "")))
        self.get_executor.assert_not_called()

    def test_existing_open_position_blocks_order(self):
        with self.assertRaisesRegex(RuntimeError, "진입 전 오픈 포지션 존재"):
            self._run(dict(BASE, snapshots=[[POSITION]]))
        self.assertEqual(self.executor.opened, [])

    def test_non_positive_price_blocks_order(self):
        with self.assertRaisesRegex(RuntimeError, "현재가 조회 실패"):
            self._run(dict(BASE, price=0.0, snapshots=[[]]))
        self.assertEqual(self.executor.opened, [])

    def test_missing_notional_raises_before_order(self):
        config = dict(CONFIG, ctrader_notional_usd=None)
        with self.assertRaises(TypeError):
            self._run(dict(BASE, config=config, snapshots=[[]]))
        self.assertEqual(self.executor.opened, [])

    def test_volume_below_min_volume_blocks_order(self):
        with self.assertRaisesRegex(RuntimeError, "minVolume=1000"):
            self._run(dict(BASE, symbol=_symbol(1000, 100000), snapshots=[[]]))
        self.assertEqual(self.executor.opened, [])

    def test_missing_position_after_open_raises(self):
        with self.assertRaisesRegex(RuntimeError, "진입 후 오픈 포지션이 1개가 아님"):
            self._run(dict(BASE, snapshots=[[], []]))
        self.assertEqual(self.executor.closed, [])

    def test_position_left_after_close_raises(self):
        with self.assertRaisesRegex(RuntimeError, "청산 후 오픈 포지션 남음"):
            self._run(dict(BASE, snapshots=[[], [POSITION], [POSITION]]))

    def test_balance_divides_by_money_digits(self):
        balance = ctrader_onboard._balance({"trader": SimpleNamespace(balance=1234567, moneyDigits=2)})
        self.assertEqual(balance, 12345.67)

    def test_open_positions_raises_when_executor_returns_none(self):
        executor = mock.Mock()
        executor.get_position = mock.AsyncMock(return_value=None)
        with self.assertRaisesRegex(RuntimeError, "오픈 포지션 조회 실패"):
            asyncio.run(ctrader_onboard._open_positions(executor))


class OnboardRouterTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def test_onboard_returns_run_result(self):
        run = mock.AsyncMock(return_value={"volume": 200, "lots": 2.0})
        with mock.patch("common.ctrader_onboard.run_onboard", run):
            response = self.client.post("/auth/ctrader/onboard", params={"strategy": "spot_perp_cvd"})
        run.assert_awaited_once_with("spot_perp_cvd")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "volume": 200, "lots": 2.0})

    def test_onboard_returns_error_message_on_runtime_error(self):
        run = mock.AsyncMock(side_effect=RuntimeError("진입 전 오픈 포지션 존재: []"))
        with mock.patch("common.ctrader_onboard.run_onboard", run):
            response = self.client.post("/auth/ctrader/onboard", params={"strategy": "spot_perp_cvd"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {"ok": False, "error": "진입 전 오픈 포지션 존재: []"})

    def test_onboard_rejects_get(self):
        response = self.client.get("/auth/ctrader/onboard", params={"strategy": "spot_perp_cvd"})
        self.assertEqual(response.status_code, 405)


if __name__ == "__main__":
    unittest.main()
