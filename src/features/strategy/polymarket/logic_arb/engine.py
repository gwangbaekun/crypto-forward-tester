"""Logic-Arb — 엔진 (조합 차익: 포함관계 / 분할).

pair_hedge 엔진과 동일한 패턴 (module-level _cfg, poll loop, ws 콜백, DB 저장).
차이: 단일 시장이 아니라 **사다리 그룹** 을 만들어 시장 간 무위험 구조를 검사한다.

발화 전 기계적 검증 게이트:
  1. 산술 파싱 성공 (parse.py) — 제목 유사성 아님
  2. 동일 해상도 시점 (end_ts ±tol) 로 그룹핑
  3. ws best_ask 로 후보 프리스크린 → REST /book 으로 실제 best_ask + size 재확인
  4. 각 다리 ask_size ≥ min_ask_size (체결 가능성)
  5. fee_buffer 반영 후에도 min_profit 이상

kill-criteria 계측: 위반 지속시간을 로깅 (봇 선점 비율 판단 → 철수 근거).
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import yaml

from features.strategy.polymarket._data.client import fetch_active_events_by_keyword, fetch_book
from features.strategy.polymarket._data import ws_client as ws
from features.strategy.polymarket.logic_arb import parse as la_parse
from features.strategy.polymarket.logic_arb import signal as la_signal
from features.strategy.polymarket.logic_arb.signal import ArbSignal
from db.session import get_session
from db.models import PolymarketSignal

log = logging.getLogger("polymarket.logic_arb")

_CFG_PATH = Path(__file__).parent / "config.yaml"
_cfg: dict = {}
_ladders: list[la_parse.Ladder] = []
_token_index: dict[str, tuple[dict, str]] = {}   # token_id -> (market, "YES"|"NO")
_last_signal_ts: dict[str, float] = {}
_violation_seen: dict[str, float] = {}            # signature -> first_seen_ts (persistence)
_COOLDOWN_S = 300


def _load_cfg() -> dict:
    with open(_CFG_PATH) as f:
        return yaml.safe_load(f)


# --- ask 소스: ws best_ask 우선, 없으면 마켓 스냅샷 가격 (프리스크린용 근사) ---
def _ask_of(token_id: str) -> float | None:
    lvl = ws.price_book.get(token_id)
    if lvl is not None and lvl.best_ask is not None:
        return lvl.best_ask
    idx = _token_index.get(token_id)
    if idx is None:
        return None
    market, side = idx
    price = market.get("yes_price") if side == "YES" else market.get("no_price")
    if price is None:
        return None
    return float(price) + 0.005   # spread 절반 추정 (프리스크린 보수화)


def _signature(sig: ArbSignal) -> str:
    return sig.kind + "|" + "|".join(sorted(leg.token_id for leg in sig.legs))


async def _collect() -> None:
    """BTC 마켓 수집 → 파싱 → 사다리 그룹핑. _token_index 재구성."""
    global _ladders, _token_index
    keywords = _cfg.get("keywords", ["bitcoin", "btc"])
    min_vol = _cfg.get("min_volume_usd", 5000)
    tol = _cfg.get("end_ts_tolerance_sec", 3600)
    same_slug = _cfg.get("require_same_slug", False)

    markets = await fetch_active_events_by_keyword(keywords, min_volume=min_vol)
    lms = la_parse.build_ladder_markets(markets)
    _ladders = la_parse.group_ladders(lms, tol_sec=tol, require_same_slug=same_slug)

    _token_index = {}
    for ladder in _ladders:
        for lm in ladder.members:
            _token_index[lm.market["yes_token_id"]] = (lm.market, "YES")
            _token_index[lm.market["no_token_id"]] = (lm.market, "NO")

    log.debug("[LA] collected: %d BTC markets → %d parsed → %d ladders",
              len(markets), len(lms), len(_ladders))


async def _confirm_leg(token_id: str) -> tuple[float, float] | None:
    """REST /book 으로 실제 best_ask + ask_size 재확인. (ask, size) 또는 None."""
    book = await fetch_book(token_id)
    if not book or book.get("best_ask") is None:
        return None
    return float(book["best_ask"]), float(book.get("ask_size") or 0.0)


async def _confirm_and_emit(sig: ArbSignal) -> None:
    """후보를 REST book 으로 재확인 (실 ask + size 게이트) 후 통과 시 저장."""
    min_size = _cfg.get("min_ask_size", 20)
    fee_buffer = _cfg.get("fee_buffer", 0.01)
    min_profit = _cfg.get("min_profit", 0.005)

    real_cost = 0.0
    for leg in sig.legs:
        conf = await _confirm_leg(leg.token_id)
        if conf is None:
            log.debug("[LA] confirm drop — no book for %s", leg.token_id[:12])
            return
        ask, size = conf
        if size < min_size:
            log.debug("[LA] confirm drop — thin book size=%.0f < %d (%s)",
                      size, min_size, leg.label)
            return
        leg.ask = ask
        real_cost += ask

    profit = sig.guaranteed_payoff - real_cost - fee_buffer
    if profit < min_profit:
        log.debug("[LA] confirm drop — post-book profit %.4f < %.4f", profit, min_profit)
        return

    sig.cost = real_cost
    sig.profit = profit
    sig.profit_pct = profit / real_cost * 100 if real_cost > 0 else 0.0

    key = _signature(sig)
    last = _last_signal_ts.get(key, 0)
    if time.time() - last < _COOLDOWN_S:
        return
    _last_signal_ts[key] = time.time()

    log.info("[LA] %s ARB cost=%.3f profit=+%.2f%% | %s",
             sig.kind.upper(), sig.cost, sig.profit_pct, sig.detail)
    _save_signal(sig)


def _save_signal(sig: ArbSignal) -> None:
    yes_leg = next((l for l in sig.legs if l.side == "YES"), None)
    no_leg = next((l for l in sig.legs if l.side == "NO"), None)
    db = get_session()
    try:
        row = PolymarketSignal(
            strategy="logic_arb",
            condition_id=sig.condition_ids[0] if sig.condition_ids else None,
            question=sig.detail[:500],
            signal_type=("LOGIC_ARB_" + sig.kind.upper())[:32],
            yes_price=yes_leg.ask if yes_leg else None,
            no_price=no_leg.ask if no_leg else None,
            pair_cost=sig.cost,
            divergence=sig.profit,
            side="BOTH",
            volume_usd=sig.volume_usd,
            hours_to_end=None,
            yes_token_id=yes_leg.token_id if yes_leg else None,
            no_token_id=no_leg.token_id if no_leg else None,
            event_end_ts=sig.end_ts,
        )
        db.add(row)
        db.commit()
    except Exception as e:
        db.rollback()
        log.warning("[LA] DB save failed: %s", e)
    finally:
        db.close()


def _track_persistence(current_sigs: list[ArbSignal]) -> None:
    """위반 지속시간 계측 (kill-criteria). 소멸한 위반의 수명을 로깅."""
    if not _cfg.get("persistence_track", True):
        return
    now = time.time()
    current_keys = {_signature(s) for s in current_sigs}
    for k in current_keys:
        _violation_seen.setdefault(k, now)
    gone = [k for k in _violation_seen if k not in current_keys]
    short = 0
    for k in gone:
        life = now - _violation_seen.pop(k)
        if life < 60:
            short += 1
        log.debug("[LA][persist] violation gone after %.1fs", life)
    if gone:
        log.info("[LA][persist] %d violations resolved (%d within 60s → 봇 선점 신호)",
                 len(gone), short)


async def _refresh_and_scan() -> list[ArbSignal]:
    fee_buffer = _cfg.get("fee_buffer", 0.01)
    min_profit = _cfg.get("min_profit", 0.005)
    try:
        await _collect()
    except Exception as e:
        log.warning("[LA] collect failed: %s", e)
        return []

    all_candidates: list[ArbSignal] = []
    for ladder in _ladders:
        try:
            cands = la_signal.scan_ladder(ladder, _ask_of, fee_buffer, min_profit)
            all_candidates.extend(cands)
        except Exception as e:
            log.debug("[LA] ladder scan error: %s", e)

    _track_persistence(all_candidates)

    # 후보를 REST book 으로 재확인 후 저장 (병렬, 소량)
    await asyncio.gather(*[_confirm_and_emit(c) for c in all_candidates],
                         return_exceptions=True)
    return all_candidates


async def on_price_update(token_id: str) -> None:
    """ws best_ask 변경 시 해당 토큰이 속한 사다리만 빠르게 재검사."""
    if token_id not in _token_index:
        return
    fee_buffer = _cfg.get("fee_buffer", 0.01)
    min_profit = _cfg.get("min_profit", 0.005)
    for ladder in _ladders:
        if not any(token_id in (lm.market["yes_token_id"], lm.market["no_token_id"])
                   for lm in ladder.members):
            continue
        try:
            for c in la_signal.scan_ladder(ladder, _ask_of, fee_buffer, min_profit):
                await _confirm_and_emit(c)
        except Exception as e:
            log.debug("[LA] ws callback error: %s", e)


async def run(ws_client: ws.CLOBWSClient) -> None:
    global _cfg
    _cfg = _load_cfg()
    if not _cfg.get("enabled", False):
        log.debug("[LA] disabled — skipping")
        return

    ws.register_callback(on_price_update)
    interval = _cfg.get("poll_interval_sec", 180)

    while True:
        await _refresh_and_scan()
        # 새로 편입된 토큰을 ws 구독에 추가 (실시간 best_ask 확보)
        for tid in list(_token_index.keys()):
            ws_client.add_tokens(tid)
        await asyncio.sleep(interval)


def get_ladders() -> list[la_parse.Ladder]:
    return _ladders
