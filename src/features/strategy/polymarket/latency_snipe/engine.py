"""Latency Snipe — 엔진 (paper 단계, 주문 없음).

1. fetch_by_expiry 로 종료임박 저유동 마켓 갱신
2. 각 마켓 YES/NO fetch_book → signal.compute()
3. 시그널 발생 시 PolymarketSignal(strategy="latency_snipe") 저장 (order_status="paper")
   → 기존 resolver 가 is_resolved/actual_pnl 채움 → 대시보드/analytics 자동 연동.

late_convergence/engine.py 를 템플릿으로 함. 차이: ws 가격 대신 오더북(fetch_book),
주문 호출 없음(paper 수집), depth/exit_bid 를 기록.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from pathlib import Path

import yaml

from features.strategy.polymarket._data.client import fetch_by_expiry, fetch_book
from features.strategy.polymarket._data import ws_client as ws
from features.strategy.polymarket.latency_snipe import signal as ls_signal
from db.session import get_session
from db.models import PolymarketSignal

log = logging.getLogger("polymarket.latency_snipe")

_CFG_PATH = Path(__file__).parent / "config.yaml"
_cfg: dict = {}
_markets: dict[str, dict] = {}
_last_signal_ts: dict[str, float] = {}
_COOLDOWN_S = 600

# 스캔 시도 라이브 피드 (인메모리, 대시보드용). 최근 것만 — 무한증가 방지.
_scan_log: deque = deque(maxlen=50)
_scan_stats: dict = {"last_scan_ts": 0, "last_candidates": 0, "cycles": 0}


def get_scan_log() -> list[dict]:
    return list(_scan_log)


def get_scan_stats() -> dict:
    return dict(_scan_stats)


def _verdict(yes_book: dict | None, no_book: dict | None, sig) -> tuple[str, str]:
    """피드용 (verdict, reason). signal 로직과 별개 — 왜 됐/안됐는지 사람이 읽게."""
    lo, hi = _cfg["entry_min_ask"], _cfg["entry_max_ask"]
    exit_min, min_size = _cfg["exit_min_bid"], _cfg["min_size_usd"]
    if sig is not None:
        if sig.sellable:
            return "SIGNAL", f"sellable: ask {sig.entry_ask:.3f} → bid {sig.exit_bid:.3f}"
        return "signal-hold", f"favorite ask {sig.entry_ask:.3f}, 되팔 bid 부족(<{exit_min})"
    # 거절 사유
    reasons = []
    for side, b in (("YES", yes_book), ("NO", no_book)):
        if not b or b.get("best_ask") is None:
            reasons.append(f"{side} ask=None")
        else:
            a = b["best_ask"]
            if a < lo:
                reasons.append(f"{side} ask {a:.3f}<{lo}(미결정)")
            elif a > hi:
                reasons.append(f"{side} ask {a:.3f}>{hi}(이미수렴)")
    return "reject", " · ".join(reasons) or "no favorite in band"


def _record_attempt(market: dict, yes_book, no_book, sig) -> None:
    verdict, reason = _verdict(yes_book, no_book, sig)
    _scan_log.appendleft({
        "ts":           int(time.time()),
        "question":     (market.get("question") or "")[:80],
        "volume_usd":   market.get("volume_usd"),
        "hours_to_end": market.get("hours_to_end"),
        "yes_ask":      (yes_book or {}).get("best_ask"),
        "yes_bid":      (yes_book or {}).get("best_bid"),
        "no_ask":       (no_book or {}).get("best_ask"),
        "no_bid":       (no_book or {}).get("best_bid"),
        "verdict":      verdict,
        "reason":       reason,
    })


def _load_cfg() -> dict:
    with open(_CFG_PATH) as f:
        return yaml.safe_load(f)


def _kw_ok(question: str, kws: list[str]) -> bool:
    if not kws:
        return True
    ql = (question or "").lower()
    return any(k.lower() in ql for k in kws)


async def _refresh_and_scan(ws_client: ws.CLOBWSClient | None = None) -> None:
    """저유동 종료임박 마켓 갱신 + 오더북으로 시그널 체크."""
    global _markets
    max_hours = _cfg.get("max_scan_hours", 6.0)
    max_vol   = _cfg.get("max_volume_usd", 50000)
    min_vol   = _cfg.get("min_volume_usd", 200)
    kws       = _cfg.get("allow_keywords", [])

    try:
        fetched = await fetch_by_expiry(max_hours=max_hours, min_volume=min_vol)
    except Exception as e:
        log.warning("[LS] market refresh failed: %s", e)
        return

    cand = [
        m for m in fetched
        if m.get("condition_id") and m["volume_usd"] < max_vol
        and _kw_ok(m.get("question", ""), kws)
        and m.get("yes_token_id") and m.get("no_token_id")
    ]
    _markets = {m["condition_id"]: m for m in cand}
    _scan_stats.update(last_scan_ts=int(time.time()), last_candidates=len(cand),
                       cycles=_scan_stats["cycles"] + 1)
    log.debug("[LS] low-liq candidates: %d (≤%.0fh, <$%.0f)", len(cand), max_hours, max_vol)

    for cid, market in list(_markets.items()):
        try:
            await _check_market(cid, market)
        except Exception as e:
            log.debug("[LS] scan error %s: %s", cid[:12], e)


async def _check_market(cid: str, market: dict) -> None:
    yes_book = await fetch_book(market["yes_token_id"])
    no_book  = await fetch_book(market["no_token_id"])
    sig = ls_signal.compute(market, yes_book, no_book, _cfg)
    _record_attempt(market, yes_book, no_book, sig)
    if sig is None:
        return

    last = _last_signal_ts.get(cid, 0)
    if time.time() - last < _COOLDOWN_S:
        return
    _last_signal_ts[cid] = time.time()

    if _already_signaled(cid, sig.side):
        return

    log.info(
        "[LS] %s | %s | ask=%.3f bid=%s sellable=%s | %.1fh | $%.0f vol",
        sig.side, sig.question[:45], sig.entry_ask,
        f"{sig.exit_bid:.3f}" if sig.exit_bid else "-", sig.sellable,
        sig.hours_to_end, sig.volume_usd,
    )
    row_id = _save_signal(sig)
    if row_id:
        asyncio.create_task(_place_order_and_update(sig, row_id))


def _already_signaled(condition_id: str, side: str | None) -> bool:
    """동일 condition_id+side 로 unresolved 시그널 있으면 True (중복 차단)."""
    from sqlalchemy import select as sa_select
    db = get_session()
    try:
        stmt = sa_select(PolymarketSignal).where(
            PolymarketSignal.strategy == "latency_snipe",
            PolymarketSignal.condition_id == condition_id,
            PolymarketSignal.is_resolved == 0,
        )
        if side:
            stmt = stmt.where(PolymarketSignal.side == side)
        return db.execute(stmt).first() is not None
    except Exception as e:
        log.warning("[LS] _already_signaled failed: %s", e)
        return False
    finally:
        db.close()


def _save_signal(sig: ls_signal.LSSignal) -> int | None:
    """PolymarketSignal 저장 (paper — 주문 없음). depth/exit_bid 를 기존 필드에 매핑."""
    db = get_session()
    try:
        row = PolymarketSignal(
            strategy      = "latency_snipe",
            condition_id  = sig.condition_id,
            question      = sig.question[:500],
            signal_type   = f"LS_{sig.side}_{'sellable' if sig.sellable else 'hold'}",
            yes_price     = sig.entry_ask if sig.side == "YES" else None,
            no_price      = sig.entry_ask if sig.side == "NO"  else None,
            pair_cost     = sig.exit_bid,        # 정산 전 되팔 가격
            divergence    = sig.spread,          # entry_ask - exit_bid
            side          = sig.side,
            volume_usd    = sig.volume_usd,
            hours_to_end  = sig.hours_to_end,
            yes_token_id  = sig.yes_token_id,
            no_token_id   = sig.no_token_id,
            event_end_ts  = sig.end_ts,
            order_status  = "pending",           # LIVE면 _place_order_and_update가 갱신
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    except Exception as e:
        db.rollback()
        log.warning("[LS] DB save failed: %s", e)
        return None
    finally:
        db.close()


async def _place_order_and_update(sig: ls_signal.LSSignal, row_id: int) -> None:
    """LIVE면 favorite 측 매수. 유효 PK 없으면 skip (late_convergence와 동일 경로)."""
    from features.strategy.polymarket._data.executor import is_live_mode
    if not is_live_mode():
        # paper 모드 — 주문 안 함. status 를 paper 로 명확히 라벨.
        from sqlalchemy import select
        db = get_session()
        try:
            row = db.execute(select(PolymarketSignal).where(PolymarketSignal.id == row_id)).scalar_one_or_none()
            if row:
                row.order_status = "paper"
                db.commit()
        finally:
            db.close()
        return

    from features.strategy.polymarket._data.executor import place_order
    from sqlalchemy import select

    token_id = sig.yes_token_id if sig.side == "YES" else sig.no_token_id
    if not token_id:
        return

    # 동일 condition_id로 이미 live/matched 주문 있으면 중복 차단
    _db = get_session()
    try:
        existing = _db.execute(
            select(PolymarketSignal).where(
                PolymarketSignal.strategy == "latency_snipe",
                PolymarketSignal.condition_id == sig.condition_id,
                PolymarketSignal.order_status.in_(["live", "matched"]),
            )
        ).first()
        if existing:
            row = _db.execute(select(PolymarketSignal).where(PolymarketSignal.id == row_id)).scalar_one_or_none()
            if row:
                row.order_status = "skipped"
                row.order_error  = f"dup: already {existing[0].order_status}"
                _db.commit()
            return
    except Exception as e:
        log.warning("[LS] dup check failed: %s", e)
    finally:
        _db.close()

    result = await place_order(token_id, sig.entry_ask, max_usd=_cfg.get("max_order_usd", 0.0))

    db = get_session()
    try:
        row = db.execute(select(PolymarketSignal).where(PolymarketSignal.id == row_id)).scalar_one_or_none()
        if row:
            row.poly_order_id = result.get("order_id") or ""
            row.order_status  = result.get("status", "failed")
            row.order_error   = result.get("error") or ""
            db.commit()
    except Exception as e:
        db.rollback()
        log.warning("[LS] order DB update failed: %s", e)
    finally:
        db.close()


async def run(ws_client: ws.CLOBWSClient | None = None) -> None:
    global _cfg
    _cfg = _load_cfg()
    if not _cfg.get("enabled", True):
        log.debug("[LS] disabled — skipping")
        return
    interval = _cfg.get("poll_interval_sec", 120)
    while True:
        await _refresh_and_scan(ws_client)
        await asyncio.sleep(interval)


def get_markets() -> dict[str, dict]:
    return _markets
