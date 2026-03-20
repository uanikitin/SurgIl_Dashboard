"""
Суточный отчёт по скважинам — API + HTML page.

Два роутера:
  - router       (prefix=/api/daily-report) — JSON/redirect API
  - pages_router  (без prefix)              — HTML страница /daily-report

Подключается в app.py:
    app.include_router(daily_report_router)
    app.include_router(daily_report_pages)
"""
from __future__ import annotations

import logging
import time as time_module
from datetime import date, timedelta
from typing import List

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.deps import get_current_user
from backend.documents.models import Document, DocumentType
from backend.documents.numbering import build_doc_number
from backend.models.wells import Well
from backend.config.status_registry import STATUS_LIST

router = APIRouter(prefix="/api/daily-report", tags=["daily-report"])
pages_router = APIRouter(tags=["daily-report-pages"])
templates = Jinja2Templates(directory="backend/templates")
templates.env.globals["time"] = lambda: int(time_module.time())
log = logging.getLogger(__name__)

DEFAULT_STATUSES = {"Наблюдение", "Адаптация", "Оптимизация"}


# ──────────────────── HTML PAGE ────────────────────

@pages_router.get("/daily-report", response_class=HTMLResponse)
def daily_report_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """Страница управления суточными отчётами."""
    wells = db.query(Well).order_by(Well.number).all()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    today = date.today().isoformat()

    # Attach current_status to each well for the template
    status_color_map = {s["label"]: s["color"] for s in STATUS_LIST}
    for w in wells:
        row = db.execute(text("""
            SELECT status FROM well_status
            WHERE well_id = :wid
            ORDER BY dt_start DESC LIMIT 1
        """), {"wid": w.id}).fetchone()
        w.current_status = row[0] if row else None
        w.current_status_color = status_color_map.get(w.current_status, "#ccc")

    return templates.TemplateResponse(
        "daily_report.html",
        {
            "request": request,
            "current_user": current_user,
            "is_admin": request.session.get("is_admin", False),
            "wells": wells,
            "default_date": yesterday,
            "today": today,
            "statuses": STATUS_LIST,
            "default_statuses": DEFAULT_STATUSES,
        },
    )


# ──────────────────── API ────────────────────

