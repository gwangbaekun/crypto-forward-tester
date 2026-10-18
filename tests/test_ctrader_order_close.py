import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.ctrader.router import router

PARAMS = {"strategy": "spot_perp_cvd", "side": "long", "symbol": "ETHUSDT"}


class OrderCloseTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def _post(self, executor_for):
        with mock.patch("features.ctrader.router._executor_for", executor_for):
            response = self.client.post("/auth/ctrader/order/close", params=PARAMS)
        return response

    def test_no_executor_returns_503_with_reason(self):
        executor_for = mock.Mock(return_value=(None, {}, "로컬 환경 — 차단"))
        response = self._post(executor_for)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"ok": False, "error": "로컬 환경 — 차단"})

    def test_close_failure_returns_502(self):
        executor = mock.Mock()
        executor.close_position = mock.AsyncMock(return_value=None)
        response = self._post(mock.Mock(return_value=(executor, {}, None)))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {"ok": False, "error": "청산 실패 — 서버 로그 확인"})

    def test_close_success_returns_result(self):
        executor = mock.Mock()
        executor.close_position = mock.AsyncMock(return_value={"positionId": 15313997, "avgPrice": 2410.5})
        executor_for = mock.Mock(return_value=(executor, {}, None))
        response = self._post(executor_for)
        executor_for.assert_called_once_with("spot_perp_cvd")
        executor.close_position.assert_awaited_once_with("ETHUSDT", "long")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"ok": True, "side": "long", "result": {"positionId": 15313997, "avgPrice": 2410.5}},
        )


if __name__ == "__main__":
    unittest.main()
