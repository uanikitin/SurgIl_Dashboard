"""add lora_sensors table

Revision ID: f08e467e8866
Revises: b555c8a234d4
Create Date: 2026-02-06 21:21:30.069136

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f08e467e8866'
down_revision: Union[str, Sequence[str], None] = 'b555c8a234d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'lora_sensors',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('serial_number', sa.String(length=50), nullable=False),
        sa.Column('channel', sa.Integer(), nullable=False),
        sa.Column('position', sa.String(length=10), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=True),
        sa.Column('note', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_lora_sensors_id'), 'lora_sensors', ['id'], unique=False)
    op.create_index(op.f('ix_lora_sensors_serial_number'), 'lora_sensors', ['serial_number'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_lora_sensors_serial_number'), table_name='lora_sensors')
    op.drop_index(op.f('ix_lora_sensors_id'), table_name='lora_sensors')
    op.drop_table('lora_sensors')
