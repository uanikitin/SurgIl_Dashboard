# backend/models/reagents.py (обновляем существующую модель)
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, DECIMAL, ForeignKey
from sqlalchemy.orm import relationship
from backend.db import Base


class ReagentSupply(Base):
    __tablename__ = "reagent_supplies"

    id = Column(Integer, primary_key=True)

    # Старое поле для обратной совместимости
    reagent = Column(String, nullable=False)

    # НОВОЕ: Внешний ключ на каталог
    reagent_id = Column(Integer, ForeignKey("reagent_catalog.id"), nullable=True)

    # ✅ ПРАВИЛЬНО: Используем DECIMAL для точных чисел
    qty = Column(DECIMAL(14, 3), nullable=False)  # 14 цифр всего, 3 после запятой

    # Единица измерения
    unit = Column(String, nullable=False, default="шт")

    received_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    source = Column(String, nullable=True)
    location = Column(String, nullable=True)
    comment = Column(Text, nullable=True)

    # Связь с каталогом
    catalog_item = relationship("ReagentCatalog", backref="supplies")