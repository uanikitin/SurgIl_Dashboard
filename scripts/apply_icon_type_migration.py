"""
Add icon_type column to map_object table.
Run: python scripts/apply_icon_type_migration.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db import engine
from sqlalchemy import text

SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'map_object' AND column_name = 'icon_type'
    ) THEN
        ALTER TABLE map_object ADD COLUMN icon_type VARCHAR(30) NOT NULL DEFAULT 'default';
    END IF;
END $$;
"""

if __name__ == "__main__":
    with engine.begin() as conn:
        conn.execute(text(SQL))
    print("OK: icon_type column added to map_object.")
