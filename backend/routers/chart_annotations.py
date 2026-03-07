"""
/api/chart-annotations — CRUD для пользовательских аннотаций на графиках.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Query, HTTPException

router = APIRouter(prefix="/api/chart-annotations", tags=["chart-annotations"])
log = logging.getLogger(__name__)


@router.get("")
def list_annotations(well_id: int = Query(...)):
    from backend.db import SessionLocal
    from backend.models.chart_annotation import ChartAnnotation

    db = SessionLocal()
    try:
        rows = (
            db.query(ChartAnnotation)
            .filter(ChartAnnotation.well_id == well_id)
            .order_by(ChartAnnotation.dt_start)
            .all()
        )
        return [
            {
                "id": r.id,
                "well_id": r.well_id,
                "ann_type": r.ann_type,
                "dt_start": r.dt_start.isoformat(),
                "dt_end": r.dt_end.isoformat() if r.dt_end else None,
                "text": r.text,
                "color": r.color,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()


@router.post("")
def create_annotation(data: dict):
    from backend.db import SessionLocal
    from backend.models.chart_annotation import ChartAnnotation

    well_id = data.get("well_id")
    ann_type = data.get("ann_type", "point")
    dt_start_str = data.get("dt_start")
    dt_end_str = data.get("dt_end")
    text = (data.get("text") or "").strip()
    color = data.get("color", "#ff9800")

    if not well_id or not dt_start_str or not text:
        raise HTTPException(400, "well_id, dt_start, text required")

    if ann_type not in ("point", "range"):
        raise HTTPException(400, "ann_type must be 'point' or 'range'")

    dt_start = datetime.fromisoformat(dt_start_str)
    dt_end = datetime.fromisoformat(dt_end_str) if dt_end_str else None

    if ann_type == "range" and not dt_end:
        raise HTTPException(400, "range annotation requires dt_end")

    db = SessionLocal()
    try:
        ann = ChartAnnotation(
            well_id=well_id,
            ann_type=ann_type,
            dt_start=dt_start,
            dt_end=dt_end,
            text=text[:500],
            color=color,
        )
        db.add(ann)
        db.commit()
        db.refresh(ann)
        return {
            "id": ann.id,
            "well_id": ann.well_id,
            "ann_type": ann.ann_type,
            "dt_start": ann.dt_start.isoformat(),
            "dt_end": ann.dt_end.isoformat() if ann.dt_end else None,
            "text": ann.text,
            "color": ann.color,
        }
    finally:
        db.close()


@router.put("/{ann_id}")
def update_annotation(ann_id: int, data: dict):
    from backend.db import SessionLocal
    from backend.models.chart_annotation import ChartAnnotation

    db = SessionLocal()
    try:
        ann = db.query(ChartAnnotation).filter(ChartAnnotation.id == ann_id).first()
        if not ann:
            raise HTTPException(404, "Annotation not found")

        if "text" in data:
            ann.text = (data["text"] or "").strip()[:500]
        if "color" in data:
            ann.color = data["color"]

        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.delete("/{ann_id}")
def delete_annotation(ann_id: int):
    from backend.db import SessionLocal
    from backend.models.chart_annotation import ChartAnnotation

    db = SessionLocal()
    try:
        ann = db.query(ChartAnnotation).filter(ChartAnnotation.id == ann_id).first()
        if not ann:
            raise HTTPException(404, "Annotation not found")
        db.delete(ann)
        db.commit()
        return {"ok": True}
    finally:
        db.close()
