"""add sensor_ids to pressure_raw

Revision ID: 76aca8af6371
Revises: dd7788a00c33
Create Date: 2026-02-12

Adds sensor_id_tube and sensor_id_line to pressure_raw for traceability.
Allows reassignment of well_id when equipment_installation dates change.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '76aca8af6371'
down_revision: Union[str, Sequence[str], None] = 'dd7788a00c33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('pressure_raw', sa.Column('sensor_id_tube', sa.Integer(), nullable=True))
    op.add_column('pressure_raw', sa.Column('sensor_id_line', sa.Integer(), nullable=True))
    op.create_index('ix_pressure_raw_sensor_tube', 'pressure_raw', ['sensor_id_tube'])
    op.create_index('ix_pressure_raw_sensor_line', 'pressure_raw', ['sensor_id_line'])


def downgrade() -> None:
    op.drop_index('ix_pressure_raw_sensor_line', table_name='pressure_raw')
    op.drop_index('ix_pressure_raw_sensor_tube', table_name='pressure_raw')
    op.drop_column('pressure_raw', 'sensor_id_line')
    op.drop_column('pressure_raw', 'sensor_id_tube')
