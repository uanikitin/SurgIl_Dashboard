"""add pressure_mask

Revision ID: f7e8d9c0b1a2
Revises: 2af335e6ce72
Create Date: 2026-03-03
"""
from alembic import op
import sqlalchemy as sa

revision = "f7e8d9c0b1a2"
down_revision = "2af335e6ce72"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pressure_mask",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("well_id", sa.Integer(), nullable=False),
        sa.Column("problem_type", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("affected_sensor", sa.String(10), nullable=False),
        sa.Column("correction_method", sa.String(20), nullable=False),
        sa.Column("dt_start", sa.DateTime(), nullable=False),
        sa.Column("dt_end", sa.DateTime(), nullable=False),
        sa.Column("manual_delta_p", sa.Float(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["well_id"], ["wells.id"]),
        sa.CheckConstraint(
            "affected_sensor IN ('p_tube', 'p_line')",
            name="chk_mask_sensor",
        ),
        sa.CheckConstraint(
            "correction_method IN ('median_1d', 'median_3d', 'delta_reconstruct', 'interpolate', 'exclude')",
            name="chk_mask_method",
        ),
        sa.CheckConstraint(
            "problem_type IN ('hydrate', 'comm_loss', 'sensor_fault', 'manual')",
            name="chk_mask_problem_type",
        ),
        sa.CheckConstraint("dt_end > dt_start", name="chk_mask_range"),
    )
    op.create_index("ix_pressure_mask_well", "pressure_mask", ["well_id"])
    op.create_index("ix_pressure_mask_range", "pressure_mask", ["well_id", "dt_start", "dt_end"])


def downgrade():
    op.drop_index("ix_pressure_mask_range", table_name="pressure_mask")
    op.drop_index("ix_pressure_mask_well", table_name="pressure_mask")
    op.drop_table("pressure_mask")
