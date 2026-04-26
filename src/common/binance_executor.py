"""
Binance Futures Executor — v2 전략 자동 주문 실행기.

환경변수:
    BINANCE_API_KEY      Binance Futures API 키
    BINANCE_API_SECRET   Binance Futures API 시크릿
    BINANCE_TESTNET      "true" (기본) | "false" — 실계좌 전환 시 false

레버리지: 1x 고정 (코드로 강제, 변경 불가)
마진 타입: ISOLATED (안전)
포지션 크기: 잔고의 95% (수수료 버퍼 5%)
주문 타입: 시장가(MARKET)

사용법:
    from app.common.binance_executor import get_executor
    ex = get_executor()
    if ex:
        await ex.open_position("BTCUSDT", "long")
        await ex.close_position("BTCUSDT", "long")
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import math
import os
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx

# ── 설정 ────────────────────────────────────────────────────────────────────

LEVERAGE       = 1       # 안전한 fallback — strategies_master.yaml binance_leverage 로 오버라이드됨
MARGIN_TYPE    = "ISOLATED"
BALANCE_RATIO  = 0.95    # 잔고의 95% 사용 (수수료 버퍼)
REQUEST_TIMEOUT = 10     # 초

LIVE_URL     = "https://fapi.binance.com"
TESTNET_URL  = "https://testnet.binancefuture.com"


class BinanceExecutor:
    """
    Binance USDS-Margined Futures 주문 실행기.

    - 레버리지 1x 강제
    - open_position(): 시장가 진입
    - close_position(): reduceOnly 시장가 청산
    """

    def __init__(self) -> None:
        self._key    = os.environ.get("BINANCE_API_KEY", "").strip()
        self._secret = os.environ.get("BINANCE_API_SECRET", "").strip()
        testnet_env  = os.environ.get("BINANCE_TESTNET", "true").strip().lower()
        self._testnet = testnet_env != "false"
        self._base   = TESTNET_URL if self._testnet else LIVE_URL
        self._proxy  = os.environ.get("QUOTAGUARDSTATIC_URL") or None
        self._step_cache: Dict[str, float] = {}   # symbol → stepSize

        mode = "TESTNET" if self._testnet else "LIVE"
        proxy_info = f", proxy={self._proxy}" if self._proxy else ""

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────

    def _sign(self, params: dict) -> str:
        """HMAC-SHA256 서명 포함 query string 반환."""
        params["timestamp"] = int(time.time() * 1000)
        qs  = urlencode(params)
        sig = hmac.new(
            self._secret.encode("utf-8"),
            qs.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return qs + f"&signature={sig}"

    def _headers(self) -> dict:
        return {"X-MBX-APIKEY": self._key}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=REQUEST_TIMEOUT, proxy=self._proxy)

    async def _get(self, path: str, params: dict = None) -> Any:
        signed = self._sign(params or {})
        url    = f"{self._base}{path}?{signed}"
        async with self._client() as client:
            r = await client.get(url, headers=self._headers())
            if r.status_code == 401:
                mode = "TESTNET" if self._testnet else "LIVE"
                raise RuntimeError(
                    f"Binance 401 — API 키/시크릿 불일치 또는 권한 없음 "
                    f"(현재 모드: {mode}, base={self._base}). "
                    "BINANCE_TESTNET / BINANCE_API_KEY / BINANCE_API_SECRET 환경변수 확인 필요."
                )
            r.raise_for_status()
            return r.json()

    async def _post(self, path: str, params: dict = None) -> Any:
        signed = self._sign(params or {})
        url    = f"{self._base}{path}"
        async with self._client() as client:
            r = await client.post(url, data=signed, headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def _delete(self, path: str, params: dict = None) -> Any:
        signed = self._sign(params or {})
        url    = f"{self._base}{path}?{signed}"
        async with self._client() as client:
            r = await client.delete(url, headers=self._headers())
            r.raise_for_status()
            return r.json()

    # ── 계좌 정보 ─────────────────────────────────────────────────────────

    async def get_market_price(self, symbol: str) -> float:
        """
        현재 거래소(testnet/live)의 심볼 마크 가격.
        open_position 수량 계산에 사용 — 항상 주문 대상 거래소 가격을 사용해야 함.
        """
        url = f"{self._base}/fapi/v1/ticker/price?symbol={symbol}"
        async with self._client() as client:
            r = await client.get(url)
            r.raise_for_status()
            return float(r.json().get("price", 0))

    async def get_usdt_balance(self) -> float:
        """사용 가능한 USDT 잔고."""
        data = await self._get("/fapi/v2/balance")
        for asset in data:
            if asset.get("asset") == "USDT":
                return float(asset.get("availableBalance", 0))
        return 0.0

    async def get_position(self, symbol: str) -> Optional[Dict]:
        """현재 Binance 상 오픈 포지션 (없으면 None)."""
        data = await self._get("/fapi/v2/positionRisk", {"symbol": symbol})
        for p in data:
            amt = float(p.get("positionAmt", 0))
            if amt != 0:
                return p
        return None

    async def get_step_size(self, symbol: str) -> float:
        """심볼 최소 주문 단위 (stepSize). 캐시됨."""
        if symbol in self._step_cache:
            return self._step_cache[symbol]
        async with self._client() as client:
            r = await client.get(f"{self._base}/fapi/v1/exchangeInfo")
            info = r.json()
        for s in info.get("symbols", []):
            if s["symbol"] == symbol:
                for f in s.get("filters", []):
                    if f["filterType"] == "LOT_SIZE":
                        step = float(f["stepSize"])
                        self._step_cache[symbol] = step
                        return step
        self._step_cache[symbol] = 0.001   # BTC fallback
        return 0.001

    def _round_qty(self, qty: float, step: float) -> float:
        """stepSize에 맞게 소수점 버림."""
        precision = max(0, round(-math.log10(step)))
        return math.floor(qty * 10**precision) / 10**precision

    # ── 레버리지 / 마진 타입 설정 ─────────────────────────────────────────

    async def _ensure_leverage(self, symbol: str, leverage: int = LEVERAGE) -> None:
        """레버리지 설정."""
        try:
            await self._post("/fapi/v1/leverage", {
                "symbol": symbol, "leverage": leverage,
            })
        except Exception:
            pass

    async def _ensure_margin_type(self, symbol: str) -> None:
        """ISOLATED 마진 타입 설정 (이미 설정됨이면 오류 무시)."""
        try:
            await self._post("/fapi/v1/marginType", {
                "symbol": symbol, "marginType": MARGIN_TYPE,
            })
        except Exception:
            pass   # "No need to change margin type" → 정상

    # ── 주문 실행 ─────────────────────────────────────────────────────────

    async def open_position(
        self,
        symbol: str,
        side: str,              # "long" | "short"
        current_price: float = 0,
        leverage: Optional[int] = None,
    ) -> Optional[Dict]:
        """
        시장가 진입.
        side="long" → BUY / side="short" → SELL
        수량 = 가용잔고 × BALANCE_RATIO × leverage / 거래소_현재가
        leverage: None이면 LEVERAGE 상수 사용. strategies_master.yaml binance_leverage 로 주입.
        current_price는 무시 — 항상 거래소(testnet/live) 가격을 직접 조회해 사용.
        """
        lev = int(leverage) if leverage is not None else LEVERAGE
        if not self._key or not self._secret:
            print("[BinanceExecutor] API 키 없음 — 주문 건너뜀")
            return None

        # 이미 포지션 있으면 건너뜀
        existing = await self.get_position(symbol)
        if existing:
            return None

        balance = await self.get_usdt_balance()
        if balance < 5:
            print(f"[BinanceExecutor] 잔고 부족: {balance:.2f} USDT")
            return None

        await self._ensure_margin_type(symbol)
        await self._ensure_leverage(symbol, lev)

        # 거래소(testnet/live) 실제 가격으로 수량 계산 (mainnet WS 가격과 다를 수 있음)
        exchange_price = await self.get_market_price(symbol)
        if exchange_price <= 0:
            print(f"[BinanceExecutor] 가격 조회 실패: {symbol}")
            return None

        step = await self.get_step_size(symbol)
        margin   = balance * BALANCE_RATIO  # 증거금으로 사용할 금액
        notional = margin * lev             # 레버리지 적용 명목가치
        raw_qty  = notional / exchange_price
        qty      = self._round_qty(raw_qty, step)

        if qty <= 0:
            print(f"[BinanceExecutor] 수량 0 — 주문 불가 (balance={balance:.2f}, price={current_price})")
            return None

        order_side = "BUY" if side == "long" else "SELL"
        params = {
            "symbol":   symbol,
            "side":     order_side,
            "type":     "MARKET",
            "quantity": qty,
        }

        mode = "TESTNET" if self._testnet else "LIVE"
        print(f"[BinanceExecutor] 📌 {order_side} {qty} {symbol} @ MARKET ({mode}, price={exchange_price:,.2f}, balance={balance:.2f} USDT, {lev}x)")
        try:
            result = await self._post("/fapi/v1/order", params)
            # Binance Futures Testnet은 마켓 주문 응답에서 avgPrice="0" 반환하는 경우가 있음.
            # exchange_price(주문 직전 조회한 가격)로 정규화해서 caller가 올바른 체결가를 쓸 수 있게 함.
            if float(result.get("avgPrice") or 0) <= 0:
                result["avgPrice"] = str(exchange_price)
            fill_price = float(result.get("avgPrice", exchange_price))
            print(f"[BinanceExecutor] ✅ 체결: {order_side} {qty} @ ${fill_price:,.2f}")
            return result
        except httpx.HTTPStatusError as e:
            print(f"[BinanceExecutor] ❌ 주문 실패: {e.response.text}")
            return None

    async def place_tp_sl(
        self,
        symbol: str,
        side: str,          # 포지션 방향 "long" | "short"
        tp: Optional[float] = None,
        sl: Optional[float] = None,
    ) -> None:
        """
        거래소 레벨 TP/SL 주문 (closePosition=true).
        서버 다운 중에도 거래소가 자동 청산 → 웹서버 불필요.
        기존 TP/SL 주문이 있으면 먼저 취소 후 재등록.
        """
        # 현재 Binance TESTNET REST 엔드포인트는 TP/SL용 Algo 주문 타입을 부분적으로만 지원하거나
        # 문서와 다르게 동작하는 케이스가 있어, 테스트넷에서는 TP/SL 주문을 생략한다.
        # (실계좌 LIVE 환경에서만 실제 TP/SL 주문을 등록한다.)
        if self._testnet:
            return

        if not self._key or not self._secret:
            return
        if tp is None and sl is None:
            return

        try:
            # 기존 TP/SL 주문 취소 (중복 방지)
            await self._cancel_open_orders(symbol)

            close_side = "SELL" if side == "long" else "BUY"

            if tp is not None and tp > 0:
                try:
                    await self._post("/fapi/v1/order", {
                        "symbol":        symbol,
                        "side":          close_side,
                        "type":          "TAKE_PROFIT_MARKET",
                        "stopPrice":     round(tp, 2),
                        "closePosition": "true",
                    })
                    print(f"[BinanceExecutor] 🎯 TP 등록: ${tp:,.2f}")
                except httpx.HTTPStatusError as e:
                    print(f"[BinanceExecutor] TP 등록 실패: {e.response.text}")
                except Exception as e:
                    print(f"[BinanceExecutor] TP 등록 예외: {e}")

            if sl is not None and sl > 0:
                try:
                    await self._post("/fapi/v1/order", {
                        "symbol":        symbol,
                        "side":          close_side,
                        "type":          "STOP_MARKET",
                        "stopPrice":     round(sl, 2),
                        "closePosition": "true",
                    })
                    print(f"[BinanceExecutor] 🛡 SL 등록: ${sl:,.2f}")
                except httpx.HTTPStatusError as e:
                    print(f"[BinanceExecutor] SL 등록 실패: {e.response.text}")
                except Exception as e:
                    print(f"[BinanceExecutor] SL 등록 예외: {e}")
        except Exception as e:
            # TP/SL 전체 실패 시에도 호출자(진입/청산/Telegram) 흐름은 끊기지 않도록 예외 삼킴
            print(f"[BinanceExecutor] place_tp_sl 예외(무시): {e}")

    async def cancel_tp_sl(self, symbol: str) -> None:
        """청산 시 미체결 TP/SL 주문 정리."""
        await self._cancel_open_orders(symbol)

    async def _cancel_open_orders(self, symbol: str) -> None:
        """symbol의 미체결 주문 전체 취소 (DELETE)."""
        try:
            await self._delete("/fapi/v1/allOpenOrders", {"symbol": symbol})
        except httpx.HTTPStatusError as e:
            txt = e.response.text[:200]  # HTML 응답 방지용 truncate
            if "-2011" not in txt:
                print(f"[BinanceExecutor] 주문 취소 오류: {txt}")
        except Exception as e:
            print(f"[BinanceExecutor] 주문 취소 오류: {e}")

    async def close_position(
        self,
        symbol: str,
        side: str,              # 진입 시 포지션 방향 "long" | "short"
    ) -> Optional[Dict]:
        """
        시장가 청산 (reduceOnly). 절대 실패하면 안 됨 — 수량 stepSize 반올림, 재시도, 전체 예외 처리.
        Binance에서 실제 포지션 수량을 읽어 정확히 청산.
        """
        if not self._key or not self._secret:
            print("[BinanceExecutor] ❌ 청산 불가: API 키 없음")
            return None

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            pos = await self.get_position(symbol)
            if not pos:
                return None

            pos_amt = float(pos.get("positionAmt", 0))
            raw_qty = abs(pos_amt)
            if raw_qty == 0:
                return None

            step = await self.get_step_size(symbol)
            qty = self._round_qty(raw_qty, step)
            if qty <= 0:
                print(f"[BinanceExecutor] ❌ 청산 수량 0 (raw={raw_qty}, step={step}) — LOT_SIZE 불일치")
                return None

            close_side = "SELL" if pos_amt > 0 else "BUY"
            params = {
                "symbol":     symbol,
                "side":       close_side,
                "type":       "MARKET",
                "quantity":   qty,
                "reduceOnly": "true",
            }

            print(f"[BinanceExecutor] 🔔 청산 {close_side} {qty} {symbol} @ MARKET (reduceOnly) 시도 {attempt}/{max_attempts}")
            try:
                result = await self._post("/fapi/v1/order", params)
                # Testnet avgPrice="0" 대비: 현재 마크 가격으로 정규화
                if float(result.get("avgPrice") or 0) <= 0:
                    mark_price = await self.get_market_price(symbol)
                    if mark_price > 0:
                        result["avgPrice"] = str(mark_price)
                fill_price = float(result.get("avgPrice") or 0)
                print(f"[BinanceExecutor] ✅ 청산 체결 @ ${fill_price:,.2f}")
                await self._cancel_open_orders(symbol)
                return result
            except httpx.HTTPStatusError as e:
                txt = (e.response.text or "")[:300]
                # 재시도 불가: 인증/권한/정밀도 오류
                if "-1111" in txt or "-2010" in txt or "-2011" in txt or "-401" in txt or "-403" in txt:
                    print(f"[BinanceExecutor] ❌ 청산 실패 (재시도 안 함): {txt}")
                    return None
                print(f"[BinanceExecutor] ❌ 청산 실패 ({attempt}/{max_attempts}): {txt}")
                if attempt < max_attempts:
                    await asyncio.sleep(2.0)
                    continue
                return None
            except (httpx.TimeoutException, httpx.ConnectError, OSError) as e:
                print(f"[BinanceExecutor] ❌ 청산 네트워크 오류 ({attempt}/{max_attempts}): {e}")
                if attempt < max_attempts:
                    await asyncio.sleep(2.0)
                    continue
                return None
            except Exception as e:
                print(f"[BinanceExecutor] ❌ 청산 예외 ({attempt}/{max_attempts}): {e}")
                if attempt < max_attempts:
                    await asyncio.sleep(2.0)
                    continue
                return None

        return None


# ── 싱글톤 ──────────────────────────────────────────────────────────────────

_executor: Optional[BinanceExecutor] = None


def get_executor() -> Optional[BinanceExecutor]:
    """
    API 키가 설정되어 있으면 BinanceExecutor 반환, 없으면 None.
    BINANCE_API_KEY 환경변수가 없으면 자동 비활성화 (forward test만 동작).
    """
    global _executor
    if _executor is None:
        key = os.environ.get("BINANCE_API_KEY", "").strip()
        if not key:
            return None
        _executor = BinanceExecutor()
    return _executor
