# backend/documents/services/auto_create.py
"""
Сервис автоматического создания актов расхода реагентов.
Используется для:
- Cron-задачи 1-го числа каждого месяца
- Ручного запуска через UI/API
"""

from __future__ import annotations

from datetime import datetime, date
from calendar import monthrange
from typing import Optional
from dataclasses import dataclass, field

from sqlalchemy.orm import Session
from sqlalchemy import and_

from backend.documents.models import Document, DocumentType
from backend.documents.models_notifications import JobExecutionLog
from backend.models.wells import Well
from backend.documents.services.reagent_expense import get_wells_with_events, refill_reagent_expense_items
from backend.documents.numbering import build_doc_number


@dataclass
class AutoCreateResult:
    """Результат автосоздания актов"""
    total_wells: int = 0
    created: int = 0
    skipped_no_events: int = 0
    skipped_duplicate: int = 0
    errors: int = 0
    details: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_wells": self.total_wells,
            "created": self.created,
            "skipped_no_events": self.skipped_no_events,
            "skipped_duplicate": self.skipped_duplicate,
            "errors": self.errors,
            "details": self.details,
        }

    @property
    def status(self) -> str:
        """Определяет общий статус выполнения"""
        if self.errors > 0 and self.created == 0:
            return "failed"
        elif self.errors > 0:
            return "partial"
        elif self.created == 0:
            return "skipped"
        return "success"


def get_previous_month(reference_date: date = None) -> tuple[int, int]:
    """Возвращает (year, month) предыдущего месяца"""
    if reference_date is None:
        reference_date = date.today()

    if reference_date.month == 1:
        return reference_date.year - 1, 12
    return reference_date.year, reference_date.month - 1


def check_duplicate_exists(
    db: Session,
    doc_type_id: int,
    well_id: int,
    year: int,
    month: int
) -> bool:
    """Проверяет, существует ли уже акт для данной скважины и периода"""
    existing = db.query(Document).filter(
        and_(
            Document.doc_type_id == doc_type_id,
            Document.well_id == well_id,
            Document.period_year == year,
            Document.period_month == month,
            Document.deleted_at.is_(None),
        )
    ).first()
    return existing is not None


def auto_create_reagent_expense_acts(
    db: Session,
    year: int,
    month: int,
    *,
    well_ids: list[int] | None = None,
    status_names: list[str] | None = None,
    act_date: date | None = None,
    triggered_by: str = "manual",
    triggered_by_user_id: int | None = None,
    create_job_log: bool = True,
) -> AutoCreateResult:
    """
    Автоматически создаёт акты расхода реагентов за указанный период.

    Аргументы:
        db: сессия БД
        year, month: период (год, месяц)
        well_ids: список ID скважин (если None — все скважины с событиями)
        status_names: фильтр по статусам (если None — все статусы)
        act_date: дата акта (если None — последний день месяца)
        triggered_by: источник запуска ('cron', 'manual', 'api')
        triggered_by_user_id: ID пользователя (для manual)
        create_job_log: создавать ли запись в job_execution_logs

    Возвращает:
        AutoCreateResult с детальной статистикой
    """
    result = AutoCreateResult()

    # Получаем тип документа
    doc_type = db.query(DocumentType).filter(DocumentType.code == "reagent_expense").first()
    if not doc_type:
        result.errors = 1
        result.details.append({"error": "DocumentType 'reagent_expense' not found"})
        return result

    # Дата акта: последний день месяца если не указана
    if act_date is None:
        last_day = monthrange(year, month)[1]
        act_date = date(year, month, last_day)

    # Определяем скважины для обработки
    if well_ids is None:
        # Получаем скважины с реагентными событиями за период
        well_ids = get_wells_with_events(db, year, month)

    result.total_wells = len(well_ids)

    if not well_ids:
        result.skipped_no_events = 0
        result.details.append({"info": f"No wells with reagent events for {month:02d}.{year}"})
        return result

    # Создаём запись в логе задач
    job_log = None
    if create_job_log:
        job_log = JobExecutionLog(
            job_type="reagent_expense_auto_create",
            params={
                "year": year,
                "month": month,
                "well_ids": well_ids,
                "status_names": status_names,
                "act_date": act_date.isoformat() if act_date else None,
            },
            started_at=datetime.now(),
            status="running",
            triggered_by=triggered_by,
            triggered_by_user_id=triggered_by_user_id,
        )
        db.add(job_log)
        db.flush()

    # Обрабатываем каждую скважину
    for well_id in well_ids:
        well = db.query(Well).filter(Well.id == well_id).first()
        if not well:
            result.errors += 1
            result.details.append({
                "well_id": well_id,
                "status": "error",
                "error": "Well not found"
            })
            continue

        # Проверка дубликата
        if check_duplicate_exists(db, doc_type.id, well_id, year, month):
            result.skipped_duplicate += 1
            result.details.append({
                "well_id": well_id,
                "well_number": well.number,
                "status": "skipped",
                "reason": "duplicate"
            })
            continue

        # Проверяем, есть ли события у этой скважины
        well_has_events = well_id in get_wells_with_events(db, year, month)
        if not well_has_events:
            result.skipped_no_events += 1
            result.details.append({
                "well_id": well_id,
                "well_number": well.number,
                "status": "skipped",
                "reason": "no_events"
            })
            continue

        try:
            # Создаём документ (номер сгенерируем после добавления в сессию)
            doc = Document(
                doc_type_id=doc_type.id,
                well_id=well_id,
                period_year=year,
                period_month=month,
                period_start=date(year, month, 1),
                period_end=act_date,
                status="draft",
                meta={
                    "act_date": act_date.isoformat(),
                    "status_names": status_names or [],
                    "auto_created": True,
                    "auto_created_at": datetime.now().isoformat(),
                    "triggered_by": triggered_by,
                },
            )
            # Привязываем well для build_doc_number
            doc.well = well
            db.add(doc)
            db.flush()

            # Генерируем номер документа (после flush, чтобы были ID)
            doc.doc_number = build_doc_number(db, doc, doc_type)
            db.flush()

            # Заполняем строки из событий
            items_count = refill_reagent_expense_items(
                db,
                doc.id,
                allowed_statuses=status_names if status_names else None,
            )

            result.created += 1
            result.details.append({
                "well_id": well_id,
                "well_number": well.number,
                "status": "created",
                "document_id": doc.id,
                "doc_number": doc.doc_number,
                "items_count": items_count,
            })

        except Exception as e:
            result.errors += 1
            result.details.append({
                "well_id": well_id,
                "well_number": well.number if well else None,
                "status": "error",
                "error": str(e)
            })

    # Обновляем лог задачи
    if job_log:
        job_log.finished_at = datetime.now()
        job_log.status = result.status
        job_log.result_summary = result.to_dict()
        if result.errors > 0:
            error_details = [d for d in result.details if d.get("status") == "error"]
            job_log.error_message = str(error_details) if error_details else None

    db.commit()
    return result


def run_monthly_auto_create(
    db: Session,
    triggered_by: str = "cron",
) -> AutoCreateResult:
    """
    Запускает автосоздание актов за предыдущий месяц.
    Вызывается из cron-задачи 1-го числа каждого месяца.
    """
    year, month = get_previous_month()

    return auto_create_reagent_expense_acts(
        db,
        year=year,
        month=month,
        well_ids=None,  # все скважины с событиями
        status_names=None,  # все статусы
        act_date=None,  # последний день месяца
        triggered_by=triggered_by,
        create_job_log=True,
    )
