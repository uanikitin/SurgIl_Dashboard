"""
Роутер «Зависимость двух параметров» (scatter + регрессия).

POST /api/param-correlation/compute — облако точек X↔Y за период + регрессия.
Переиспользуемый блок (kind='param_correlation'), сохраняется в любую главу.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db import SessionLocal
from backend.models.wells import Well
from backend.services.param_correlation_service import build_param_correlation

router = APIRouter(prefix="/api/param-correlation", tags=["param-correlation"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class CorrelationRequest(BaseModel):
    well_id: int
    period_from: date
    period_to: date
    x_signal: str = "dp"
    y_signal: str = "q"
    label: str | None = None


@router.post("/compute")
def compute_correlation(req: CorrelationRequest, db: Session = Depends(get_db)):
    """Считает зависимость X↔Y за период. Возвращает snapshot."""
    w = db.query(Well).filter(Well.id == req.well_id).first()
    well_number = str(w.number) if w else None

    snapshot = build_param_correlation(
        db,
        well_id=req.well_id,
        well_number=well_number,
        period_from=req.period_from,
        period_to=req.period_to,
        x_signal=req.x_signal,
        y_signal=req.y_signal,
        label=req.label,
    )
    snapshot["computed_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    return {
        "ok": snapshot.get("block_status") not in ("no_data",),
        "snapshot": snapshot,
    }
