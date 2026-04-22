"""add well_daily table for customer daily reports

Revision ID: a8b9c0d1e2f3
Revises: c3d4e5f6a7b8
Create Date: 2026-04-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "well_daily",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("ggu", sa.String(length=16), nullable=False),
        sa.Column("well", sa.String(length=32), nullable=False),
        sa.Column("choke_mm", sa.Float(), nullable=True),
        sa.Column("p_wellhead", sa.Float(), nullable=True),
        sa.Column("p_annular", sa.Float(), nullable=True),
        sa.Column(
            "annular_packer",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("p_flowline", sa.Float(), nullable=True),
        sa.Column("q_gas_total", sa.Float(), nullable=True),
        sa.Column("q_gas_working", sa.Float(), nullable=True),
        sa.Column("shutdown_min", sa.Float(), nullable=True),
        sa.Column("p_static", sa.Float(), nullable=True),
        sa.Column("source_sheet", sa.String(length=128), nullable=True),
        sa.Column("source_file", sa.String(length=255), nullable=True),
        sa.Column(
            "loaded_at",
            sa.DateTime(timezone=False),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("date", "ggu", "well", name="pk_well_daily"),
    )
    op.create_index(
        "ix_well_daily_well_date", "well_daily", ["well", "date"], unique=False
    )
    op.create_index(
        "ix_well_daily_ggu_date", "well_daily", ["ggu", "date"], unique=False
    )
    op.create_index(
        "ix_well_daily_date", "well_daily", ["date"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_well_daily_date", table_name="well_daily")
    op.drop_index("ix_well_daily_ggu_date", table_name="well_daily")
    op.drop_index("ix_well_daily_well_date", table_name="well_daily")
    op.drop_table("well_daily")
