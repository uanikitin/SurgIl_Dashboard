# backend/models/lora_sensor.py
"""
LoRa-манометры и история их установок на скважины.

Архитектура:
  1. LoRaSensor — физический датчик с "прошивкой" откуда читать данные:
     - csv_group: номер файла CSV (1-6) = DD.MM.YYYY.{group}_arc.csv
     - csv_channel: канал внутри файла (1-5) = Ptr_X / Pshl_X
     - csv_column: какую колонку читать ('Ptr' или 'Pshl')

  2. SensorInstallation — история установок датчика на скважины:
     - sensor_id → LoRaSensor
     - well_id → Well
     - position: куда установлен ('tube' = устье, 'line' = шлейф)
     - installed_at / removed_at — период работы на скважине

При импорте CSV:
  1. Из имени файла получаем csv_group
  2. Для каждой пары (csv_channel, csv_column) находим датчик
  3. По sensor_installations находим well_id и position на момент измерения
  4. Записываем данные в p_tube или p_line в зависимости от position
"""

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship

from backend.db import Base


class LoRaSensor(Base):
    """Физический датчик LoRa (манометр) с прошивкой."""
    __tablename__ = "lora_sensors"

    id = Column(Integer, primary_key=True, index=True)

    # Серийный номер датчика (уникальный)
    serial_number = Column(String(50), nullable=False, unique=True, index=True)

    # === Прошивка: откуда читать данные в CSV ===
    # Группа CSV файла (1-6): DD.MM.YYYY.{csv_group}_arc.csv
    csv_group = Column(Integer, nullable=False)

    # Канал внутри файла (1-5): соответствует Ptr_X / Pshl_X
    csv_channel = Column(Integer, nullable=False)

    # Какую колонку читать: 'Ptr' или 'Pshl'
    csv_column = Column(String(10), nullable=False)  # 'Ptr' | 'Pshl'

    # Человекочитаемое описание
    label = Column(String(100), nullable=True)

    # Примечание
    note = Column(String(500), nullable=True)

    # Служебные
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Связи
    installations = relationship("SensorInstallation", back_populates="sensor", cascade="all, delete-orphan")

    @property
    def csv_column_name(self) -> str:
        """Полное имя колонки в CSV: Ptr_1, Pshl_3 и т.д."""
        return f"{self.csv_column}_{self.csv_channel}"

    def __repr__(self):
        return f"<LoRaSensor {self.serial_number} g{self.csv_group}/{self.csv_column}_{self.csv_channel}>"


class SensorInstallation(Base):
    """История установок датчика на скважины."""
    __tablename__ = "sensor_installations"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Датчик
    sensor_id = Column(Integer, ForeignKey("lora_sensors.id", ondelete="CASCADE"), nullable=False)

    # Скважина
    well_id = Column(Integer, ForeignKey("wells.id", ondelete="CASCADE"), nullable=False)

    # Куда установлен на скважине: 'tube' (устье) или 'line' (шлейф)
    position = Column(String(10), nullable=False)  # 'tube' | 'line'

    # Период установки
    installed_at = Column(DateTime, nullable=False)
    removed_at = Column(DateTime, nullable=True)  # NULL = активная установка

    # Примечания
    notes = Column(String(500), nullable=True)

    # Служебные
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Связи
    sensor = relationship("LoRaSensor", back_populates="installations")
    well = relationship("Well")

    @property
    def is_active(self) -> bool:
        """Активная ли установка (датчик сейчас на этой скважине)."""
        return self.removed_at is None

    def __repr__(self):
        status = "active" if self.is_active else f"until {self.removed_at}"
        pos = "устье" if self.position == "tube" else "шлейф"
        return f"<SensorInstallation sensor={self.sensor_id} well={self.well_id} {pos} {status}>"
