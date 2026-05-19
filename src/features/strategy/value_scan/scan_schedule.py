"""Value Scan — 시장별 마지막 스캔 시각(DB) + 하루 1회 catch-up."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from db.models import ValueScanMarketMeta
from db.session import get_session

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")
_ET = ZoneInfo("America/New_York")

MARKET_CONFIG: dict[str, dict[str, Any]] = {
    "kospi": {
        "tz": _KST,
        "hour": 15,
        "minute": 35,
        "label": "KOSPI",
    },
    "nasdaq": {
        "tz": _ET,
        "hour": 16,
        "minute": 5,
        "label": "NASDAQ / S&P",
    },
}


def market_tz(market: str) -> ZoneInfo:
    return MARKET_CONFIG[market]["tz"]


def trading_date(market: str, when: Optional[datetime] = None) -> str:
    """해당 시장 기준 거래일 (YYYY-MM-DD)."""
    tz = market_tz(market)
    dt = when.astimezone(tz) if when else datetime.now(tz)
    return dt.strftime("%Y-%m-%d")


def is_weekday(market: str, when: Optional[datetime] = None) -> bool:
    tz = market_tz(market)
    dt = when.astimezone(tz) if when else datetime.now(tz)
    return dt.weekday() < 5


def is_past_scan_window(market: str, when: Optional[datetime] = None) -> bool:
    """장 마감 후 스캔 시각( KOSPI 15:35 KST / NASDAQ 16:05 ET ) 이후인지."""
    cfg = MARKET_CONFIG[market]
    tz = cfg["tz"]
    now = when.astimezone(tz) if when else datetime.now(tz)
    return (now.hour, now.minute) >= (cfg["hour"], cfg["minute"])


def _row_to_dict(row: ValueScanMarketMeta) -> dict[str, Any]:
    tz = market_tz(row.market)
    local = row.last_scan_at.astimezone(tz) if row.last_scan_at else None
    return {
        "market": row.market,
        "last_scan_at": row.last_scan_at.isoformat() if row.last_scan_at else None,
        "last_scan_at_local": local.strftime("%Y-%m-%d %H:%M:%S %Z") if local else None,
        "last_trading_date": row.last_trading_date,
        "scanned": row.last_scanned,
        "buy_signals": row.last_buy,
        "sell_signals": row.last_sell,
        "new_entries": row.last_new_entries,
        "new_exits": row.last_new_exits,
    }


def get_market_meta(market: str, session: Optional[Session] = None) -> Optional[dict[str, Any]]:
    own = session is None
    session = session or get_session()
    try:
        row = session.get(ValueScanMarketMeta, market)
        return _row_to_dict(row) if row else None
    finally:
        if own:
            session.close()


def get_all_market_meta() -> dict[str, dict[str, Any]]:
    session = get_session()
    try:
        rows = session.query(ValueScanMarketMeta).all()
        out = {m: {} for m in MARKET_CONFIG}
        for row in rows:
            out[row.market] = _row_to_dict(row)
        return out
    finally:
        session.close()


def needs_scan_today(market: str) -> bool:
    """오늘(시장 거래일) 아직 스캔 안 했으면 True."""
    meta = get_market_meta(market)
    today = trading_date(market)
    if meta is None:
        return True
    return meta.get("last_trading_date") != today


def should_run_catchup(market: str) -> bool:
    """평일 + 스캔 시각 이후 + 오늘 미스캔."""
    if market not in MARKET_CONFIG:
        return False
    if not is_weekday(market):
        return False
    if not is_past_scan_window(market):
        return False
    return needs_scan_today(market)


def record_market_scan(
    market: str,
    *,
    scanned: int = 0,
    buy: int = 0,
    sell: int = 0,
    new_entries: int = 0,
    new_exits: int = 0,
    when: Optional[datetime] = None,
) -> dict[str, Any]:
    """스캔 완료 시 DB에 시장별 마지막 시각·거래일 저장."""
    now = when or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    tdate = trading_date(market, now)

    session = get_session()
    try:
        row = session.get(ValueScanMarketMeta, market)
        if row is None:
            row = ValueScanMarketMeta(market=market)
            session.add(row)
        row.last_scan_at = now
        row.last_trading_date = tdate
        row.last_scanned = scanned
        row.last_buy = buy
        row.last_sell = sell
        row.last_new_entries = new_entries
        row.last_new_exits = new_exits
        session.commit()
        session.refresh(row)
        logger.info(
            "[ValueScan] recorded %s scan trading_date=%s at=%s",
            market,
            tdate,
            now.isoformat(),
        )
        return _row_to_dict(row)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def build_schedule_status(*, running: bool) -> dict[str, Any]:
    """API / 대시보드용."""
    markets = get_all_market_meta()
    now_kst = datetime.now(_KST)
    now_et = datetime.now(_ET)
    catchup: dict[str, bool] = {}
    for m in MARKET_CONFIG:
        catchup[m] = should_run_catchup(m)

    return {
        "running": running,
        "server_utc": datetime.now(UTC).isoformat(),
        "server_kst": now_kst.strftime("%Y-%m-%d %H:%M:%S"),
        "server_et": now_et.strftime("%Y-%m-%d %H:%M:%S"),
        "markets": markets,
        "catchup_pending": catchup,
        "windows": {
            m: {
                "after_local": f"{cfg['hour']:02d}:{cfg['minute']:02d}",
                "tz": str(cfg["tz"]),
                "trading_date": trading_date(m),
                "weekday": is_weekday(m),
                "past_window": is_past_scan_window(m),
                "needs_scan_today": needs_scan_today(m),
            }
            for m, cfg in MARKET_CONFIG.items()
        },
    }
