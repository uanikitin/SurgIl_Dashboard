# backend/models/lora_sensor.py
"""
LoRa-манометр (физический датчик).

Каждый канал = 2 датчика:
  - position='tube'  — манометр на устье (Ptr)
  - position='line'  — манометр на шлейфе (Pshl)

Датчик привязан к каналу навсегда (serial_number + channel).
При переезде на другую скважину меняется запись в well_channels,
а не здесь.
"""

from sqlalchemy import Column, Integer, String, DateTime, func
from sqlalchemy.orm import relationship

from backend.db import Base


class LoRaSensor(Base):
    __tablename__ = "lora_sensors"

    id = Column(Integer, primary_key=True, index=True)

    # Серийный номер датчика (уникальный)
    serial_number = Column(String(50), nullable=False, unique=True, index=True)

    # Номер канала (1–10), к которому привязан датчик
    channel = Column(Integer, nullable=False)

    # Место установки: 'tube' (устье/Ptr) или 'line' (шлейф/Pshl)
    position = Column(String(10), nullable=False)  # 'tube' | 'line'

    # Человекочитаемое описание (опционально)
    label = Column(String(100), nullable=True)

    # Примечание
    note = Column(String(500), nullable=True)

    # Служебные
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self):
        pos = "устье" if self.position == "tube" else "шлейф"
        return f"<LoRaSensor {self.serial_number} ch={self.channel} {pos}>"
