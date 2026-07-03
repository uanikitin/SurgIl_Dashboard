"""add financial acts: contract_price, document_items money cols, reagent groups

Revision ID: fa1financial01
Revises: p2r4d6r8t0y2
Create Date: 2026-07-02

Аддитивная миграция (только ADD/CREATE, без DROP существующих данных).
Всё через IF NOT EXISTS-совместимые op-вызовы там, где это возможно.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "fa1financial01"
down_revision: Union[str, Sequence[str], None] = "p2r4d6r8t0y2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) document_types: путь к .docx-шаблону
    op.add_column(
        "document_types",
        sa.Column("docx_template_name", sa.String(length=100), nullable=True),
    )

    # 2) Таблица прайса контракта (цены с датами вступления в силу)
    op.create_table(
        "contract_price",
        sa.Column("id", sa.Integer(), primary_key=True),
        # adaptation | optimization | foam_dosing
        sa.Column("work_type", sa.String(length=50), nullable=False, index=True),
        # NULL = общая цена по умолчанию; иначе — для конкретной скважины
        sa.Column(
            "well_id", sa.Integer(),
            sa.ForeignKey("wells.id", ondelete="CASCADE"),
            nullable=True, index=True,
        ),
        # Адаптация — фикс/скв-операцию; Оптимизация — цена за МЕСЯЦ; Дозирование — цена/операцию
        sa.Column("price_per_unit", sa.Numeric(18, 2), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("contract_ref", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_contract_price_lookup",
        "contract_price",
        ["work_type", "well_id", "effective_from"],
        unique=True,
    )

    # 3) document_items: денежные и идентификационные поля по строке
    for col in (
        sa.Column("well_number", sa.String(length=50), nullable=True),
        sa.Column("work_group", sa.String(length=50), nullable=True),
        sa.Column("unit", sa.String(length=50), nullable=True),
        sa.Column("price_per_unit", sa.Numeric(18, 2), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("vat_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("amount_with_vat", sa.Numeric(18, 2), nullable=True),
        sa.Column("period_label", sa.String(length=100), nullable=True),
    ):
        op.add_column("document_items", col)

    # 4) reagent_catalog: группа для финакта + фактическая стоимость
    op.add_column(
        "reagent_catalog",
        sa.Column("act_group", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "reagent_catalog",
        sa.Column("unit_cost", sa.Numeric(18, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reagent_catalog", "unit_cost")
    op.drop_column("reagent_catalog", "act_group")
    for name in (
        "period_label", "amount_with_vat", "vat_amount", "amount",
        "price_per_unit", "unit", "work_group", "well_number",
    ):
        op.drop_column("document_items", name)
    op.drop_index("ix_contract_price_lookup", table_name="contract_price")
    op.drop_table("contract_price")
    op.drop_column("document_types", "docx_template_name")
