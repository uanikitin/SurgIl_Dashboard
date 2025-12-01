from sqlalchemy import Column, Integer, String, Float
from sqlalchemy.orm import relationship
from ..db import Base
from .well_channel import WellChannel


class Well(Base):
    __tablename__ = "wells"  # таблица уже может существовать у тебя в БД

    id   = Column(Integer, primary_key=True)
    number = Column(Integer, nullable=True)
    name = Column(String(64), unique=True, nullable=False)  # «Скв 1367»
    lat  = Column(Float, nullable=True)   # широта (WGS84)
    lon  = Column(Float, nullable=True)   # долгота (WGS84)
    description = Column(String, nullable=True)
    current_status = Column(String(64), nullable=True)

    equipment = relationship(
        "WellEquipment",
        back_populates="well",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    channels = relationship("WellChannel", back_populates="well")

    # <<< НОВОЕ: список заметок для скважины >>>
    notes = relationship(
        "WellNote",
        back_populates="well",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Well id={self.id} name={self.name!r}>"