"""Базовые показатели скважины (по данным заказчика, наблюдения и т.д.)

Для адаптационного отчёта: «закрепляем» характерные значения за период
(например, 1 месяц до начала работ) и потом сравниваем с ними любой другой
период. На скважину может быть несколько baseline-ов с разными именами.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey, Index,
    Integer, String, Text,
)
from sqlalchemy.sql import func

from ..db import Base


class CustomerBaseline(Base):
    __tablename__ = "customer_baseline"

    id = Column(Integer, primary_key=True)
    well_id = Column(
        Integer, ForeignKey("wells.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name = Column(String(128), nullable=False)
    source = Column(String(32), nullable=False, server_default="customer")
    # 'customer' / 'observation' / 'manual'

    period_from = Column(Date, nullable=False)
    period_to = Column(Date, nullable=False)

    days_count = Column(Integer, nullable=True)

    q_total_avg = Column(Float, nullable=True)
    q_total_median = Column(Float, nullable=True)
    q_working_avg = Column(Float, nullable=True)
    q_working_median = Column(Float, nullable=True)

    p_wellhead_avg = Column(Float, nullable=True)
    p_wellhead_median = Column(Float, nullable=True)
    p_flowline_avg = Column(Float, nullable=True)
    p_flowline_median = Column(Float, nullable=True)
    dp_avg = Column(Float, nullable=True)
    dp_median = Column(Float, nullable=True)

    shutdown_min_total = Column(Float, nullable=True)
    shutdown_min_avg = Column(Float, nullable=True)
    shutdown_days_count = Column(Integer, nullable=True)

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    created_by = Column(String(200), nullable=True)
    is_pinned = Column(Boolean, nullable=False, server_default="false")

    __table_args__ = (
        Index("ix_customer_baseline_well_pinned", "well_id", "is_pinned"),
    )

    def __repr__(self) -> str:
        return (
            f"<CustomerBaseline #{self.id} well={self.well_id} "
            f"name={self.name!r} {self.period_from}..{self.period_to}>"
        )
