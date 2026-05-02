"""ORM-модель `comparison_set` — набор кривых для сравнения.

Используется в «Конструкторе сравнения» в отчёте адаптации и на странице
данных заказчика. Один набор = один график с N кривыми (см.
`comparison_curve`). Все кривые набора накладываются на общую ось X
(`x_axis_mode`: 'offset' — дни от начала каждого периода, 'date' — даты).

Скважина одна на набор (требование: «в рамках одного отчёта только одна
скважина»). Для разных скважин — разные наборы.

`in_report=True` означает, что набор будет включён в PDF-главу
«Сравнение» (графики встраиваются по тексту нужных глав).
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..db import Base


class ComparisonSet(Base):
    __tablename__ = "comparison_set"

    id = Column(Integer, primary_key=True)
    well_id = Column(
        Integer, ForeignKey("wells.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    # Подпись/описание под графиком в PDF.

    x_axis_mode = Column(
        String(16), nullable=False, server_default="offset",
    )
    # 'offset' — день от начала каждой кривой; 'date' — фактические даты.

    in_report = Column(
        Boolean, nullable=False, server_default="true",
    )
    # Включать ли в PDF-главу.

    sort_order = Column(Integer, nullable=False, server_default="0")

    created_at = Column(
        DateTime, nullable=False, server_default=func.now(),
    )
    updated_at = Column(
        DateTime, nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )
    created_by = Column(String(200), nullable=True)

    curves = relationship(
        "ComparisonCurve",
        back_populates="set",
        cascade="all, delete-orphan",
        order_by="ComparisonCurve.order_index",
    )

    __table_args__ = (
        Index("ix_comparison_set_well_inreport", "well_id", "in_report"),
    )

    def __repr__(self) -> str:
        return f"<ComparisonSet #{self.id} well={self.well_id} name={self.name!r}>"
