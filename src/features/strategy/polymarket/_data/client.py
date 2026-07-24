"""Polymarket async data client — Gamma API + CLOB API.

async 버전. httpx.AsyncClient 사용.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE  = "https://clob.polymarket.com"
TIMEOUT    = 15.0


def _parse_ts(iso: str | None) -> int | None:
    if not iso:
        return None
    iso = iso.rstrip("Z").split(".")[0]
    return int(datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp())


def _safe_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _normalize(m: dict, ev: dict | None = None) -> dict[str, Any]:
    """Raw Gamma market → 표준 dict."""
    clob = json.loads(m.get("clobTokenIds") or "[]") if isinstance(m.get("clobTokenIds"), str) else (m.get("clobTokenIds") or [])
    prices_raw = m.get("outcomePrices")
    prices = json.loads(prices_raw) if isinstance(prices_raw, str) else (prices_raw or [])

    y = _safe_float(prices[0]) if prices else None
    n = _safe_float(prices[1]) if len(prices) > 1 else None

    resolved_yes: bool | None = None
    if y is not None and n is not None:
        if y > 0.98 and n < 0.02:
            resolved_yes = True
        elif y < 0.02 and n > 0.98:
            resolved_yes = False

    last_price = _safe_float(m.get("lastTradePrice")) or y

    end_ts = _parse_ts(m.get("endDate") or m.get("endDateIso"))
    if end_ts is None and ev:
        end_ts = _parse_ts(ev.get("endDate"))

    vol = float(m.get("volumeNum") or m.get("volume") or 0.0)
    if vol == 0.0 and ev:
        vol = float(ev.get("volumeNum") or ev.get("volume") or 0.0)

    slug = (ev.get("slug") if ev else None) or m.get("slug")

    return {
        "condition_id":  m.get("conditionId"),
        "question":      m.get("question", "") or "",
        "slug":          slug,
        "start_ts":      _parse_ts(m.get("startDate") or m.get("startDateIso")),
        "end_ts":        end_ts,
        "volume_usd":    vol,
        "yes_token_id":  clob[0] if clob else None,
        "no_token_id":   clob[1] if len(clob) > 1 else None,
        "yes_price":     y,
        "no_price":      n,
        "last_yes_price": last_price,
        "resolved_yes":  resolved_yes,
        "is_closed":     bool(m.get("closed") or (ev.get("closed") if ev else False)),
        "best_bid":      _safe_float(m.get("bestBid")),
        "best_ask":      _safe_float(m.get("bestAsk")),
    }


async def fetch_markets(
    keyword: str,
    include_closed: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """이벤트 endpoint 경유 keyword 검색. active 마켓만 기본."""
    params: dict[str, Any] = {
        "limit": limit,
        "offset": offset,
        "order": "volume",
        "ascending": "false",
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.get(f"{GAMMA_BASE}/events", params=params)
        r.raise_for_status()
        events = r.json()

    if not isinstance(events, list):
        events = events.get("data", [])

    kw = keyword.lower()
    results: list[dict[str, Any]] = []

    for ev in events:
        title = (ev.get("title") or "").lower()
        title_match = kw in title
        is_ev_closed = bool(ev.get("closed"))

        for m in ev.get("markets", []):
            q = (m.get("question") or "").lower()
            if not title_match and kw not in q:
                continue
            if not include_closed and is_ev_closed:
                continue
            results.append(_normalize(m, ev))

    return results


_CHUNK_DAYS = 14   # prices-history startTs/endTs 구간 상한 ≈15일 (btc_backtest 실측과 동일)


async def _fetch_range(token_id: str, start_ts: int, end_ts: int, fidelity: int) -> list[dict[str, Any]]:
    """[start_ts, end_ts]를 CHUNK_DAYS 창으로 쪼개 fetch, ts 기준 dedupe 후 concat."""
    import time as _time
    end_ts = min(end_ts, int(_time.time()))
    step = _CHUNK_DAYS * 86400
    merged: dict[int, float] = {}
    t = start_ts
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        while t < end_ts:
            u = min(t + step, end_ts)
            r = await cli.get(f"{CLOB_BASE}/prices-history", params={
                "market": token_id, "startTs": t, "endTs": u, "fidelity": fidelity,
            })
            if r.status_code == 200:
                for p in (r.json() or {}).get("history", []):
                    if "t" in p and "p" in p:
                        merged[int(p["t"])] = float(p["p"])
            t = u
    return [{"ts": k, "price": merged[k]} for k in sorted(merged)]


async def fetch_curve_full(
    token_id: str, fidelity: int = 60,
    start_ts: int | None = None, end_ts: int | None = None,
) -> list[dict[str, Any]]:
    """전 기간 60분봉 (워치리스트 add 시 최초 1회 · btc_backtest fetch_curve 와 동일 방식).

    CLOB 의 interval=max 는 실측상 최근 ~720개(30일)로 캡되어 있어 오래된 마켓의
    과거 스파이크를 놓친다. start_ts/end_ts(마켓 실제 수명)를 알면 14일 창으로 쪼개
    전체 기간을 받고, 모르면 interval=max 로 fallback.
    """
    if start_ts and end_ts:
        return await _fetch_range(token_id, start_ts, end_ts, fidelity)

    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.get(f"{CLOB_BASE}/prices-history", params={
            "market": token_id, "interval": "max", "fidelity": fidelity,
        })
        if r.status_code != 200:
            return []
        data = r.json()
    history = data.get("history", []) if isinstance(data, dict) else []
    return [{"ts": int(p["t"]), "price": float(p["p"])} for p in history if "t" in p and "p" in p]


async def fetch_market_by_token(token_id: str) -> dict[str, Any] | None:
    """clob YES/NO 토큰 ID로 마켓 메타 직접 조회 (워치리스트 수동 추가용)."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.get(f"{GAMMA_BASE}/markets", params={"clob_token_ids": token_id})
        if r.status_code != 200:
            return None
        data = r.json()
    if not isinstance(data, list) or not data:
        return None
    return _normalize(data[0])


