# backend/models/reagent_inventory.py (обновляем)
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, Text, DECIMAL, ForeignKey
from sqlalchemy.orm import relationship
from backend.db import Base


class ReagentInventorySnapshot(Base):
    __tablename__ = "reagent_inventory"

    id = Column(Integer, primary_key=True)

    reagent = Column(String, nullable=False)
    reagent_id = Column(Integer, ForeignKey("reagent_catalog.id"), nullable=True)

    # ✅ ПРАВИЛЬНО: Используем DECIMAL
    qty = Column(DECIMAL(14, 3), nullable=False)

    unit = Column(String, nullable=False, default="шт")
    snapshot_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    location = Column(String, nullable=True)
    comment = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)

    catalog_item = relationship("ReagentCatalog", backref="inventories")