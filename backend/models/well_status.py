# backend/models/well_status.py

from sqlalchemy import Column, Integer, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from ..db import Base

# Допустимые значения статуса (для справки / UI)
ALLOWED_STATUS = (
    "Наблюдение",
    "Адаптация",
    "Оптимизация",
    "Освоение",
    "Скважина не обслуживается",
    "Простой",
    "Другое",
)


class WellStatus(Base):
    __tablename__ = "well_status"  # таблица истории статусов

    id = Column(Integer, primary_key=True)
    well_id = Column(
        Integer,
        ForeignKey("wells.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Статус храним текстом (например "Наблюдение", "Адаптация", "На КРС", ...)
    status = Column(Text, nullable=False)

    # Начало / конец действия статуса
    dt_start = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    dt_end = Column(
        DateTime(timezone=True),
        nullable=True,   # NULL = статус ещё актуален
    )

    # Доп. примечание к статусу (если нужно)
    note = Column(Text, nullable=True)

    # Быстрый доступ к объекту скважины (JOIN)
    well = relationship("Well", lazy="joined")

    def __repr__(self) -> str:
        return (
            f"<WellStatus well_id={self.well_id} "
            f"status={self.status!r} start={self.dt_start} end={self.dt_end}>"
        )

    @property
    def is_active(self) -> bool:
        """Статус ещё действует (нет dt_end)."""
        return self.dt_end is None

    def duration_days(self, now=None) -> float:
        """
        Длительность статуса в сутках.
        Если dt_end = NULL, считаем до now (по умолчанию текущий момент).
        """
        from datetime import datetime

        if now is None:
            now = datetime.now(self.dt_start.tzinfo)

        end = self.dt_end or now
        delta = end - self.dt_start
        return delta.total_seconds() / 86400.0  # секунды → сутки