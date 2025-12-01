# backend/models/well_channel.py

from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship

from backend.db import Base  # если Base у тебя в другом месте — оставь как было


class WellChannel(Base):
    __tablename__ = "well_channels"

    id = Column(Integer, primary_key=True, index=True)
    well_id = Column(Integer, ForeignKey("wells.id"), nullable=False)

    # колонка channel (integer) — номер/идентификатор канала
    channel = Column(Integer, nullable=False)

    # даты из БД: started_at / ended_at
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)

    # текстовое примечание
    note = Column(String(500), nullable=True)

    # служебные временные метки
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # связь с Well
    well = relationship("Well", back_populates="channels")