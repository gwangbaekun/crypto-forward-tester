"""
cTrader Executor (FTMO/Open API) - skeleton.

주의:
    이 파일은 안전한 초기 껍데기다. 실제 주문 API 호출은 아직 구현하지 않았다.
    환경변수가 있어도 live 주문은 실행하지 않고 로그만 남긴다.

환경변수(예상):
    CTRADER_CLIENT_ID
    CTRADER_CLIENT_SECRET
    CTRADER_REDIRECT_URI
    CTRADER_ACCESS_TOKEN
    CTRADER_REFRESH_TOKEN
    CTRADER_ACCOUNT_ID
    CTRADER_ENV             "demo" | "live" (기본 demo)
"""
from __future__ import annotations

import os
from typing import Dict, Optional


class CTraderExecutor:
    """
    cTrader 주문 실행기 인터페이스.

    Base realtime flow와 동일하게 아래 메서드 시그니처를 유지한다:
      - open_position(symbol, side, current_price, leverage)
      - close_position(symbol, side)
      - place_tp_sl(symbol, side, tp, sl)
      - cancel_tp_sl(symbol)
      - get_position(symbol)
      - get_market_price(symbol)
    """

    def __init__(self) -> None:
        self._client_id = os.environ.get("CTRADER_CLIENT_ID", "").strip()
        self._client_secret = os.environ.get("CTRADER_CLIENT_SECRET", "").strip()
        self._redirect_uri = os.environ.get("CTRADER_REDIRECT_URI", "").strip()
        self._access_token = os.environ.get("CTRADER_ACCESS_TOKEN", "").strip()
        self._refresh_token = os.environ.get("CTRADER_REFRESH_TOKEN", "").strip()
        self._account_id = os.environ.get("CTRADER_ACCOUNT_ID", "").strip()
        self._env = os.environ.get("CTRADER_ENV", "demo").strip().lower() or "demo"
        self._is_live = self._env == "live"

    def _ready(self) -> bool:
        return bool(self._access_token and self._account_id)

    async def get_market_price(self, symbol: str) -> float:
        # TODO: cTrader Open API 시세 조회로 교체
        return 0.0

    async def get_position(self, symbol: str) -> Optional[Dict]:
        # TODO: cTrader Open API 포지션 조회로 교체
        return None

    async def open_position(
        self,
        symbol: str,
        side: str,  # "long" | "short"
        current_price: float = 0,
        leverage: Optional[int] = None,
    ) -> Optional[Dict]:
        if not self._ready():
            return None
        # TODO: 실제 cTrader 신규 포지션 주문 구현
        return None

    async def close_position(
        self,
        symbol: str,
        side: str,  # "long" | "short"
    ) -> Optional[Dict]:
        if not self._ready():
            return None
        # TODO: 실제 cTrader 포지션 청산 구현
        return None

    async def place_tp_sl(
        self,
        symbol: str,
        side: str,
        tp: Optional[float] = None,
        sl: Optional[float] = None,
    ) -> None:
        if not self._ready():
            return
        # TODO: 실제 cTrader TP/SL 수정 구현
        pass

    async def cancel_tp_sl(self, symbol: str) -> None:
        if not self._ready():
            return
        # TODO: 실제 cTrader 미체결 보호주문 취소 구현
        pass


_executor: Optional[CTraderExecutor] = None


def get_executor() -> Optional[CTraderExecutor]:
    """
    cTrader executor 싱글톤.
    access_token/account_id가 없으면 None 반환해서 안전하게 비활성화한다.
    """
    global _executor
    if _executor is None:
        token = os.environ.get("CTRADER_ACCESS_TOKEN", "").strip()
        account_id = os.environ.get("CTRADER_ACCOUNT_ID", "").strip()
        if not token or not account_id:
            return None
        _executor = CTraderExecutor()
    return _executor
