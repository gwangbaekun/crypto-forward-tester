"""
빗썸 Public 시세 클라이언트 (인증 불필요).

Bithumb API 2.0 = Upbit 호환 스키마.
  - 마켓 목록: GET /v1/market/all
  - 분/일 캔들: GET /v1/candles/minutes/{unit}, /v1/candles/days
  - market 포맷: "KRW-BTC", 응답은 최신순(desc) 최대 200개/요청, `to`로 과거 페이지네이션.

PHASE 0~1 리서치 전용. 키·IP화이트리스트·bridge 전부 불필요(전부 public).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd
import requests

BASE = "https://api.bithumb.com"
_SESSION = requests.Session()
_SESSION.headers.update({"accept": "application/json"})

# 빗썸 REST 예의상 간격(초). 과도호출 차단 회피.
_MIN_INTERVAL = 0.12
_last_call = 0.0


def _get(path: str, params: dict | None = None) -> list | dict:
    global _last_call
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    r = _SESSION.get(f"{BASE}{path}", params=params, timeout=15)
    _last_call = time.monotonic()
    r.raise_for_status()
    return r.json()


def get_krw_markets(include_details: bool = True) -> pd.DataFrame:
    """KRW 마켓 전체 목록. columns: market, korean_name, english_name, [market_warning]."""
    data = _get("/v1/market/all", {"isDetails": str(include_details).lower()})
    df = pd.DataFrame(data)
    return df[df["market"].str.startswith("KRW-")].reset_index(drop=True)


def get_candles(
    market: str,
    unit: str = "days",
    count: int = 200,
    to: str | datetime | None = None,
) -> pd.DataFrame:
    """
    캔들 1페이지(최대 200). unit: "days" | "minutes/1" | "minutes/5" | ...
    to: 이 시각 '이전'까지 (KST naive 문자열 "YYYY-MM-DD HH:MM:SS" 또는 UTC datetime).
    반환: 시간 오름차순 DataFrame (open_time[UTC], open, high, low, close, volume, turnover).
    """
    if unit == "days":
        path = "/v1/candles/days"
    else:
        path = f"/v1/candles/{unit}"  # e.g. "minutes/5"
    params: dict = {"market": market, "count": min(count, 200)}
    if to is not None:
        if isinstance(to, datetime):
            to = to.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        params["to"] = to
    data = _get(path, params)
    if not data:
        return pd.DataFrame(
            columns=["open_time", "open", "high", "low", "close", "volume", "turnover"]
        )
    df = pd.DataFrame(data)
    out = pd.DataFrame(
        {
            "open_time": pd.to_datetime(df["candle_date_time_utc"], utc=True),
            "open": df["opening_price"].astype(float),
            "high": df["high_price"].astype(float),
            "low": df["low_price"].astype(float),
            "close": df["trade_price"].astype(float),
            "volume": df["candle_acc_trade_volume"].astype(float),
            "turnover": df["candle_acc_trade_price"].astype(float),
        }
    )
    return out.sort_values("open_time").reset_index(drop=True)


def get_all_candles(
    market: str,
    unit: str = "days",
    max_pages: int = 60,
) -> pd.DataFrame:
    """
    `to` 페이지네이션으로 상장 시점까지 전체 캔들을 긁는다.
    페이지가 200개 미만이면 더 이상 과거가 없다는 뜻 → 종료.
    """
    frames: list[pd.DataFrame] = []
    to: str | datetime | None = None
    for _ in range(max_pages):
        page = get_candles(market, unit=unit, count=200, to=to)
        if page.empty:
            break
        frames.append(page)
        if len(page) < 200:
            break  # 상장 시점 도달
        # 다음 페이지는 이 페이지 가장 오래된 봉 '이전'
        to = page["open_time"].iloc[0].to_pydatetime()
    if not frames:
        return pd.DataFrame()
    full = pd.concat(frames).drop_duplicates("open_time").sort_values("open_time")
    return full.reset_index(drop=True)


if __name__ == "__main__":
    # PHASE 0 스모크 테스트: 데이터가 실제로 들어오는지 눈으로 확인
    mkts = get_krw_markets()
    print(f"[markets] KRW 마켓 수: {len(mkts)}")
    print(mkts.head(3).to_string(index=False))

    print("\n[candles] KRW-BTC 최근 일봉 5개:")
    btc = get_candles("KRW-BTC", unit="days", count=5)
    print(btc.to_string(index=False))
