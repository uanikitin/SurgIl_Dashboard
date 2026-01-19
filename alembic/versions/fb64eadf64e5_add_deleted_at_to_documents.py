"""add deleted_at to documents

Revision ID: fb64eadf64e5
Revises: efd7724b1099
Create Date: 2026-01-13 14:03:34.061812

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fb64eadf64e5'
down_revision: Union[str, Sequence[str], None] = 'efd7724b1099'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("documents", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index("ix_documents_deleted_at", "documents", ["deleted_at"])
    pass


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_documents_deleted_at", table_name="documents")
    op.drop_column("documents", "deleted_at")
    pass
