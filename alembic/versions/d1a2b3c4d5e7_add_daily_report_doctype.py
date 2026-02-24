"""add daily_report document types

Revision ID: d1a2b3c4d5e7
Revises: a1b2c3d4e5f6
Create Date: 2026-02-22
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d1a2b3c4d5e7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO document_types (
            code, name_ru, name_en, category,
            latex_template_name,
            is_periodic, period_type,
            requires_well, requires_period,
            auto_number_prefix, auto_number_format,
            description, sort_order
        ) VALUES
        (
            'daily_report_well',
            'Суточный отчёт по скважине',
            'Daily Well Report',
            'operational',
            'daily_report.tex',
            FALSE, NULL,
            TRUE, TRUE,
            'СО',
            '{prefix}-{well}-{date}',
            'Суточный отчёт по одной скважине: давления, дебит, события, реагенты',
            200
        ),
        (
            'daily_report_all',
            'Суточный сводный отчёт',
            'Daily Summary Report',
            'operational',
            'daily_report.tex',
            FALSE, NULL,
            FALSE, TRUE,
            'СО-ALL',
            '{prefix}-{date}-{seq:02d}',
            'Сводный суточный отчёт по всем скважинам',
            210
        )
        ON CONFLICT (code) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM document_types
        WHERE code IN ('daily_report_well', 'daily_report_all');
    """)
