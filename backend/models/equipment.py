"""
Модели SQLAlchemy для управления оборудованием

Создайте файл: backend/models/equipment.py
"""

from __future__ import annotations
from datetime import datetime, date
from typing import Optional, List
from sqlalchemy import (
    Column, Integer, String, Text, Date, DateTime,
    Numeric, ForeignKey, JSON, Index, Boolean
)
from sqlalchemy.orm import relationship, Mapped, mapped_column
from backend.db import Base


class Equipment(Base):
    """
    Справочник оборудования
    """
    __tablename__ = "equipment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Основная информация
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    equipment_type: Mapped[Optional[str]] = mapped_column(String(100))
    serial_number: Mapped[Optional[str]] = mapped_column(String(100), unique=True)

    # Дополнительная информация
    description: Mapped[Optional[str]] = mapped_column(Text)
    manufacturer: Mapped[Optional[str]] = mapped_column(String(200))
    manufacture_date: Mapped[Optional[date]] = mapped_column(Date)

    # Технические характеристики
    specifications: Mapped[Optional[dict]] = mapped_column(JSON)

    # Статус
    status: Mapped[str] = mapped_column(String(50), default="available")
    current_location: Mapped[Optional[str]] = mapped_column(String(200))

    # Сервис
    last_maintenance_date: Mapped[Optional[date]] = mapped_column(Date)
    next_maintenance_date: Mapped[Optional[date]] = mapped_column(Date)
    maintenance_interval_days: Mapped[Optional[int]] = mapped_column(Integer)

    # Метаданные
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    condition = Column(String(20), default='working')

    # Relationships
    installations: Mapped[List["EquipmentInstallation"]] = relationship(
        "EquipmentInstallation",
        back_populates="equipment",
        order_by="EquipmentInstallation.installed_at.desc()"
    )
    maintenance_records: Mapped[List["EquipmentMaintenance"]] = relationship(
        "EquipmentMaintenance",
        back_populates="equipment",
        order_by="EquipmentMaintenance.maintenance_date.desc()"
    )

    def __repr__(self):
        return f"<Equipment {self.name} ({self.serial_number})>"


class EquipmentInstallation(Base):
    """
    История установок/демонтажа оборудования
    """
    __tablename__ = "equipment_installation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Связи
    equipment_id: Mapped[int] = mapped_column(Integer, ForeignKey("equipment.id"), nullable=False)
    well_id: Mapped[int] = mapped_column(Integer, ForeignKey("wells.id"), nullable=False)
    document_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("documents.id"))

    # Даты
    installed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    removed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Давления при установке
    tube_pressure_install: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))
    line_pressure_install: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))

    # Давления при демонтаже
    tube_pressure_remove: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))
    line_pressure_remove: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))

    # Дополнительная информация
    installation_reason: Mapped[Optional[str]] = mapped_column(Text)
    removal_reason: Mapped[Optional[str]] = mapped_column(Text)
    condition_on_install: Mapped[Optional[str]] = mapped_column(String(100))
    condition_on_removal: Mapped[Optional[str]] = mapped_column(String(100))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # Ответственные лица
    installed_by: Mapped[Optional[str]] = mapped_column(String(200))
    removed_by: Mapped[Optional[str]] = mapped_column(String(200))

    # Метаданные
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    equipment: Mapped["Equipment"] = relationship("Equipment", back_populates="installations")
    well: Mapped["Well"] = relationship("Well")
    document: Mapped[Optional["Document"]] = relationship("Document")

    installation_location = Column(String(50))  # Устье, НКТ, Шлейф, Затруб, Інше
    no_document_confirmed = Column(Boolean, default=False)

    def __repr__(self):
        return f"<EquipmentInstallation {self.equipment_id} on Well {self.well_id}>"


class EquipmentMaintenance(Base):
    """
    Сервисное обслуживание оборудования
    """
    __tablename__ = "equipment_maintenance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Связи
    equipment_id: Mapped[int] = mapped_column(Integer, ForeignKey("equipment.id"), nullable=False)

    # Информация о ТО
    maintenance_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    maintenance_type: Mapped[str] = mapped_column(String(100), nullable=False)

    # Детали
    description: Mapped[str] = mapped_column(Text, nullable=False)
    performed_by: Mapped[Optional[str]] = mapped_column(String(200))

    # Результаты
    status_before: Mapped[Optional[str]] = mapped_column(String(100))
    status_after: Mapped[Optional[str]] = mapped_column(String(100))
    issues_found: Mapped[Optional[str]] = mapped_column(Text)
    actions_taken: Mapped[Optional[str]] = mapped_column(Text)

    # Запчасти и материалы
    parts_used: Mapped[Optional[dict]] = mapped_column(JSON)
    cost: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))

    # Следующее ТО
    next_maintenance_date: Mapped[Optional[date]] = mapped_column(Date)

    # Метаданные
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    equipment: Mapped["Equipment"] = relationship("Equipment", back_populates="maintenance_records")

    def __repr__(self):
        return f"<EquipmentMaintenance {self.maintenance_type} for {self.equipment_id}>"


# Индексы
Index("idx_equipment_serial", Equipment.serial_number)
Index("idx_equipment_status", Equipment.status)
Index("idx_equipment_type", Equipment.equipment_type)

Index("idx_equip_install_equipment", EquipmentInstallation.equipment_id)
Index("idx_equip_install_well", EquipmentInstallation.well_id)
Index("idx_equip_install_dates", EquipmentInstallation.installed_at, EquipmentInstallation.removed_at)

Index("idx_equip_maint_equipment", EquipmentMaintenance.equipment_id)
Index("idx_equip_maint_date", EquipmentMaintenance.maintenance_date)
Index("idx_equip_maint_type", EquipmentMaintenance.maintenance_type)