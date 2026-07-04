"""
Period Report Router — отчёты за произвольный период.

Prefix: /api/period-report. Tags: ["period-report"].

Endpoints:
  GET  /{well_id}/list         — список отчётов скважины
  POST /{well_id}              — создать отчёт
  GET  /{report_id}            — получить отчёт
  PUT  /{report_id}            — обновить отчёт
  DELETE /{report_id}          — удалить отчёт
  GET  /{report_id}/preview    — HTML preview
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models.period_report import PeriodReport

router = APIRouter(prefix="/api/period-report", tags=["period-report"])
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class PeriodReportCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    period_start: date
    period_end: date
    blocks_snapshot: List[dict] = Field(default_factory=list)
    status: str = "draft"


class PeriodReportUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    blocks_snapshot: Optional[List[dict]] = None
    status: Optional[str] = None


class PeriodReportOut(BaseModel):
    id: int
    well_id: int
    title: str
    period_start: date
    period_end: date
    blocks_snapshot: List[dict]
    status: str
    created_at: Any
    updated_at: Any

    class Config:
        from_attributes = True


class PeriodReportListItem(BaseModel):
    id: int
    title: str
    period_start: date
    period_end: date
    status: str
    blocks_count: int
    created_at: Any

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/{well_id}/list", response_model=List[PeriodReportListItem])
def list_reports(well_id: int, db: Session = Depends(get_db)):
    """Список отчётов скважины."""
    rows = (
        db.query(PeriodReport)
        .filter(PeriodReport.well_id == well_id)
        .order_by(desc(PeriodReport.created_at))
        .all()
    )
    result = []
    for r in rows:
        blocks = r.blocks_snapshot if isinstance(r.blocks_snapshot, list) else []
        result.append(PeriodReportListItem(
            id=r.id,
            title=r.title,
            period_start=r.period_start,
            period_end=r.period_end,
            status=r.status,
            blocks_count=len(blocks),
            created_at=r.created_at,
        ))
    return result


@router.post("/{well_id}", response_model=PeriodReportOut, status_code=http_status.HTTP_201_CREATED)
def create_report(well_id: int, body: PeriodReportCreate, db: Session = Depends(get_db)):
    """Создать новый отчёт."""
    if body.period_end < body.period_start:
        raise HTTPException(400, "period_end must be >= period_start")

    report = PeriodReport(
        well_id=well_id,
        title=body.title,
        period_start=body.period_start,
        period_end=body.period_end,
        blocks_snapshot=body.blocks_snapshot,
        status=body.status,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    log.info(f"Created PeriodReport id={report.id} well={well_id} title={body.title!r}")
    return report


@router.get("/{report_id}", response_model=PeriodReportOut)
def get_report(report_id: int, db: Session = Depends(get_db)):
    """Получить отчёт по ID."""
    report = db.query(PeriodReport).filter(PeriodReport.id == report_id).first()
    if not report:
        raise HTTPException(404, f"PeriodReport {report_id} not found")
    return report


@router.put("/{report_id}", response_model=PeriodReportOut)
def update_report(report_id: int, body: PeriodReportUpdate, db: Session = Depends(get_db)):
    """Обновить отчёт."""
    report = db.query(PeriodReport).filter(PeriodReport.id == report_id).first()
    if not report:
        raise HTTPException(404, f"PeriodReport {report_id} not found")

    if body.title is not None:
        report.title = body.title
    if body.period_start is not None:
        report.period_start = body.period_start
    if body.period_end is not None:
        report.period_end = body.period_end
    if body.blocks_snapshot is not None:
        report.blocks_snapshot = body.blocks_snapshot
    if body.status is not None:
        report.status = body.status

    # Validate period
    if report.period_end < report.period_start:
        raise HTTPException(400, "period_end must be >= period_start")

    db.commit()
    db.refresh(report)
    log.info(f"Updated PeriodReport id={report_id}")
    return report


@router.delete("/{report_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_report(report_id: int, db: Session = Depends(get_db)):
    """Удалить отчёт."""
    report = db.query(PeriodReport).filter(PeriodReport.id == report_id).first()
    if not report:
        raise HTTPException(404, f"PeriodReport {report_id} not found")

    db.delete(report)
    db.commit()
    log.info(f"Deleted PeriodReport id={report_id}")
    return None


@router.get("/{report_id}/preview")
def preview_report(report_id: int, db: Session = Depends(get_db)):
    """HTML preview отчёта (блоки из snapshot)."""
    report = db.query(PeriodReport).filter(PeriodReport.id == report_id).first()
    if not report:
        raise HTTPException(404, f"PeriodReport {report_id} not found")

    # Простой HTML preview — список блоков
    blocks = report.blocks_snapshot if isinstance(report.blocks_snapshot, list) else []

    html_parts = [
        f"<h2>{report.title}</h2>",
        f"<p>Период: {report.period_start} — {report.period_end}</p>",
        f"<p>Статус: {report.status}</p>",
        f"<hr>",
    ]

    if not blocks:
        html_parts.append("<p><em>Блоки анализа пока не добавлены.</em></p>")
    else:
        for i, block in enumerate(blocks, 1):
            kind = block.get("kind", "unknown")
            title = block.get("title", f"Блок {i}")
            html_parts.append(f"<div class='preview-block'><h4>{i}. {title} ({kind})</h4></div>")

    return {"html": "\n".join(html_parts)}
