"""
Роутер «Спектр распределения давления» (стабильность скважины).

POST /api/pressure-spectrum/compute — считает гистограммы P_уст и ΔP за период
+ метрики стабильности. Возвращает snapshot для сохранения в блок
(customer_report_block, kind='pressure_spectrum') через общий CRUD блоков.

Переиспользуемый модуль: блок сохраняется в любую главу через params.chapter.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db import SessionLocal
from backend.models.wells import Well
from backend.services.pressure_spectrum_service import (
    build_pressure_spectrum,
    DEFAULT_BIN_WIDTH_PRESSURE,
    DEFAULT_BIN_WIDTH_DP,
)

router = APIRouter(prefix="/api/pressure-spectrum", tags=["pressure-spectrum"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class SpectrumRequest(BaseModel):
    well_id: int
    period_from: date
    period_to: date
    bin_width_pressure: float = DEFAULT_BIN_WIDTH_PRESSURE
    bin_width_dp: float = DEFAULT_BIN_WIDTH_DP
    label: str | None = None
    # Регулируемые критерии стабильности (None → мягкие дефолты сервиса)
    cv_threshold: float | None = None
    outlier_threshold: float | None = None
    niqr_p_stable: float | None = None
    niqr_p_moderate: float | None = None
    niqr_dp_stable: float | None = None
    niqr_dp_moderate: float | None = None
    remove_outliers: bool = False


@router.post("/compute")
def compute_spectrum(req: SpectrumRequest, db: Session = Depends(get_db)):
    """Считает спектр распределения P_уст и ΔP за период. Возвращает snapshot."""
    w = db.query(Well).filter(Well.id == req.well_id).first()
    well_number = str(w.number) if w else None

    snapshot = build_pressure_spectrum(
        db,
        well_id=req.well_id,
        well_number=well_number,
        period_from=req.period_from,
        period_to=req.period_to,
        bin_width_pressure=req.bin_width_pressure,
        bin_width_dp=req.bin_width_dp,
        label=req.label,
        cv_threshold=req.cv_threshold,
        outlier_threshold=req.outlier_threshold,
        niqr_p_stable=req.niqr_p_stable,
        niqr_p_moderate=req.niqr_p_moderate,
        niqr_dp_stable=req.niqr_dp_stable,
        niqr_dp_moderate=req.niqr_dp_moderate,
        remove_outliers=req.remove_outliers,
    )
    snapshot["computed_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    return {
        "ok": snapshot.get("block_status") not in ("no_data",),
        "snapshot": snapshot,
    }
