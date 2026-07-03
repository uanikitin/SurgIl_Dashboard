"""Библиотека подписантов финансового акта (двуязычно, по сторонам).

Хранит переиспользуемые записи подписантов. Для каждого акта выбираются:
- подписанты в ШАПКЕ (преамбула) и подписанты ВНИЗУ (блок подписей) — независимо.
Выбор акта фиксируется в Document.meta (header_sigs / sign_sigs).
"""
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func

from ..db import Base

SIDE_CONTRACTOR = "contractor"  # Исполнитель (ООО «UNITOOL»)
SIDE_CUSTOMER = "customer"      # Заказчик (СП ООО «Uz-Kor Gas Chemical»)


class ActSignatory(Base):
    __tablename__ = "act_signatory"

    id = Column(Integer, primary_key=True)
    side = Column(String(20), nullable=False, index=True)
    position_ru = Column(String(200), nullable=False)
    position_en = Column(String(200))
    name_ru = Column(String(200), nullable=False)
    name_en = Column(String(200))
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    def as_dict(self) -> dict:
        return {
            "id": self.id, "side": self.side,
            "position_ru": self.position_ru, "position_en": self.position_en or "",
            "name_ru": self.name_ru, "name_en": self.name_en or "",
        }
