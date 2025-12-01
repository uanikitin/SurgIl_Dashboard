# backend/models/well_equipment.py

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from ..db import Base  # так же, как в других моделях (Well, Event и т.п.)


class WellEquipment(Base):
    """
    Оборудование на скважине.

    Спроектировано так, чтобы:
    - одна запись = один "жизненный цикл" установки
      (установка → работа → демонтаж)
    - историю можно смотреть по well_id и по конкретному оборудованию (id)
    - позже можно подвесить таблицу событий по оборудованию (ремонт, сбой и т.п.)
    """
    __tablename__ = "well_equipment"

    id = Column(Integer, primary_key=True, index=True)

    # Скважина, к которой привязано оборудование
    well_id = Column(Integer, ForeignKey("wells.id"), index=True, nullable=False)

    # Тип оборудования: код из справочника (например: "wellhead_sensor", "line_sensor" и т.п.)
    type_code = Column(String(50), nullable=False)

    # Серийный номер
    serial_number = Column(String(100), nullable=True)

    # Номер канала связи (1–50), опционально
    channel = Column(Integer, nullable=True)

    # Даты установки / демонтажа (для истории)
    installed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    removed_at = Column(DateTime, nullable=True)

    # Дополнительное примечание
    note = Column(String(500), nullable=True)

    # Технические поля (на будущее, удобно для логов)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # Связь обратно на Well (если в Well определишь relationship)
    well = relationship("Well", back_populates="equipment", lazy="joined")