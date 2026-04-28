"""ORM-модель `customer_report_block` — блоки анализа данных заказчика,
добавляемые в отчёт об адаптации.

3 типа блоков (kind):
  * baseline         — анализ базового периода ДО работ.
  * period_analysis  — анализ за выбранный период (обычно совпадает с отчётом).
  * comparison       — сравнение двух произвольных периодов.

Хранится:
  * params       — JSONB с параметрами запроса (period, source, well, metric…)
                   для воспроизводимого live-расчёта.
  * data_snapshot — JSONB снимок данных, который генерируется
                   при формировании PDF (для воспроизводимости отчёта
                   даже после изменения первичных данных / масок).
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from ..db import Base


class CustomerReportBlock(Base):
    __tablename__ = "customer_report_block"

    id = Column(Integer, primary_key=True, autoincrement=True)
    well_id = Column(Integer, ForeignKey("wells.id"), nullable=False, index=True)

    # 'baseline' | 'period_analysis' | 'comparison'
    kind = Column(String(32), nullable=False)

    title = Column(String(200), nullable=False)
    params = Column(JSONB, nullable=False, default=dict)
    data_snapshot = Column(JSONB, nullable=True)
    comment = Column(Text, nullable=True)

    in_report = Column(Boolean, nullable=False, default=True, server_default="true")
    sort_order = Column(Integer, nullable=False, default=0)

    created_at = Column(
        DateTime(timezone=False), nullable=False, server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=False), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )

    __table_args__ = (
        Index(
            "ix_crb_well_inreport_sort",
            "well_id", "in_report", "sort_order",
        ),
    )

    def __repr__(self) -> str:
        return f"<CustomerReportBlock id={self.id} well={self.well_id} kind={self.kind}>"
