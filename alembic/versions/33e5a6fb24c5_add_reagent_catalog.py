"""add reagent_catalog

Revision ID: 33e5a6fb24c5
Revises: c3e8c8ab68aa
Create Date: 2026-01-04 23:20:19.315652

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '33e5a6fb24c5'
down_revision: Union[str, Sequence[str], None] = 'c3e8c8ab68aa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

# migrations/versions/xxxx_add_reagent_catalog.py
"""Add reagent catalog table with DECIMAL fields

Revision ID: xxxx
Revises: previous_revision
Create Date: 2024-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


def upgrade():
    # Создаем таблицу каталога с DECIMAL полями
    op.create_table('reagent_catalog',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('name', sa.String(length=255), nullable=False),
                    sa.Column('code', sa.String(length=100), nullable=True),
                    sa.Column('default_unit', sa.String(length=20), nullable=False, server_default='шт'),
                    sa.Column('category', sa.String(length=100), nullable=True),
                    # ✅ Используем DECIMAL для точных чисел
                    sa.Column('min_stock', sa.DECIMAL(precision=10, scale=3), nullable=True),
                    sa.Column('max_stock', sa.DECIMAL(precision=10, scale=3), nullable=True),
                    sa.Column('is_active', sa.Boolean(), nullable=True, server_default='true'),
                    sa.Column('description', sa.Text(), nullable=True),
                    sa.Column('supplier_info', sa.Text(), nullable=True),
                    sa.Column('created_at', sa.DateTime(), nullable=True),
                    sa.Column('updated_at', sa.DateTime(), nullable=True),
                    sa.PrimaryKeyConstraint('id'),
                    sa.UniqueConstraint('code'),
                    sa.UniqueConstraint('name')
                    )
    op.create_index(op.f('ix_reagent_catalog_name'), 'reagent_catalog', ['name'], unique=False)

    # Обновляем существующие таблицы для использования DECIMAL
    # Если нужно обновить reagent_supplies.qty
    op.alter_column('reagent_supplies', 'qty',
                    existing_type=sa.NUMERIC(precision=14, scale=3),
                    type_=sa.DECIMAL(precision=14, scale=3),
                    existing_nullable=False
                    )

    # Добавляем reagent_id в reagent_supplies
    op.add_column('reagent_supplies', sa.Column('reagent_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_reagent_supplies_catalog', 'reagent_supplies', 'reagent_catalog', ['reagent_id'], ['id'])

    # Добавляем reagent_id в reagent_inventory
    op.add_column('reagent_inventory', sa.Column('reagent_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_reagent_inventory_catalog', 'reagent_inventory', 'reagent_catalog', ['reagent_id'],
                          ['id'])


def downgrade():
    # Удаляем внешние ключи и колонки
    op.drop_constraint('fk_reagent_inventory_catalog', 'reagent_inventory', type_='foreignkey')
    op.drop_column('reagent_inventory', 'reagent_id')

    op.drop_constraint('fk_reagent_supplies_catalog', 'reagent_supplies', type_='foreignkey')
    op.drop_column('reagent_supplies', 'reagent_id')

    # Возвращаем старый тип для qty если нужно
    op.alter_column('reagent_supplies', 'qty',
                    existing_type=sa.DECIMAL(precision=14, scale=3),
                    type_=sa.NUMERIC(precision=14, scale=3),
                    existing_nullable=False
                    )

    op.drop_table('reagent_catalog')