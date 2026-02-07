"""add pressure_hourly and pressure_latest tables

Revision ID: b555c8a234d4
Revises: dc5e59d24cbb
Create Date: 2026-02-06 16:23:05.361168

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b555c8a234d4'
down_revision: Union[str, Sequence[str], None] = 'dc5e59d24cbb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('pressure_hourly',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('well_id', sa.Integer(), nullable=False),
        sa.Column('hour_start', sa.DateTime(), nullable=False),
        sa.Column('p_tube_avg', sa.Float(), nullable=True),
        sa.Column('p_tube_min', sa.Float(), nullable=True),
        sa.Column('p_tube_max', sa.Float(), nullable=True),
        sa.Column('p_line_avg', sa.Float(), nullable=True),
        sa.Column('p_line_min', sa.Float(), nullable=True),
        sa.Column('p_line_max', sa.Float(), nullable=True),
        sa.Column('reading_count', sa.Integer(), nullable=True),
        sa.Column('has_gaps', sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(['well_id'], ['wells.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('well_id', 'hour_start', name='uq_pressure_hourly_well_hour')
    )
    op.create_table('pressure_latest',
        sa.Column('well_id', sa.Integer(), nullable=False),
        sa.Column('measured_at', sa.DateTime(), nullable=True),
        sa.Column('p_tube', sa.Float(), nullable=True),
        sa.Column('p_line', sa.Float(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['well_id'], ['wells.id']),
        sa.PrimaryKeyConstraint('well_id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('pressure_latest')
    op.drop_table('pressure_hourly')
