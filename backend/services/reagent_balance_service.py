# backend/services/reagent_balance_service.py
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from backend.models.events import Event
from backend.models.reagent_catalog import ReagentCatalog
from backend.models.reagent_inventory import ReagentInventorySnapshot
from backend.models.reagents import ReagentSupply


ZERO = Decimal("0")
EPS = Decimal("0.001")  # порог значимого расхождения


def _D(x) -> Decimal:
    """Безопасно приводит значение к Decimal."""
    if x is None:
        return ZERO
    if isinstance(x, Decimal):
        return x
    # str(x) чтобы не ловить двоичную грязь float напрямую
    return Decimal(str(x))


class ReagentBalanceService:
    """
    Сервис для расчета актуальных остатков реагентов
    с учетом последней инвентаризации
    """

    @staticmethod
    def get_current_balance(
        db: Session,
        reagent_name: str,
        as_of_date: Optional[datetime] = None,
    ) -> Dict:
        """
        Рассчитывает актуальный остаток реагента на дату.

        Логика:
        1. Найти последнюю инвентаризацию до as_of_date
        2. Если инвентаризация найдена:
           - остаток = инвентаризация.qty + поставки_после - расход_после
        3. Если инвентаризации нет:
           - остаток = все_поставки - весь_расход до as_of_date
        """
        if as_of_date is None:
            as_of_date = datetime.now()  # naive, как в БД

        # 1) Последняя инвентаризация до даты
        last_inventory = (
            db.query(ReagentInventorySnapshot)
            .filter(
                ReagentInventorySnapshot.reagent == reagent_name,
                ReagentInventorySnapshot.snapshot_at <= as_of_date,
            )
            .order_by(desc(ReagentInventorySnapshot.snapshot_at))
            .first()
        )

        if last_inventory:
            base_qty = _D(last_inventory.qty)
            base_date = last_inventory.snapshot_at

            # Поставки после инвентаризации
            supplies_after = _D(
                db.query(func.sum(ReagentSupply.qty))
                .filter(
                    ReagentSupply.reagent == reagent_name,
                    ReagentSupply.received_at > base_date,
                    ReagentSupply.received_at <= as_of_date,
                )
                .scalar()
            )

            # Расход после инвентаризации (из событий)
            consumption_after = _D(
                db.query(func.sum(Event.qty))
                .filter(
                    Event.reagent == reagent_name,
                    Event.event_time > base_date,
                    Event.event_time <= as_of_date,
                    Event.event_type == "reagent",
                )
                .scalar()
            )

            current_balance = base_qty + supplies_after - consumption_after

            return {
                "reagent": reagent_name,
                "current_balance": current_balance,
                "last_inventory_date": base_date,
                "last_inventory_qty": base_qty,
                "supplies_after_inventory": supplies_after,
                "consumption_after_inventory": consumption_after,
                "calculation_method": "based_on_inventory",
                "as_of_date": as_of_date,
            }

        # 2) Нет инвентаризации — считаем с начала
        total_supplies = _D(
            db.query(func.sum(ReagentSupply.qty))
            .filter(
                ReagentSupply.reagent == reagent_name,
                ReagentSupply.received_at <= as_of_date,
            )
            .scalar()
        )

        total_consumption = _D(
            db.query(func.sum(Event.qty))
            .filter(
                Event.reagent == reagent_name,
                Event.event_time <= as_of_date,
                Event.event_type == "reagent",
            )
            .scalar()
        )

        current_balance = total_supplies - total_consumption

        return {
            "reagent": reagent_name,
            "current_balance": current_balance,
            "total_supplies": total_supplies,
            "total_consumption": total_consumption,
            "calculation_method": "supply_minus_consumption",
            "as_of_date": as_of_date,
        }

    @staticmethod
    def get_all_reagents_balance(
        db: Session,
        as_of_date: Optional[datetime] = None,
    ) -> List[Dict]:
        """Получает балансы для всех реагентов из каталога."""
        reagents = (
            db.query(ReagentCatalog)
            .filter(ReagentCatalog.is_active == True)  # noqa: E712
            .all()
        )

        balances: List[Dict] = []
        for reagent in reagents:
            balances.append(
                ReagentBalanceService.get_current_balance(db, reagent.name, as_of_date)
            )
        return balances

    @staticmethod
    def get_discrepancy_report(
        db: Session,
        days: int = 7,
    ) -> List[Dict]:
        """
        Отчет о расхождениях между расчетным и фактическим остатком
        за последние N дней.
        """
        from_date = datetime.utcnow() - timedelta(days=days)

        inventories = (
            db.query(ReagentInventorySnapshot)
            .filter(ReagentInventorySnapshot.snapshot_at >= from_date)
            .order_by(ReagentInventorySnapshot.snapshot_at.desc())
            .all()
        )

        report: List[Dict] = []
        for inv in inventories:
            calculated = ReagentBalanceService.get_current_balance(
                db, inv.reagent, inv.snapshot_at - timedelta(seconds=1)
            )

            inv_qty = _D(inv.qty)
            calc_qty = _D(calculated.get("current_balance"))

            discrepancy = inv_qty - calc_qty

            if abs(discrepancy) > EPS:
                report.append(
                    {
                        "reagent": inv.reagent,
                        "inventory_date": inv.snapshot_at,
                        "inventory_qty": inv_qty,
                        "calculated_qty": calc_qty,
                        "discrepancy": discrepancy,
                        "location": inv.location,
                        "created_by": inv.created_by,
                    }
                )

        return report

    @staticmethod
    def get_average_daily_consumption(
        db: Session,
        reagent_name: str,
        days: int = 30,
    ) -> float:
        """Среднесуточный расход за последние N дней."""
        if days <= 0:
            return 0.0

        from_date = datetime.utcnow() - timedelta(days=days)

        total_consumption = _D(
            db.query(func.sum(Event.qty))
            .filter(
                Event.reagent == reagent_name,
                Event.event_time >= from_date,
                Event.event_type == "reagent",
            )
            .scalar()
        )

        avg = total_consumption / Decimal(days)
        return float(avg)