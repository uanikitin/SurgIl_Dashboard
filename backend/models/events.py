# backend/models/events.py
from sqlalchemy import (
    Column,
    BigInteger,
    String,
    Float,
    DateTime,
    Text,Integer, DECIMAL, ForeignKey
)
from ..db import Base


class Event(Base):
    """
    Модель для уже существующей таблицы events из телеграм-бота.
    НИЧЕГО не создаём, просто описываем структуру для чтения.
    """

    __tablename__ = "events"  # ← именно то имя, что на скрине

    id = Column(BigInteger, primary_key=True, index=True)

    chat_id = Column(BigInteger, nullable=True)
    user_id = Column(BigInteger, nullable=True)

    # В твоей БД это колонка well (тип text):
    well = Column(String, index=True)  # здесь лежит номер скважины как строка, например "1367"

    event_type = Column(String, nullable=True)
    # reagent    = Column(String, nullable=True)
    # qty        = Column(Float, nullable=True)

    p_tube     = Column(Float, nullable=True)
    p_line     = Column(Float, nullable=True)

    event_time = Column(DateTime, index=True)

    description = Column(Text, nullable=True)

    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)

    created_at  = Column(DateTime, nullable=True)

    equip_type   = Column(String, nullable=True)
    equip_points = Column(String, nullable=True)
    equip_other  = Column(String, nullable=True)

    purge_phase = Column(String, nullable=True)
    other_kind  = Column(String, nullable=True)
    geo_status  = Column(String, nullable=True)

    # Для расхода реагентов
    reagent = Column(String, nullable=True)
    # ✅ ПРАВИЛЬНО: Используем DECIMAL
    qty = Column(DECIMAL(10, 3), nullable=True)