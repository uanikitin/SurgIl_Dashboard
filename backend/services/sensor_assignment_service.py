"""
Service for LoRa sensor role assignments (tube/line timeline).

Role resolution order:
  1. Newest assignment whose [valid_from, valid_to) contains t_local
  2. Fallback: default role from csv_column (Ptr→tube, Pshl→line)

All timestamps compared in Kungrad local time (UTC+5), matching how users
enter valid_from in the UI.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models.sensor_assignment import SensorAssignment


VALID_ROLES = ("tube", "line")


def load_assignment_cache() -> dict:
    """
    Returns {sensor_id: [(valid_from, valid_to, role), ...]} sorted by valid_from.
    Loaded once per import run to avoid per-row queries.
    """
    from backend.db import engine as pg_engine

    cache: dict = {}
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT sensor_id, valid_from, valid_to, role "
                "FROM lora_sensor_assignment "
                "ORDER BY sensor_id, valid_from"
            )
        ).fetchall()

    for sensor_id, valid_from, valid_to, role in rows:
        cache.setdefault(sensor_id, []).append((valid_from, valid_to, role))
    return cache


def resolve_role_at(
    sensor_id: int,
    t_local: datetime,
    assignment_cache: Optional[dict],
    default_role: str,
) -> str:
    """Role for sensor at local time `t_local`. Falls back to default_role."""
    if not assignment_cache:
        return default_role
    intervals = assignment_cache.get(sensor_id)
    if not intervals:
        return default_role

    # Newest first (list is sorted ascending by valid_from)
    for valid_from, valid_to, role in reversed(intervals):
        if valid_from is not None and t_local < valid_from:
            continue
        if valid_to is not None and t_local >= valid_to:
            continue
        return role
    return default_role


def get_active_assignment(db: Session, sensor_id: int) -> Optional[SensorAssignment]:
    return (
        db.query(SensorAssignment)
        .filter(
            SensorAssignment.sensor_id == sensor_id,
            SensorAssignment.valid_to.is_(None),
        )
        .order_by(SensorAssignment.valid_from.desc())
        .first()
    )


def list_assignments(db: Session, sensor_id: int) -> list[SensorAssignment]:
    return (
        db.query(SensorAssignment)
        .filter(SensorAssignment.sensor_id == sensor_id)
        .order_by(SensorAssignment.valid_from.desc())
        .all()
    )


def create_assignment(
    db: Session,
    sensor_id: int,
    role: str,
    valid_from: datetime,
    note: Optional[str] = None,
    created_by: Optional[int] = None,
) -> SensorAssignment:
    """
    Create new active assignment. Closes previous active one (if any) by
    setting its valid_to = valid_from.

    Raises ValueError on invalid role or if valid_from is not strictly after
    the previous active assignment's valid_from.
    """
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {VALID_ROLES}, got {role!r}")

    active = get_active_assignment(db, sensor_id)
    if active is not None:
        if valid_from <= active.valid_from:
            raise ValueError(
                f"valid_from ({valid_from}) must be after previous active "
                f"assignment start ({active.valid_from})"
            )
        active.valid_to = valid_from

    new_row = SensorAssignment(
        sensor_id=sensor_id,
        role=role,
        valid_from=valid_from,
        valid_to=None,
        note=note,
        created_by=created_by,
    )
    db.add(new_row)
    db.flush()
    return new_row
