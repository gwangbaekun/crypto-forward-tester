"""
공통 Trade DTO — Forward DB / 백테스트 trades[] 를 동일 JSON 스키마로 통일.

차트 API는 `chart_contract` 모듈의 btc_backtest `/api/chart` 형태와 정렬한다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = "1"


def _parse_iso_to_ts(iso: Optional[str]) -> int:
    if not iso:
        return 0
    s = iso.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (TypeError, ValueError):
        return 0


@dataclass
class TradeDTO:
    """캐논 거래 한 건 — forward / backtest 공통."""

    schema_version: str = SCHEMA_VERSION
    trade_id: Optional[int] = None
    strategy_run_id: Optional[str] = None
    symbol: str = ""
    side: str = ""  # "long" | "short"
    entry_ts: int = 0
    exit_ts: int = 0
    entry: float = 0.0
    exit: Optional[float] = None
    pnl_pct: Optional[float] = None
    label: Optional[str] = None
    status: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def from_forward_trade_row(
    row: Dict[str, Any],
    *,
    strategy_run_id: Optional[str] = None,
) -> TradeDTO:
    """
    `BaseForwardTest.get_trades_from_db` 한 행(dict) → TradeDTO.
    """
    side = str(row.get("side") or "").lower()
    if side not in ("long", "short"):
        side = "long"

    entry_ts = _parse_iso_to_ts(row.get("opened_at"))
    exit_ts = _parse_iso_to_ts(row.get("closed_at"))

    meta = {
        "sl_price": row.get("sl_price"),
        "tp1_price": row.get("tp1_price"),
        "duration_min": row.get("duration_min"),
        "close_note": row.get("close_note"),
        "source": "forward_test",
    }

    label = row.get("close_note") or row.get("status")

    return TradeDTO(
        trade_id=row.get("id"),
        strategy_run_id=strategy_run_id,
        symbol=str(row.get("symbol") or ""),
        side=side,
        entry_ts=entry_ts,
        exit_ts=exit_ts,
        entry=float(row.get("entry_price") or 0),
        exit=float(row["exit_price"]) if row.get("exit_price") is not None else None,
        pnl_pct=float(row["pnl_pct"]) if row.get("pnl_pct") is not None else None,
        label=str(label) if label is not None else None,
        status=str(row.get("status") or "") or None,
        meta={k: v for k, v in meta.items() if v is not None},
    )


def from_backtest_cvd_trade(
    row: Dict[str, Any],
    *,
    symbol: str,
    strategy_run_id: Optional[str] = None,
) -> TradeDTO:
    """
    `serve_backtest.api_backtest` 의 trade dict 한 건 → TradeDTO.

    - direction: 1 → long, -1 → short
    - entry_ts / exit_ts: 이미 Unix 초
    """
    direction = row.get("direction")
    if direction == 1:
        side = "long"
    elif direction == -1:
        side = "short"
    else:
        side = "long"  # 폴백

    exit_px = row.get("exit_px")
    return TradeDTO(
        trade_id=int(row["id"]) if row.get("id") is not None else None,
        strategy_run_id=strategy_run_id,
        symbol=symbol,
        side=side,
        entry_ts=int(row.get("entry_ts") or 0),
        exit_ts=int(row.get("exit_ts") or 0),
        entry=float(row.get("entry_px") or 0),
        exit=float(exit_px) if exit_px is not None else None,
        pnl_pct=float(row["pnl_pct"]) if row.get("pnl_pct") is not None else None,
        label=str(row.get("reason") or "") or None,
        status=str(row.get("reason") or "") or None,
        meta={
            "source": "backtest_cvd",
            "m15i": row.get("m15i"),
            "m15_ts": row.get("m15_ts"),
            "minute": row.get("minute"),
            "running_vr": row.get("running_vr"),
            "tps": row.get("tps"),
            "advances": row.get("advances"),
            "final_tp_idx": row.get("final_tp_idx"),
        },
    )


def forward_rows_to_dtos(
    rows: Iterable[Dict[str, Any]],
    *,
    strategy_run_id: Optional[str] = None,
) -> List[TradeDTO]:
    return [from_forward_trade_row(r, strategy_run_id=strategy_run_id) for r in rows]


def backtest_cvd_rows_to_dtos(
    rows: Iterable[Dict[str, Any]],
    *,
    symbol: str,
    strategy_run_id: Optional[str] = None,
) -> List[TradeDTO]:
    return [from_backtest_cvd_trade(r, symbol=symbol, strategy_run_id=strategy_run_id) for r in rows]
