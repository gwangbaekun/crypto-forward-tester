"""Bayesian FOMC — 엔진.

FRED 키 없으면 graceful skip (LC/PH는 계속 동작).
1시간마다 REST로 Fed 관련 마켓 조회 → 모델 확률과 비교 → 시그널.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date
from pathlib import Path

import yaml

from features.strategy.polymarket._data import client as poly_client
from features.strategy.polymarket._data.economic import build_fred_cache, latest_before, yoy
from features.strategy.polymarket.bayesian_fomc.model import FOMCModel
from features.strategy.polymarket.bayesian_fomc import signal as bf_signal
from db.session import get_session
from db.models import PolymarketSignal

log = logging.getLogger("polymarket.bayesian_fomc")

_CFG_PATH = Path(__file__).parent / "config.yaml"
_cfg:       dict = {}
_model:     FOMCModel | None = None
_fred_cache: dict | None = None


def _load_cfg() -> dict:
    with open(_CFG_PATH) as f:
        return yaml.safe_load(f)


def _fred_key() -> str | None:
    return os.environ.get("FRED_API_KEY", "").strip() or None


async def _init_model() -> bool:
    """FRED 데이터로 모델 초기화. 키 없으면 False 반환."""
    global _model, _fred_cache

    key = _fred_key()
    if not key:
        log.warning("[BF] FRED_API_KEY 없음 — Bayesian FOMC 비활성")
        return False

    try:
        log.debug("[BF] FRED 데이터 로딩 중…")
        from features.strategy.polymarket._data.economic import fetch_series
        import pandas as pd

        start_year = _cfg.get("fred_start_year", 2010)

        _fred_cache = await build_fred_cache(api_key=key, start_year=start_year)

        # FOMC 히스토리 구성 (금리 변화 기준)
        s_rate = _fred_cache["DFEDTARU"]
        rate_diff = s_rate.diff().dropna()

        rows = []
        s_cpi   = _fred_cache["CPIAUCSL"]
        s_pce   = _fred_cache["PCEPI"]
        s_unrate = _fred_cache["UNRATE"]

        for meet_date, delta in rate_diff.items():
            if delta == 0:
                continue
            outcome = 1 if delta > 0 else -1
            as_of   = meet_date

            cpi_v   = latest_before(s_cpi,   as_of)
            pce_v   = latest_before(s_pce,   as_of)
            ur_v    = latest_before(s_unrate, as_of)
            rate_v  = latest_before(s_rate,   as_of)

            if None in (cpi_v, pce_v, ur_v, rate_v):
                continue

            rows.append({
                "meeting_date": pd.Timestamp(meet_date),
                "cpi_yoy":      yoy(s_cpi,  as_of, cpi_v),
                "pce_yoy":      yoy(s_pce,  as_of, pce_v),
                "unrate":       ur_v,
                "fedfunds_ub":  rate_v,
                "outcome":      outcome,
            })

        import pandas as pd
        fomc_df = pd.DataFrame(rows).sort_values("meeting_date").reset_index(drop=True)
        log.debug("[BF] FOMC 히스토리: %d 결정 로드 완료", len(fomc_df))

        _model = FOMCModel(min_samples=10, C=2.0)
        _model.load(fomc_df)
        return True

    except Exception as e:
        log.warning("[BF] 모델 초기화 실패: %s", e)
        return False


def _current_features() -> dict[str, float] | None:
    if _fred_cache is None:
        return None
    today = date.today()

    s_cpi    = _fred_cache["CPIAUCSL"]
    s_pce    = _fred_cache["PCEPI"]
    s_unrate = _fred_cache["UNRATE"]
    s_rate   = _fred_cache["DFEDTARU"]

    cpi_v  = latest_before(s_cpi,    today)
    pce_v  = latest_before(s_pce,    today)
    ur_v   = latest_before(s_unrate, today)
    rate_v = latest_before(s_rate,   today)

    if None in (cpi_v, pce_v, ur_v, rate_v):
        return None

    return {
        "cpi_yoy":     yoy(s_cpi,  today, cpi_v),
        "pce_yoy":     yoy(s_pce,  today, pce_v),
        "unrate":      ur_v,
        "fedfunds_ub": rate_v,
    }


def _save_signal(sig: bf_signal.BayesianSignal) -> None:
    db = get_session()
    try:
        row = PolymarketSignal(
            strategy     = "bayesian_fomc",
            condition_id = sig.condition_id,
            question     = sig.question[:500],
            signal_type  = f"BAYES_{sig.side}",
            yes_price    = sig.market_prob,
            no_price     = 1.0 - sig.market_prob,
            pair_cost    = None,
            divergence   = sig.divergence,
            side         = sig.side,
            volume_usd   = sig.volume_usd,
            hours_to_end = None,
            yes_token_id = sig.yes_token_id,
            no_token_id  = sig.no_token_id,
        )
        db.add(row)
        db.commit()
    except Exception as e:
        db.rollback()
        log.warning("[BF] DB save failed: %s", e)
    finally:
        db.close()


async def _scan_once() -> None:
    if _model is None or _fred_cache is None:
        return

    features = _current_features()
    if features is None:
        log.debug("[BF] FRED 특성값 없음, 스킵")
        return

    model_prob = _model.predict(date.today(), features)
    if model_prob is None:
        log.debug("[BF] 학습 데이터 부족, 스킵")
        return

    log.debug(
        "[BF] P(hike)=%.3f | features={%s}",
        model_prob,
        ", ".join(f"{k}:{round(v, 2)}" for k, v in features.items()),
    )

    keywords = _cfg.get("keywords", ["Fed"])
    min_vol  = _cfg.get("min_volume_usd", 5000)
    markets  = await poly_client.fetch_all_active(keywords, min_volume=min_vol)

    for m in markets:
        yes_price = m.get("last_yes_price") or m.get("yes_price")
        if yes_price is None:
            continue

        sig = bf_signal.compute(m, yes_price, model_prob, _cfg)
        if sig is None:
            continue

        log.debug(
            "[BF] SIGNAL %s | model=%.3f mkt=%.3f div=%+.3f | %s | $%.0f vol",
            sig.side, sig.model_prob, sig.market_prob, sig.divergence,
            sig.question[:50], sig.volume_usd,
        )
        _save_signal(sig)


async def run() -> None:
    global _cfg
    _cfg = _load_cfg()

    if not _cfg.get("enabled", True):
        log.debug("[BF] disabled — skipping")
        return

    ok = await _init_model()
    if not ok:
        return

    interval = _cfg.get("poll_interval_sec", 3600)
    while True:
        try:
            await _scan_once()
        except Exception as e:
            log.warning("[BF] scan error: %s", e)
        await asyncio.sleep(interval)
