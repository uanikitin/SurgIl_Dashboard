"""add chat_message_log table for Telegram messaging from dashboard

Revision ID: 74277b472c00
Revises: 76aca8af6371
Create Date: 2026-02-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '74277b472c00'
down_revision: Union[str, Sequence[str], None] = '76aca8af6371'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'chat_message_log',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('chat_title', sa.Text(), nullable=True),
        sa.Column('message_text', sa.Text(), nullable=False),
        sa.Column('parse_mode', sa.String(20), server_default='HTML', nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('telegram_message_id', sa.Integer(), nullable=True),
        sa.Column('sent_by_user_id', sa.Integer(),
                  sa.ForeignKey('dashboard_users.id'), nullable=True),
        sa.Column('sent_by_username', sa.String(100), nullable=True),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_chat_msg_log_chat_id', 'chat_message_log', ['chat_id'])
    op.create_index('idx_chat_msg_log_created', 'chat_message_log', ['created_at'])


def downgrade() -> None:
    op.drop_index('idx_chat_msg_log_created', table_name='chat_message_log')
    op.drop_index('idx_chat_msg_log_chat_id', table_name='chat_message_log')
    op.drop_table('chat_message_log')
