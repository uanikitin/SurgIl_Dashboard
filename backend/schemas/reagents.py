# backend/schemas/reagents.py
from datetime import datetime
from pydantic import BaseModel


class ReagentBase(BaseModel):
    reagent: str
    qty: float
    unit: str = "kg"
    source: str | None = None
    location: str | None = None
    comment: str | None = None


class ReagentCreate(ReagentBase):
    """
    Схема для создания записи о приходе реагента.
    received_at можно не указывать – тогда сервер проставит сам.
    """
    received_at: datetime | None = None


class ReagentRead(ReagentBase):
    """
    То, что возвращаем наружу при чтении отдельной записи.
    """
    id: int
    received_at: datetime

    class Config:
        from_attributes = True  # Pydantic v2 (аналог orm_mode=True)


class ReagentBalance(BaseModel):
    """
    Остаток по каждому реагенту.
    """
    reagent: str
    total_qty: float
    unit: str | None = None

class InventoryBase(BaseModel):
    reagent: str
    qty: float
    unit: str = "kg"
    location: str | None = None
    comment: str | None = None


class InventoryCreate(InventoryBase):
    snapshot_at: datetime | None = None


class InventoryRead(InventoryBase):
    id: int
    snapshot_at: datetime

    class Config:
        from_attributes = True