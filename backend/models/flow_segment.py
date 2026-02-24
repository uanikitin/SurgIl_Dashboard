"""
ORM-модель для сохранённых участков анализа дебита.

Таблица flow_segment хранит выделенные пользователем участки
графика дебита с кэшированной статистикой (JSONB).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, CheckConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from backend.db import Base


class FlowSegment(Base):
    __tablename__ = "flow_segment"

    id = Column(Integer, primary_key=True)
    well_id = Column(Integer, ForeignKey("wells.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False, default="Участок")

    dt_start = Column(DateTime, nullable=False)  # UTC
    dt_end = Column(DateTime, nullable=False)     # UTC

    # Кэш статистики участка (вычисляется при создании)
    stats = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("dt_end > dt_start", name="chk_segment_range"),
    )
