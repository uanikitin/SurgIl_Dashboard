"""add_chart_annotation

Revision ID: 2af335e6ce72
Revises: d1a2b3c4d5e7
Create Date: 2026-02-26 18:32:28.580430

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2af335e6ce72'
down_revision: Union[str, Sequence[str], None] = 'd1a2b3c4d5e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chart_annotation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("well_id", sa.Integer(), sa.ForeignKey("wells.id"), nullable=False),
        sa.Column("ann_type", sa.String(20), nullable=False, server_default="point"),
        sa.Column("dt_start", sa.DateTime(), nullable=False),
        sa.Column("dt_end", sa.DateTime(), nullable=True),
        sa.Column("text", sa.String(500), nullable=False),
        sa.Column("color", sa.String(20), nullable=False, server_default="#ff9800"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_chart_annotation_well_id", "chart_annotation", ["well_id"])


def downgrade() -> None:
    op.drop_index("ix_chart_annotation_well_id")
    op.drop_table("chart_annotation")