async def fetch_all_active(keywords: list[str], min_volume: float = 0) -> list[dict[str, Any]]:
    """여러 keyword 를 합쳐서 active 마켓 목록 반환. 중복 condition_id 제거."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for kw in keywords:
        for m in await fetch_markets(kw, include_closed=False, limit=200):
            cid = m["condition_id"] or m["question"]
            if cid in seen:
                continue
            seen.add(cid)
            if m["volume_usd"] >= min_volume:
                out.append(m)
    return out


async def fetch_active_events_by_keyword(
    keywords: list[str],
    min_volume: float = 0.0,
    page_limit: int = 200,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    """active(미종료) 이벤트를 volume 내림차순 페이징하며, 이벤트 title 이 keyword 를
    포함하면 그 이벤트의 **열린 자식 마켓** 전부를 반환.

    사다리형 그룹 이벤트("What price will Bitcoin hit …?")는 자식 마켓 question 에
    자산명이 없을 수 있어(예: "Will Bitcoin reach $95k…"는 있음), 이벤트 title 로 매칭한다.
    닫힌 자식 마켓·중복 condition_id 는 제외.
    """
    kws = [k.lower() for k in keywords]
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        for page in range(max_pages):
            params: dict[str, Any] = {
                "limit": page_limit,
                "offset": page * page_limit,
                "order": "volume",
                "ascending": "false",
                "active": "true",
                "closed": "false",
            }
            r = await cli.get(f"{GAMMA_BASE}/events", params=params)
            if r.status_code == 422:
                break
            r.raise_for_status()
            events = r.json()
            if not isinstance(events, list):
                events = events.get("data", [])
            if not events:
                break

            for ev in events:
                title = (ev.get("title") or "").lower()
                if not any(k in title for k in kws):
                    continue
                if bool(ev.get("closed")):
                    continue
                for m in ev.get("markets", []):
                    norm = _normalize(m, ev)
                    cid = norm["condition_id"] or norm["question"]
                    if not cid or cid in seen:
                        continue
                    if norm["is_closed"]:
                        continue
                    if norm["volume_usd"] < min_volume:
                        continue
                    seen.add(cid)
                    out.append(norm)

    return out


async def fetch_prices(token_id: str, interval: str = "1h") -> list[dict[str, Any]]:
    """CLOB 가격 히스토리 (active 마켓만 유효). Returns [{"ts": int, "price": float}]."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.get(f"{CLOB_BASE}/prices-history", params={
            "market": token_id, "interval": interval
        })
        if r.status_code != 200:
            return []
        data = r.json()
    history = data.get("history", []) if isinstance(data, dict) else []
    return [{"ts": int(p["t"]), "price": float(p["p"])} for p in history if "t" in p and "p" in p]


