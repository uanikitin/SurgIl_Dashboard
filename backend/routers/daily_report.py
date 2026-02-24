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

    # Create document
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

    # Create document
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

    from backend.services.daily_report_service import generate_daily_report_pdf
    pdf_rel = generate_daily_report_pdf(doc, db)
    doc.pdf_filename = pdf_rel
    doc.status = "generated"
    db.commit()

    return RedirectResponse(url=f"/documents/{doc.id}", status_code=303)
