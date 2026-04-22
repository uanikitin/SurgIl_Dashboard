"""add customer_baseline table for adaptation report baselines

Revision ID: c4f5a6b7d8e9
Revises: a8b9c0d1e2f3
Create Date: 2026-04-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c4f5a6b7d8e9"
down_revision: Union[str, Sequence[str], None] = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer_baseline",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("well_id", sa.Integer(),
                  sa.ForeignKey("wells.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False,
                  server_default=sa.text("'customer'")),
        # 'customer' / 'observation' / 'manual'

        sa.Column("period_from", sa.Date(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),

        # Усреднённые показатели за период (могут быть NULL если данных нет)
        sa.Column("days_count", sa.Integer(), nullable=True),
        sa.Column("q_total_avg", sa.Float(), nullable=True),
        sa.Column("q_total_median", sa.Float(), nullable=True),
        sa.Column("q_working_avg", sa.Float(), nullable=True),
        sa.Column("q_working_median", sa.Float(), nullable=True),
        sa.Column("p_wellhead_avg", sa.Float(), nullable=True),
        sa.Column("p_wellhead_median", sa.Float(), nullable=True),
        sa.Column("p_flowline_avg", sa.Float(), nullable=True),
        sa.Column("p_flowline_median", sa.Float(), nullable=True),
        sa.Column("dp_avg", sa.Float(), nullable=True),
        sa.Column("dp_median", sa.Float(), nullable=True),
        sa.Column("shutdown_min_total", sa.Float(), nullable=True),
        sa.Column("shutdown_min_avg", sa.Float(), nullable=True),
        sa.Column("shutdown_days_count", sa.Integer(), nullable=True),

        # Метаданные
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.Column("is_pinned", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
    )
    op.create_index(
        "ix_customer_baseline_well_pinned",
        "customer_baseline", ["well_id", "is_pinned"],
    )


def downgrade() -> None:
    op.drop_index("ix_customer_baseline_well_pinned",
                  table_name="customer_baseline")
    op.drop_table("customer_baseline")
