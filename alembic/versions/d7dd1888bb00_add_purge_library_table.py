"""add_purge_library_table

Revision ID: d7dd1888bb00
Revises: b2c3d4e5f6a7
Create Date: 2026-03-19 23:33:36.650207

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7dd1888bb00'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Библиотека продувок — эталонные данные из маркеров + LoRa."""
    op.execute("""
        CREATE TABLE IF NOT EXISTS purge_library (
            id SERIAL PRIMARY KEY,
            well_id INTEGER NOT NULL REFERENCES wells(id),

            -- Маркеры событий (ручные показания)
            start_time TIMESTAMP NOT NULL,
            press_time TIMESTAMP,
            stop_time TIMESTAMP,

            has_start BOOLEAN NOT NULL DEFAULT TRUE,
            has_press BOOLEAN NOT NULL DEFAULT FALSE,
            has_stop BOOLEAN NOT NULL DEFAULT FALSE,
            description TEXT,

            -- Ручные показания манометра (кгс/см²)
            gauge_start_p_tube FLOAT,
            gauge_start_p_line FLOAT,
            gauge_press_p_tube FLOAT,
            gauge_press_p_line FLOAT,
            gauge_stop_p_tube FLOAT,
            gauge_stop_p_line FLOAT,

            -- Фаза 1: Стравливание (start → press)
            venting_duration_min FLOAT,
            pressure_drop_atm FLOAT,
            venting_rate_atm_per_min FLOAT,

            -- Фаза 2: Набор давления (press → stop)
            buildup_duration_min FLOAT,
            pressure_rise_atm FLOAT,
            buildup_rate_atm_per_min FLOAT,
            overshoot_above_baseline_atm FLOAT,

            -- Общие параметры
            total_downtime_min FLOAT,

            -- P_line поведение
            p_line_mean FLOAT,
            p_line_std FLOAT,
            p_line_stable BOOLEAN,

            -- LoRa видимость
            lora_visible BOOLEAN DEFAULT FALSE,
            lora_baseline FLOAT,
            lora_min_during_purge FLOAT,
            lora_drop_atm FLOAT,
            lora_emission_integral FLOAT,
            lora_stabilization_min FLOAT,
            lora_data_points INTEGER,

            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_purge_library_well
            ON purge_library(well_id);
        CREATE INDEX IF NOT EXISTS idx_purge_library_start
            ON purge_library(start_time);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_purge_library_well_start
            ON purge_library(well_id, start_time);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS purge_library;")
