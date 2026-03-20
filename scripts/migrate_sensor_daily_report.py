"""
Create sensor_daily_report table.
Run: venv/bin/python scripts/migrate_sensor_daily_report.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db import engine
from sqlalchemy import text

SQL = """
CREATE TABLE IF NOT EXISTS sensor_daily_report (
    id SERIAL PRIMARY KEY,
    report_date DATE NOT NULL,
    well_id INTEGER NOT NULL REFERENCES wells(id),
    sensor_role VARCHAR(10) NOT NULL,
    lora_sensor_id INTEGER,
    sensor_serial VARCHAR(64),

    well_name VARCHAR(128),
    well_lat DOUBLE PRECISION,
    well_lon DOUBLE PRECISION,

    expected_readings INTEGER DEFAULT 1440,
    actual_readings INTEGER DEFAULT 0,
    missing_count INTEGER DEFAULT 0,
    false_zero_count INTEGER DEFAULT 0,
    valid_count INTEGER DEFAULT 0,
    uptime_pct DOUBLE PRECISION DEFAULT 0,

    p_mean DOUBLE PRECISION,
    p_std DOUBLE PRECISION,
    p_min DOUBLE PRECISION,
    p_max DOUBLE PRECISION,
    p_median DOUBLE PRECISION,

    spikes_hampel INTEGER DEFAULT 0,
    spikes_instant INTEGER DEFAULT 0,
    spikes_pct DOUBLE PRECISION DEFAULT 0,

    sync_both_ok INTEGER DEFAULT 0,
    sync_only_this INTEGER DEFAULT 0,
    sync_only_other INTEGER DEFAULT 0,
    sync_both_miss INTEGER DEFAULT 0,
    sync_both_ok_pct DOUBLE PRECISION DEFAULT 0,

    gap_count INTEGER DEFAULT 0,
    gap_max_minutes INTEGER DEFAULT 0,
    gap_total_minutes INTEGER DEFAULT 0,

    quality_grade VARCHAR(1),
    quality_flags VARCHAR(500),

    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_sensor_daily_report UNIQUE (report_date, well_id, sensor_role)
);

CREATE INDEX IF NOT EXISTS ix_sdr_date ON sensor_daily_report (report_date);
CREATE INDEX IF NOT EXISTS ix_sdr_well ON sensor_daily_report (well_id);
CREATE INDEX IF NOT EXISTS ix_sdr_grade ON sensor_daily_report (quality_grade);
"""

if __name__ == "__main__":
    with engine.begin() as conn:
        conn.execute(text(SQL))
    print("OK: sensor_daily_report table created.")
