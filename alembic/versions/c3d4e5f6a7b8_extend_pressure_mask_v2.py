"""extend pressure_mask types and methods v2

Revision ID: c3d4e5f6a7b8
Revises: e5f6a7b8c9d0
Create Date: 2026-03-31
"""
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade():
    # Drop old constraints and create extended ones
    op.drop_constraint("chk_mask_sensor", "pressure_mask", type_="check")
    op.create_check_constraint(
        "chk_mask_sensor_v2",
        "pressure_mask",
        "affected_sensor IN ('p_tube', 'p_line', 'both')",
    )

    op.drop_constraint("chk_mask_method", "pressure_mask", type_="check")
    op.create_check_constraint(
        "chk_mask_method_v2",
        "pressure_mask",
        "correction_method IN ("
        "'median_1d', 'median_3d', 'delta_reconstruct', 'delta_noise', "
        "'interpolate', 'interpolate_noise', 'exclude', 'zero_flow'"
        ")",
    )

    op.drop_constraint("chk_mask_problem_type", "pressure_mask", type_="check")
    op.create_check_constraint(
        "chk_mask_problem_type_v2",
        "pressure_mask",
        "problem_type IN ("
        "'hydrate', 'comm_loss', 'sensor_fault', 'manual', 'degradation', 'purge', "
        "'pipeline_maintenance', 'gsp_switch', 'well_shutdown'"
        ")",
    )


def downgrade():
    op.drop_constraint("chk_mask_problem_type_v2", "pressure_mask", type_="check")
    op.create_check_constraint(
        "chk_mask_problem_type",
        "pressure_mask",
        "problem_type IN ('hydrate', 'comm_loss', 'sensor_fault', 'manual', 'degradation', 'purge')",
    )

    op.drop_constraint("chk_mask_method_v2", "pressure_mask", type_="check")
    op.create_check_constraint(
        "chk_mask_method",
        "pressure_mask",
        "correction_method IN ('median_1d', 'median_3d', 'delta_reconstruct', 'interpolate', 'exclude')",
    )

    op.drop_constraint("chk_mask_sensor_v2", "pressure_mask", type_="check")
    op.create_check_constraint(
        "chk_mask_sensor",
        "pressure_mask",
        "affected_sensor IN ('p_tube', 'p_line')",
    )
