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

    # =========================================================================
    # АНАЛИТИКА: Расход по дням
    # =========================================================================

    @staticmethod
    def get_daily_consumption(
        db: Session,
        date_from: datetime,
        date_to: datetime,
        reagent_names: Optional[List[str]] = None,
        well_numbers: Optional[List[str]] = None,
    ) -> List[Dict]:
        """
        Возвращает суммарный расход по дням за период.
        Группировка по дате и реагенту.

        Returns: [{"date": "2025-01-24", "reagent": "SMOD", "qty": 15.0, "count": 3}, ...]
        """
        query = (
            db.query(
                func.date(Event.event_time).label("date"),
                Event.reagent,
                func.sum(Event.qty).label("qty"),
                func.count(Event.id).label("count"),
            )
            .filter(
                Event.event_time >= date_from,
                Event.event_time <= date_to,
                Event.event_type == "reagent",
                Event.reagent.isnot(None),
            )
        )

        if reagent_names:
            query = query.filter(Event.reagent.in_(reagent_names))

        if well_numbers:
            query = query.filter(Event.well.in_(well_numbers))

        rows = (
            query
            .group_by(func.date(Event.event_time), Event.reagent)
            .order_by(func.date(Event.event_time))
            .all()
        )

        return [
            {
                "date": str(row.date),
                "reagent": row.reagent,
                "qty": float(row.qty or 0),
                "count": row.count,
            }
            for row in rows
        ]

    # =========================================================================
    # АНАЛИТИКА: Расход по скважинам
    # =========================================================================

    @staticmethod
    def get_consumption_by_wells(
        db: Session,
        date_from: datetime,
        date_to: datetime,
        reagent_names: Optional[List[str]] = None,
        well_numbers: Optional[List[str]] = None,
        well_statuses: Optional[List[str]] = None,
    ) -> List[Dict]:
        """
        Возвращает расход по скважинам за период.

        Returns: [{"well": "1367", "reagent": "SMOD", "qty": 45.0, "count": 12}, ...]
        """
        from backend.models.wells import Well
        from backend.models.well_status import WellStatus

        query = (
            db.query(
                Event.well,
                Event.reagent,
                func.sum(Event.qty).label("qty"),
                func.count(Event.id).label("count"),
            )
            .filter(
                Event.event_time >= date_from,
                Event.event_time <= date_to,
                Event.event_type == "reagent",
                Event.reagent.isnot(None),
                Event.well.isnot(None),
            )
        )

        if reagent_names:
            query = query.filter(Event.reagent.in_(reagent_names))

        if well_numbers:
            query = query.filter(Event.well.in_(well_numbers))

        # Фільтр по статусу скважини
        if well_statuses:
            # Отримуємо номери скважин з потрібними статусами
            wells_with_status = (
                db.query(Well.well_number)
                .join(WellStatus, WellStatus.well_id == Well.id)
                .filter(
                    WellStatus.status.in_(well_statuses),
                    WellStatus.dt_end.is_(None)  # тільки активні статуси
                )
                .distinct()
                .all()
            )
            well_nums = [str(w[0]) for w in wells_with_status if w[0]]
            if well_nums:
                query = query.filter(Event.well.in_(well_nums))
            else:
                # Якщо немає скважин з таким статусом - порожній результат
                return []

        rows = (
            query
            .group_by(Event.well, Event.reagent)
            .order_by(func.sum(Event.qty).desc())
            .all()
        )

        return [
            {
                "well": row.well,
                "reagent": row.reagent,
                "qty": float(row.qty or 0),
                "count": row.count,
            }
            for row in rows
        ]

    # =========================================================================
    # АНАЛИТИКА: Статистика по реагенту
    # =========================================================================

    @staticmethod
    def get_reagent_statistics(
        db: Session,
        reagent_name: str,
        date_from: datetime,
        date_to: datetime,
    ) -> Dict:
        """
        Детальная статистика по одному реагенту за период.

        Returns: {
            "reagent": "SMOD",
            "total_qty": 150.0,
            "total_count": 45,
            "wells": [{"well": "1367", "qty": 30.0, "count": 10}, ...],
            "daily_avg": 5.0
        }
        """
        # Общий расход
        total = (
            db.query(
                func.sum(Event.qty).label("qty"),
                func.count(Event.id).label("count"),
            )
            .filter(
                Event.reagent == reagent_name,
                Event.event_time >= date_from,
                Event.event_time <= date_to,
                Event.event_type == "reagent",
            )
            .first()
        )

        # По скважинам
        wells_data = (
            db.query(
                Event.well,
                func.sum(Event.qty).label("qty"),
                func.count(Event.id).label("count"),
            )
            .filter(
                Event.reagent == reagent_name,
                Event.event_time >= date_from,
                Event.event_time <= date_to,
                Event.event_type == "reagent",
                Event.well.isnot(None),
            )
            .group_by(Event.well)
            .order_by(func.sum(Event.qty).desc())
            .all()
        )

        days = max(1, (date_to - date_from).days)
        total_qty = float(total.qty or 0)

        return {
            "reagent": reagent_name,
            "total_qty": total_qty,
            "total_count": total.count or 0,
            "wells": [
                {"well": w.well, "qty": float(w.qty or 0), "count": w.count}
                for w in wells_data
            ],
            "daily_avg": total_qty / days,
            "period_days": days,
        }

    # =========================================================================
    # ПРОГНОЗ: Дата окончания реагента
    # =========================================================================

    @staticmethod
    def get_depletion_forecast(
        db: Session,
        reagent_name: str,
        avg_days: int = 30,
    ) -> Dict:
        """
        Прогноз даты окончания реагента.

        Returns: {
            "reagent": "SMOD",
            "current_balance": 150.0,
            "avg_daily_consumption": 5.0,
            "days_remaining": 30,
            "forecast_date": "2025-02-24",
            "has_data": True
        }
        """
        # Текущий остаток
        balance_data = ReagentBalanceService.get_current_balance(db, reagent_name)
        current_balance = float(balance_data.get("current_balance", 0))

        # Средний расход
        avg_daily = ReagentBalanceService.get_average_daily_consumption(
            db, reagent_name, avg_days
        )

        result = {
            "reagent": reagent_name,
            "current_balance": current_balance,
            "avg_daily_consumption": avg_daily,
            "calculation_days": avg_days,
            "days_remaining": None,
            "forecast_date": None,
            "has_data": avg_daily > 0,
        }

        if avg_daily > 0 and current_balance > 0:
            days_remaining = int(current_balance / avg_daily)
            forecast_date = datetime.now() + timedelta(days=days_remaining)
            result["days_remaining"] = days_remaining
            result["forecast_date"] = forecast_date.strftime("%Y-%m-%d")

        return result

    @staticmethod
    def get_all_depletion_forecasts(
        db: Session,
        avg_days: int = 30,
    ) -> List[Dict]:
        """Прогнозы для всех активных реагентов."""
        reagents = (
            db.query(ReagentCatalog)
            .filter(ReagentCatalog.is_active == True)  # noqa: E712
            .all()
        )

        forecasts = []
        for reagent in reagents:
            forecast = ReagentBalanceService.get_depletion_forecast(
                db, reagent.name, avg_days
            )
            forecasts.append(forecast)

        # Сортируем: сначала те, у кого скоро закончится
        forecasts.sort(
            key=lambda x: x["days_remaining"] if x["days_remaining"] is not None else 9999
        )

        return forecasts

    # =========================================================================
    # ПРОГНОЗ ПО РЕАГЕНТАХ (СКЛАДСЬКИЙ) з фільтрами
    # =========================================================================

    @staticmethod
    def get_reagent_forecasts_with_filters(
        db: Session,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        reagent_names: Optional[List[str]] = None,
        well_numbers: Optional[List[str]] = None,
        well_statuses: Optional[List[str]] = None,
        lead_days: int = 15,
        include_depleted: bool = False,
    ) -> List[Dict]:
        """
        Прогноз вичерпання реагентів (складський рівень) з фільтрами.

        Параметри:
        - date_from, date_to: період для розрахунку avg_daily
        - reagent_names: фільтр по реагентах
        - well_numbers: фільтр по скважинах (враховуються при розрахунку розходу)
        - well_statuses: фільтр по статусах скважин
        - lead_days: за скільки днів до вичерпання рекомендувати замовлення

        Returns: [
            {
                "reagent": "SMOD",
                "unit": "шт",
                "stock": 150.0,
                "avg_daily": 5.0,
                "days_remaining": 30,
                "depletion_date": "2025-02-24",
                "order_by_date": "2025-02-09",
                "risk_status": "warning",  # critical/warning/normal/no_data
                "has_consumption": True,
                "top_consumers": [{"well": "1367", "qty": 30.0, "share": 0.2}, ...]
            },
            ...
        ]
        """
        from backend.models.wells import Well
        from backend.models.well_status import WellStatus

        # Визначаємо період для розрахунку avg_daily
        if date_to is None:
            date_to = datetime.now()
        if date_from is None:
            date_from = date_to - timedelta(days=30)

        period_days = max(1, (date_to - date_from).days)

        # Отримуємо список реагентів з каталогу
        reagents_query = (
            db.query(ReagentCatalog)
            .filter(ReagentCatalog.is_active == True)  # noqa: E712
        )
        if reagent_names:
            reagents_query = reagents_query.filter(ReagentCatalog.name.in_(reagent_names))

        reagents = reagents_query.all()

        if not reagents:
            return []

        # Підготовка фільтра по скважинах (з урахуванням статусів)
        well_filter = None
        if well_numbers:
            well_filter = well_numbers
        elif well_statuses:
            wells_with_status = (
                db.query(Well.well_number)
                .join(WellStatus, WellStatus.well_id == Well.id)
                .filter(
                    WellStatus.status.in_(well_statuses),
                    WellStatus.dt_end.is_(None)
                )
                .distinct()
                .all()
            )
            well_filter = [str(w[0]) for w in wells_with_status if w[0]]

        results = []

        for reagent in reagents:
            reagent_name = reagent.name

            # 1) Поточний залишок
            balance_data = ReagentBalanceService.get_current_balance(db, reagent_name)
            stock = float(balance_data.get("current_balance", 0))

            # Пропускаємо реагенти з нульовим залишком (якщо не включено depleted)
            if stock <= 0 and not include_depleted:
                continue

            # 2) Розрахунок споживання за період (з урахуванням фільтрів)
            consumption_query = (
                db.query(
                    func.sum(Event.qty).label("total_qty"),
                    func.count(Event.id).label("event_count"),
                )
                .filter(
                    Event.reagent == reagent_name,
                    Event.event_time >= date_from,
                    Event.event_time <= date_to,
                    Event.event_type == "reagent",
                )
            )

            if well_filter:
                consumption_query = consumption_query.filter(Event.well.in_(well_filter))

            consumption_result = consumption_query.first()
            total_consumption = float(consumption_result.total_qty or 0)
            event_count = consumption_result.event_count or 0

            # avg_daily
            avg_daily = total_consumption / period_days if period_days > 0 else 0

            # 3) Прогноз
            days_remaining = None
            depletion_date = None
            order_by_date = None
            risk_status = "no_data"

            if avg_daily > 0:
                days_remaining = int(stock / avg_daily)
                depletion_dt = datetime.now() + timedelta(days=days_remaining)
                depletion_date = depletion_dt.strftime("%d.%m.%Y")

                order_by_dt = depletion_dt - timedelta(days=lead_days)
                if order_by_dt > datetime.now():
                    order_by_date = order_by_dt.strftime("%d.%m.%Y")
                else:
                    order_by_date = "Вже пора!"

                # Визначаємо статус ризику
                if days_remaining <= 7:
                    risk_status = "critical"
                elif days_remaining <= 30:
                    risk_status = "warning"
                else:
                    risk_status = "normal"
            else:
                risk_status = "no_data"

            # 4) ТОП споживачів (скважини)
            top_consumers_query = (
                db.query(
                    Event.well,
                    func.sum(Event.qty).label("qty"),
                )
                .filter(
                    Event.reagent == reagent_name,
                    Event.event_time >= date_from,
                    Event.event_time <= date_to,
                    Event.event_type == "reagent",
                    Event.well.isnot(None),
                )
            )

            if well_filter:
                top_consumers_query = top_consumers_query.filter(Event.well.in_(well_filter))

            top_consumers_data = (
                top_consumers_query
                .group_by(Event.well)
                .order_by(func.sum(Event.qty).desc())
                .limit(10)
                .all()
            )

            top_consumers = []
            for tc in top_consumers_data:
                qty = float(tc.qty or 0)
                share = qty / total_consumption if total_consumption > 0 else 0
                top_consumers.append({
                    "well": tc.well,
                    "qty": round(qty, 2),
                    "share": round(share * 100, 1),
                })

            results.append({
                "reagent": reagent_name,
                "unit": reagent.default_unit or "шт",
                "stock": round(stock, 2),
                "avg_daily": round(avg_daily, 3),
                "total_consumption": round(total_consumption, 2),
                "event_count": event_count,
                "period_days": period_days,
                "days_remaining": days_remaining,
                "depletion_date": depletion_date,
                "order_by_date": order_by_date,
                "risk_status": risk_status,
                "has_consumption": avg_daily > 0,
                "top_consumers": top_consumers,
                "lead_days": lead_days,
            })

        # Сортуємо по днях до вичерпання (критичні зверху)
        results.sort(
            key=lambda x: x["days_remaining"] if x["days_remaining"] is not None else 9999
        )

        return results

    # =========================================================================
    # ПЛАН ЗАКУПКИ
    # =========================================================================

    @staticmethod
    def calculate_procurement_plan(
        db: Session,
        target_days: int = 60,
        lead_days: int = 15,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        reagent_names: Optional[List[str]] = None,
        well_numbers: Optional[List[str]] = None,
        well_statuses: Optional[List[str]] = None,
        include_depleted: bool = False,
    ) -> Dict:
        """
        Розраховує план закупки реагентів.

        Параметри:
        - target_days: на скільки днів забезпечити (горизонт планування)
        - lead_days: за скільки днів до вичерпання рекомендувати замовлення
        - date_from, date_to: період для розрахунку avg_daily
        - reagent_names, well_numbers, well_statuses: фільтри

        Формула:
        - needed_total = avg_daily * target_days
        - to_order = max(0, needed_total - stock_now)

        Returns: {
            "params": {target_days, lead_days, period_days, ...},
            "items": [
                {
                    "reagent": "SMOD",
                    "unit": "шт",
                    "stock": 150.0,
                    "avg_daily": 5.0,
                    "needed_total": 300.0,
                    "to_order": 150.0,
                    "order_by_date": "2025-02-09",
                    "priority": "high"  # high/medium/low
                },
                ...
            ],
            "summary": {
                "total_items": 5,
                "items_to_order": 3,
                "critical_count": 1,
                "warning_count": 2
            }
        }
        """
        # Отримуємо прогнози з фільтрами
        forecasts = ReagentBalanceService.get_reagent_forecasts_with_filters(
            db=db,
            date_from=date_from,
            date_to=date_to,
            reagent_names=reagent_names,
            well_numbers=well_numbers,
            well_statuses=well_statuses,
            lead_days=lead_days,
            include_depleted=include_depleted,
        )

        # Визначаємо період
        if date_to is None:
            date_to = datetime.now()
        if date_from is None:
            date_from = date_to - timedelta(days=30)
        period_days = max(1, (date_to - date_from).days)

        items = []
        critical_count = 0
        warning_count = 0

        for f in forecasts:
            avg_daily = f["avg_daily"]
            stock = f["stock"]

            # Скільки потрібно на target_days
            needed_total = avg_daily * target_days
            to_order = max(0, needed_total - stock)

            # Пріоритет
            priority = "low"
            if f["risk_status"] == "critical":
                priority = "high"
                critical_count += 1
            elif f["risk_status"] == "warning":
                priority = "medium"
                warning_count += 1

            items.append({
                "reagent": f["reagent"],
                "unit": f["unit"],
                "stock": f["stock"],
                "avg_daily": f["avg_daily"],
                "days_remaining": f["days_remaining"],
                "needed_total": round(needed_total, 2),
                "to_order": round(to_order, 2),
                "order_by_date": f["order_by_date"],
                "depletion_date": f["depletion_date"],
                "priority": priority,
                "risk_status": f["risk_status"],
            })

        # Сортуємо: спочатку ті, що потрібно замовити, потім по пріоритету
        priority_order = {"high": 0, "medium": 1, "low": 2}
        items.sort(key=lambda x: (
            0 if x["to_order"] > 0 else 1,
            priority_order.get(x["priority"], 3),
            x["days_remaining"] if x["days_remaining"] is not None else 9999
        ))

        items_to_order = sum(1 for i in items if i["to_order"] > 0)

        return {
            "params": {
                "target_days": target_days,
                "lead_days": lead_days,
                "period_days": period_days,
                "date_from": date_from.strftime("%Y-%m-%d") if date_from else None,
                "date_to": date_to.strftime("%Y-%m-%d") if date_to else None,
                "calculated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            },
            "items": items,
            "summary": {
                "total_items": len(items),
                "items_to_order": items_to_order,
                "critical_count": critical_count,
                "warning_count": warning_count,
            }
        }

    # =========================================================================
    # ПРОГНОЗ ПО СКВАЖИНАХ: Группировка по скважинам
    # =========================================================================

    @staticmethod
    def get_forecast_by_wells(
        db: Session,
        avg_days: int = 30,
    ) -> List[Dict]:
        """
        Прогноз вичерпання реагентів, згрупований по скважинах.

        Для кожної скважини:
        - Беремо всі реагенти, які використовувались на ній за період
        - Рахуємо середній розхід по цій скважині
        - Залишок — загальний складський
        - Прогноз = залишок / розхід_на_скважині

        Returns: [
            {
                "well": "1367",
                "reagents": [
                    {
                        "reagent": "SMOD",
                        "stock": 150.0,
                        "avg_daily": 2.5,
                        "days_remaining": 60,
                        "forecast_date": "2025-03-25",
                        "total_consumed": 75.0,
                        "event_count": 12
                    },
                    ...
                ],
                "total_consumed": 200.0,
                "reagent_count": 5
            },
            ...
        ]
        """
        from_date = datetime.utcnow() - timedelta(days=avg_days)
        to_date = datetime.utcnow()

        # 1) Отримуємо споживання по скважинах і реагентах
        consumption_data = (
            db.query(
                Event.well,
                Event.reagent,
                func.sum(Event.qty).label("total_qty"),
                func.count(Event.id).label("event_count"),
            )
            .filter(
                Event.event_time >= from_date,
                Event.event_time <= to_date,
                Event.event_type == "reagent",
                Event.reagent.isnot(None),
                Event.well.isnot(None),
                Event.qty > 0,
            )
            .group_by(Event.well, Event.reagent)
            .all()
        )

        if not consumption_data:
            return []

        # 2) Кешуємо залишки реагентів (загальні складські)
        reagent_stocks = {}

        def get_stock(reagent_name: str) -> float:
            if reagent_name not in reagent_stocks:
                balance = ReagentBalanceService.get_current_balance(db, reagent_name)
                reagent_stocks[reagent_name] = float(balance.get("current_balance", 0))
            return reagent_stocks[reagent_name]

        # 3) Групуємо по скважинах
        wells_map: Dict[str, Dict] = {}

        for row in consumption_data:
            well = row.well
            reagent = row.reagent
            total_qty = float(row.total_qty or 0)
            event_count = row.event_count or 0

            if well not in wells_map:
                wells_map[well] = {
                    "well": well,
                    "reagents": [],
                    "total_consumed": 0.0,
                }

            # Середній розхід на день по цій скважині
            avg_daily = total_qty / max(1, avg_days)

            # Залишок (загальний складський)
            stock = get_stock(reagent)

            # Прогноз
            days_remaining = None
            forecast_date = None

            if avg_daily > 0 and stock > 0:
                days_remaining = int(stock / avg_daily)
                forecast_dt = datetime.now() + timedelta(days=days_remaining)
                forecast_date = forecast_dt.strftime("%Y-%m-%d")

            wells_map[well]["reagents"].append({
                "reagent": reagent,
                "stock": stock,
                "avg_daily": round(avg_daily, 3),
                "days_remaining": days_remaining,
                "forecast_date": forecast_date,
                "total_consumed": round(total_qty, 3),
                "event_count": event_count,
                "has_data": avg_daily > 0,
            })
            wells_map[well]["total_consumed"] += total_qty

        # 4) Формуємо результат
        result = []
        for well, data in wells_map.items():
            # Фільтруємо реагенти з нульовим залишком
            data["reagents"] = [r for r in data["reagents"] if r["stock"] > 0]

            if not data["reagents"]:
                continue

            # Сортуємо реагенти по днях до вичерпання (критичні зверху)
            data["reagents"].sort(
                key=lambda x: x["days_remaining"] if x["days_remaining"] is not None else 9999
            )
            data["reagent_count"] = len(data["reagents"])
            data["total_consumed"] = round(data["total_consumed"], 2)
            result.append(data)

        # Сортуємо скважини по загальному споживанню (найбільше зверху)
        result.sort(key=lambda x: x["total_consumed"], reverse=True)

        return result

    # =========================================================================
    # АНАЛИТИКА: Уникальные реагенты и скважины
    # =========================================================================

    @staticmethod
    def get_unique_reagents_from_events(
        db: Session,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> List[str]:
        """Получить уникальные названия реагентов из событий."""
        query = (
            db.query(Event.reagent)
            .filter(Event.reagent.isnot(None))
            .filter(Event.event_type == "reagent")
        )

        if date_from:
            query = query.filter(Event.event_time >= date_from)
        if date_to:
            query = query.filter(Event.event_time <= date_to)

        rows = query.distinct().all()
        return [r[0] for r in rows if r[0]]

    @staticmethod
    def get_unique_wells_from_events(
        db: Session,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> List[str]:
        """Получить уникальные номера скважин из событий."""
        query = (
            db.query(Event.well)
            .filter(Event.well.isnot(None))
            .filter(Event.event_type == "reagent")
        )

        if date_from:
            query = query.filter(Event.event_time >= date_from)
        if date_to:
            query = query.filter(Event.event_time <= date_to)

        rows = query.distinct().order_by(Event.well).all()
        return [r[0] for r in rows if r[0]]