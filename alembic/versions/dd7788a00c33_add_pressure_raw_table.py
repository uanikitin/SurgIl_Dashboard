"""add pressure_raw table for raw readings in PostgreSQL

Revision ID: dd7788a00c33
Revises: cc1909d34843
Create Date: 2026-02-11

Mirrors SQLite pressure_readings so charts work on Render.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'dd7788a00c33'
down_revision: Union[str, Sequence[str], None] = 'cc1909d34843'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('pressure_raw',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('well_id', sa.Integer(), nullable=False),
        sa.Column('measured_at', sa.DateTime(), nullable=False),
        sa.Column('p_tube', sa.Float(), nullable=True),
        sa.Column('p_line', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('well_id', 'measured_at', name='uq_pressure_raw_well_time'),
    )
    op.create_index('ix_pressure_raw_well_measured', 'pressure_raw',
                     ['well_id', 'measured_at'])


def downgrade() -> None:
    op.drop_index('ix_pressure_raw_well_measured', table_name='pressure_raw')
    op.drop_table('pressure_raw')
