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