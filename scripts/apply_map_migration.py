"""
Apply map_object table and can_view_map column migration.
Run: python scripts/apply_map_migration.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db import engine
from sqlalchemy import text

SQL = """
CREATE TABLE IF NOT EXISTS map_object (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    description TEXT,
    icon_color VARCHAR(20) NOT NULL DEFAULT '#e74c3c',
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'dashboard_users' AND column_name = 'can_view_map'
    ) THEN
        ALTER TABLE dashboard_users ADD COLUMN can_view_map BOOLEAN NOT NULL DEFAULT FALSE;
    END IF;
END $$;
"""

if __name__ == "__main__":
    with engine.begin() as conn:
        conn.execute(text(SQL))
    print("OK: map_object table created, can_view_map column added.")
