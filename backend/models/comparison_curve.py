"""ORM-модель `comparison_curve` — одна кривая в наборе сравнения.

Источники данных (`source`) — те же, что в `/api/customer-daily/series`:
  * 'customer'     — well_daily (сводка УзКорГаз).
  * 'our_pressure' — наши манометры LoRa, агрегированные по часам.
  * 'our_flow'     — наш расчётный дебит из flow-конвейера.
  * 'baseline'     — зафиксированный baseline (ссылка `baseline_id`).

Период:
  * Если `baseline_id` задан — period_from/period_to берём из baseline.
  * Иначе — используем поля `period_from` / `period_to` напрямую.

`metric` — какой ряд показывать на оси Y:
  * 'q_total' / 'q_working' — дебит.
  * 'dp'                    — перепад давления.
  * 'p_wellhead' / 'p_flowline' — давления.

`label` — подпись в легенде. `color` — HEX-цвет (опционально, иначе авто).
`description` — текст под кривой в PDF (необязательное).
"""
from __future__ import annotations

from sqlalchemy import (
    Column, Date, ForeignKey, Index, Integer, String, Text,
)
from sqlalchemy.orm import relationship

from ..db import Base


class ComparisonCurve(Base):
    __tablename__ = "comparison_curve"

    id = Column(Integer, primary_key=True)

    set_id = Column(
        Integer, ForeignKey("comparison_set.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    order_index = Column(Integer, nullable=False, server_default="0")

    source = Column(String(32), nullable=False)
    # 'customer' | 'our_pressure' | 'our_flow' | 'baseline'

    baseline_id = Column(
        Integer, ForeignKey("customer_baseline.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Для source='baseline' — обязателен; для остальных — игнорируется.

    period_from = Column(Date, nullable=True)
    period_to = Column(Date, nullable=True)
    # Для source != 'baseline' — обязательны.

    metric = Column(String(32), nullable=False)
    # 'q_total' | 'q_working' | 'dp' | 'p_wellhead' | 'p_flowline'

    label = Column(String(200), nullable=False)
    color = Column(String(16), nullable=True)
    description = Column(Text, nullable=True)

    set = relationship("ComparisonSet", back_populates="curves")

    __table_args__ = (
        Index("ix_comparison_curve_set_order", "set_id", "order_index"),
    )

    def __repr__(self) -> str:
        return (
            f"<ComparisonCurve #{self.id} set={self.set_id} "
            f"src={self.source} metric={self.metric}>"
        )
