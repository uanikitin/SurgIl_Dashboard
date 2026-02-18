"""
ORM-модели для системы анализа дебита газа.

Три таблицы:
  - flow_scenario     — расчётный сценарий (параметры + метаданные)
  - flow_correction   — коррекции к сценарию (исключения, интерполяция, ...)
  - flow_result       — суточные результаты расчёта
"""
from __future__ import annotations

from datetime import datetime, date

from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean,
    DateTime, Date, ForeignKey, CheckConstraint, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from backend.db import Base


class FlowScenario(Base):
    __tablename__ = "flow_scenario"

    id = Column(Integer, primary_key=True)
    well_id = Column(Integer, ForeignKey("wells.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)

    # Период (UTC)
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)

    # Параметры формулы (фиксируются при создании)
    choke_mm = Column(Float)  # NULL = авто из well_construction
    multiplier = Column(Float, nullable=False, default=4.1)
    c1 = Column(Float, nullable=False, default=2.919)
    c2 = Column(Float, nullable=False, default=4.654)
    c3 = Column(Float, nullable=False, default=286.95)
    critical_ratio = Column(Float, nullable=False, default=0.5)

    # Сглаживание
    smooth_enabled = Column(Boolean, nullable=False, default=True)
    smooth_window = Column(Integer, nullable=False, default=17)
    smooth_polyorder = Column(Integer, nullable=False, default=3)

    # Исключённые продувки (comma-separated IDs)
    exclude_purge_ids = Column(Text, default="")

    # Базовый сценарий (макс. 1 на скважину)
    is_baseline = Column(Boolean, nullable=False, default=False)

    # Статус: draft → calculated → locked
    status = Column(
        String(20), nullable=False, default="draft",
        info={"check": "status IN ('draft', 'calculated', 'locked')"},
    )

    # Метаданные (summary, pdf_path, notes, ...)
    meta = Column(JSONB, nullable=False, default=dict)

    # Аудит
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime)

    # Relationships
    well = relationship("Well", lazy="joined")
    corrections = relationship(
        "FlowCorrection",
        back_populates="scenario",
        cascade="all, delete-orphan",
        order_by="FlowCorrection.sort_order",
    )
    results = relationship(
        "FlowResult",
        back_populates="scenario",
        cascade="all, delete-orphan",
        order_by="FlowResult.result_date",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'calculated', 'locked')",
            name="chk_flow_scenario_status",
        ),
        Index(
            "ix_flow_scenario_baseline",
            "well_id", "is_baseline",
            postgresql_where="is_baseline = TRUE AND deleted_at IS NULL",
        ),
    )


class FlowCorrection(Base):
    __tablename__ = "flow_correction"

    id = Column(Integer, primary_key=True)
    scenario_id = Column(
        Integer,
        ForeignKey("flow_scenario.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Тип коррекции
    correction_type = Column(
        String(20), nullable=False,
        info={"check": "correction_type IN ('exclude','interpolate','manual_value','clamp')"},
    )

    # Временной диапазон
    dt_start = Column(DateTime, nullable=False)
    dt_end = Column(DateTime, nullable=False)

    # Для manual_value
    manual_p_tube = Column(Float)
    manual_p_line = Column(Float)

    # Для clamp
    clamp_min = Column(Float)
    clamp_max = Column(Float)

    # Для interpolate
    interp_method = Column(String(20), default="linear")

    # Аннотация
    reason = Column(Text)

    # Порядок применения
    sort_order = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    scenario = relationship("FlowScenario", back_populates="corrections")

    __table_args__ = (
        CheckConstraint(
            "correction_type IN ('exclude','interpolate','manual_value','clamp')",
            name="chk_flow_correction_type",
        ),
        CheckConstraint("dt_end > dt_start", name="chk_flow_correction_range"),
        Index("ix_flow_correction_range", "scenario_id", "dt_start", "dt_end"),
    )


class FlowResult(Base):
    __tablename__ = "flow_result"

    id = Column(Integer, primary_key=True)
    scenario_id = Column(
        Integer,
        ForeignKey("flow_scenario.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    result_date = Column(Date, nullable=False)

    # Суточные агрегаты дебита (тыс. м3/сут)
    avg_flow_rate = Column(Float)
    min_flow_rate = Column(Float)
    max_flow_rate = Column(Float)
    median_flow_rate = Column(Float)
    cumulative_flow = Column(Float)  # тыс. м3 за день

    # Давление (кгс/см2)
    avg_p_tube = Column(Float)
    avg_p_line = Column(Float)
    avg_dp = Column(Float)

    # Потери
    purge_loss = Column(Float, default=0)
    downtime_minutes = Column(Float, default=0)

    # Качество данных
    data_points = Column(Integer, default=0)
    corrected_points = Column(Integer, default=0)

    # Relationships
    scenario = relationship("FlowScenario", back_populates="results")

    __table_args__ = (
        UniqueConstraint("scenario_id", "result_date", name="uq_flow_result_scenario_date"),
    )
