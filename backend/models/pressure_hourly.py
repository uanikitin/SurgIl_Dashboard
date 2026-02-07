"""
PressureHourly — почасовые агрегаты давлений (PostgreSQL на Render.com).

Данные агрегируются из pressure.db (локальный SQLite) и отправляются сюда.
Дашборд читает только эту таблицу — минимальная нагрузка на PostgreSQL.
"""

from sqlalchemy import Column, Integer, Float, Boolean, DateTime, ForeignKey, UniqueConstraint

from backend.db import Base


class PressureHourly(Base):
    __tablename__ = "pressure_hourly"

    id = Column(Integer, primary_key=True, autoincrement=True)
    well_id = Column(Integer, ForeignKey("wells.id"), nullable=False)
    hour_start = Column(DateTime, nullable=False)       # начало часа, UTC
    p_tube_avg = Column(Float, nullable=True)
    p_tube_min = Column(Float, nullable=True)
    p_tube_max = Column(Float, nullable=True)
    p_line_avg = Column(Float, nullable=True)
    p_line_min = Column(Float, nullable=True)
    p_line_max = Column(Float, nullable=True)
    reading_count = Column(Integer, default=0)           # кол-во валидных замеров
    has_gaps = Column(Boolean, default=False)             # True если < 50 замеров/час

    __table_args__ = (
        UniqueConstraint("well_id", "hour_start", name="uq_pressure_hourly_well_hour"),
    )

    def __repr__(self):
        return f"<PressureHourly well={self.well_id} hour={self.hour_start}>"
