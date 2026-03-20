"""
SensorDailyReport — ежесуточная сводка качества работы датчика.

Одна строка = один физический датчик за одни сутки.
Для скважины с двумя датчиками (tube + line) — 2 строки.
Таблица хранится в PostgreSQL, заполняется ежедневным джобом.
"""

from sqlalchemy import (
    Column, Integer, Float, String, Date, DateTime,
    ForeignKey, UniqueConstraint, Index, func,
)

from backend.db import Base


class SensorDailyReport(Base):
    __tablename__ = "sensor_daily_report"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # --- Идентификация ---
    report_date = Column(Date, nullable=False)                         # сутки (UTC)
    well_id = Column(Integer, ForeignKey("wells.id"), nullable=False)
    sensor_role = Column(String(10), nullable=False)                   # 'tube' | 'line'
    lora_sensor_id = Column(Integer, nullable=True)                    # lora_sensors.id (может быть NULL)
    sensor_serial = Column(String(64), nullable=True)                  # серийный номер на дату отчёта

    # --- Геопривязка (денормализация) ---
    well_name = Column(String(128), nullable=True)
    well_lat = Column(Float, nullable=True)
    well_lon = Column(Float, nullable=True)

    # --- Объём данных ---
    expected_readings = Column(Integer, default=1440)     # сколько строк должно быть за сутки
    actual_readings = Column(Integer, default=0)          # сколько строк реально в БД
    missing_count = Column(Integer, default=0)            # NULL значения (датчик -1/-2 при импорте)
    false_zero_count = Column(Integer, default=0)         # ложные нули (== 0.0)
    out_of_range_count = Column(Integer, default=0)       # вне диапазона (< 0 или > 100)
    valid_count = Column(Integer, default=0)              # достоверные точки
    uptime_pct = Column(Float, default=0)                 # valid / expected * 100

    # --- Статистика давления ---
    p_mean = Column(Float, nullable=True)
    p_std = Column(Float, nullable=True)
    p_min = Column(Float, nullable=True)
    p_max = Column(Float, nullable=True)
    p_median = Column(Float, nullable=True)

    # --- Выбросы ---
    spikes_hampel = Column(Integer, default=0)
    spikes_instant = Column(Integer, default=0)
    spikes_pct = Column(Float, default=0)                 # (hampel + instant) / actual * 100

    # --- Синхронность пары ---
    sync_both_ok = Column(Integer, default=0)             # оба датчика работали
    sync_only_this = Column(Integer, default=0)           # только этот датчик
    sync_only_other = Column(Integer, default=0)          # только другой
    sync_both_miss = Column(Integer, default=0)           # оба не передавали
    sync_both_ok_pct = Column(Float, default=0)           # sync_both_ok / expected * 100

    # --- Временные разрывы ---
    gap_count = Column(Integer, default=0)                # разрывов > 5 мин
    gap_max_minutes = Column(Integer, default=0)          # самый длинный разрыв
    gap_total_minutes = Column(Integer, default=0)        # суммарное время без данных

    # --- Деградация датчика ---
    degradation_count = Column(Integer, default=0)        # эпизодов деградации за сутки
    degradation_total_min = Column(Integer, default=0)    # суммарное время деградации (мин)
    degradation_max_min = Column(Integer, default=0)      # максимальный эпизод (мин)

    # --- Итоговая оценка ---
    quality_grade = Column(String(1), nullable=True)      # A / B / C / F
    quality_flags = Column(String(500), nullable=True)    # JSON: ["many_zeros", "long_gap"]

    # --- Служебные ---
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("report_date", "well_id", "sensor_role",
                         name="uq_sensor_daily_report"),
        Index("ix_sdr_date", "report_date"),
        Index("ix_sdr_well", "well_id"),
        Index("ix_sdr_grade", "quality_grade"),
    )

    def __repr__(self):
        return (
            f"<SensorDailyReport {self.report_date} "
            f"well={self.well_id} {self.sensor_role} grade={self.quality_grade}>"
        )
