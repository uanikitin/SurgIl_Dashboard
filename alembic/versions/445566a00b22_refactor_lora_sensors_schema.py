"""Refactor lora_sensors schema: channel→csv_channel, position→csv_column, add position to installations.

Revision ID: 445566a00b22
Revises: 332211c00d11
Create Date: 2026-02-09

Изменения:
1. lora_sensors: channel → csv_channel
2. lora_sensors: position → csv_column (с преобразованием 'tube'→'Ptr', 'line'→'Pshl')
3. sensor_installations: добавляем position (переносим из старого lora_sensors.position)
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '445566a00b22'
down_revision: Union[str, Sequence[str], None] = '332211c00d11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    # 1. Добавляем position в sensor_installations
    # Сначала nullable, потом заполним и сделаем NOT NULL
    op.add_column('sensor_installations', sa.Column('position', sa.String(10), nullable=True))

    # 2. Заполняем position в sensor_installations из lora_sensors.position
    op.execute("""
        UPDATE sensor_installations si
        SET position = (
            SELECT ls.position
            FROM lora_sensors ls
            WHERE ls.id = si.sensor_id
        )
    """)

    # 3. Делаем position NOT NULL
    op.alter_column('sensor_installations', 'position', nullable=False)

    # 4. В lora_sensors: переименовываем channel → csv_channel
    op.alter_column('lora_sensors', 'channel', new_column_name='csv_channel')

    # 5. В lora_sensors: переименовываем position → csv_column
    op.alter_column('lora_sensors', 'position', new_column_name='csv_column')

    # 6. Преобразуем значения: 'tube' → 'Ptr', 'line' → 'Pshl'
    op.execute("""
        UPDATE lora_sensors
        SET csv_column = CASE
            WHEN csv_column = 'tube' THEN 'Ptr'
            WHEN csv_column = 'line' THEN 'Pshl'
            ELSE csv_column
        END
    """)

    # 7. Удаляем старый индекс и создаём новый
    op.drop_index('ix_lora_sensors_csv_group', table_name='lora_sensors')
    op.create_index('ix_lora_sensors_csv', 'lora_sensors', ['csv_group', 'csv_channel', 'csv_column'])


def downgrade() -> None:
    """Downgrade schema."""

    # 1. Переименовываем обратно csv_column → position
    op.execute("""
        UPDATE lora_sensors
        SET csv_column = CASE
            WHEN csv_column = 'Ptr' THEN 'tube'
            WHEN csv_column = 'Pshl' THEN 'line'
            ELSE csv_column
        END
    """)
    op.alter_column('lora_sensors', 'csv_column', new_column_name='position')

    # 2. Переименовываем csv_channel → channel
    op.alter_column('lora_sensors', 'csv_channel', new_column_name='channel')

    # 3. Пересоздаём индекс
    op.drop_index('ix_lora_sensors_csv', table_name='lora_sensors')
    op.create_index('ix_lora_sensors_csv_group', 'lora_sensors', ['csv_group', 'channel', 'position'])

    # 4. Удаляем position из sensor_installations
    op.drop_column('sensor_installations', 'position')
