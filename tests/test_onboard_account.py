import asyncio
import contextlib
import importlib.util
import io
import os
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "ctrader" / "onboard_account.py"

CONFIG = {
    "ctrader_account_id": 49204299,
    "ctrader_env": "live",
    "ctrader_symbol_id": 323,
    "ctrader_units_per_lot": 100,
    "ctrader_notional_usd": 5000.0,
}

POSITION = {"positionId": 777, "volume": 10}


def _load_script():
    spec = importlib.util.spec_from_file_location("onboard_account", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    with mock.patch("asyncio.run", side_effect=lambda coro: coro.close()):
        spec.loader.exec_module(module)
    return module


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


class OnboardAccountScriptTests(unittest.TestCase):
    def _run(self, scenario):
        module = _load_script()
        self.executor = FakeExecutor(scenario["snapshots"])
        self.get_executor = mock.Mock(return_value=self.executor)
        self.out = io.StringIO()
        infos = [_info(1000000, scenario["symbol"]), _info(999950, scenario["symbol"])]
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, "argv", ["onboard_account.py", "spot_perp_cvd"]))
            stack.enter_context(mock.patch.dict(os.environ, {"CTRADER_CLIENT_ID": "cid", "CTRADER_CLIENT_SECRET": "secret"}))
            stack.enter_context(mock.patch("features.strategy.common.config_loader.get_ctrader_config", return_value=scenario["config"]))
            stack.enter_context(mock.patch("features.strategy.common.config_loader.get_master_config", return_value={"spot_perp_cvd": {"symbol": "ETHUSDT"}}))
            stack.enter_context(mock.patch("common.ctrader_token_store.get_tokens", return_value=scenario["tokens"]))
            stack.enter_context(mock.patch("common.ctrader_executor.get_executor_unavailable_reason", return_value=scenario["reason"]))
            stack.enter_context(mock.patch("common.ctrader_executor.get_executor", self.get_executor))
            stack.enter_context(mock.patch("features.ctrader.router._market_price", mock.AsyncMock(return_value=scenario["price"])))
            stack.enter_context(mock.patch.object(module, "_fetch_trader_and_symbol", side_effect=infos))
            stack.enter_context(mock.patch("asyncio.sleep", mock.AsyncMock()))
            stack.enter_context(contextlib.redirect_stdout(self.out))
            asyncio.run(module.main())

    def test_round_trip_uses_strategy_config_and_min_volume(self):
        self._run(BASE)
        self.get_executor.assert_called_once_with(
            account_id=49204299, env="live", symbol_id=323, units_per_lot=100, notional_usd=5000.0,
        )
        self.assertEqual(self.executor.opened, [{"symbol": "", "side": "long", "volume": 10}])
        self.assertEqual(self.executor.closed, [{"position_id": 777, "volume": 10}])
        self.assertIn("volume=200 lots=2.0", self.out.getvalue())
        self.assertIn("차이=$-0.50", self.out.getvalue())

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
        module = _load_script()
        balance = module._balance({"trader": SimpleNamespace(balance=1234567, moneyDigits=2)})
        self.assertEqual(balance, 12345.67)

    def test_open_positions_raises_when_executor_returns_none(self):
        module = _load_script()
        executor = mock.Mock()
        executor.get_position = mock.AsyncMock(return_value=None)
        with self.assertRaisesRegex(RuntimeError, "오픈 포지션 조회 실패"):
            asyncio.run(module._open_positions(executor))


if __name__ == "__main__":
    unittest.main()
