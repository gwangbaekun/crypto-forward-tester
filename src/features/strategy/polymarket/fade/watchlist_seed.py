"""fade 워치리스트 seed — watchlist.yaml(git 커밋본) → DB 비파괴적 upsert.

배경: 워치리스트("종목")는 DB(local sqlite / Railway Postgres)에 저장되어 환경마다
divergence 가 났다. watchlist.yaml 을 단일 source of truth 로 두고, 시작 시 이 파일에
있으나 DB 에 없는 condition_id 만 삽입한다. 이미 있는 행은 건드리지 않는다(수동 상태변경 보존).
→ yaml 을 커밋/푸시하면 Railway 재배포 시 자동으로 같은 종목을 갖게 된다.

fail-fast: yaml 이 없거나 파싱 실패하면 즉시 예외. (fallback 금지)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

_WATCHLIST_PATH = Path(__file__).parent / "watchlist.yaml"
log = logging.getLogger("polymarket.fade.seed")


def load_watchlist_config() -> list[dict[str, Any]]:
    """watchlist.yaml → markets 리스트. 없으면/깨졌으면 예외."""
    if not _WATCHLIST_PATH.exists():
        raise FileNotFoundError(f"fade watchlist config 없음: {_WATCHLIST_PATH}")
    data = yaml.safe_load(_WATCHLIST_PATH.read_text()) or {}
    markets = data.get("markets")
    if not isinstance(markets, list):
        raise ValueError(f"watchlist.yaml 'markets' 리스트 아님: {type(markets)}")
    return markets


def seed_watchlist_from_config() -> int:
    """yaml 에 있으나 DB 에 없는 종목을 upsert. 삽입한 개수 반환."""
    from db.session import get_session
    from db.models import PolymarketFadeWatch

    markets = load_watchlist_config()
    db = get_session()
    inserted = 0
    try:
        for m in markets:
            cid = m.get("condition_id")
            if not cid:
                raise ValueError(f"watchlist.yaml 항목에 condition_id 없음: {m}")
            if db.get(PolymarketFadeWatch, cid) is not None:
                continue  # 이미 있음 → 비파괴적으로 건너뜀
            db.add(PolymarketFadeWatch(
                condition_id=cid,
                question=m.get("question"),
                yes_token_id=m.get("yes_token_id"),
                no_token_id=m.get("no_token_id"),
                volume_usd=m.get("volume_usd"),
                start_ts=m.get("start_ts"),
                end_ts=m.get("end_ts"),
                status=m.get("status", "included"),
            ))
            inserted += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    log.info("[fade] watchlist seed: %d개 신규 삽입 (yaml 총 %d)", inserted, len(markets))
    return inserted


# ── yaml 갱신 헬퍼 (엔드포인트에서 add/status/delete 시 호출) ──────────────────

def upsert_watchlist_yaml(market: dict[str, Any]) -> None:
    """add/status 시 yaml 에 반영 (condition_id 기준 덮어쓰기)."""
    cid = market.get("condition_id")
    if not cid:
        raise ValueError(f"upsert_watchlist_yaml: condition_id 없음: {market}")
    markets = load_watchlist_config()
    keep = ("condition_id", "question", "yes_token_id", "no_token_id",
            "volume_usd", "start_ts", "end_ts", "status")
    entry = {k: market.get(k) for k in keep}
    for i, m in enumerate(markets):
        if m.get("condition_id") == cid:
            markets[i] = entry
            break
    else:
        markets.append(entry)
    _write_watchlist_yaml(markets)


def remove_from_watchlist_yaml(condition_id: str) -> None:
    """delete 시 yaml 에서 제거 (남아있으면 재시작 때 좀비로 부활하므로 필수)."""
    markets = [m for m in load_watchlist_config() if m.get("condition_id") != condition_id]
    _write_watchlist_yaml(markets)


def _write_watchlist_yaml(markets: list[dict[str, Any]]) -> None:
    header = (
        "# fade 전략 워치리스트 — SOURCE OF TRUTH (git 커밋 대상)\n"
        "# 시작 시 seed_watchlist_from_config()가 DB에 없는 condition_id를 upsert (비파괴적).\n"
        "# 종목 추가/삭제/상태변경은 /fade/market/* 엔드포인트가 이 파일도 함께 갱신.\n"
    )
    with open(_WATCHLIST_PATH, "w") as f:
        f.write(header)
        yaml.safe_dump({"markets": markets}, f, allow_unicode=True,
                       sort_keys=False, width=200)
