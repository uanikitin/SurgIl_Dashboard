"""add act_signatory (список подписантов финансового акта)

Revision ID: fa2signatories01
Revises: fa1financial01
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "fa2signatories01"
down_revision: Union[str, Sequence[str], None] = "fa1financial01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "act_signatory",
        sa.Column("id", sa.Integer(), primary_key=True),
        # contractor_director | customer_chairman | customer_deputy
        sa.Column("role", sa.String(length=40), nullable=False, index=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("role", "name", name="uq_signatory_role_name"),
    )


def downgrade() -> None:
    op.drop_table("act_signatory")
