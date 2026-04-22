"""ORM-модель `well_daily` — суточные сводки скважин от заказчика (УзКорГаз).

Источник данных: ежемесячный Excel-файл «Суточная сводка <МЕСЯЦ ГОД>г.xlsx».
Парсер: backend/utils/parsing_day_report_UZKOR.py.

Уникальный ключ: (date, ggu, well). Повторная загрузка того же месяца UPSERT-ит
существующие записи (фронт спрашивает подтверждение перезаписи).
"""
from __future__ import annotations

from sqlalchemy import Boolean, Column, Date, DateTime, Float, String
from sqlalchemy.sql import func

from ..db import Base


class WellDaily(Base):
    __tablename__ = "well_daily"

    date = Column(Date, primary_key=True, nullable=False)
    ggu = Column(String(16), primary_key=True, nullable=False)
    well = Column(String(32), primary_key=True, nullable=False)

    choke_mm = Column(Float, nullable=True)
    p_wellhead = Column(Float, nullable=True)
    p_annular = Column(Float, nullable=True)
    annular_packer = Column(Boolean, nullable=False, default=False, server_default="false")
    p_flowline = Column(Float, nullable=True)
    q_gas_total = Column(Float, nullable=True)
    q_gas_working = Column(Float, nullable=True)
    shutdown_min = Column(Float, nullable=True)
    p_static = Column(Float, nullable=True)

    source_sheet = Column(String(128), nullable=True)
    source_file = Column(String(255), nullable=True)
    loaded_at = Column(
        DateTime(timezone=False),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<WellDaily {self.date} {self.ggu}/{self.well}>"
