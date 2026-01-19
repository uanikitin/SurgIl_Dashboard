# backend/api/reagents.py
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from datetime import datetime

from ..db import get_db
from ..schemas.reagents import ReagentCreate, ReagentRead, ReagentBalance
from backend.repositories.reagents_service import (
    create_reagent_supply,
    list_reagent_supplies,
    get_reagent_balance_by_name,
)

router = APIRouter(
    prefix="/api/reagents",
    tags=["Reagents"],
)


@router.get("", response_model=list[ReagentRead])
def api_list_reagents(db: Session = Depends(get_db)):
    return list_reagent_supplies(db)


@router.post("", response_model=ReagentRead, status_code=status.HTTP_201_CREATED)
def api_create_reagent(data: ReagentCreate, db: Session = Depends(get_db)):
    dt = data.received_at or datetime.utcnow()
    obj = create_reagent_supply(
        db,
        reagent=data.reagent.strip(),
        qty=data.qty,
        unit=data.unit or "kg",
        received_at=dt,
        source=data.source,
        location=data.location,
        comment=data.comment,
    )
    return obj


@router.get("/balance", response_model=list[ReagentBalance])
def api_reagents_balance(db: Session = Depends(get_db)):
    """
    ВАЖНО: сейчас считает только суммарный ПРИХОД по reagent_supplies,
    без вычитания расхода из Event.
    """
    rows = get_reagent_balance_by_name(db)
    return [
        ReagentBalance(reagent=name, total_qty=qty, unit=unit)
        for (name, qty, unit) in rows
    ]