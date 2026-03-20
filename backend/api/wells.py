from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..db import get_db
from ..models.wells import Well

from datetime import datetime
from fastapi import Form
from fastapi.responses import RedirectResponse


from backend.services.equipment_loader import EQUIPMENT_LIST, EQUIPMENT_BY_CODE
router = APIRouter(prefix="/api/wells", tags=["Wells"])



@router.get("")
def list_wells(db: Session = Depends(get_db)):
    """
    Список всех скважин.

    Пока без фильтров: просто всё, что есть в таблице wells.
    Используем для:
      - выпадающего меню выбора скважины
      - плиток на главной странице
    """
    rows = db.query(Well).order_by(Well.name.asc()).all()
    return rows


@router.get("/nav")
def wells_nav(db: Session = Depends(get_db)):
    """Lightweight list for navigation panel: id, number, status, status_color."""
    from backend.models.well_status import WellStatus
    from backend.config.status_registry import css_by_label, STATUS_BY_CODE, STATUS_LIST

    wells = db.query(Well).order_by(Well.number.asc()).all()
    well_ids = [w.id for w in wells]

    active = {}
    if well_ids:
        rows = db.query(WellStatus).filter(
            WellStatus.well_id.in_(well_ids),
            WellStatus.dt_end.is_(None),
        ).all()
        active = {s.well_id: s.status for s in rows}

    result = []
    for w in wells:
        status_label = active.get(w.id)
        css_code = css_by_label(status_label)
        color = STATUS_BY_CODE.get(css_code, {}).get("color", "#94a3b8")
        result.append({
            "id": w.id,
            "number": w.number or str(w.id),
            "name": w.name or "",
            "status": status_label or "",
            "color": color,
        })

    # Status display order for frontend grouping
    status_order = [s["label"] for s in STATUS_LIST]
    return {"wells": result, "status_order": status_order}


@router.get("/{well_id}")
def get_well(well_id: int, db: Session = Depends(get_db)):
    """
    Получить одну скважину по её ID.
    Нужна для страницы конкретной скважины и детальной информации.
    """
    row = db.query(Well).filter(Well.id == well_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Скважина {well_id} не найдена")
    return row