"""добавление таблицы учета оборудования

Revision ID: 563cd50f1f97
Revises: fb64eadf64e5
Create Date: 2026-01-15 20:48:25.610118

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '563cd50f1f97'
down_revision: Union[str, Sequence[str], None] = 'fb64eadf64e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

"""add equipment management tables"""



def upgrade():
    # ============================================================================
    # 1. Создание таблицы equipment (Справочник оборудования)
    # ============================================================================
    op.create_table(
        'equipment',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('equipment_type', sa.String(length=100), nullable=True),
        sa.Column('serial_number', sa.String(length=100), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('manufacturer', sa.String(length=200), nullable=True),
        sa.Column('manufacture_date', sa.Date(), nullable=True),
        sa.Column('specifications', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='available'),
        sa.Column('current_location', sa.String(length=200), nullable=True),
        sa.Column('last_maintenance_date', sa.Date(), nullable=True),
        sa.Column('next_maintenance_date', sa.Date(), nullable=True),
        sa.Column('maintenance_interval_days', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('serial_number')
    )

    # Индексы для equipment
    op.create_index('idx_equipment_serial', 'equipment', ['serial_number'], unique=False)
    op.create_index('idx_equipment_status', 'equipment', ['status'], unique=False)
    op.create_index('idx_equipment_type', 'equipment', ['equipment_type'], unique=False)

    # ============================================================================
    # 2. Создание таблицы equipment_installation (История установок)
    # ============================================================================
    op.create_table(
        'equipment_installation',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('equipment_id', sa.Integer(), nullable=False),
        sa.Column('well_id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=True),
        sa.Column('installed_at', sa.DateTime(), nullable=False),
        sa.Column('removed_at', sa.DateTime(), nullable=True),
        sa.Column('tube_pressure_install', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('line_pressure_install', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('tube_pressure_remove', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('line_pressure_remove', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('installation_reason', sa.Text(), nullable=True),
        sa.Column('removal_reason', sa.Text(), nullable=True),
        sa.Column('condition_on_install', sa.String(length=100), nullable=True),
        sa.Column('condition_on_removal', sa.String(length=100), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('installed_by', sa.String(length=200), nullable=True),
        sa.Column('removed_by', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['equipment_id'], ['equipment.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['well_id'], ['wells.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='SET NULL')
    )

    # Индексы для equipment_installation
    op.create_index('idx_equip_install_equipment', 'equipment_installation', ['equipment_id'], unique=False)
    op.create_index('idx_equip_install_well', 'equipment_installation', ['well_id'], unique=False)
    op.create_index('idx_equip_install_dates', 'equipment_installation', ['installed_at', 'removed_at'], unique=False)

    # ============================================================================
    # 3. Создание таблицы equipment_maintenance (Сервисное обслуживание)
    # ============================================================================
    op.create_table(
        'equipment_maintenance',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('equipment_id', sa.Integer(), nullable=False),
        sa.Column('maintenance_date', sa.DateTime(), nullable=False),
        sa.Column('maintenance_type', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('performed_by', sa.String(length=200), nullable=True),
        sa.Column('status_before', sa.String(length=100), nullable=True),
        sa.Column('status_after', sa.String(length=100), nullable=True),
        sa.Column('issues_found', sa.Text(), nullable=True),
        sa.Column('actions_taken', sa.Text(), nullable=True),
        sa.Column('parts_used', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('cost', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('next_maintenance_date', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['equipment_id'], ['equipment.id'], ondelete='CASCADE')
    )

    # Индексы для equipment_maintenance
    op.create_index('idx_equip_maint_equipment', 'equipment_maintenance', ['equipment_id'], unique=False)
    op.create_index('idx_equip_maint_date', 'equipment_maintenance', ['maintenance_date'], unique=False)
    op.create_index('idx_equip_maint_type', 'equipment_maintenance', ['maintenance_type'], unique=False)

    # ============================================================================
    # 4. Добавление новых типов документов
    # ============================================================================
    # Проверяем, существует ли таблица document_types
    conn = op.get_bind()

    # Добавляем типы документов для установки/демонтажа оборудования
    op.execute("""
        INSERT INTO document_types (code, name_ru, name_en, category, is_periodic, sort_order) 
        VALUES 
            ('equipment_install', 'Акт установки оборудования', 'Акт установки оборудования', 'operational', false, 40),
            ('equipment_removal', 'Акт демонтажа оборудования', 'Акт демонтажа оборудования', 'operational', false, 41)
        ON CONFLICT (code) DO NOTHING;
    """)


def downgrade():
    # ВАЖНО: Откат миграции удаляет таблицы и данные!
    # Используйте с осторожностью на production

    # Удаляем типы документов
    op.execute("""
        DELETE FROM document_types 
        WHERE code IN ('equipment_install', 'equipment_removal');
    """)

    # Удаляем индексы и таблицы в обратном порядке
    op.drop_index('idx_equip_maint_type', table_name='equipment_maintenance')
    op.drop_index('idx_equip_maint_date', table_name='equipment_maintenance')
    op.drop_index('idx_equip_maint_equipment', table_name='equipment_maintenance')
    op.drop_table('equipment_maintenance')

    op.drop_index('idx_equip_install_dates', table_name='equipment_installation')
    op.drop_index('idx_equip_install_well', table_name='equipment_installation')
    op.drop_index('idx_equip_install_equipment', table_name='equipment_installation')
    op.drop_table('equipment_installation')

    op.drop_index('idx_equipment_type', table_name='equipment')
    op.drop_index('idx_equipment_status', table_name='equipment')
    op.drop_index('idx_equipment_serial', table_name='equipment')
    op.drop_table('equipment')