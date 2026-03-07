"""
ORM-модель для пользовательских аннотаций на графике давлений.

Аннотация привязана к скважине и конкретному моменту времени (point)
или временному диапазону (range). Отображается через chartjs-plugin-annotation.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey

from backend.db import Base


class ChartAnnotation(Base):
    __tablename__ = "chart_annotation"

    id = Column(Integer, primary_key=True)
    well_id = Column(Integer, ForeignKey("wells.id"), nullable=False, index=True)

    ann_type = Column(String(20), nullable=False, default="point")  # "point" | "range"
    dt_start = Column(DateTime, nullable=False)   # для point: момент; для range: начало
    dt_end = Column(DateTime, nullable=True)      # для range: конец; для point: NULL

    text = Column(String(500), nullable=False)
    color = Column(String(20), nullable=False, default="#ff9800")

    created_at = Column(DateTime, default=datetime.utcnow)
