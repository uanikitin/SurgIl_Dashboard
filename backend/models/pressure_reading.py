"""
PressureReading — сырые замеры давлений (локальный SQLite pressure.db).

Одна запись = один момент времени для одной скважины.
Данные приходят из двух источников (CSV и SQLite Tracing),
дубли отбрасываются по UNIQUE(well_id, measured_at).
"""

from sqlalchemy import Column, Integer, Float, String, DateTime, UniqueConstraint, Index

from backend.db_pressure import PressureBase


class PressureReading(PressureBase):
    __tablename__ = "pressure_readings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    well_id = Column(Integer, nullable=False)          # FK к wells.id (логический)
    channel = Column(Integer, nullable=False)           # канал на момент импорта
    measured_at = Column(DateTime, nullable=False)      # UTC
    p_tube = Column(Float, nullable=True)               # давление на устье (Ptr)
    p_line = Column(Float, nullable=True)               # давление на шлейфе (Pshl)
    source = Column(String(10), nullable=False)         # "csv" или "sqlite"
    source_file = Column(String(128), nullable=True)    # имя файла-источника
    sensor_id_tube = Column(Integer, nullable=True)     # lora_sensors.id для p_tube
    sensor_id_line = Column(Integer, nullable=True)     # lora_sensors.id для p_line

    __table_args__ = (
        UniqueConstraint("well_id", "measured_at", name="uq_well_measured"),
        Index("ix_pressure_measured_at", "measured_at"),
    )

    def __repr__(self):
        return (
            f"<PressureReading well={self.well_id} "
            f"at={self.measured_at} tube={self.p_tube} line={self.p_line}>"
        )
