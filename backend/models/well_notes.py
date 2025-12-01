# backend/models/well_notes.py
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from datetime import datetime
from sqlalchemy.orm import relationship

from ..db import Base


class WellNote(Base):
    __tablename__ = "well_notes"

    id = Column(Integer, primary_key=True, index=True)

    # привязка к скважине
    well_id = Column(Integer, ForeignKey("wells.id"), index=True, nullable=False)

    # ВРЕМЯ НАБЛЮДЕНИЯ / ЗАМЕТКИ (то, что ты ставишь в форме note_time)
    note_time = Column(DateTime, nullable=False)

    # Текст заметки
    text = Column(String(2000), nullable=False)

    # Служебные таймстемпы (когда создано/обновлено в системе)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # связь обратно к скважине
    well = relationship("Well", back_populates="notes")