"""Fade 워치리스트 자동 스크리너 — 종목 '이름'이 아니라 '가격 거동'으로 후보를 뽑는다.

배경: 워치리스트가 watchlist.yaml 수동 91개에 묶여 있어 달이 바뀌면 신규 마켓을
      전혀 못 잡았다. 수집 경로도 이벤트 title 키워드 매칭이라 주제에 종속적이었다.

판정 기준(설계 확정값, config.yaml `screener` 섹션에서만 관리):
  1. 밴드      — YES 가격이 [band_lo, band_hi] 안
  2. 볼륨      — volume_usd >= min_volume_usd
  3. 체류율    — 최근 곡선의 dwell_ratio_min 이상이 밴드 안에 머물렀다
  4. 스파이크  — 같은 구간에 detect_spikes 가 min_spikes 회 이상 잡힌다
  → 3·4 가 "5~15%대에 머물다 가끔 스파이크" 를 기계적으로 표현한 것.

수동/자동 구분 (마이그레이션 없이):
  watchlist.yaml 에 있는 condition_id = **수동**. 스크리너는 절대 건드리지 않는다.
  yaml 에 없는 DB 행 = 스크리너가 넣은 **자동**. 편입·해제 모두 이쪽에만 적용된다.
  자동분을 yaml 에 쓰지 않는 이유: 매 시간 900행을 커밋하면 yaml 이 수동 원장의
  의미를 잃는다. 재배포로 사라져도 다음 스캔이 결정론적으로 복원한다.

안전장치:
  - 열린 포지션이 있는 마켓은 밴드를 이탈해도 해제하지 않는다(고아 포지션 방지).
  - 슬라이스가 Gamma offset 상한에 걸리면 커버리지 불완전 → 로그로 드러낸다.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select

from db.session import get_session
from db.models import PolymarketFadeWatch, PolymarketFadePosition
from features.strategy.polymarket._data import client as poly_client
from features.strategy.polymarket.fade import signal as fade_signal
from features.strategy.polymarket.fade.watchlist_seed import load_watchlist_config

log = logging.getLogger("polymarket.fade.screener")

_REQUIRED_KEYS = (
    "enabled", "interval_hours", "band_lo", "band_hi", "min_volume_usd",
    "dwell_ratio_min", "min_spikes", "history_fidelity", "history_concurrency",
    "months_ahead", "auto_include", "auto_drop",
)

# 스크리너가 넣은 자동 편입분에 붙는 상태값. 밴드를 이탈하면 이 값으로 되돌린다.
# 수동 'excluded'(유저가 직접 뺀 것)와 구분해야 자동 편입이 수동 결정을 덮지 않는다.
STATUS_DROPPED = "dropped"


def validate_cfg(cfg: dict) -> None:
    missing = [k for k in _REQUIRED_KEYS if k not in cfg]
    if missing:
        raise RuntimeError(f"[fade.screener] config.yaml screener 필수 키 누락: {missing}")


# ── 판정 (순수 함수) ─────────────────────────────────────────────────────────

def dwell_ratio(pts: list[dict], band_lo: float, band_hi: float) -> float:
    """곡선 포인트 중 밴드 안에 있던 비율."""
    if not pts:
        return 0.0
    inside = sum(1 for p in pts if band_lo <= p["price"] <= band_hi)
    return inside / len(pts)


def evaluate(pts: list[dict], cfg: dict) -> dict[str, Any]:
    """한 종목의 곡선 → 편입 판정. detect_spikes 는 라이브 엔진과 같은 함수를 쓴다."""
    lo, hi = cfg["band_lo"], cfg["band_hi"]
    spike_cfg = {
        "lookback_s":  cfg["spike_lookback_s"],
        "spike_rel":   cfg["spike_rel"],
        "spike_abs":   cfg["spike_abs"],
        "p0_lo":       lo,
        "p0_hi":       hi,
        "cooldown_hours": cfg["spike_cooldown_hours"],
    }
    spikes = fade_signal.detect_spikes(pts, spike_cfg) if pts else []
    dwell = dwell_ratio(pts, lo, hi)
    return {
        "n_points": len(pts),
        "dwell":    dwell,
        "n_spikes": len(spikes),
        "passed":   bool(pts)
                    and dwell >= cfg["dwell_ratio_min"]
                    and len(spikes) >= cfg["min_spikes"],
    }


# ── DB 헬퍼 ─────────────────────────────────────────────────────────────────

def _manual_cids() -> set[str]:
    """watchlist.yaml 에 실린 condition_id = 수동 관리분. 스크리너 제외 대상."""
    return {m["condition_id"] for m in load_watchlist_config() if m.get("condition_id")}


def _open_position_cids() -> set[str]:
    db = get_session()
    try:
        rows = db.execute(
            select(PolymarketFadePosition.condition_id)
            .where(PolymarketFadePosition.status == "open")
        ).scalars().all()
        return set(rows)
    finally:
        db.close()


def _existing_rows() -> dict[str, PolymarketFadeWatch]:
    db = get_session()
    try:
        return {w.condition_id: w
                for w in db.execute(select(PolymarketFadeWatch)).scalars().all()}
    finally:
        db.close()


# ── 메인 ────────────────────────────────────────────────────────────────────

async def run_screen(cfg: dict) -> dict[str, Any]:
    """1회 스캔. 반환값은 그대로 대시보드/로그용 리포트."""
    validate_cfg(cfg)
    t0 = time.time()
    lo, hi = cfg["band_lo"], cfg["band_hi"]

    # 1) 전수 열거 — 히스토리 요청 0건
    markets, capped = await poly_client.screen_band_markets(
        band_lo=lo, band_hi=hi,
        min_volume=cfg["min_volume_usd"],
        months_ahead=cfg["months_ahead"],
    )
    if capped:
        log.warning("[fade.screener] offset 상한 도달 슬라이스 %s — 해당 구간 커버리지 불완전. "
                    "볼륨 하한을 올리거나 슬라이스를 더 잘게 쪼개야 한다.", capped)

    band_cids = {m["condition_id"] for m in markets}
    manual = _manual_cids()
    existing = _existing_rows()
    open_cids = _open_position_cids()

    # 2) 검증 대상 = 밴드에 있으면서 아직 included 가 아닌 자동 관리분
    to_verify = [m for m in markets
                 if m["condition_id"] not in manual
                 and (existing.get(m["condition_id"]) is None
                      or existing[m["condition_id"]].status != "included")]
    # 수동 excluded 는 유저 결정 — 되살리지 않는다
    to_verify = [m for m in to_verify
                 if not (existing.get(m["condition_id"])
                         and existing[m["condition_id"]].status == "excluded")]

    curves = await poly_client.fetch_curve_batch(
        [m["yes_token_id"] for m in to_verify],
        fidelity=cfg["history_fidelity"],
        concurrency=cfg["history_concurrency"],
    )

    passed: list[dict] = []
    for m in to_verify:
        ev = evaluate(curves.get(m["yes_token_id"]) or [], cfg)
        if ev["passed"]:
            passed.append({**m, **ev})

    # 3) 편입 / 해제
    included_n = _include(passed, cfg) if cfg["auto_include"] else 0
    dropped, skipped_open = _drop_out_of_band(
        band_cids, manual, open_cids) if cfg["auto_drop"] else (0, 0)

    report = {
        "elapsed_s":      round(time.time() - t0, 1),
        "band_markets":   len(markets),
        "verified":       len(to_verify),
        "curves_ok":      len(curves),
        "passed":         len(passed),
        "included":       included_n,
        "dropped":        dropped,
        "kept_open_pos":  skipped_open,
        "capped_slices":  capped,
        "manual_count":   len(manual),
    }
    log.info("[fade.screener] 밴드 %d · 검증 %d · 통과 %d · 편입 %d · 해제 %d (%.1fs)",
             report["band_markets"], report["verified"], report["passed"],
             report["included"], report["dropped"], report["elapsed_s"])
    return report


def _include(passed: list[dict], cfg: dict) -> int:
    """통과분을 included 로 DB upsert. yaml 은 건드리지 않는다(수동 원장 보존)."""
    if not passed:
        return 0
    db = get_session()
    n = 0
    try:
        for m in passed:
            cid = m["condition_id"]
            row = db.get(PolymarketFadeWatch, cid)
            if row is None:
                db.add(PolymarketFadeWatch(
                    condition_id=cid,
                    question=m.get("question"),
                    yes_token_id=m.get("yes_token_id"),
                    no_token_id=m.get("no_token_id"),
                    volume_usd=m.get("volume_usd"),
                    start_ts=m.get("start_ts"),
                    end_ts=m.get("end_ts"),
                    status="included",
                ))
                n += 1
            elif row.status == STATUS_DROPPED:
                # 밴드로 복귀 — 자동분만 되살린다
                row.status = "included"
                row.volume_usd = m.get("volume_usd")
                n += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return n


def _drop_out_of_band(band_cids: set[str], manual: set[str],
                      open_cids: set[str]) -> tuple[int, int]:
    """밴드를 벗어난 자동 편입분을 구독 해제(status=dropped).

    수동분·열린 포지션 보유분은 건드리지 않는다. 후자를 해제하면 엔진이
    _yes_map 에서 빼버려 청산 로직이 그 포지션을 영영 못 보게 된다.
    """
    db = get_session()
    dropped = kept = 0
    try:
        rows = db.execute(
            select(PolymarketFadeWatch)
            .where(PolymarketFadeWatch.status == "included")
        ).scalars().all()
        for row in rows:
            cid = row.condition_id
            if cid in manual or cid in band_cids:
                continue
            if cid in open_cids:
                kept += 1
                continue
            row.status = STATUS_DROPPED
            dropped += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return dropped, kept
