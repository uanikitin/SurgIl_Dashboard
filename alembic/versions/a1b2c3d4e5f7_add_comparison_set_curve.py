"""add comparison_set + comparison_curve tables (merge two heads)

Объединяет две головы (`c4f5a6b7d8e9` customer_baseline и
`b7e1a9f20001` lora_sensor_assignment) и создаёт таблицы для
«Конструктора сравнения» отчёта адаптации:
  * comparison_set   — набор кривых (привязан к скважине).
  * comparison_curve — одна кривая (источник + период + метрика).

Безопасно: только CREATE TABLE / CREATE INDEX. Существующих данных
не трогает.

Revision ID: a1b2c3d4e5f7
Revises: c4f5a6b7d8e9, b7e1a9f20001
Create Date: 2026-04-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, Sequence[str], None] = ("c4f5a6b7d8e9", "b7e1a9f20001")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─── comparison_set ──────────────────────────────────────────────
    op.create_table(
        "comparison_set",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "well_id", sa.Integer(),
            sa.ForeignKey("wells.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "x_axis_mode", sa.String(length=16),
            nullable=False, server_default=sa.text("'offset'"),
        ),
        sa.Column(
            "in_report", sa.Boolean(),
            nullable=False, server_default=sa.text("true"),
        ),
        sa.Column(
            "sort_order", sa.Integer(),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at", sa.DateTime(),
            nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(),
            nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.CheckConstraint(
            "x_axis_mode IN ('offset','date')",
            name="ck_comparison_set_xmode",
        ),
    )
    op.create_index(
        "ix_comparison_set_well_inreport",
        "comparison_set", ["well_id", "in_report"],
    )

    # ─── comparison_curve ────────────────────────────────────────────
    op.create_table(
        "comparison_curve",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "set_id", sa.Integer(),
            sa.ForeignKey("comparison_set.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column(
            "order_index", sa.Integer(),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "baseline_id", sa.Integer(),
            sa.ForeignKey("customer_baseline.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("period_from", sa.Date(), nullable=True),
        sa.Column("period_to", sa.Date(), nullable=True),
        sa.Column("metric", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("color", sa.String(length=16), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "source IN ('customer','our_pressure','our_flow','baseline')",
            name="ck_comparison_curve_source",
        ),
        sa.CheckConstraint(
            "metric IN ('q_total','q_working','dp','p_wellhead','p_flowline')",
            name="ck_comparison_curve_metric",
        ),
        # Если source='baseline' — должен быть baseline_id; иначе период.
        sa.CheckConstraint(
            "(source = 'baseline' AND baseline_id IS NOT NULL) "
            "OR (source <> 'baseline' AND period_from IS NOT NULL "
            "    AND period_to IS NOT NULL)",
            name="ck_comparison_curve_source_period",
        ),
    )
    op.create_index(
        "ix_comparison_curve_set_order",
        "comparison_curve", ["set_id", "order_index"],
    )


def downgrade() -> None:
    op.drop_index("ix_comparison_curve_set_order", table_name="comparison_curve")
    op.drop_table("comparison_curve")
    op.drop_index("ix_comparison_set_well_inreport", table_name="comparison_set")
    op.drop_table("comparison_set")
