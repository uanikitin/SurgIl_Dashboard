# backend/models/reagent_inventory.py
"""
Модель для хранения инвентаризаций (фактических остатков) реагентов.

При инвентаризации сохраняется:
- qty: фактическое количество (пересчёт)
- calculated_qty: расчётное количество на момент инвентаризации
- discrepancy: разница (факт - расчёт), недостача (<0) или излишек (>0)
"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, Text, DECIMAL, ForeignKey
from sqlalchemy.orm import relationship
from backend.db import Base


class ReagentInventorySnapshot(Base):
    __tablename__ = "reagent_inventory"

    id = Column(Integer, primary_key=True)

    reagent = Column(String, nullable=False, index=True)
    reagent_id = Column(Integer, ForeignKey("reagent_catalog.id"), nullable=True)

    # Фактическое количество при инвентаризации
    qty = Column(DECIMAL(14, 3), nullable=False)

    # Расчётное количество на момент инвентаризации (для отслеживания отклонений)
    calculated_qty = Column(DECIMAL(14, 3), nullable=True)

    # Отклонение: qty - calculated_qty (положительное = излишек, отрицательное = недостача)
    discrepancy = Column(DECIMAL(14, 3), nullable=True)

    unit = Column(String, nullable=False, default="шт")
    snapshot_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True
    )
    location = Column(String, nullable=True)
    comment = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)

    catalog_item = relationship("ReagentCatalog", backref="inventories")