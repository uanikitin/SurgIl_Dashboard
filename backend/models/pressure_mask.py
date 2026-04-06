"""
PressureMask — маска коррекции данных давления.

Хранит инструкцию по замене значений «плохого» датчика
на расчётный период. Оригинальные данные (pressure_raw) не изменяются —
коррекции применяются in-memory при загрузке графиков и расчётов.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from backend.db import Base


class PressureMask(Base):
    __tablename__ = "pressure_mask"

    id = Column(Integer, primary_key=True)
    well_id = Column(Integer, ForeignKey("wells.id"), nullable=False, index=True)

    # Тип проблемы (для аналитики и цвета на графике)
    problem_type = Column(
        String(20), nullable=False, default="manual",
    )  # 'hydrate' | 'comm_loss' | 'sensor_fault' | 'manual' | 'degradation' | 'purge'

    # Какой датчик неисправен
    affected_sensor = Column(
        String(10), nullable=False,
    )  # 'p_tube' | 'p_line' | 'both'

    # Метод коррекции
    correction_method = Column(
        String(20), nullable=False,
    )  # 'median_1d' | 'median_3d' | 'delta_reconstruct' | 'delta_noise'
      # | 'interpolate' | 'interpolate_noise' | 'exclude' | 'zero_flow'

    # Временной диапазон (UTC)
    dt_start = Column(DateTime, nullable=False)
    dt_end = Column(DateTime, nullable=False)

    # Для delta_reconstruct: медиана ΔP (если None — рассчитывается автоматически)
    manual_delta_p = Column(Float, nullable=True)

    # Переключатель (вкл/выкл без удаления)
    is_active = Column(Boolean, nullable=False, default=True)

    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Verification workflow
    is_verified = Column(Boolean, nullable=False, default=False)
    verified_at = Column(DateTime, nullable=True)
    verified_by = Column(String(100), nullable=True)

    # Detection source
    source = Column(String(20), nullable=False, default="manual")
    detection_confidence = Column(Float, nullable=True)
    batch_id = Column(String(50), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "affected_sensor IN ('p_tube', 'p_line', 'both')",
            name="chk_mask_sensor_v2",
        ),
        CheckConstraint(
            "correction_method IN ("
            "'median_1d', 'median_3d', 'delta_reconstruct', 'delta_noise', "
            "'interpolate', 'interpolate_noise', 'exclude', 'zero_flow'"
            ")",
            name="chk_mask_method_v2",
        ),
        CheckConstraint(
            "problem_type IN ("
            "'hydrate', 'comm_loss', 'sensor_fault', 'manual', 'degradation', 'purge', "
            "'pipeline_maintenance', 'gsp_switch', 'well_shutdown'"
            ")",
            name="chk_mask_problem_type_v2",
        ),
        CheckConstraint("dt_end > dt_start", name="chk_mask_range"),
        Index("ix_pressure_mask_range", "well_id", "dt_start", "dt_end"),
    )
