"""ORM-модель `period_report` — отчёты за произвольный период.

Хранит группу блоков анализа (period_analysis, segment, comparison)
как единый отчёт с названием и периодом.

blocks_snapshot — JSONB массив блоков:
  [
    {"kind": "period_analysis", "params": {...}, "data_snapshot": {...}},
    {"kind": "period_segment", "params": {...}, "data_snapshot": {...}},
    ...
  ]
"""
from __future__ import annotations

from sqlalchemy import (
    Column, Date, DateTime, ForeignKey, Index, Integer, String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from ..db import Base


class PeriodReport(Base):
    __tablename__ = "period_report"

    id = Column(Integer, primary_key=True, autoincrement=True)
    well_id = Column(Integer, ForeignKey("wells.id"), nullable=False, index=True)

    title = Column(String(200), nullable=False)  # "Отчет март 2026"
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)

    # Массив блоков: [{kind, params, data_snapshot}, ...]
    blocks_snapshot = Column(JSONB, nullable=False, default=list)

    # draft | final
    status = Column(String(20), nullable=False, default="draft")

    created_at = Column(
        DateTime(timezone=False), nullable=False, server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=False), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )

    __table_args__ = (
        Index("ix_period_report_well_period", "well_id", "period_start", "period_end"),
    )

    def __repr__(self) -> str:
        return f"<PeriodReport id={self.id} well={self.well_id} title={self.title!r}>"
