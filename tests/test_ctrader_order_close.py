import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.ctrader.router import router

PARAMS = {"strategy": "spot_perp_cvd", "side": "long", "symbol": "ETHUSDT"}

CFG = {
    "ctrader_account_id": 49204299,
    "ctrader_env": "live",
    "ctrader_symbol_id": 323,
    "ctrader_units_per_lot": 100,
}

CHECK = {"position_id": 15313997, "match": True, "units_per_lot_measured": 100.0}


class OrderCloseTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def _post(self, executor_for, verify):
        self.verify = verify
        with mock.patch("features.ctrader.router._executor_for", executor_for), \
                mock.patch("common.ctrader_deals.verify_position", self.verify):
            response = self.client.post("/auth/ctrader/order/close", params=PARAMS)
        return response

    def test_no_executor_returns_503_with_reason(self):
        executor_for = mock.Mock(return_value=(None, {}, "로컬 환경 — 차단"))
        response = self._post(executor_for, mock.AsyncMock(return_value=CHECK))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"ok": False, "error": "로컬 환경 — 차단"})

    def test_close_failure_returns_502(self):
        executor = mock.Mock()
        executor.close_position = mock.AsyncMock(return_value=None)
        response = self._post(mock.Mock(return_value=(executor, {}, None)), mock.AsyncMock(return_value=CHECK))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {"ok": False, "error": "청산 실패 — 서버 로그 확인"})

    def test_close_success_returns_result_and_check(self):
        executor = mock.Mock()
        executor.close_position = mock.AsyncMock(return_value={"positionId": 15313997, "avgPrice": 2410.5})
        executor_for = mock.Mock(return_value=(executor, CFG, None))
        response = self._post(executor_for, mock.AsyncMock(return_value=CHECK))
        executor_for.assert_called_once_with("spot_perp_cvd")
        executor.close_position.assert_awaited_once_with("ETHUSDT", "long")
        self.verify.assert_awaited_once_with(
            account_id=49204299, env="live", symbol_id=323, units_per_lot=100, position_id=15313997,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"ok": True, "side": "long", "result": {"positionId": 15313997, "avgPrice": 2410.5}, "check": CHECK},
        )

    def test_check_failure_returns_502_after_close(self):
        executor = mock.Mock()
        executor.close_position = mock.AsyncMock(return_value={"positionId": 15313997, "executionType": 2})
        verify = mock.AsyncMock(side_effect=RuntimeError("가격 변동 부족 — 판정 불가 (grossProfit=0.05)"))
        response = self._post(mock.Mock(return_value=(executor, CFG, None)), verify)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json(),
            {"ok": False, "closed": True, "error": "청산됨 — 판정 실패: 가격 변동 부족 — 판정 불가 (grossProfit=0.05)"},
        )


if __name__ == "__main__":
    unittest.main()
