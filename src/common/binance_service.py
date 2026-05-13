import math
import time
from typing import List, Optional
import pandas as pd
import httpx

_rest_429_until: float = 0.0
_klines_ban_until: float = 0.0  # 418 IP ban / 429 rate limit


async def fetch_binance_klines(
    symbol: str,
    interval: str = "1h",
    limit: int = 500
) -> Optional[pd.DataFrame]:
    """
    Binance Futures에서 OHLCV 데이터 가져오기
    - symbol: BTCUSDT, ETHUSDT 등
    - interval: 1m, 5m, 15m, 1h, 4h, 1d 등
    - limit: 최대 1500
    """
    global _klines_ban_until
    now = time.time()
    if now < _klines_ban_until:
        remaining = int(_klines_ban_until - now)
        print(f"[binance_service] klines banned — skip ({remaining}s 남음, {symbol} {interval})")
        return None

    try:
        url = "https://fapi.binance.com/fapi/v1/klines"
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": min(limit, 1500)
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            if resp.status_code in (418, 429):
                retry_after = int(resp.headers.get("Retry-After", 0))
                cooldown = retry_after if retry_after > 0 else (300 if resp.status_code == 418 else 60)
                _klines_ban_until = time.time() + cooldown
                label = "418 IP ban" if resp.status_code == 418 else "429 rate limit"
                print(f"[binance_service] {label} ({symbol} {interval}) — {cooldown}s 대기")
                return None
            resp.raise_for_status()
            klines = resp.json()

        if not klines:
            return None
        
        # Binance klines format: [Open time, Open, High, Low, Close, Volume, ...]
        df = pd.DataFrame(klines, columns=[
            'OpenTime', 'Open', 'High', 'Low', 'Close', 'Volume',
            'CloseTime', 'QuoteVolume', 'Trades', 'TakerBuyBase', 'TakerBuyQuote', 'Ignore'
        ])
        
        # 타입 변환
        df['OpenTime'] = pd.to_datetime(df['OpenTime'], unit='ms')
        df['CloseTime'] = pd.to_datetime(df['CloseTime'], unit='ms')
        df['Open'] = df['Open'].astype(float)
        df['High'] = df['High'].astype(float)
        df['Low'] = df['Low'].astype(float)
        df['Close'] = df['Close'].astype(float)
        df['Volume'] = df['Volume'].astype(float)
        
        # 인덱스를 시간으로 설정
        df.set_index('OpenTime', inplace=True)
        
        df['TakerBuyBase'] = df['TakerBuyBase'].astype(float)

        # 필요한 컬럼만 선택
        df = df[['Open', 'High', 'Low', 'Close', 'Volume', 'TakerBuyBase']]

        return df

    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        retry_after = int(e.response.headers.get("Retry-After", 0))
        if status in (418, 429):
            cooldown = retry_after if retry_after > 0 else (300 if status == 418 else 60)
            _klines_ban_until = time.time() + cooldown
            label = "418 IP ban" if status == 418 else "429 rate limit"
            print(f"[binance_service] {label} ({symbol} {interval}) — {cooldown}s 대기")
        else:
            print(f"[binance_service] klines fetch error ({symbol} {interval}): {e}")
        return None
    except Exception as e:
        print(f"[binance_service] klines fetch error ({symbol} {interval}): {e}")
        return None


async def fetch_mark_price(symbol: str) -> Optional[float]:
    """Binance Futures mark price REST fallback (WS stale 시 사용)."""
    global _rest_429_until
    if time.time() < _rest_429_until:
        return None
    try:
        url = "https://fapi.binance.com/fapi/v1/premiumIndex"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, params={"symbol": symbol.upper()})
            resp.raise_for_status()
            return float(resp.json()["markPrice"])
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            _rest_429_until = time.time() + 30.0
            print(f"[fetch_mark_price] 429 rate limit — REST fallback 30초 중단")
        else:
            print(f"[fetch_mark_price] {symbol} REST fallback 실패: {type(e).__name__}: {e}")
        return None
    except Exception as e:
        print(f"[fetch_mark_price] {symbol} REST fallback 실패: {type(e).__name__}: {e}")
        return None


async def fetch_cvd_seed(symbol: str, limit: int = 1000) -> List[list]:
    """
    간단한 CVD 시드 데이터 생성:
    - Binance Futures aggTrades REST API에서 최근 체결 가져오기
    - buyer is maker(m) 플래그로 매수/매도 방향 구분
    - qty를 누적해 CVD 시계열 생성
    반환 형식: [[timestamp_ms, cvd], ...]
    """
    sym = symbol.upper()
    url = "https://fapi.binance.com/fapi/v1/aggTrades"
    params = {"symbol": sym, "limit": limit}

    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        trades = resp.json()

    series: List[list] = []
    cvd = 0.0
    for t in trades:
        try:
            price = float(t.get("p", 0.0))
            qty = float(t.get("q", 0.0))
            is_sell = bool(t.get("m", False))  # buyer is maker -> sell aggressive
            cvd += -qty if is_sell else qty
            ts = int(t.get("T"))  # trade time (ms)
            if not math.isfinite(cvd):
                continue
            series.append([ts, float(cvd)])
        except Exception:
            continue

    return series


async def get_open_interest(symbol: str, period: str, limit: int = 500) -> List[dict]:
    """
    Binance Futures Open Interest History 가져오기
    GET /fapi/v1/openInterestHist
    - symbol: BTCUSDT
    - period: 5m, 15m, 1h, 4h, 1d 등
    - limit: default 500, max 500
    
    Response format:
    [
      {
        "symbol": "BTCUSDT",
        "sumOpenInterest": "20403.63700000",
        "sumOpenInterestValue": "1505707841.45000000",
        "timestamp": 1583127900000
      },
      ...
    ]
    """
    url = "https://fapi.binance.com/futures/data/openInterestHist"
    params = {
        "symbol": symbol.upper(),
        "period": period,
        "limit": min(limit, 500)
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            return data
    except Exception as e:
        print(f"Failed to fetch Open Interest History for {symbol} ({period}): {e}")
        return []
