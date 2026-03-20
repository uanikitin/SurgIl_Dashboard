"""
/api/pressure-masks — CRUD для масок коррекции давления + авто-детекция.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Query, HTTPException

router = APIRouter(prefix="/api/pressure-masks", tags=["pressure-masks"])
log = logging.getLogger(__name__)

KUNGRAD_OFFSET = timedelta(hours=5)


def _mask_to_dict(m) -> dict:
    return {
        "id": m.id,
        "well_id": m.well_id,
        "problem_type": m.problem_type,
        "affected_sensor": m.affected_sensor,
        "correction_method": m.correction_method,
        "dt_start": (m.dt_start + KUNGRAD_OFFSET).isoformat() if m.dt_start else None,
        "dt_end": (m.dt_end + KUNGRAD_OFFSET).isoformat() if m.dt_end else None,
        "manual_delta_p": m.manual_delta_p,
        "is_active": m.is_active,
        "is_verified": m.is_verified,
        "verified_at": m.verified_at.isoformat() if m.verified_at else None,
        "verified_by": m.verified_by,
        "source": m.source,
        "detection_confidence": m.detection_confidence,
        "batch_id": m.batch_id,
        "reason": m.reason,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


# ──────────────────── LIST ────────────────────


@router.get("")
def list_masks(well_id: int = Query(...)):
    from backend.db import SessionLocal
    from backend.models.pressure_mask import PressureMask

    db = SessionLocal()
    try:
        rows = (
            db.query(PressureMask)
            .filter(PressureMask.well_id == well_id)
            .order_by(PressureMask.dt_start)
            .all()
        )
        return [_mask_to_dict(r) for r in rows]
    finally:
        db.close()


# ──────────────────── AUTO-DETECT (read-only) ────────────────────
# (до /{mask_id}, иначе "detect" перехватится как mask_id)


@router.get("/detect")
def detect(
    well_id: int = Query(...),
    days: int = Query(30, ge=1, le=365),
):
    """Запуск базовой эвристики детекции аномалий давления (без сохранения)."""
    from backend.services.pressure_mask_service import detect_anomalies

    results = detect_anomalies(well_id, days=days)
    return {"well_id": well_id, "days": days, "anomalies": results}


# ──────────────────── AUTO-DETECT + CREATE ────────────────────


@router.post("/auto-detect")
def auto_detect(data: dict):
    """Run all detectors and create mask records for found anomalies."""
    from backend.services.pressure_mask_service import auto_create_masks

    well_id = data.get("well_id")
    if not well_id:
        raise HTTPException(400, "well_id is required")
    days = data.get("days", 7)

    result = auto_create_masks(well_id=well_id, days=days)
    return result


# ──────────────────── PENDING (unverified) ────────────────────


@router.get("/pending")
def list_pending(well_id: int = Query(...)):
    """List unverified active masks for a well."""
    from backend.db import SessionLocal
    from backend.models.pressure_mask import PressureMask

    db = SessionLocal()
    try:
        rows = (
            db.query(PressureMask)
            .filter(
                PressureMask.well_id == well_id,
                PressureMask.is_active == True,
                PressureMask.is_verified == False,
            )
            .order_by(PressureMask.dt_start)
            .all()
        )
        return [_mask_to_dict(r) for r in rows]
    finally:
        db.close()


# ──────────────────── VERIFY BATCH ────────────────────


@router.post("/verify-batch")
def verify_batch(data: dict):
    """
    Approve or reject masks in batch.

    Body: {mask_ids: [1,2,3], action: "approve"|"reject"}
    """
    from backend.db import SessionLocal
    from backend.models.pressure_mask import PressureMask

    mask_ids = data.get("mask_ids", [])
    action = data.get("action")

    if not mask_ids:
        raise HTTPException(400, "mask_ids is required")
    if action not in ("approve", "reject"):
        raise HTTPException(400, "action must be 'approve' or 'reject'")

    db = SessionLocal()
    try:
        masks = (
            db.query(PressureMask)
            .filter(PressureMask.id.in_(mask_ids))
            .all()
        )
        if not masks:
            raise HTTPException(404, "No masks found")

        now = datetime.utcnow()
        updated = 0
        for m in masks:
            if action == "approve":
                m.is_verified = True
                m.verified_at = now
                m.verified_by = data.get("verified_by", "operator")
            else:
                m.is_active = False
            updated += 1

        db.commit()
        return {"ok": True, "action": action, "updated": updated}
    finally:
        db.close()


# ──────────────────── SUMMARY ────────────────────


@router.get("/summary")
def mask_summary(
    well_id: int = Query(...),
    dt_start: str = Query(...),
    dt_end: str = Query(...),
):
    """Get mask summary for a period (for reports)."""
    from backend.services.pressure_mask_service import get_mask_summary_for_period

    try:
        start = datetime.fromisoformat(dt_start) - KUNGRAD_OFFSET
        end = datetime.fromisoformat(dt_end) - KUNGRAD_OFFSET
    except ValueError:
        raise HTTPException(400, "Invalid date format")

    return get_mask_summary_for_period(well_id, start, end)


# ──────────────────── GET ONE ────────────────────


@router.get("/{mask_id}")
def get_mask(mask_id: int):
    from backend.db import SessionLocal
    from backend.models.pressure_mask import PressureMask

    db = SessionLocal()
    try:
        m = db.query(PressureMask).filter(PressureMask.id == mask_id).first()
        if not m:
            raise HTTPException(404, "Mask not found")
        return _mask_to_dict(m)
    finally:
        db.close()


# ──────────────────── CREATE ────────────────────


@router.post("")
def create_mask(data: dict):
    from backend.db import SessionLocal
    from backend.models.pressure_mask import PressureMask

    required = ("well_id", "affected_sensor", "correction_method", "dt_start", "dt_end")
    for field in required:
        if not data.get(field):
            raise HTTPException(400, f"{field} is required")

    valid_sensors = ("p_tube", "p_line")
    if data["affected_sensor"] not in valid_sensors:
        raise HTTPException(400, f"affected_sensor must be one of: {valid_sensors}")

    valid_methods = ("median_1d", "median_3d", "delta_reconstruct", "interpolate", "exclude")
    if data["correction_method"] not in valid_methods:
        raise HTTPException(400, f"correction_method must be one of: {valid_methods}")

    valid_types = ("hydrate", "comm_loss", "sensor_fault", "manual", "degradation", "purge")
    problem_type = data.get("problem_type", "manual")
    if problem_type not in valid_types:
        raise HTTPException(400, f"problem_type must be one of: {valid_types}")

    # Фронтенд передаёт время в Кунграде — конвертируем в UTC
    try:
        dt_start = datetime.fromisoformat(data["dt_start"]) - KUNGRAD_OFFSET
        dt_end = datetime.fromisoformat(data["dt_end"]) - KUNGRAD_OFFSET
    except ValueError:
        raise HTTPException(400, "Invalid dt_start/dt_end format")

    if dt_end <= dt_start:
        raise HTTPException(400, "dt_end must be after dt_start")

    db = SessionLocal()
    try:
        m = PressureMask(
            well_id=data["well_id"],
            problem_type=problem_type,
            affected_sensor=data["affected_sensor"],
            correction_method=data["correction_method"],
            dt_start=dt_start,
            dt_end=dt_end,
            manual_delta_p=data.get("manual_delta_p"),
            is_active=data.get("is_active", True),
            reason=data.get("reason"),
        )
        db.add(m)
        db.commit()
        db.refresh(m)
        return _mask_to_dict(m)
    finally:
        db.close()


# ──────────────────── UPDATE ────────────────────


@router.put("/{mask_id}")
def update_mask(mask_id: int, data: dict):
    from backend.db import SessionLocal
    from backend.models.pressure_mask import PressureMask

    db = SessionLocal()
    try:
        m = db.query(PressureMask).filter(PressureMask.id == mask_id).first()
        if not m:
            raise HTTPException(404, "Mask not found")

        editable_fields = (
            "problem_type", "affected_sensor", "correction_method",
            "manual_delta_p", "is_active", "reason",
        )
        for field in editable_fields:
            if field in data:
                setattr(m, field, data[field])

        if "dt_start" in data:
            m.dt_start = datetime.fromisoformat(data["dt_start"]) - KUNGRAD_OFFSET
        if "dt_end" in data:
            m.dt_end = datetime.fromisoformat(data["dt_end"]) - KUNGRAD_OFFSET

        db.commit()
        db.refresh(m)
        return _mask_to_dict(m)
    finally:
        db.close()


# ──────────────────── TOGGLE ────────────────────


@router.patch("/{mask_id}/toggle")
def toggle_mask(mask_id: int):
    from backend.db import SessionLocal
    from backend.models.pressure_mask import PressureMask

    db = SessionLocal()
    try:
        m = db.query(PressureMask).filter(PressureMask.id == mask_id).first()
        if not m:
            raise HTTPException(404, "Mask not found")
        m.is_active = not m.is_active
        db.commit()
        return {"ok": True, "id": m.id, "is_active": m.is_active}
    finally:
        db.close()


# ──────────────────── DELETE ────────────────────


@router.delete("/{mask_id}")
def delete_mask(mask_id: int):
    from backend.db import SessionLocal
    from backend.models.pressure_mask import PressureMask

    db = SessionLocal()
    try:
        m = db.query(PressureMask).filter(PressureMask.id == mask_id).first()
        if not m:
            raise HTTPException(404, "Mask not found")
        db.delete(m)
        db.commit()
        return {"ok": True, "id": mask_id}
    finally:
        db.close()