@router.post("/create/single")
def create_single_report(
    well_id: int = Form(...),
    report_date: str = Form(...),
    downtime_threshold_min: int = Form(default=5),
    comparison_days: int = Form(default=7),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """Create daily report for a single well and generate PDF."""
    rd = date.fromisoformat(report_date)

    doc_type = db.query(DocumentType).filter(
        DocumentType.code == "daily_report_well"
    ).first()
    if not doc_type:
        raise HTTPException(400, "DocumentType daily_report_well not found. Run migration.")

    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        raise HTTPException(404, "Well not found")

    # Reuse existing (non-deleted) document for same well+date, or create new
    doc = db.query(Document).filter(
        Document.doc_type_id == doc_type.id,
        Document.well_id == well_id,
        Document.period_start == rd,
        Document.period_end == rd,
        Document.deleted_at.is_(None),
    ).first()

    if doc:
        doc.meta = {
            "report_date": rd.isoformat(),
            "well_ids": [well_id],
            "downtime_threshold_min": downtime_threshold_min,
            "comparison_days": comparison_days,
        }
        doc.status = "draft"
        doc.created_by_name = current_user
        db.commit()
        db.refresh(doc)
    else:
        doc = Document(
            doc_type_id=doc_type.id,
            well_id=well_id,
            period_start=rd,
            period_end=rd,
            period_month=rd.month,
            period_year=rd.year,
            created_by_name=current_user,
            status="draft",
            meta={
                "report_date": rd.isoformat(),
                "well_ids": [well_id],
                "downtime_threshold_min": downtime_threshold_min,
                "comparison_days": comparison_days,
            },
        )
        db.add(doc)
        db.flush()
        doc.doc_number = build_doc_number(db, doc, doc_type)
        db.commit()
        db.refresh(doc)

    # Generate PDF
    try:
        from backend.services.daily_report_service import generate_daily_report_pdf
        pdf_rel = generate_daily_report_pdf(doc, db)
        doc.pdf_filename = pdf_rel
        doc.status = "generated"
        db.commit()
    except Exception as e:
        log.error("PDF generation failed: %s", e)
        db.commit()  # keep the document even if PDF failed

    return RedirectResponse(url=f"/documents/{doc.id}", status_code=303)


@router.post("/create/all")
def create_all_report(
    report_date: str = Form(...),
    well_ids: List[int] = Form(default=[]),
    downtime_threshold_min: int = Form(default=5),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """Create summary daily report for selected wells and generate PDF."""
    rd = date.fromisoformat(report_date)

    doc_type = db.query(DocumentType).filter(
        DocumentType.code == "daily_report_all"
    ).first()
    if not doc_type:
        raise HTTPException(400, "DocumentType daily_report_all not found. Run migration.")

    # Use selected wells or fall back to all
    if well_ids:
        wells = db.query(Well).filter(Well.id.in_(well_ids)).order_by(Well.number).all()
    else:
        wells = db.query(Well).order_by(Well.number).all()

    if not wells:
        raise HTTPException(404, "No wells found")

    final_well_ids = [w.id for w in wells]

    # Reuse existing (non-deleted) summary document for same date, or create new
    doc = db.query(Document).filter(
        Document.doc_type_id == doc_type.id,
        Document.well_id.is_(None),
        Document.period_start == rd,
        Document.period_end == rd,
        Document.deleted_at.is_(None),
    ).first()

    if doc:
        doc.meta = {
            "report_date": rd.isoformat(),
            "well_ids": final_well_ids,
            "downtime_threshold_min": downtime_threshold_min,
        }
        doc.status = "draft"
        doc.created_by_name = current_user
        db.commit()
        db.refresh(doc)
    else:
        doc = Document(
            doc_type_id=doc_type.id,
            well_id=None,
            period_start=rd,
            period_end=rd,
            period_month=rd.month,
            period_year=rd.year,
            created_by_name=current_user,
            status="draft",
            meta={
                "report_date": rd.isoformat(),
                "well_ids": final_well_ids,
                "downtime_threshold_min": downtime_threshold_min,
            },
        )
        db.add(doc)
        db.flush()
        doc.doc_number = build_doc_number(db, doc, doc_type)
        db.commit()
        db.refresh(doc)

    # Generate PDF
    try:
        from backend.services.daily_report_service import generate_daily_report_pdf
        pdf_rel = generate_daily_report_pdf(doc, db)
        doc.pdf_filename = pdf_rel
        doc.status = "generated"
        db.commit()
    except Exception as e:
        log.error("PDF generation failed for summary report: %s", e)
        db.commit()

    return RedirectResponse(url=f"/documents/{doc.id}", status_code=303)


@router.post("/create/summary")
def create_summary_report(
    report_date: str = Form(...),
    well_ids: List[int] = Form(default=[]),
    downtime_threshold_min: int = Form(default=5),
    trend_target: str = Form(default="flow"),
    trend_days: int = Form(default=2),
    include_charts: str = Form(default="true"),
    chart_style: str = Form(default="line"),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """Create compact summary report (tables only, no per-well detail pages)."""
    rd = date.fromisoformat(report_date)

    doc_type = db.query(DocumentType).filter(
        DocumentType.code == "daily_report_all"
    ).first()
    if not doc_type:
        raise HTTPException(400, "DocumentType daily_report_all not found. Run migration.")

    if well_ids:
        wells = db.query(Well).filter(Well.id.in_(well_ids)).order_by(Well.number).all()
    else:
        wells = db.query(Well).order_by(Well.number).all()

    if not wells:
        raise HTTPException(404, "No wells found")

    final_well_ids = [w.id for w in wells]

    doc = db.query(Document).filter(
        Document.doc_type_id == doc_type.id,
        Document.well_id.is_(None),
        Document.period_start == rd,
        Document.period_end == rd,
        Document.deleted_at.is_(None),
    ).first()

    meta = {
        "report_date": rd.isoformat(),
        "well_ids": final_well_ids,
        "downtime_threshold_min": downtime_threshold_min,
        "report_mode": "summary",
        "trend_target": trend_target,
        "trend_days": trend_days,
        "include_charts": include_charts.lower() in ("true", "1", "on"),
        "chart_style": chart_style if chart_style in ("line", "bar", "area", "stem") else "line",
    }

    if doc:
        doc.meta = meta
        doc.status = "draft"
        doc.created_by_name = current_user
        db.commit()
        db.refresh(doc)
    else:
        doc = Document(
            doc_type_id=doc_type.id,
            well_id=None,
            period_start=rd,
            period_end=rd,
            period_month=rd.month,
            period_year=rd.year,
            created_by_name=current_user,
            status="draft",
            meta=meta,
        )
        db.add(doc)
        db.flush()
        doc.doc_number = build_doc_number(db, doc, doc_type)
        db.commit()
        db.refresh(doc)

    try:
        from backend.services.daily_report_service import generate_summary_report_pdf
        pdf_rel = generate_summary_report_pdf(doc, db)
        doc.pdf_filename = pdf_rel
        doc.status = "generated"
        db.commit()
    except Exception as e:
        log.exception("Summary PDF generation failed: %s", e)
        db.commit()

    return RedirectResponse(url=f"/documents/{doc.id}", status_code=303)


@router.post("/create/monthly")
def create_monthly_report(
    period_year: int = Form(...),
    period_month: int = Form(...),
    well_ids: List[int] = Form(default=[]),
    downtime_threshold_min: int = Form(default=5),
    trend_target: str = Form(default="flow"),
    include_charts: str = Form(default="true"),
    chart_style: str = Form(default="line"),
    loss_window_hours: int = Form(default=24),
    status_filter: List[str] = Form(default=[]),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """Create monthly summary report for selected wells."""
    import calendar
    rd_start = date(period_year, period_month, 1)
    last_day = calendar.monthrange(period_year, period_month)[1]
    rd_end = date(period_year, period_month, last_day)
    # If current month, end at yesterday (not future)
    today = date.today()
    if rd_end >= today:
        rd_end = today - timedelta(days=1) if today.day > 1 else today

    doc_type = db.query(DocumentType).filter(
        DocumentType.code == "daily_report_all"
    ).first()
    if not doc_type:
        raise HTTPException(400, "DocumentType daily_report_all not found.")

    if well_ids:
        wells = db.query(Well).filter(Well.id.in_(well_ids)).order_by(Well.number).all()
    else:
        wells = db.query(Well).order_by(Well.number).all()

    if not wells:
        raise HTTPException(404, "No wells found")

    final_well_ids = [w.id for w in wells]

    meta = {
        "period_year": period_year,
        "period_month": period_month,
        "period_end_day": rd_end.day,
        "well_ids": final_well_ids,
        "downtime_threshold_min": downtime_threshold_min,
        "report_mode": "monthly",
        "trend_target": trend_target,
        "include_charts": include_charts.lower() in ("true", "1", "on"),
        "chart_style": chart_style if chart_style in ("line", "bar", "area", "stem") else "line",
        "loss_window_hours": max(6, min(loss_window_hours, 72)),
        "status_filter": status_filter if status_filter else [],
    }

    doc = Document(
        doc_type_id=doc_type.id,
        well_id=None,
        period_start=rd_start,
        period_end=rd_end,
        period_month=period_month,
        period_year=period_year,
        created_by_name=current_user,
        status="draft",
        meta=meta,
    )
    db.add(doc)
    db.flush()
    doc.doc_number = build_doc_number(db, doc, doc_type)
    db.commit()
    db.refresh(doc)

    try:
        from backend.services.daily_report_service import generate_monthly_report_pdf
        pdf_rel = generate_monthly_report_pdf(doc, db)
        doc.pdf_filename = pdf_rel
        doc.status = "generated"
        db.commit()
    except Exception as e:
        log.exception("Monthly PDF generation failed: %s", e)
        db.commit()

    return RedirectResponse(url=f"/documents/{doc.id}", status_code=303)


@router.post("/{doc_id}/regenerate")
def regenerate_report(
    doc_id: int,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """Regenerate PDF for an existing daily report document."""
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(404, "Document not found")

    if not doc.doc_type or doc.doc_type.code not in ("daily_report_well", "daily_report_all"):
        raise HTTPException(400, "Not a daily report document")

    meta = doc.meta or {}
    mode = meta.get("report_mode", "")
    if mode == "monthly":
        from backend.services.daily_report_service import generate_monthly_report_pdf
        pdf_rel = generate_monthly_report_pdf(doc, db)
    elif mode == "summary":
        from backend.services.daily_report_service import generate_summary_report_pdf
        pdf_rel = generate_summary_report_pdf(doc, db)
    else:
        from backend.services.daily_report_service import generate_daily_report_pdf
        pdf_rel = generate_daily_report_pdf(doc, db)

    doc.pdf_filename = pdf_rel
    doc.status = "generated"
    db.commit()

    return RedirectResponse(url=f"/documents/{doc.id}", status_code=303)
