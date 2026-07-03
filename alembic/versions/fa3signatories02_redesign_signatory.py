"""redesign act_signatory: библиотека подписантов (сторона, должность/ФИО RU+EN)

Revision ID: fa3signatories02
Revises: fa2signatories01
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "fa3signatories02"
down_revision: Union[str, Sequence[str], None] = "fa2signatories01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("act_signatory")
    op.create_table(
        "act_signatory",
        sa.Column("id", sa.Integer(), primary_key=True),
        # contractor (Исполнитель) | customer (Заказчик)
        sa.Column("side", sa.String(length=20), nullable=False, index=True),
        sa.Column("position_ru", sa.String(length=200), nullable=False),
        sa.Column("position_en", sa.String(length=200), nullable=True),
        sa.Column("name_ru", sa.String(length=200), nullable=False),
        sa.Column("name_en", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("act_signatory")
    op.create_table(
        "act_signatory",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role", sa.String(length=40), nullable=False, index=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("role", "name", name="uq_signatory_role_name"),
    )
