from datetime import datetime
from typing import Optional, List

from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.models.reagents import ReagentSupply


def create_reagent_supply(
    db: Session,
    *,
    reagent: str,
    qty: float,
    unit: str = "шт",
    received_at: Optional[datetime] = None,
    source: Optional[str] = None,
    location: Optional[str] = None,
    comment: Optional[str] = None,
) -> ReagentSupply:
    """
    Записывает поступление реагента в базу.
    """
    if received_at is None:
        received_at = datetime.utcnow()

    obj = ReagentSupply(
        reagent=reagent,
        qty=qty,
        unit=unit,
        received_at=received_at,
        source=source,
        location=location,
        comment=comment,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def list_reagent_supplies(
    db: Session,
    skip: int = 0,
    limit: int = 100,
) -> List[ReagentSupply]:
    """
    Возвращает историю поступлений (пагинация по желанию).
    """
    return (
        db.query(ReagentSupply)
        .order_by(ReagentSupply.received_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_reagent_balance_by_name(db: Session):
    """
    Старый простой расчёт остатков только по приходу,
    без учёта расхода: сумма qty по каждому реагенту.
    """
    rows = (
        db.query(
            ReagentSupply.reagent,
            ReagentSupply.unit,
            func.sum(ReagentSupply.qty).label("total_qty"),
        )
        .group_by(ReagentSupply.reagent, ReagentSupply.unit)
        .order_by(ReagentSupply.reagent)
        .all()
    )

    return [(r.reagent, float(r.total_qty), r.unit) for r in rows]