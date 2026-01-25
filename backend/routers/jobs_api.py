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
from backend.documents.models_notifications import JobExecutionLog, DocumentSendLog
from backend.documents.models import Document
from backend.documents.services.notification_service import (
    send_document_telegram,
    send_document_email,
    get_send_history,
)

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


# =============================================================================
# POST /api/jobs/send/telegram/{document_id}
# =============================================================================

@router.post("/send/telegram/{document_id}")
def api_send_telegram(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    chat_id: Optional[str] = Query(None, description="ID чата Telegram (опционально)"),
):
    """
    Отправить документ в Telegram.
    Требует авторизации пользователя.
    """
    # Проверяем авторизацию (только ручной запуск)
    if "user_id" not in request.session:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_id = request.session["user_id"]

    # Получаем документ
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Отправляем
    result = send_document_telegram(
        db,
        document,
        chat_id=chat_id,
        triggered_by="manual",
        triggered_by_user_id=user_id,
    )

    if result.success:
        return {
            "status": "sent",
            "channel": "telegram",
            "recipient": result.recipient,
            "response": result.response_data,
        }
    else:
        return JSONResponse(
            status_code=400,
            content={
                "status": "failed",
                "channel": "telegram",
                "recipient": result.recipient,
                "error": result.error,
            }
        )


# =============================================================================
# POST /api/jobs/send/email/{document_id}
# =============================================================================

@router.post("/send/email/{document_id}")
def api_send_email(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    to_email: str = Query(..., description="Email получателя"),
    cc_email: Optional[str] = Query(None, description="Email для копии"),
):
    """
    Отправить документ на Email.
    Требует авторизации пользователя.
    """
    # Проверяем авторизацию
    if "user_id" not in request.session:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_id = request.session["user_id"]

    # Получаем документ
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Отправляем
    result = send_document_email(
        db,
        document,
        to_email=to_email,
        cc_email=cc_email,
        triggered_by="manual",
        triggered_by_user_id=user_id,
    )

    if result.success:
        return {
            "status": "sent",
            "channel": "email",
            "recipient": result.recipient,
            "response": result.response_data,
        }
    else:
        return JSONResponse(
            status_code=400,
            content={
                "status": "failed",
                "channel": "email",
                "recipient": result.recipient,
                "error": result.error,
            }
        )


# =============================================================================
# GET /api/jobs/send/history/{document_id}
# =============================================================================

@router.get("/send/history/{document_id}")
def api_send_history(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    channel: Optional[str] = Query(None, description="Фильтр по каналу (telegram/email)"),
):
    """
    История отправок документа.
    """
    # Проверяем авторизацию
    if "user_id" not in request.session:
        raise HTTPException(status_code=401, detail="Unauthorized")

    logs = get_send_history(db, document_id, channel=channel)

    return {
        "document_id": document_id,
        "items": [
            {
                "id": log.id,
                "channel": log.channel,
                "recipient": log.recipient,
                "status": log.status,
                "sent_at": log.sent_at.isoformat() if log.sent_at else None,
                "error_message": log.error_message,
                "triggered_by": log.triggered_by,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ]
    }
