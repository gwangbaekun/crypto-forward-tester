"""DB models for forward test."""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def create_tables(engine) -> None:
    Base.metadata.create_all(engine)
    _ensure_forward_trades_columns(engine)
    _ensure_polymarket_signals_columns(engine)


def _ensure_forward_trades_columns(engine) -> None:
    """
    Backfill columns for legacy SQLite DB files.

    SQLAlchemy create_all() creates missing tables only, so existing tables
    need explicit ALTER TABLE statements for newly added columns.
    """
    insp = inspect(engine)
    if "forward_trades" not in insp.get_table_names():
        return

    existing_columns = {c["name"] for c in insp.get_columns("forward_trades")}
    required_columns = {
        "pnl_pct_net": "ALTER TABLE forward_trades ADD COLUMN pnl_pct_net FLOAT",
    }

    missing_alters = [
        alter_sql
        for col_name, alter_sql in required_columns.items()
        if col_name not in existing_columns
    ]
    if not missing_alters:
        return

    # SQLite auto-commits DDL; begin() keeps behavior consistent on other DBs.
    with engine.begin() as conn:
        for alter_sql in missing_alters:
            conn.execute(text(alter_sql))


class ForwardTrade(Base):
    """Forward test 가상 거래."""

    __tablename__ = "forward_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    entry_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    entry_report_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    trigger_tfs: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    direction_detail: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    entry_source: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, default="engine")

    sl_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tp1_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tp2_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    sl_history: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tp1_history: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 재시작 후 포지션 완전 복구용 — tpsl_mode, level_map, tp_levels, sl_levels 등 직렬화
    position_meta: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pnl_pct_net: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    close_note: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    strategy: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)


class ValueScanMarketMeta(Base):
    """시장별 마지막 일간 스캔 (하루 1회 catch-up 판단용)."""

    __tablename__ = "value_scan_market_meta"

    market: Mapped[str] = mapped_column(String(16), primary_key=True)
    last_scan_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_trading_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True, index=True)
    last_scanned: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_buy: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_sell: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_new_entries: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_new_exits: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class ValueScanPosition(Base):
    """Value Scan 오픈 포지션 (KOSPI / NASDAQ)."""

    __tablename__ = "value_scan_positions"
    __table_args__ = (UniqueConstraint("market", "symbol", name="uq_value_scan_pos_market_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    sector: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    first_entry_date: Mapped[str] = mapped_column(String(10), nullable=False)
    invested_usd: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    per: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sector_median: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    eps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fwd_eps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pbr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    lots: Mapped[list["ValueScanLot"]] = relationship(
        back_populates="position",
        cascade="all, delete-orphan",
        order_by="ValueScanLot.lot_date",
    )


class ValueScanLot(Base):
    """포지션 내 $1 lot (추가 매수 단위)."""

    __tablename__ = "value_scan_lots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("value_scan_positions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lot_date: Mapped[str] = mapped_column(String(10), nullable=False)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unit_usd: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    position: Mapped["ValueScanPosition"] = relationship(back_populates="lots")


class ValueScanClosedTrade(Base):
    """청산된 Value Scan 거래."""

    __tablename__ = "value_scan_closed_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    sector: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    first_entry_date: Mapped[str] = mapped_column(String(10), nullable=False)
    exit_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    invested_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pnl_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hold_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    per: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sector_median: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    eps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fwd_eps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pbr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    lots: Mapped[list["ValueScanClosedLot"]] = relationship(
        back_populates="trade",
        cascade="all, delete-orphan",
        order_by="ValueScanClosedLot.lot_date",
    )


class ValueScanClosedLot(Base):
    """청산 시점 lot 스냅샷."""

    __tablename__ = "value_scan_closed_lots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("value_scan_closed_trades.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lot_date: Mapped[str] = mapped_column(String(10), nullable=False)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unit_usd: Mapped[float] = mapped_column(Float, nullable=True, default=1.0)

    trade: Mapped["ValueScanClosedTrade"] = relationship(back_populates="lots")


def _ensure_polymarket_signals_columns(engine) -> None:
    """polymarket_signals 신규 컬럼 backfill (기존 DB 호환)."""
    insp = inspect(engine)
    if "polymarket_signals" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("polymarket_signals")}
    required = {
        "event_end_ts":   "ALTER TABLE polymarket_signals ADD COLUMN event_end_ts INTEGER",
        "is_resolved":    "ALTER TABLE polymarket_signals ADD COLUMN is_resolved INTEGER DEFAULT 0",
        "actual_outcome": "ALTER TABLE polymarket_signals ADD COLUMN actual_outcome VARCHAR(8)",
        "actual_pnl":     "ALTER TABLE polymarket_signals ADD COLUMN actual_pnl FLOAT",
        "resolved_at":    "ALTER TABLE polymarket_signals ADD COLUMN resolved_at TIMESTAMP",
    }
    missing = [sql for col, sql in required.items() if col not in existing]
    if not missing:
        return
    with engine.begin() as conn:
        for sql in missing:
            conn.execute(text(sql))


class ValueScanSnapshot(Base):
    """스캔 결과 rows — date + market 기준으로 upsert."""

    __tablename__ = "value_scan_snapshots"

    id:         Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    date:       Mapped[str]      = mapped_column(String(10), nullable=False, index=True)
    market:     Mapped[str]      = mapped_column(String(16), nullable=False, index=True)
    rows_json:  Mapped[str]      = mapped_column(Text, nullable=False)
    saved_at:   Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("date", "market", name="uq_vs_snapshot_date_market"),)


