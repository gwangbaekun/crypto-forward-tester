"""DB models for forward test."""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def create_tables(engine) -> None:
    Base.metadata.create_all(engine)


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
