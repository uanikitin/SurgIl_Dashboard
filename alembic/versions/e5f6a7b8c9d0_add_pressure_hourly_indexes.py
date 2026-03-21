"""add pressure_hourly and pressure_latest indexes

Revision ID: e5f6a7b8c9d0
Revises: b2c3d4e5f6a7
Create Date: 2026-03-20 12:00:00.000000

"""
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pressure_hourly — основная таблица для графиков, запрашивается по (well_id, hour_start)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_pressure_hourly_well_hour
        ON pressure_hourly (well_id, hour_start)
    """)

    # pressure_latest — запрашивается по well_id
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_pressure_latest_well
        ON pressure_latest (well_id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_pressure_hourly_well_hour")
    op.execute("DROP INDEX IF EXISTS ix_pressure_latest_well")
