"""
PressureLatest — последние известные давления по каждой скважине (PostgreSQL).

Одна строка на скважину. Обновляется при каждом sync.
Используется для плиток на дашборде (текущее давление).
"""

from sqlalchemy import Column, Integer, Float, DateTime, ForeignKey

from backend.db import Base


class PressureLatest(Base):
    __tablename__ = "pressure_latest"

    well_id = Column(Integer, ForeignKey("wells.id"), primary_key=True)
    measured_at = Column(DateTime, nullable=True)       # UTC
    p_tube = Column(Float, nullable=True)
    p_line = Column(Float, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<PressureLatest well={self.well_id} tube={self.p_tube} line={self.p_line}>"
