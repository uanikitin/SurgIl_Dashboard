# backend/services/well_sync_service.py
"""
Сервис синхронизации скважин из таблицы events в таблицу wells.
Автоматически создаёт записи в wells для новых скважин из Telegram-бота.
"""
import logging
from typing import List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct

from backend.models.wells import Well
from backend.models.events import Event

logger = logging.getLogger(__name__)


def get_unique_wells_from_events(db: Session) -> List[str]:
    """
    Получить все уникальные номера скважин из таблицы events.
    """
    rows = (
        db.query(distinct(Event.well))
        .filter(Event.well.isnot(None))
        .filter(Event.well != "")
        .order_by(Event.well)
        .all()
    )
    result = [r[0] for r in rows if r[0]]
    logger.info(f"[well_sync] Уникальные скважины из events: {len(result)} шт")
    return result


def get_first_coordinates_for_well(db: Session, well_number: str) -> Tuple[float, float] | None:
    """
    Получить первые (самые ранние) координаты для скважины из events.
    Возвращает (lat, lon) или None если координат нет.
    """
    event = (
        db.query(Event.lat, Event.lon)
        .filter(Event.well == well_number)
        .filter(Event.lat.isnot(None))
        .filter(Event.lon.isnot(None))
        .order_by(Event.event_time.asc())
        .first()
    )
    if event and event.lat and event.lon:
        return (event.lat, event.lon)
    return None


def sync_wells_from_events(db: Session) -> List[Well]:
    """
    Синхронизировать скважины: создать записи в wells для всех
    уникальных скважин из events, которых ещё нет в wells.

    Для новых скважин также подтягиваются первые координаты из events.

    Возвращает список созданных скважин.
    """
    # 1. Получить все уникальные номера скважин из events
    event_wells = get_unique_wells_from_events(db)

    if not event_wells:
        print("[well_sync] Нет скважин в events")
        return []

    print(f"[well_sync] Скважины из events: {event_wells}")

    # 2. Получить существующие скважины из wells
    existing_wells = (
        db.query(Well.number, Well.name)
        .all()
    )

    # Создаём множества для быстрого поиска
    existing_numbers = {str(w.number) for w in existing_wells if w.number}
    existing_names = {w.name for w in existing_wells if w.name}

    print(f"[well_sync] Существующие numbers: {existing_numbers}")
    print(f"[well_sync] Существующие names: {existing_names}")

    # 3. Найти скважины которых нет в wells
    created_wells = []

    for well_num in event_wells:
        well_num_str = str(well_num).strip()

        # Проверяем по number и по name (формат "Скв XXXX")
        name_variant = f"Скв {well_num_str}"

        if well_num_str in existing_numbers:
            print(f"[well_sync] {well_num_str} уже есть в numbers")
            continue
        if name_variant in existing_names:
            print(f"[well_sync] {name_variant} уже есть в names")
            continue

        # Также проверяем если number это число
        try:
            num_int = int(well_num_str)
            if str(num_int) in existing_numbers:
                print(f"[well_sync] {num_int} уже есть в numbers (int)")
                continue
        except ValueError:
            num_int = None

        print(f"[well_sync] Создаём новую скважину: {name_variant}")

        # 4. Создать новую скважину
        coords = get_first_coordinates_for_well(db, well_num_str)
        print(f"[well_sync] Координаты для {well_num_str}: {coords}")

        new_well = Well(
            number=num_int,
            name=name_variant,
            lat=coords[0] if coords else None,
            lon=coords[1] if coords else None,
        )

        db.add(new_well)
        created_wells.append(new_well)

    if created_wells:
        db.commit()
        # Refresh чтобы получить id
        for w in created_wells:
            db.refresh(w)
        print(f"[well_sync] Создано {len(created_wells)} новых скважин")

    return created_wells


def update_well_coordinates_from_events(db: Session, well: Well) -> bool:
    """
    Обновить координаты скважины из events, если они отсутствуют.
    Возвращает True если координаты были обновлены.
    """
    if well.lat is not None and well.lon is not None:
        return False

    # Ищем по number
    well_key = str(well.number) if well.number else None

    if not well_key:
        return False

    coords = get_first_coordinates_for_well(db, well_key)

    if coords:
        well.lat = coords[0]
        well.lon = coords[1]
        db.commit()
        return True

    return False


def fix_wells_missing_data(db: Session) -> int:
    """
    Исправить скважины у которых отсутствуют name или координаты.
    Заполняет name в формате "Скв XXXX" и подтягивает координаты из events.
    Возвращает количество исправленных скважин.
    """
    fixed_count = 0

    # Найти все скважины
    all_wells = db.query(Well).all()

    for well in all_wells:
        changed = False

        # Исправить name если отсутствует
        if (not well.name or well.name.strip() == "") and well.number:
            well.name = f"Скв {well.number}"
            changed = True
            print(f"[fix_wells] Исправлено имя для скважины {well.number}: {well.name}")

        # Исправить координаты если отсутствуют
        if (well.lat is None or well.lon is None) and well.number:
            coords = get_first_coordinates_for_well(db, str(well.number))
            if coords:
                well.lat = coords[0]
                well.lon = coords[1]
                changed = True
                print(f"[fix_wells] Добавлены координаты для скважины {well.number}: {coords}")

        if changed:
            fixed_count += 1

    if fixed_count > 0:
        db.commit()
        print(f"[fix_wells] Всего исправлено {fixed_count} скважин")

    return fixed_count
