"""add documents system

Revision ID: efd7724b1099
Revises: 33e5a6fb24c5
Create Date: 2026-01-12 09:19:11.017981
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "efd7724b1099"
down_revision: Union[str, Sequence[str], None] = "33e5a6fb24c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- 1) document_types ---
    op.create_table(
        "document_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=50), nullable=False, unique=True),
        sa.Column("name_ru", sa.String(length=200), nullable=False),
        sa.Column("name_en", sa.String(length=200), nullable=True),
        sa.Column(
            "category",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'operational'"),
        ),

        sa.Column("latex_template_name", sa.String(length=100), nullable=True),
        sa.Column("excel_template_name", sa.String(length=100), nullable=True),

        sa.Column("is_periodic", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("period_type", sa.String(length=20), nullable=True),

        sa.Column("requires_well", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("requires_period", sa.Boolean(), nullable=False, server_default=sa.text("false")),

        sa.Column("auto_number_prefix", sa.String(length=20), nullable=True),
        sa.Column(
            "auto_number_format",
            sa.String(length=50),
            nullable=False,
            server_default=sa.text("'{prefix}-{year}-{seq:03d}'"),
        ),

        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),

        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )

    op.create_index("ix_document_types_code", "document_types", ["code"], unique=True)
    op.create_index("ix_document_types_category", "document_types", ["category"], unique=False)
    op.create_index("ix_document_types_is_active", "document_types", ["is_active"], unique=False)

    # --- 2) documents ---
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),

        sa.Column("doc_type_id", sa.Integer(), nullable=False),
        sa.Column("doc_number", sa.String(length=100), nullable=True, unique=True),

        sa.Column("well_id", sa.Integer(), nullable=True),

        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("period_month", sa.Integer(), nullable=True),
        sa.Column("period_year", sa.Integer(), nullable=True),

        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_by_name", sa.String(length=200), nullable=True),

        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),

        sa.Column("signed_at", sa.DateTime(), nullable=True),
        sa.Column("signed_by_name", sa.String(length=200), nullable=True),
        sa.Column("signed_by_position", sa.String(length=200), nullable=True),

        sa.Column("pdf_filename", sa.String(length=500), nullable=True),
        sa.Column("excel_filename", sa.String(length=500), nullable=True),
        sa.Column("latex_source", sa.Text(), nullable=True),

        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),

        sa.Column("notes", sa.Text(), nullable=True),

        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("parent_id", sa.Integer(), nullable=True),

        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),

        sa.CheckConstraint(
            "status IN ('draft', 'generated', 'signed', 'sent', 'archived', 'cancelled')",
            name="valid_status",
        ),
        sa.CheckConstraint(
            "(period_start IS NULL AND period_end IS NULL) OR "
            "(period_start IS NOT NULL AND period_end IS NOT NULL)",
            name="valid_period",
        ),

        sa.ForeignKeyConstraint(["doc_type_id"], ["document_types.id"], name="fk_documents_doc_type_id"),
        sa.ForeignKeyConstraint(["parent_id"], ["documents.id"], name="fk_documents_parent_id"),
        # ВАЖНО: wells должна уже существовать
        sa.ForeignKeyConstraint(["well_id"], ["wells.id"], name="fk_documents_well_id"),
    )

    op.create_index("ix_documents_doc_type_id", "documents", ["doc_type_id"], unique=False)
    op.create_index("ix_documents_well_id", "documents", ["well_id"], unique=False)
    op.create_index("ix_documents_status", "documents", ["status"], unique=False)
    op.create_index("ix_documents_created_at", "documents", ["created_at"], unique=False)

    op.create_index("idx_documents_period", "documents", ["period_year", "period_month"], unique=False)
    op.create_index("idx_documents_period_dates", "documents", ["period_start", "period_end"], unique=False)

    # --- 3) document_items ---
    op.create_table(
        "document_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),

        sa.Column("work_type", sa.String(length=200), nullable=True),
        sa.Column("event_time", sa.DateTime(), nullable=True),
        sa.Column("event_time_str", sa.String(length=50), nullable=True),

        sa.Column("quantity", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("reagent_name", sa.String(length=200), nullable=True),
        sa.Column("stage", sa.String(length=100), nullable=True),

        # ВАЖНО: BIGINT
        sa.Column("event_id", sa.BigInteger(), nullable=True),

        sa.Column("notes", sa.Text(), nullable=True),

        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"],
            name="fk_document_items_document_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("document_id", "line_number", name="unique_line_per_document"),
    )

    op.create_index("ix_document_items_document_id", "document_items", ["document_id"], unique=False)
    op.create_index("ix_document_items_event_id", "document_items", ["event_id"], unique=False)
    op.create_index("idx_document_items_line", "document_items", ["document_id", "line_number"], unique=False)

    # --- 4) document_signatures ---
    op.create_table(
        "document_signatures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), nullable=False),

        sa.Column("role", sa.String(length=100), nullable=False),
        sa.Column("role_title_ru", sa.String(length=200), nullable=True),

        sa.Column("signer_name", sa.String(length=200), nullable=True),
        sa.Column("signer_position", sa.String(length=200), nullable=True),
        sa.Column("company_name", sa.String(length=300), nullable=True),

        sa.Column("signed_at", sa.DateTime(), nullable=True),
        sa.Column("signature_image_path", sa.String(length=500), nullable=True),

        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("notes", sa.Text(), nullable=True),

        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"],
            name="fk_document_signatures_document_id",
            ondelete="CASCADE",
        ),
    )

    op.create_index("ix_document_signatures_document_id", "document_signatures", ["document_id"], unique=False)
    op.create_index("ix_document_signatures_role", "document_signatures", ["role"], unique=False)

    # --- (опционально, но полезно) seed document_types ---
    op.execute(
        """
        INSERT INTO document_types (
            code, name_ru, name_en, category,
            latex_template_name, excel_template_name,
            is_periodic, period_type,
            requires_well, requires_period,
            auto_number_prefix, sort_order
        ) VALUES
        ('well_acceptance', 'Акт приёма скважины', 'Well Acceptance Act', 'operational',
         'well_acceptance.tex', 'well_acceptance.xlsx',
         FALSE, NULL, TRUE, FALSE, 'АПС', 10),

        ('well_transfer', 'Акт передачи скважины', 'Well Transfer Act', 'operational',
         'well_transfer.tex', 'well_transfer.xlsx',
         FALSE, NULL, TRUE, FALSE, 'АПЕ', 20),

        ('well_service_start', 'Акт приёма на обслуживание', 'Service Start Act', 'operational',
         'well_service_start.tex', 'well_service_start.xlsx',
         FALSE, NULL, TRUE, FALSE, 'АПО', 30),

        ('equipment_install', 'Акт установки оборудования', 'Equipment Installation Act', 'operational',
         'equipment_install.tex', 'equipment_install.xlsx',
         FALSE, NULL, TRUE, FALSE, 'АУО', 40),

        ('equipment_remove', 'Акт демонтажа оборудования', 'Equipment Removal Act', 'operational',
         'equipment_remove.tex', 'equipment_remove.xlsx',
         FALSE, NULL, TRUE, FALSE, 'АДО', 50),

        ('reagent_expense', 'Акт расхода реагентов', 'Reagent Expense Act', 'financial',
         'reagent_expense.tex', 'reagent_expense.xlsx',
         TRUE, 'monthly', FALSE, TRUE, 'АРР', 100),

        ('invoice', 'Счёт-фактура', 'Invoice', 'financial',
         'invoice.tex', 'invoice.xlsx',
         TRUE, 'monthly', FALSE, TRUE, 'СФ', 110)

        ON CONFLICT (code) DO NOTHING;
        """
    )


def downgrade() -> None:
    # В обратном порядке
    op.drop_index("ix_document_signatures_role", table_name="document_signatures")
    op.drop_index("ix_document_signatures_document_id", table_name="document_signatures")
    op.drop_table("document_signatures")

    op.drop_index("idx_document_items_line", table_name="document_items")
    op.drop_index("ix_document_items_event_id", table_name="document_items")
    op.drop_index("ix_document_items_document_id", table_name="document_items")
    op.drop_table("document_items")

    op.drop_index("idx_documents_period_dates", table_name="documents")
    op.drop_index("idx_documents_period", table_name="documents")
    op.drop_index("ix_documents_created_at", table_name="documents")
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_index("ix_documents_well_id", table_name="documents")
    op.drop_index("ix_documents_doc_type_id", table_name="documents")
    op.drop_table("documents")

    op.drop_index("ix_document_types_is_active", table_name="document_types")
    op.drop_index("ix_document_types_category", table_name="document_types")
    op.drop_index("ix_document_types_code", table_name="document_types")
    op.drop_table("document_types")