class PolymarketSignal(Base):
    """Polymarket 전략 시그널 로그."""

    __tablename__ = "polymarket_signals"

    id:             Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy:       Mapped[str]            = mapped_column(String(32),  nullable=False, index=True)
    condition_id:   Mapped[Optional[str]]  = mapped_column(String(128), nullable=True,  index=True)
    question:       Mapped[Optional[str]]  = mapped_column(String(512), nullable=True)
    signal_type:    Mapped[Optional[str]]  = mapped_column(String(32),  nullable=True)
    yes_price:      Mapped[Optional[float]] = mapped_column(Float,       nullable=True)
    no_price:       Mapped[Optional[float]] = mapped_column(Float,       nullable=True)
    pair_cost:      Mapped[Optional[float]] = mapped_column(Float,       nullable=True)
    divergence:     Mapped[Optional[float]] = mapped_column(Float,       nullable=True)
    side:           Mapped[Optional[str]]  = mapped_column(String(8),   nullable=True)
    volume_usd:     Mapped[Optional[float]] = mapped_column(Float,       nullable=True)
    hours_to_end:   Mapped[Optional[float]] = mapped_column(Float,       nullable=True)
    yes_token_id:   Mapped[Optional[str]]  = mapped_column(String(128), nullable=True)
    no_token_id:    Mapped[Optional[str]]  = mapped_column(String(128), nullable=True)
    event_end_ts:   Mapped[Optional[int]]  = mapped_column(Integer,      nullable=True)
    is_resolved:    Mapped[int]            = mapped_column(Integer,      nullable=False, default=0)
    actual_outcome: Mapped[Optional[str]]  = mapped_column(String(8),   nullable=True)
    actual_pnl:     Mapped[Optional[float]] = mapped_column(Float,       nullable=True)
    resolved_at:    Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at:     Mapped[datetime]       = mapped_column(DateTime,     nullable=False, default=datetime.utcnow)
    # 실거래 주문 추적
    poly_order_id:  Mapped[Optional[str]]  = mapped_column(String(128), nullable=True)
    order_status:   Mapped[Optional[str]]  = mapped_column(String(16),  nullable=True)  # filled/failed/skipped


class CTraderToken(Base):
    """cTrader OAuth tokens (singleton row, key='default')."""

    __tablename__ = "ctrader_tokens"

    key: Mapped[str] = mapped_column(String(32), primary_key=True, default="default")
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
