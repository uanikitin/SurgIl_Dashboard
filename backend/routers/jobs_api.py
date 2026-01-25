# backend/routers/jobs_api.py
"""
API для фоновых задач (автосоздание актов, и т.д.)

Эндпоинты:
- POST /api/jobs/reagent-expense/auto-create  — запуск автосоздания
- GET  /api/jobs/logs                         — список выполненных задач
- GET  /api/jobs/logs/{id}                    — детали конкретной задачи

Защита:
- Для cron-задач: заголовок X-Job-Secret
- Для UI: авторизация пользователя
"""

from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import date
from typing import Optional

from backend.db import get_db
from backend.settings import settings
from backend.documents.services.auto_create import (
    auto_create_reagent_expense_acts,
    run_monthly_auto_create,
    get_previous_month,
)
from backend.documents.models_notifications import JobExecutionLog

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def verify_job_secret(x_job_secret: Optional[str] = Header(None)) -> bool:
    """Проверяет секретный ключ для cron-задач"""
    if not x_job_secret:
        return False
    return x_job_secret == settings.JOB_API_SECRET


def get_auth_context(
    request: Request,
    x_job_secret: Optional[str] = None,
):
    """
    Возвращает контекст авторизации:
    - triggered_by: 'cron' | 'manual' | 'api'
    - user_id: ID пользователя (если manual)
    """
    # Проверяем cron-секрет
    if x_job_secret and x_job_secret == settings.JOB_API_SECRET:
        return {"triggered_by": "cron", "user_id": None}

    # Проверяем сессию пользователя (синхронно)
    if "user_id" in request.session:
        return {
            "triggered_by": "manual",
            "user_id": request.session["user_id"],
        }

    # Если ничего не подошло — ошибка
    raise HTTPException(
        status_code=401,
        detail="Unauthorized: provide X-Job-Secret header or login"
    )


# =============================================================================
# POST /api/jobs/reagent-expense/auto-create
# =============================================================================

@router.post("/reagent-expense/auto-create")
def api_reagent_expense_auto_create(
    request: Request,
    db: Session = Depends(get_db),
    x_job_secret: Optional[str] = Header(None),
    # Параметры (опционально)
    year: Optional[int] = Query(None, description="Год периода (по умолчанию — предыдущий месяц)"),
    month: Optional[int] = Query(None, description="Месяц периода (1-12)"),
    well_ids: Optional[str] = Query(None, description="ID скважин через запятую (по умолчанию — все с событиями)"),
):
    """
    Запускает автосоздание актов расхода реагентов.

    Защита:
    - X-Job-Secret header (для cron)
    - или авторизованный пользователь

    Если year/month не указаны — создаёт за предыдущий месяц.
    """
    auth = get_auth_context(request, x_job_secret)

    # Определяем период
    if year is None or month is None:
        year, month = get_previous_month()

    # Парсим well_ids
    parsed_well_ids = None
    if well_ids:
        try:
            parsed_well_ids = [int(x.strip()) for x in well_ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid well_ids format")

    # Запускаем автосоздание
    result = auto_create_reagent_expense_acts(
        db,
        year=year,
        month=month,
        well_ids=parsed_well_ids,
        triggered_by=auth["triggered_by"],
        triggered_by_user_id=auth["user_id"],
        create_job_log=True,
    )

    return JSONResponse(
        status_code=200 if result.status in ("success", "skipped") else 207,
        content={
            "status": result.status,
            "period": f"{month:02d}.{year}",
            "summary": {
                "total_wells": result.total_wells,
                "created": result.created,
                "skipped_duplicate": result.skipped_duplicate,
                "skipped_no_events": result.skipped_no_events,
                "errors": result.errors,
            },
            "details": result.details,
        }
    )


@router.post("/reagent-expense/auto-create-previous-month")
def api_reagent_expense_auto_create_previous_month(
    request: Request,
    db: Session = Depends(get_db),
    x_job_secret: Optional[str] = Header(None),
):
    """
    Запускает автосоздание за предыдущий месяц.
    Предназначен для вызова из Render Cron Job 1-го числа каждого месяца.
    """
    auth = get_auth_context(request, x_job_secret)

    result = run_monthly_auto_create(
        db,
        triggered_by=auth["triggered_by"],
    )

    return JSONResponse(
        status_code=200 if result.status in ("success", "skipped") else 207,
        content={
            "status": result.status,
            "summary": result.to_dict(),
        }
    )


# =============================================================================
# GET /api/jobs/logs
# =============================================================================

@router.get("/logs")
def api_job_logs(
    request: Request,
    db: Session = Depends(get_db),
    x_job_secret: Optional[str] = Header(None),
    job_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """
    Список выполненных задач.
    """
    auth = get_auth_context(request, x_job_secret)

    query = db.query(JobExecutionLog)

    if job_type:
        query = query.filter(JobExecutionLog.job_type == job_type)
    if status:
        query = query.filter(JobExecutionLog.status == status)

    total = query.count()
    logs = query.order_by(desc(JobExecutionLog.started_at)).offset(offset).limit(limit).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": log.id,
                "job_type": log.job_type,
                "status": log.status,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "finished_at": log.finished_at.isoformat() if log.finished_at else None,
                "triggered_by": log.triggered_by,
                "result_summary": log.result_summary,
            }
            for log in logs
        ]
    }


@router.get("/logs/{log_id}")
def api_job_log_detail(
    log_id: int,
    request: Request,
    db: Session = Depends(get_db),
    x_job_secret: Optional[str] = Header(None),
):
    """
    Детали конкретной задачи.
    """
    auth = get_auth_context(request, x_job_secret)

    log = db.query(JobExecutionLog).filter(JobExecutionLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Job log not found")

    return {
        "id": log.id,
        "job_type": log.job_type,
        "params": log.params,
        "status": log.status,
        "started_at": log.started_at.isoformat() if log.started_at else None,
        "finished_at": log.finished_at.isoformat() if log.finished_at else None,
        "triggered_by": log.triggered_by,
        "triggered_by_user_id": log.triggered_by_user_id,
        "result_summary": log.result_summary,
        "error_message": log.error_message,
    }