async def fetch_book(token_id: str) -> dict[str, Any] | None:
    """CLOB 오더북. best_ask(최저 ask), best_bid(최고 bid)와 각 size 반환.

    {"best_ask": float|None, "ask_size": float, "best_bid": float|None,
     "bid_size": float, "tick": float} 형태. 호출 실패/빈 book 이면 None.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.get(f"{CLOB_BASE}/book", params={"token_id": token_id},
                          headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None
        d = r.json()
    if not isinstance(d, dict) or "asks" not in d:
        return None
    asks = d.get("asks") or []
    bids = d.get("bids") or []
    best_ask = min(asks, key=lambda a: float(a["price"])) if asks else None
    best_bid = max(bids, key=lambda b: float(b["price"])) if bids else None
    return {
        "best_ask": float(best_ask["price"]) if best_ask else None,
        "ask_size": float(best_ask["size"]) if best_ask else 0.0,
        "best_bid": float(best_bid["price"]) if best_bid else None,
        "bid_size": float(best_bid["size"]) if best_bid else 0.0,
        "tick":     float(d.get("tick_size") or 0.01),
    }


async def fetch_current_price(token_id: str) -> float | None:
    """토큰 현재가. 없으면 None."""
    hist = await fetch_prices(token_id, interval="1h")
    if not hist:
        return None
    return max(hist, key=lambda p: p["ts"])["price"]


async def fetch_by_expiry(
    max_hours: float = 72.0,
    min_volume: float = 0.0,
    page_limit: int = 200,
    max_pages: int = 30,
) -> list[dict[str, Any]]:
    """종료 임박순 전체 마켓 스캔.

    active 이벤트를 endDate ascending 으로 페이징하며
    end_ts ∈ (now, now + max_hours) 인 마켓만 반환.
    min_volume 필터 적용. 결과는 hours_to_end 오름차순.
    """
    now = int(datetime.now(UTC).timestamp())
    cutoff = now + int(max_hours * 3600)

    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        for page in range(max_pages):
            params: dict[str, Any] = {
                "limit": page_limit,
                "offset": page * page_limit,
                "order": "endDate",
                "ascending": "true",
                "closed": "false",
                "active": "true",
            }
            r = await cli.get(f"{GAMMA_BASE}/events", params=params)
            if r.status_code == 422:
                # gamma offset 한도(~2000) 도달 — 더 깊은 페이지 없음. 안전 종료.
                break
            r.raise_for_status()
            events = r.json()
            if not isinstance(events, list):
                events = events.get("data", [])
            if not events:
                break

            all_past_cutoff = True
            for ev in events:
                ev_end = _parse_ts(ev.get("endDate"))
                # 이벤트 종료 시각이 cutoff 이전인 것만 처리
                if ev_end is not None and ev_end > cutoff:
                    continue
                all_past_cutoff = False
                if bool(ev.get("closed")):
                    continue

                for m in ev.get("markets", []):
                    norm = _normalize(m, ev)
                    cid = norm["condition_id"] or norm["question"]
                    if not cid or cid in seen:
                        continue
                    if norm["is_closed"]:
                        continue
                    end = norm["end_ts"]
                    if end is None or end <= now or end > cutoff:
                        continue
                    if norm["volume_usd"] < min_volume:
                        continue
                    seen.add(cid)
                    hours_left = (end - now) / 3600
                    norm["hours_to_end"] = round(hours_left, 2)
                    out.append(norm)

            if all_past_cutoff:
                break

    return sorted(out, key=lambda m: m["end_ts"] or 0)
