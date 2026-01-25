"""add notification and job tables

Revision ID: dc5e59d24cbb
Revises: 563cd50f1f97
Create Date: 2026-01-26 01:22:57.461441

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'dc5e59d24cbb'
down_revision: Union[str, Sequence[str], None] = '563cd50f1f97'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Таблица логов выполнения задач
    op.create_table('job_execution_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_type', sa.String(length=100), nullable=False),
        sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('result_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('triggered_by', sa.String(length=100), nullable=True),
        sa.Column('triggered_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_job_log_type_started', 'job_execution_logs', ['job_type', 'started_at'], unique=False)
    op.create_index(op.f('ix_job_execution_logs_job_type'), 'job_execution_logs', ['job_type'], unique=False)
    op.create_index(op.f('ix_job_execution_logs_started_at'), 'job_execution_logs', ['started_at'], unique=False)
    op.create_index(op.f('ix_job_execution_logs_status'), 'job_execution_logs', ['status'], unique=False)

    # Таблица настроек уведомлений
    op.create_table('notification_configs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('doc_type_id', sa.Integer(), nullable=True),
        sa.Column('well_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('telegram_enabled', sa.Boolean(), nullable=True),
        sa.Column('telegram_chat_id', sa.String(length=100), nullable=True),
        sa.Column('telegram_template', sa.Text(), nullable=True),
        sa.Column('email_enabled', sa.Boolean(), nullable=True),
        sa.Column('email_to', sa.Text(), nullable=True),
        sa.Column('email_cc', sa.Text(), nullable=True),
        sa.Column('email_subject_template', sa.String(length=500), nullable=True),
        sa.Column('email_body_template', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('is_default', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['doc_type_id'], ['document_types.id'], ),
        sa.ForeignKeyConstraint(['well_id'], ['wells.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_notification_configs_doc_type_id'), 'notification_configs', ['doc_type_id'], unique=False)
    op.create_index(op.f('ix_notification_configs_is_active'), 'notification_configs', ['is_active'], unique=False)
    op.create_index(op.f('ix_notification_configs_well_id'), 'notification_configs', ['well_id'], unique=False)

    # Таблица логов отправки документов
    op.create_table('document_send_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('channel', sa.String(length=50), nullable=False),
        sa.Column('recipient', sa.String(length=500), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('response_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('triggered_by', sa.String(length=100), nullable=True),
        sa.Column('triggered_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_send_log_doc_channel', 'document_send_logs', ['document_id', 'channel'], unique=False)
    op.create_index(op.f('ix_document_send_logs_channel'), 'document_send_logs', ['channel'], unique=False)
    op.create_index(op.f('ix_document_send_logs_created_at'), 'document_send_logs', ['created_at'], unique=False)
    op.create_index(op.f('ix_document_send_logs_document_id'), 'document_send_logs', ['document_id'], unique=False)
    op.create_index(op.f('ix_document_send_logs_status'), 'document_send_logs', ['status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_document_send_logs_status'), table_name='document_send_logs')
    op.drop_index(op.f('ix_document_send_logs_document_id'), table_name='document_send_logs')
    op.drop_index(op.f('ix_document_send_logs_created_at'), table_name='document_send_logs')
    op.drop_index(op.f('ix_document_send_logs_channel'), table_name='document_send_logs')
    op.drop_index('idx_send_log_doc_channel', table_name='document_send_logs')
    op.drop_table('document_send_logs')

    op.drop_index(op.f('ix_notification_configs_well_id'), table_name='notification_configs')
    op.drop_index(op.f('ix_notification_configs_is_active'), table_name='notification_configs')
    op.drop_index(op.f('ix_notification_configs_doc_type_id'), table_name='notification_configs')
    op.drop_table('notification_configs')

    op.drop_index(op.f('ix_job_execution_logs_status'), table_name='job_execution_logs')
    op.drop_index(op.f('ix_job_execution_logs_started_at'), table_name='job_execution_logs')
    op.drop_index(op.f('ix_job_execution_logs_job_type'), table_name='job_execution_logs')
    op.drop_index('idx_job_log_type_started', table_name='job_execution_logs')
    op.drop_table('job_execution_logs')
