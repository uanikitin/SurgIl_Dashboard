"""add well_sub_status table

Revision ID: cc1909d34843
Revises: 445566a00b22
Create Date: 2026-02-10 15:11:40.986438

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cc1909d34843'
down_revision: Union[str, Sequence[str], None] = '445566a00b22'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'well_sub_status',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('well_id', sa.Integer(), nullable=False),
        sa.Column('sub_status', sa.String(length=100), nullable=False),
        sa.Column('dt_start', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('dt_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['well_id'], ['wells.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_well_sub_status_well_id', 'well_sub_status', ['well_id'])
    op.create_index('ix_well_sub_status_dt_end', 'well_sub_status', ['dt_end'])


def downgrade() -> None:
    op.drop_index('ix_well_sub_status_dt_end', table_name='well_sub_status')
    op.drop_index('ix_well_sub_status_well_id', table_name='well_sub_status')
    op.drop_table('well_sub_status')
