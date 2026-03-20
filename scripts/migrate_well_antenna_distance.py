"""
Create well_antenna_distance table.
Run: python scripts/migrate_well_antenna_distance.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db import engine
from sqlalchemy import text

SQL = """
CREATE TABLE IF NOT EXISTS well_antenna_distance (
    id SERIAL PRIMARY KEY,
    well_id INTEGER NOT NULL REFERENCES wells(id),
    map_object_id INTEGER NOT NULL REFERENCES map_object(id),
    distance_m DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_well_antenna_dist UNIQUE (well_id, map_object_id)
);

CREATE INDEX IF NOT EXISTS ix_well_antenna_dist_well
ON well_antenna_distance (well_id);
"""

if __name__ == "__main__":
    with engine.begin() as conn:
        conn.execute(text(SQL))
    print("OK: well_antenna_distance table created.")
