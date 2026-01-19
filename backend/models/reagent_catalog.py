# backend/models/reagent_catalog.py
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, Float
from backend.db import Base


class ReagentCatalog(Base):
    """Справочник всех реагентов в системе"""
    __tablename__ = "reagent_catalog"

    id = Column(Integer, primary_key=True)

    # Название реагента (уникальное)
    name = Column(String(255), unique=True, nullable=False, index=True)

    # Код/артикул (опционально)
    code = Column(String(100), unique=True, nullable=True)

    # Единица измерения по умолчанию
    default_unit = Column(String(20), nullable=False, default="шт")

    # Категория для группировки
    category = Column(String(100), nullable=True)

    # Минимальный и максимальный остаток для уведомлений
    min_stock = Column(Float, nullable=True)
    max_stock = Column(Float, nullable=True)

    # Активен ли реагент
    is_active = Column(Boolean, default=True)

    # Дополнительная информация
    description = Column(Text, nullable=True)
    supplier_info = Column(Text, nullable=True)  # Основной поставщик

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
