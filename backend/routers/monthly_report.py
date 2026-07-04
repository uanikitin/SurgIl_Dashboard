"""
Роутер ежемесячных отчётов.

Страница /monthly-report — wizard для формирования отчётов по месяцам.
API /api/monthly-report/* — CRUD и расчёты.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db import SessionLocal
from backend.deps import get_current_user
from backend.services import monthly_report_service as svc

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/monthly-report", tags=["monthly-report"])
pages_router = APIRouter(tags=["monthly-report-pages"])

templates = Jinja2Templates(directory="backend/templates")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _json_safe(obj):
    """Преобразовать Python-значения в JSON-совместимые."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(x) for x in obj]
    if isinstance(obj, datetime):
        return obj.isoformat(timespec="minutes")
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, float):
        import math
        if math.isnan(obj) or math.isinf(obj):
            return None
        return round(obj, 4)
    return obj


# ═══════════════════════════════════════════════════════════════════
#  CRUD endpoints
# ═══════════════════════════════════════════════════════════════════

@router.get("/list")
def api_list_reports(
    well_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Список отчётов скважины."""
    return {"well_id": well_id, "reports": svc.list_reports(db, well_id)}


@router.get("/{report_id}")
def api_get_report(report_id: int, db: Session = Depends(get_db)):
    """Получить отчёт по ID."""
    report = svc.get_report(db, report_id)
    if not report:
        raise HTTPException(404, "Отчёт не найден")
    return report


class CreateReportRequest(BaseModel):
    well_id: int
    period_from: date
    period_to: date
    period_label: str | None = None
    title: str | None = None
    data_snapshot: dict | None = None


@router.post("/create")
def api_create_report(
    req: CreateReportRequest,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Создать новый отчёт."""
    try:
        report = svc.create_report(
            db,
            well_id=req.well_id,
            period_from=req.period_from,
            period_to=req.period_to,
            period_label=req.period_label,
            title=req.title,
            created_by=current_user,
            data_snapshot=req.data_snapshot,
        )
        return {"ok": True, "report": report}
    except Exception as e:
        log.exception("create_report failed")
        raise HTTPException(400, str(e))


class UpdateReportRequest(BaseModel):
    data_snapshot: dict | None = None
    title: str | None = None
    status: str | None = None


@router.put("/{report_id}")
def api_update_report(
    report_id: int,
    req: UpdateReportRequest,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Обновить отчёт."""
    report = svc.update_report(
        db, report_id,
        data_snapshot=req.data_snapshot,
        title=req.title,
        status=req.status,
    )
    if not report:
        raise HTTPException(404, "Отчёт не найден")
    return {"ok": True, "report": report}


@router.delete("/{report_id}")
def api_delete_report(
    report_id: int,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Удалить отчёт."""
    if not svc.delete_report(db, report_id):
        raise HTTPException(404, "Отчёт не найден")
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════
#  Расчёты (переиспользуют существующие сервисы)
# ═══════════════════════════════════════════════════════════════════

@router.get("/calendar-months")
def api_calendar_months(
    well_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Список календарных месяцев с данными."""
    months = svc.get_calendar_months(db, well_id)
    return {"well_id": well_id, "months": months}


class ComputeRequest(BaseModel):
    well_id: int
    period_from: date
    period_to: date


@router.post("/compute")
def api_compute(req: ComputeRequest, db: Session = Depends(get_db)):
    """Общий анализ периода."""
    data = svc.compute_general_analysis(
        db, req.well_id, req.period_from, req.period_to
    )
    return _json_safe(data)


@router.post("/compute-irv")
def api_compute_irv(req: ComputeRequest, db: Session = Depends(get_db)):
    """Сводка по ИРВ за период."""
    data = svc.compute_irv_summary(
        db, req.well_id, req.period_from, req.period_to
    )
    return _json_safe(data)


@router.post("/compare")
def api_compare(req: ComputeRequest, db: Session = Depends(get_db)):
    """Сравнение с прошлым месяцем и baseline."""
    data = svc.compute_comparison(
        db, req.well_id, req.period_from, req.period_to
    )
    return _json_safe(data)


@router.post("/segments")
def api_segments(req: ComputeRequest, db: Session = Depends(get_db)):
    """Сегментный анализ периода."""
    data = svc.compute_segments(
        db, req.well_id, req.period_from, req.period_to
    )
    return _json_safe(data)


# ═══════════════════════════════════════════════════════════════════
#  PDF (переиспользует механизм из adaptation_report)
# ═══════════════════════════════════════════════════════════════════

@router.post("/{report_id}/preview-pdf")
def api_preview_pdf(
    report_id: int,
    db: Session = Depends(get_db),
):
    """PDF-превью отчёта."""
    from backend.services.daily_report_service import (
        _ensure_dirs, _get_latex_env, _compile_latex, _tex_escape,
    )
    from fastapi.responses import FileResponse

    report = svc.get_report(db, report_id)
    if not report:
        raise HTTPException(404, "Отчёт не найден")

    _ensure_dirs()
    snapshot = report.get("data_snapshot", {})

    context = {
        "report": report,
        "general_analysis": snapshot.get("general_analysis", {}),
        "irv_summary": snapshot.get("irv_summary", {}),
        "comparison": snapshot.get("comparison", {}),
        "segments": snapshot.get("segments", []),
    }

    env = _get_latex_env()
    template = env.get_template("monthly_report.tex")
    latex_source = template.render(**context)

    base_name = f"monthly_report_{report_id}"
    pdf_path = _compile_latex(latex_source, base_name)

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"monthly_report_{report_id}.pdf",
    )


# ═══════════════════════════════════════════════════════════════════
#  HTML страница
# ═══════════════════════════════════════════════════════════════════

@pages_router.get("/monthly-report", response_class=HTMLResponse)
def monthly_report_page(
    request: Request,
    current_user: str = Depends(get_current_user),
):
    """Страница ежемесячных отчётов."""
    return templates.TemplateResponse(
        "monthly_report_wizard.html",
        {
            "request": request,
            "current_user": current_user,
            "is_admin": request.session.get("is_admin", False),
        },
    )
