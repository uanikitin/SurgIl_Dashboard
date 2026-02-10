"""modify_lora_sensors_add_installations

Revision ID: 332211c00d11
Revises: f08e467e8866
Create Date: 2026-02-09 15:25:13.112836

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '332211c00d11'
down_revision: Union[str, Sequence[str], None] = 'f08e467e8866'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Добавляем csv_group в lora_sensors
    # csv_group вычисляется как (channel-1)//5 + 1
    # channel 1-5 → group 1, channel 6-10 → group 2, и т.д.
    op.add_column('lora_sensors', sa.Column('csv_group', sa.Integer, nullable=True))

    # 2. Заполняем csv_group на основе channel
    op.execute("""
        UPDATE lora_sensors
        SET csv_group = (channel - 1) / 5 + 1
    """)

    # 3. Делаем csv_group NOT NULL
    op.alter_column('lora_sensors', 'csv_group', nullable=False)

    # 4. Добавляем индекс для поиска по группе
    op.create_index('ix_lora_sensors_csv_group', 'lora_sensors', ['csv_group', 'channel', 'position'])

    # 5. Создаём таблицу установок датчиков на скважины
    op.create_table(
        'sensor_installations',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('sensor_id', sa.Integer, sa.ForeignKey('lora_sensors.id', ondelete='CASCADE'), nullable=False),
        sa.Column('well_id', sa.Integer, sa.ForeignKey('wells.id', ondelete='CASCADE'), nullable=False),
        sa.Column('installed_at', sa.DateTime, nullable=False),
        sa.Column('removed_at', sa.DateTime, nullable=True),  # NULL = активная установка
        sa.Column('notes', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
    )

    # 6. Индексы для быстрого поиска
    op.create_index('ix_sensor_installations_sensor', 'sensor_installations', ['sensor_id'])
    op.create_index('ix_sensor_installations_well', 'sensor_installations', ['well_id'])
    op.create_index('ix_sensor_installations_time', 'sensor_installations', ['sensor_id', 'installed_at', 'removed_at'])


def downgrade() -> None:
    """Downgrade schema."""
    # Удаляем sensor_installations
    op.drop_index('ix_sensor_installations_time', table_name='sensor_installations')
    op.drop_index('ix_sensor_installations_well', table_name='sensor_installations')
    op.drop_index('ix_sensor_installations_sensor', table_name='sensor_installations')
    op.drop_table('sensor_installations')

    # Удаляем csv_group из lora_sensors
    op.drop_index('ix_lora_sensors_csv_group', table_name='lora_sensors')
    op.drop_column('lora_sensors', 'csv_group')
