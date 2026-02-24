"""add flow_segment table

Revision ID: a1b2c3d4e5f6
Revises: 5031002768e7
Create Date: 2026-02-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "5031002768e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flow_segment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("well_id", sa.Integer(), sa.ForeignKey("wells.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False, server_default="Участок"),
        sa.Column("dt_start", sa.DateTime(), nullable=False),
        sa.Column("dt_end", sa.DateTime(), nullable=False),
        sa.Column("stats", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()")),
        sa.CheckConstraint("dt_end > dt_start", name="chk_segment_range"),
    )
    op.create_index("ix_flow_segment_well_id", "flow_segment", ["well_id"])


def downgrade() -> None:
    op.drop_index("ix_flow_segment_well_id")
    op.drop_table("flow_segment")
