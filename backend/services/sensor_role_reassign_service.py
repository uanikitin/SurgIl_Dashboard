"""
Retroactive role reassignment for a LoRa sensor.

When a sensor is moved from tube↔line role at a given valid_from (local Kungrad
time), existing pressure readings written under the old column must be moved
to the new column. Data BEFORE valid_from stays untouched.

Public API:
  - preview(sensor_id, new_role, valid_from_local)  → dict with counts
  - apply(sensor_id, new_role, valid_from_local, note, created_by, overwrite)
      → creates SensorAssignment + moves existing data + recomputes aggregates

All DB-level timestamps are UTC (measured_at). `valid_from_local` is entered
by the user in Kungrad time (UTC+5) and converted to UTC here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.services.sensor_assignment_service import (
    VALID_ROLES,
    create_assignment,
)

log = logging.getLogger(__name__)

_TZ_OFFSET = timedelta(hours=5)  # Kungrad local → UTC


def _source_col_for_old_role(new_role: str) -> tuple[str, str, str, str]:
    """
    Returns (src_p_col, src_sid_col, dst_p_col, dst_sid_col) for the UPDATE.
    If new_role='tube', source is *_line (move line→tube).
    """
    if new_role == "tube":
        return "p_line", "sensor_id_line", "p_tube", "sensor_id_tube"
    return "p_tube", "sensor_id_tube", "p_line", "sensor_id_line"


def preview(
    sensor_id: int,
    new_role: str,
    valid_from_local: datetime,
) -> dict:
    """
    Count rows that would be moved/conflicted in pressure_raw and
    pressure_readings without making any changes.

    Returns {
      "valid_from_utc": datetime,
      "pg": {"to_move": N, "conflicts": N, "affected_wells": [...]},
      "sqlite": {"to_move": N, "conflicts": N, "affected_wells": [...]},
    }
    """
    if new_role not in VALID_ROLES:
        raise ValueError(f"new_role must be one of {VALID_ROLES}, got {new_role!r}")

    since_utc = valid_from_local - _TZ_OFFSET
    src_p, src_sid, dst_p, dst_sid = _source_col_for_old_role(new_role)

    result = {
        "valid_from_utc": since_utc,
        "pg": {"to_move": 0, "conflicts": 0, "affected_wells": []},
        "sqlite": {"to_move": 0, "conflicts": 0, "affected_wells": []},
    }

    # ── PostgreSQL: pressure_raw ──
    from backend.db import engine as pg_engine

    try:
        with pg_engine.connect() as conn:
            row = conn.execute(
                text(
                    f"SELECT COUNT(*) AS to_move, "
                    f"       COUNT(*) FILTER (WHERE {dst_p} IS NOT NULL) AS conflicts "
                    f"FROM pressure_raw "
                    f"WHERE {src_sid} = :sid AND measured_at >= :since"
                ),
                {"sid": sensor_id, "since": since_utc},
            ).mappings().first()
            result["pg"]["to_move"] = int(row["to_move"] or 0)
            result["pg"]["conflicts"] = int(row["conflicts"] or 0)

            wells = conn.execute(
                text(
                    f"SELECT DISTINCT well_id FROM pressure_raw "
                    f"WHERE {src_sid} = :sid AND measured_at >= :since"
                ),
                {"sid": sensor_id, "since": since_utc},
            ).scalars().all()
            result["pg"]["affected_wells"] = sorted(wells)
    except Exception as e:
        log.warning("preview PG query failed: %s", e)

    # ── SQLite: pressure_readings ──
    try:
        from backend.db_pressure import PressureSessionLocal, init_pressure_db

        init_pressure_db()
        db = PressureSessionLocal()
        try:
            row = db.execute(
                text(
                    f"SELECT COUNT(*) AS to_move, "
                    f"       SUM(CASE WHEN {dst_p} IS NOT NULL THEN 1 ELSE 0 END) AS conflicts "
                    f"FROM pressure_readings "
                    f"WHERE {src_sid} = :sid AND measured_at >= :since"
                ),
                {"sid": sensor_id, "since": since_utc},
            ).mappings().first()
            result["sqlite"]["to_move"] = int((row["to_move"] or 0) if row else 0)
            result["sqlite"]["conflicts"] = int((row["conflicts"] or 0) if row else 0)

            wells = db.execute(
                text(
                    f"SELECT DISTINCT well_id FROM pressure_readings "
                    f"WHERE {src_sid} = :sid AND measured_at >= :since"
                ),
                {"sid": sensor_id, "since": since_utc},
            ).scalars().all()
            result["sqlite"]["affected_wells"] = sorted(wells)
        finally:
            db.close()
    except Exception as e:
        log.warning("preview SQLite query failed (may not have local DB): %s", e)

    return result


def apply(
    pg_db: Session,
    sensor_id: int,
    new_role: str,
    valid_from_local: datetime,
    note: Optional[str] = None,
    created_by: Optional[int] = None,
    overwrite_conflicts: bool = False,
) -> dict:
    """
    Create assignment and retroactively move data in pressure_raw + pressure_readings
    from valid_from_local onwards. Recomputes pressure_hourly and pressure_latest.

    Parameters:
        pg_db: open PostgreSQL session (for create_assignment).
        overwrite_conflicts: if True, overwrite existing non-NULL target column
            values. If False, skip rows with conflicts.

    Returns counts dict like preview(), plus "assignment_id".
    """
    if new_role not in VALID_ROLES:
        raise ValueError(f"new_role must be one of {VALID_ROLES}, got {new_role!r}")

    since_utc = valid_from_local - _TZ_OFFSET
    src_p, src_sid, dst_p, dst_sid = _source_col_for_old_role(new_role)

    # 1) Create assignment row (closes previous active if any)
    assignment = create_assignment(
        db=pg_db,
        sensor_id=sensor_id,
        role=new_role,
        valid_from=valid_from_local,
        note=note,
        created_by=created_by,
    )
    pg_db.flush()

    pg_moved = 0
    pg_skipped = 0
    affected_wells: set[int] = set()
    sqlite_moved = 0
    sqlite_skipped = 0

    # 2) PostgreSQL: move pressure_raw
    from backend.db import engine as pg_engine

    with pg_engine.begin() as conn:
        wells = conn.execute(
            text(
                f"SELECT DISTINCT well_id FROM pressure_raw "
                f"WHERE {src_sid} = :sid AND measured_at >= :since"
            ),
            {"sid": sensor_id, "since": since_utc},
        ).scalars().all()
        affected_wells.update(wells)

        if overwrite_conflicts:
            # Move all matching rows; target column is overwritten.
            res = conn.execute(
                text(
                    f"UPDATE pressure_raw "
                    f"SET {dst_p} = {src_p}, {dst_sid} = {src_sid}, "
                    f"    {src_p} = NULL, {src_sid} = NULL "
                    f"WHERE {src_sid} = :sid AND measured_at >= :since"
                ),
                {"sid": sensor_id, "since": since_utc},
            )
            pg_moved = res.rowcount or 0
        else:
            # Count skipped (conflict) rows first
            conflict_row = conn.execute(
                text(
                    f"SELECT COUNT(*) FROM pressure_raw "
                    f"WHERE {src_sid} = :sid AND measured_at >= :since "
                    f"  AND {dst_p} IS NOT NULL"
                ),
                {"sid": sensor_id, "since": since_utc},
            ).scalar()
            pg_skipped = int(conflict_row or 0)

            res = conn.execute(
                text(
                    f"UPDATE pressure_raw "
                    f"SET {dst_p} = {src_p}, {dst_sid} = {src_sid}, "
                    f"    {src_p} = NULL, {src_sid} = NULL "
                    f"WHERE {src_sid} = :sid AND measured_at >= :since "
                    f"  AND {dst_p} IS NULL"
                ),
                {"sid": sensor_id, "since": since_utc},
            )
            pg_moved = res.rowcount or 0

    # 3) SQLite: move pressure_readings (best-effort)
    try:
        from backend.db_pressure import PressureSessionLocal, init_pressure_db

        init_pressure_db()
        db = PressureSessionLocal()
        try:
            wells = db.execute(
                text(
                    f"SELECT DISTINCT well_id FROM pressure_readings "
                    f"WHERE {src_sid} = :sid AND measured_at >= :since"
                ),
                {"sid": sensor_id, "since": since_utc},
            ).scalars().all()
            affected_wells.update(wells)

            if overwrite_conflicts:
                res = db.execute(
                    text(
                        f"UPDATE pressure_readings "
                        f"SET {dst_p} = {src_p}, {dst_sid} = {src_sid}, "
                        f"    {src_p} = NULL, {src_sid} = NULL "
                        f"WHERE {src_sid} = :sid AND measured_at >= :since"
                    ),
                    {"sid": sensor_id, "since": since_utc},
                )
                sqlite_moved = res.rowcount or 0
            else:
                conflict = db.execute(
                    text(
                        f"SELECT COUNT(*) FROM pressure_readings "
                        f"WHERE {src_sid} = :sid AND measured_at >= :since "
                        f"  AND {dst_p} IS NOT NULL"
                    ),
                    {"sid": sensor_id, "since": since_utc},
                ).scalar()
                sqlite_skipped = int(conflict or 0)

                res = db.execute(
                    text(
                        f"UPDATE pressure_readings "
                        f"SET {dst_p} = {src_p}, {dst_sid} = {src_sid}, "
                        f"    {src_p} = NULL, {src_sid} = NULL "
                        f"WHERE {src_sid} = :sid AND measured_at >= :since "
                        f"  AND {dst_p} IS NULL"
                    ),
                    {"sid": sensor_id, "since": since_utc},
                )
                sqlite_moved = res.rowcount or 0
            db.commit()
        finally:
            db.close()
    except Exception as e:
        log.warning("apply SQLite step failed (may not have local DB): %s", e)

    # 4) Recompute pressure_hourly + pressure_latest for affected wells
    try:
        from backend.services.pressure_aggregate_service import (
            aggregate_to_hourly,
            update_latest,
        )

        if affected_wells:
            aggregate_to_hourly(since=since_utc, well_ids=affected_wells)
            update_latest(well_ids=affected_wells)
    except Exception as e:
        log.warning("apply aggregate step failed: %s", e)

    log.info(
        "role reassign applied: sensor=%d new_role=%s valid_from=%s "
        "pg_moved=%d pg_skipped=%d sqlite_moved=%d sqlite_skipped=%d wells=%s",
        sensor_id, new_role, valid_from_local,
        pg_moved, pg_skipped, sqlite_moved, sqlite_skipped,
        sorted(affected_wells),
    )

    return {
        "assignment_id": assignment.id,
        "valid_from_utc": since_utc,
        "pg": {"moved": pg_moved, "skipped": pg_skipped},
        "sqlite": {"moved": sqlite_moved, "skipped": sqlite_skipped},
        "affected_wells": sorted(affected_wells),
    }
