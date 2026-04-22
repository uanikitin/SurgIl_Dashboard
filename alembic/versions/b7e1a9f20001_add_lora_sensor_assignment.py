"""add lora_sensor_assignment (sensor role timeline)

Revision ID: b7e1a9f20001
Revises: d7dd1888bb00
Create Date: 2026-04-22

Хранит историю назначений физической роли датчика (tube/line), чтобы
переопределить роль, выводимую по умолчанию из csv_column (Ptr→tube, Pshl→line).

Семантика:
- Активное назначение: valid_to IS NULL
- Переприсвоение: valid_to старой записи = valid_from новой
- Отсутствие записей → роль берётся по csv_column (обратная совместимость)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7e1a9f20001"
down_revision: Union[str, Sequence[str], None] = "d7dd1888bb00"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lora_sensor_assignment",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "sensor_id",
            sa.Integer,
            sa.ForeignKey("lora_sensors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(10), nullable=False),  # 'tube' | 'line'
        sa.Column("valid_from", sa.DateTime(timezone=False), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=False), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_by", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('tube','line')", name="ck_lsa_role"),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_lsa_valid_range",
        ),
    )
    op.create_index(
        "ix_lsa_sensor_from",
        "lora_sensor_assignment",
        ["sensor_id", "valid_from"],
    )
    op.create_index(
        "ix_lsa_sensor_active",
        "lora_sensor_assignment",
        ["sensor_id"],
        postgresql_where=sa.text("valid_to IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_lsa_sensor_active", table_name="lora_sensor_assignment")
    op.drop_index("ix_lsa_sensor_from", table_name="lora_sensor_assignment")
    op.drop_table("lora_sensor_assignment")
