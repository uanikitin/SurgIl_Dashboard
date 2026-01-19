"""add reagent_supply

Revision ID: c3e8c8ab68aa
Revises:
Create Date: 2025-12-06 19:15:16.020302
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c3e8c8ab68aa"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Baseline migration.

    Схема БД уже создана вручную.
    Эта миграция НИЧЕГО не меняет в структуре БД,
    а только фиксирует текущий ревизионный номер в таблице alembic_version.
    """
    pass


def downgrade() -> None:
    """No-op downgrade.

    Ничего не откатываем, чтобы не трогать существующую схему и данные.
    """
    pass