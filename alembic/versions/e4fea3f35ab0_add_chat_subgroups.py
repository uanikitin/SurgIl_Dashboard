"""add chat subgroups

Revision ID: e4fea3f35ab0
Revises: 74277b472c00
Create Date: 2026-02-13 12:18:29.874823

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4fea3f35ab0'
down_revision: Union[str, Sequence[str], None] = '74277b472c00'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'chat_subgroup',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_chat_subgroup_name'),
    )

    op.create_table(
        'chat_subgroup_member',
        sa.Column('subgroup_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(['subgroup_id'], ['chat_subgroup.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('subgroup_id', 'user_id'),
    )


def downgrade() -> None:
    op.drop_table('chat_subgroup_member')
    op.drop_table('chat_subgroup')
