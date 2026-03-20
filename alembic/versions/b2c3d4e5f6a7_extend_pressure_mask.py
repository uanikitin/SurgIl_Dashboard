"""extend pressure_mask with verification and detection fields

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f7
Create Date: 2026-03-19
"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade():
    # Verification fields
    op.add_column("pressure_mask", sa.Column("is_verified", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("pressure_mask", sa.Column("verified_at", sa.DateTime(), nullable=True))
    op.add_column("pressure_mask", sa.Column("verified_by", sa.String(100), nullable=True))

    # Detection source
    op.add_column("pressure_mask", sa.Column("source", sa.String(20), nullable=False, server_default="manual"))
    op.add_column("pressure_mask", sa.Column("detection_confidence", sa.Float(), nullable=True))
    op.add_column("pressure_mask", sa.Column("batch_id", sa.String(50), nullable=True))

    # Extend problem_type to include 'degradation'
    op.drop_constraint("chk_mask_problem_type", "pressure_mask", type_="check")
    op.create_check_constraint(
        "chk_mask_problem_type",
        "pressure_mask",
        "problem_type IN ('hydrate', 'comm_loss', 'sensor_fault', 'manual', 'degradation')",
    )


def downgrade():
    op.drop_constraint("chk_mask_problem_type", "pressure_mask", type_="check")
    op.create_check_constraint(
        "chk_mask_problem_type",
        "pressure_mask",
        "problem_type IN ('hydrate', 'comm_loss', 'sensor_fault', 'manual')",
    )
    op.drop_column("pressure_mask", "batch_id")
    op.drop_column("pressure_mask", "detection_confidence")
    op.drop_column("pressure_mask", "source")
    op.drop_column("pressure_mask", "verified_by")
    op.drop_column("pressure_mask", "verified_at")
    op.drop_column("pressure_mask", "is_verified")
