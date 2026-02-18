"""add flow analysis tables

Revision ID: 5031002768e7
Revises: e4fea3f35ab0
Create Date: 2026-02-17 14:53:19.605230

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = '5031002768e7'
down_revision: Union[str, Sequence[str], None] = 'e4fea3f35ab0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- flow_scenario ---
    op.create_table(
        'flow_scenario',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('well_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text()),
        sa.Column('period_start', sa.DateTime(), nullable=False),
        sa.Column('period_end', sa.DateTime(), nullable=False),
        sa.Column('choke_mm', sa.Float()),
        sa.Column('multiplier', sa.Float(), nullable=False, server_default='4.1'),
        sa.Column('c1', sa.Float(), nullable=False, server_default='2.919'),
        sa.Column('c2', sa.Float(), nullable=False, server_default='4.654'),
        sa.Column('c3', sa.Float(), nullable=False, server_default='286.95'),
        sa.Column('critical_ratio', sa.Float(), nullable=False, server_default='0.5'),
        sa.Column('smooth_enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('smooth_window', sa.Integer(), nullable=False, server_default='17'),
        sa.Column('smooth_polyorder', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('exclude_purge_ids', sa.Text(), server_default=''),
        sa.Column('is_baseline', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('status', sa.String(20), nullable=False, server_default='draft'),
        sa.Column('meta', JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_by', sa.String(200)),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('deleted_at', sa.DateTime()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['well_id'], ['wells.id']),
        sa.CheckConstraint(
            "status IN ('draft', 'calculated', 'locked')",
            name='chk_flow_scenario_status',
        ),
    )
    op.create_index('ix_flow_scenario_well', 'flow_scenario', ['well_id'])
    op.create_index(
        'ix_flow_scenario_baseline', 'flow_scenario',
        ['well_id', 'is_baseline'],
        postgresql_where=sa.text("is_baseline = TRUE AND deleted_at IS NULL"),
    )

    # --- flow_correction ---
    op.create_table(
        'flow_correction',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('scenario_id', sa.Integer(), nullable=False),
        sa.Column('correction_type', sa.String(20), nullable=False),
        sa.Column('dt_start', sa.DateTime(), nullable=False),
        sa.Column('dt_end', sa.DateTime(), nullable=False),
        sa.Column('manual_p_tube', sa.Float()),
        sa.Column('manual_p_line', sa.Float()),
        sa.Column('clamp_min', sa.Float()),
        sa.Column('clamp_max', sa.Float()),
        sa.Column('interp_method', sa.String(20), server_default='linear'),
        sa.Column('reason', sa.Text()),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['scenario_id'], ['flow_scenario.id'],
                                ondelete='CASCADE'),
        sa.CheckConstraint(
            "correction_type IN ('exclude','interpolate','manual_value','clamp')",
            name='chk_flow_correction_type',
        ),
        sa.CheckConstraint('dt_end > dt_start', name='chk_flow_correction_range'),
    )
    op.create_index('ix_flow_correction_scenario', 'flow_correction', ['scenario_id'])
    op.create_index('ix_flow_correction_range', 'flow_correction',
                    ['scenario_id', 'dt_start', 'dt_end'])

    # --- flow_result ---
    op.create_table(
        'flow_result',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('scenario_id', sa.Integer(), nullable=False),
        sa.Column('result_date', sa.Date(), nullable=False),
        sa.Column('avg_flow_rate', sa.Float()),
        sa.Column('min_flow_rate', sa.Float()),
        sa.Column('max_flow_rate', sa.Float()),
        sa.Column('median_flow_rate', sa.Float()),
        sa.Column('cumulative_flow', sa.Float()),
        sa.Column('avg_p_tube', sa.Float()),
        sa.Column('avg_p_line', sa.Float()),
        sa.Column('avg_dp', sa.Float()),
        sa.Column('purge_loss', sa.Float(), server_default='0'),
        sa.Column('downtime_minutes', sa.Float(), server_default='0'),
        sa.Column('data_points', sa.Integer(), server_default='0'),
        sa.Column('corrected_points', sa.Integer(), server_default='0'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['scenario_id'], ['flow_scenario.id'],
                                ondelete='CASCADE'),
        sa.UniqueConstraint('scenario_id', 'result_date',
                            name='uq_flow_result_scenario_date'),
    )
    op.create_index('ix_flow_result_scenario', 'flow_result', ['scenario_id'])


def downgrade() -> None:
    op.drop_table('flow_result')
    op.drop_table('flow_correction')
    op.drop_index('ix_flow_scenario_baseline', table_name='flow_scenario')
    op.drop_index('ix_flow_scenario_well', table_name='flow_scenario')
    op.drop_table('flow_scenario')
