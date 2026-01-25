from __future__ import annotations

from datetime import datetime, date, time
from calendar import monthrange
from typing import Iterable

import sqlalchemy as sa
from sqlalchemy.orm import Session

from backend.documents.models import Document, DocumentItem
from backend.models.wells import Well

# Если у тебя события реагентов лежат в таблице events:
from backend.models.events import Event  # <-- поправь импорт под свой проект

# Если у тебя есть таблица статусов скважины по интервалам (например well_status):
# from backend.models.well_status import WellStatus  # <-- поправь импорт, если есть


def _dt_range_for_month(year: int, month: int) -> tuple[datetime, datetime]:
    last_day = monthrange(year, month)[1]
    dt_from = datetime.combine(date(year, month, 1), time.min)
    dt_to = datetime.combine(date(year, month, last_day), time.max)
    return dt_from, dt_to


def refill_reagent_expense_items(
    db: Session,
    doc_id: int,
    *,
    allowed_statuses: Iterable[str] | None = None,
    source: str = "events",  # на будущее можно сделать "reagent_supplies"
) -> int:
    """
    Пересобирает строки акта расхода реагентов:
    - удаляет старые document_items
    - выбирает события за период
    - учитывает статусы (если используешь интервалы статусов)
    - вставляет строки по порядку времени
    Возвращает число добавленных строк.
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise ValueError("Document not found")

    if not doc.well_id:
        raise ValueError("Document has no well_id")

    if not doc.period_year or not doc.period_month:
        raise ValueError("Document has no period")

    year = int(doc.period_year)
    month = int(doc.period_month)
    dt_from, dt_to = _dt_range_for_month(year, month)

    # 1) чистим старые строки
    db.query(DocumentItem).filter(DocumentItem.document_id == doc_id).delete()
    db.flush()

    # 2) выбираем события реагентов
    # Предположение: в Event есть поля:
    # - well_id (или well как строка) -> поправим при необходимости
    # - event_time
    # - reagent (название)
    # - qty (кол-во)
    # - stage (этап)
    q = db.query(Event).filter(
        Event.event_time >= dt_from,
        Event.event_time <= dt_to,
    )

    # Привязка к скважине: предпочитаем well_id, иначе по строковому well
    if hasattr(Event, "well_id"):
        q = q.filter(Event.well_id == doc.well_id)
    else:
        # fallback: сравним по номеру скважины
        w = db.query(Well).filter(Well.id == doc.well_id).first()
        if not w:
            raise ValueError("Well not found for document")
        q = q.filter(sa.cast(Event.well, sa.String) == sa.cast(w.number, sa.String))

    # События реагента: минимальный фильтр
    if hasattr(Event, "reagent"):
        q = q.filter(Event.reagent.isnot(None)).filter(sa.func.length(sa.cast(Event.reagent, sa.String)) > 0)
    if hasattr(Event, "qty"):
        q = q.filter(Event.qty.isnot(None)).filter(Event.qty > 0)

    # 3) Учет статусов (если есть интервалы статусов)
    # Тут 2 варианта:
    # A) статусы хранятся в самом событии (Event.stage / Event.geo_status)
    # B) статусы — отдельная таблица интервалов well_status и нужно пересечение по времени
    #
    # Я включаю вариант А как рабочий "сразу".
    if allowed_statuses:
        # Пробуем stage/geo_status
        if hasattr(Event, "stage"):
            q = q.filter(Event.stage.in_(list(allowed_statuses)))
        elif hasattr(Event, "geo_status"):
            q = q.filter(Event.geo_status.in_(list(allowed_statuses)))

    # Вариант B (если у тебя есть интервалы статуса WellStatus):
    # if allowed_statuses:
    #     q = q.filter(
    #         db.query(WellStatus.id)
    #         .filter(WellStatus.well_id == doc.well_id)
    #         .filter(WellStatus.status.in_(list(allowed_statuses)))
    #         .filter(WellStatus.started_at <= Event.event_time)
    #         .filter(sa.or_(WellStatus.ended_at.is_(None), WellStatus.ended_at >= Event.event_time))
    #         .exists()
    #     )

    events = q.order_by(Event.event_time.asc()).all()

    # 4) пишем document_items
    line = 1
    for e in events:
        ev_dt = getattr(e, "event_time", None)

        item = DocumentItem(
            document_id=doc_id,
            line_number=line,
            work_type="Дозирование реагента",  # можно уточнить позже
            event_time=ev_dt,
            event_time_str=ev_dt.strftime("%d.%m.%Y %H:%M") if ev_dt else None,
            quantity=int(getattr(e, "qty", 1) or 1),
            reagent_name=(getattr(e, "reagent", None) or None),
            stage=(getattr(e, "stage", None) or getattr(e, "geo_status", None) or None),
            event_id=getattr(e, "id", None),
            notes=None,
        )
        db.add(item)
        line += 1

    db.flush()
    return len(events)


def get_wells_with_events(db: Session, year: int, month: int) -> list[int]:
    """
    Возвращает список well_id скважин, у которых есть реагентные события за указанный месяц.
    Реагентное событие = запись в events с reagent IS NOT NULL и qty > 0.
    """
    dt_from, dt_to = _dt_range_for_month(year, month)

    # Получаем список номеров скважин (строковые) с реагентными событиями
    well_numbers_rows = (
        db.query(Event.well)
        .filter(Event.event_time >= dt_from)
        .filter(Event.event_time <= dt_to)
        .filter(Event.reagent.isnot(None))
        .filter(sa.func.length(sa.cast(Event.reagent, sa.String)) > 0)
        .distinct()
        .all()
    )

    well_numbers_set = {str(r[0]).strip() for r in well_numbers_rows if r and r[0]}

    if not well_numbers_set:
        return []

    # Конвертируем номера скважин в well_id
    wells = (
        db.query(Well.id)
        .filter(sa.cast(Well.number, sa.String).in_(well_numbers_set))
        .all()
    )

    return [w[0] for w in wells]