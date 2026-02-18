"""
Анализ дебита газа — сценарии, коррекции, результаты.

Два роутера:
  - router      (prefix=/api/flow-analysis) — JSON API
  - pages_router (без prefix)               — HTML страница /flow-analysis

Подключается в app.py:
    app.include_router(flow_analysis_router)
    app.include_router(flow_analysis_pages_router)
"""
from __future__ import annotations

import logging
import time as time_module
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sqlalchemy import text as sa_text

from backend.db import get_db, engine as pg_engine
from backend.deps import get_current_user
from backend.models.flow_analysis import FlowScenario, FlowCorrection, FlowResult

router = APIRouter(prefix="/api/flow-analysis", tags=["flow-analysis"])
pages_router = APIRouter(tags=["flow-analysis-pages"])
templates = Jinja2Templates(directory="backend/templates")
templates.env.globals["time"] = lambda: int(time_module.time())
log = logging.getLogger(__name__)


# ──────────────────── HTML PAGE ────────────────────

@pages_router.get("/flow-analysis", response_class=HTMLResponse)
def flow_analysis_page(
    request: Request,
    current_user: str = Depends(get_current_user),
):
    """Страница анализа дебита газа."""
    return templates.TemplateResponse(
        "flow_analysis.html",
        {
            "request": request,
            "current_user": current_user,
            "is_admin": True,
        },
    )


# ──────────────────── Pydantic schemas ────────────────────

class ScenarioCreate(BaseModel):
    well_id: int
    name: str = Field(max_length=200)
    description: Optional[str] = None
    period_start: str  # ISO datetime
    period_end: str
    choke_mm: Optional[float] = None
    multiplier: float = 4.1
    c1: float = 2.919
    c2: float = 4.654
    c3: float = 286.95
    critical_ratio: float = 0.5
    smooth_enabled: bool = True
    smooth_window: int = 17
    smooth_polyorder: int = 3
    exclude_purge_ids: str = ""
    created_by: Optional[str] = None


class ScenarioUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    choke_mm: Optional[float] = None
    multiplier: Optional[float] = None
    c1: Optional[float] = None
    c2: Optional[float] = None
    c3: Optional[float] = None
    critical_ratio: Optional[float] = None
    smooth_enabled: Optional[bool] = None
    smooth_window: Optional[int] = None
    smooth_polyorder: Optional[int] = None
    exclude_purge_ids: Optional[str] = None


class CorrectionCreate(BaseModel):
    correction_type: str  # exclude | interpolate | manual_value | clamp
    dt_start: str  # ISO datetime
    dt_end: str
    manual_p_tube: Optional[float] = None
    manual_p_line: Optional[float] = None
    clamp_min: Optional[float] = None
    clamp_max: Optional[float] = None
    interp_method: str = "linear"
    reason: Optional[str] = None
    sort_order: int = 0


class CorrectionUpdate(BaseModel):
    correction_type: Optional[str] = None
    dt_start: Optional[str] = None
    dt_end: Optional[str] = None
    manual_p_tube: Optional[float] = None
    manual_p_line: Optional[float] = None
    clamp_min: Optional[float] = None
    clamp_max: Optional[float] = None
    interp_method: Optional[str] = None
    reason: Optional[str] = None
    sort_order: Optional[int] = None


# ──────────────────── helpers ────────────────────

def _get_scenario(scenario_id: int, db: Session) -> FlowScenario:
    scenario = (
        db.query(FlowScenario)
        .filter(FlowScenario.id == scenario_id, FlowScenario.deleted_at.is_(None))
        .first()
    )
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Сценарий {scenario_id} не найден")
    return scenario


def _scenario_to_dict(s: FlowScenario) -> dict:
    return {
        "id": s.id,
        "well_id": s.well_id,
        "well_number": s.well.number if s.well else None,
        "well_name": s.well.name if s.well else None,
        "name": s.name,
        "description": s.description,
        "period_start": s.period_start.isoformat() if s.period_start else None,
        "period_end": s.period_end.isoformat() if s.period_end else None,
        "choke_mm": s.choke_mm,
        "multiplier": s.multiplier,
        "c1": s.c1,
        "c2": s.c2,
        "c3": s.c3,
        "critical_ratio": s.critical_ratio,
        "smooth_enabled": s.smooth_enabled,
        "smooth_window": s.smooth_window,
        "smooth_polyorder": s.smooth_polyorder,
        "exclude_purge_ids": s.exclude_purge_ids,
        "is_baseline": s.is_baseline,
        "status": s.status,
        "meta": s.meta,
        "created_by": s.created_by,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        "corrections_count": len(s.corrections) if s.corrections else 0,
        "results_count": len(s.results) if s.results else 0,
    }


def _correction_to_dict(c: FlowCorrection) -> dict:
    return {
        "id": c.id,
        "scenario_id": c.scenario_id,
        "correction_type": c.correction_type,
        "dt_start": c.dt_start.isoformat() if c.dt_start else None,
        "dt_end": c.dt_end.isoformat() if c.dt_end else None,
        "manual_p_tube": c.manual_p_tube,
        "manual_p_line": c.manual_p_line,
        "clamp_min": c.clamp_min,
        "clamp_max": c.clamp_max,
        "interp_method": c.interp_method,
        "reason": c.reason,
        "sort_order": c.sort_order,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


# ──────────────────── SCENARIOS ────────────────────

@router.post("/scenarios")
def create_scenario(body: ScenarioCreate, db: Session = Depends(get_db)):
    """Создать новый сценарий."""
    scenario = FlowScenario(
        well_id=body.well_id,
        name=body.name,
        description=body.description,
        period_start=datetime.fromisoformat(body.period_start),
        period_end=datetime.fromisoformat(body.period_end),
        choke_mm=body.choke_mm,
        multiplier=body.multiplier,
        c1=body.c1,
        c2=body.c2,
        c3=body.c3,
        critical_ratio=body.critical_ratio,
        smooth_enabled=body.smooth_enabled,
        smooth_window=body.smooth_window,
        smooth_polyorder=body.smooth_polyorder,
        exclude_purge_ids=body.exclude_purge_ids,
        created_by=body.created_by,
    )
    db.add(scenario)
    db.commit()
    db.refresh(scenario)
    return _scenario_to_dict(scenario)


@router.get("/scenarios")
def list_scenarios(
    well_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Список сценариев (фильтр по well_id)."""
    q = db.query(FlowScenario).filter(FlowScenario.deleted_at.is_(None))
    if well_id is not None:
        q = q.filter(FlowScenario.well_id == well_id)
    q = q.order_by(FlowScenario.created_at.desc())
    return [_scenario_to_dict(s) for s in q.all()]


@router.get("/scenarios/{scenario_id}")
def get_scenario(scenario_id: int, db: Session = Depends(get_db)):
    """Сценарий с коррекциями."""
    s = _get_scenario(scenario_id, db)
    result = _scenario_to_dict(s)
    result["corrections"] = [_correction_to_dict(c) for c in s.corrections]
    return result


@router.put("/scenarios/{scenario_id}")
def update_scenario(
    scenario_id: int,
    body: ScenarioUpdate,
    db: Session = Depends(get_db),
):
    """Обновить параметры сценария (сбрасывает status -> draft)."""
    s = _get_scenario(scenario_id, db)
    if s.status == "locked":
        raise HTTPException(400, "Нельзя изменить заблокированный сценарий")

    updates = body.model_dump(exclude_unset=True)
    for key, val in updates.items():
        if key in ("period_start", "period_end") and val is not None:
            val = datetime.fromisoformat(val)
        setattr(s, key, val)

    s.status = "draft"
    s.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(s)
    return _scenario_to_dict(s)


@router.delete("/scenarios/{scenario_id}")
def delete_scenario(scenario_id: int, db: Session = Depends(get_db)):
    """Soft-delete сценария."""
    s = _get_scenario(scenario_id, db)
    s.deleted_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "id": scenario_id}


@router.post("/scenarios/{scenario_id}/set-baseline")
def set_baseline(scenario_id: int, db: Session = Depends(get_db)):
    """Пометить сценарий как базовый (сбросить предыдущий базовый для этой скважины)."""
    s = _get_scenario(scenario_id, db)

    # Сбросить предыдущий baseline для той же скважины
    db.query(FlowScenario).filter(
        FlowScenario.well_id == s.well_id,
        FlowScenario.is_baseline == True,
        FlowScenario.deleted_at.is_(None),
    ).update({"is_baseline": False})

    s.is_baseline = True
    s.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "id": scenario_id, "well_id": s.well_id}


# ──────────────────── CORRECTIONS ────────────────────

@router.post("/scenarios/{scenario_id}/corrections")
def add_correction(
    scenario_id: int,
    body: CorrectionCreate,
    db: Session = Depends(get_db),
):
    """Добавить коррекцию к сценарию."""
    s = _get_scenario(scenario_id, db)
    if s.status == "locked":
        raise HTTPException(400, "Нельзя изменить заблокированный сценарий")

    valid_types = ("exclude", "interpolate", "manual_value", "clamp")
    if body.correction_type not in valid_types:
        raise HTTPException(400, f"correction_type должен быть одним из: {valid_types}")

    corr = FlowCorrection(
        scenario_id=scenario_id,
        correction_type=body.correction_type,
        dt_start=datetime.fromisoformat(body.dt_start),
        dt_end=datetime.fromisoformat(body.dt_end),
        manual_p_tube=body.manual_p_tube,
        manual_p_line=body.manual_p_line,
        clamp_min=body.clamp_min,
        clamp_max=body.clamp_max,
        interp_method=body.interp_method,
        reason=body.reason,
        sort_order=body.sort_order,
    )
    db.add(corr)
    s.status = "draft"
    s.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(corr)
    return _correction_to_dict(corr)


@router.put("/corrections/{correction_id}")
def update_correction(
    correction_id: int,
    body: CorrectionUpdate,
    db: Session = Depends(get_db),
):
    """Обновить коррекцию."""
    corr = db.query(FlowCorrection).filter(FlowCorrection.id == correction_id).first()
    if not corr:
        raise HTTPException(404, f"Коррекция {correction_id} не найдена")

    scenario = _get_scenario(corr.scenario_id, db)
    if scenario.status == "locked":
        raise HTTPException(400, "Нельзя изменить заблокированный сценарий")

    updates = body.model_dump(exclude_unset=True)
    for key, val in updates.items():
        if key in ("dt_start", "dt_end") and val is not None:
            val = datetime.fromisoformat(val)
        setattr(corr, key, val)

    scenario.status = "draft"
    scenario.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(corr)
    return _correction_to_dict(corr)


@router.delete("/corrections/{correction_id}")
def delete_correction(correction_id: int, db: Session = Depends(get_db)):
    """Удалить коррекцию."""
    corr = db.query(FlowCorrection).filter(FlowCorrection.id == correction_id).first()
    if not corr:
        raise HTTPException(404, f"Коррекция {correction_id} не найдена")

    scenario = _get_scenario(corr.scenario_id, db)
    if scenario.status == "locked":
        raise HTTPException(400, "Нельзя изменить заблокированный сценарий")

    db.delete(corr)
    scenario.status = "draft"
    scenario.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "id": correction_id}


# ──────────────────── CALCULATION ────────────────────

@router.post("/scenarios/{scenario_id}/calculate")
def calculate_scenario(scenario_id: int, db: Session = Depends(get_db)):
    """
    Рассчитать дебит по сценарию и сохранить результаты.
    Возвращает summary + chart + daily_results.
    """
    from backend.services.flow_rate.scenario_service import run_scenario_calculation

    s = _get_scenario(scenario_id, db)
    if s.status == "locked":
        raise HTTPException(400, "Нельзя пересчитать заблокированный сценарий")

    try:
        result = run_scenario_calculation(s, db, save_results=True)
    except ValueError as e:
        raise HTTPException(404, str(e))

    db.commit()
    return result


@router.get("/scenarios/{scenario_id}/preview")
def preview_scenario(scenario_id: int, db: Session = Depends(get_db)):
    """
    Предпросмотр расчёта (без сохранения в БД).
    Быстрый пробный расчёт для настройки параметров.
    """
    from backend.services.flow_rate.scenario_service import run_scenario_calculation

    s = _get_scenario(scenario_id, db)

    try:
        result = run_scenario_calculation(s, db, save_results=False)
    except ValueError as e:
        raise HTTPException(404, str(e))

    return result


@router.get("/scenarios/{scenario_id}/results")
def get_results(scenario_id: int, db: Session = Depends(get_db)):
    """Суточные результаты из БД (без пересчёта)."""
    _get_scenario(scenario_id, db)

    results = (
        db.query(FlowResult)
        .filter(FlowResult.scenario_id == scenario_id)
        .order_by(FlowResult.result_date)
        .all()
    )

    return [
        {
            "id": r.id,
            "result_date": r.result_date.isoformat(),
            "avg_flow_rate": r.avg_flow_rate,
            "min_flow_rate": r.min_flow_rate,
            "max_flow_rate": r.max_flow_rate,
            "median_flow_rate": r.median_flow_rate,
            "cumulative_flow": r.cumulative_flow,
            "avg_p_tube": r.avg_p_tube,
            "avg_p_line": r.avg_p_line,
            "avg_dp": r.avg_dp,
            "purge_loss": r.purge_loss,
            "downtime_minutes": r.downtime_minutes,
            "data_points": r.data_points,
            "corrected_points": r.corrected_points,
        }
        for r in results
    ]


# ──────────────────── COMPARISON ────────────────────

@router.get("/compare")
def compare(
    scenario_id: int = Query(..., description="ID текущего сценария"),
    baseline_id: int = Query(..., description="ID базового сценария"),
    granularity: str = Query("daily", description="daily | weekly | monthly"),
    db: Session = Depends(get_db),
):
    """Сравнение двух сценариев."""
    from backend.services.flow_rate.scenario_service import compare_scenarios

    if granularity not in ("daily", "weekly", "monthly"):
        raise HTTPException(400, "granularity must be daily, weekly, or monthly")

    _get_scenario(scenario_id, db)
    _get_scenario(baseline_id, db)

    return compare_scenarios(scenario_id, baseline_id, granularity, db)


# ──────────────────── EVENTS ────────────────────

@router.get("/events")
def get_events(
    well_id: int = Query(...),
    start: str = Query(...),
    end: str = Query(...),
):
    """События скважины за период (для отображения на графике)."""
    query = sa_text("""
        SELECT e.event_time, e.event_type, e.description,
               e.purge_phase, e.p_tube, e.p_line, e.reagent, e.qty
        FROM events e
        JOIN wells w ON e.well = w.number::text
        WHERE w.id = :well_id
          AND e.event_time BETWEEN :start AND :end
        ORDER BY e.event_time
    """)
    with pg_engine.connect() as conn:
        rows = conn.execute(query, {"well_id": well_id, "start": start, "end": end}).fetchall()

    return [
        {
            "event_time": row[0].isoformat() if row[0] else None,
            "event_type": row[1],
            "description": row[2],
            "purge_phase": row[3],
            "p_tube": float(row[4]) if row[4] is not None else None,
            "p_line": float(row[5]) if row[5] is not None else None,
            "reagent": row[6],
            "qty": float(row[7]) if row[7] is not None else None,
        }
        for row in rows
    ]


# ──────────────────── REPORT ────────────────────

@router.post("/scenarios/{scenario_id}/report")
def generate_report(
    scenario_id: int,
    baseline_id: Optional[int] = Query(None, description="ID базового сценария для сравнения"),
    db: Session = Depends(get_db),
):
    """Сгенерировать LaTeX PDF-отчёт по сценарию."""
    from backend.services.flow_rate.report_service import generate_flow_report

    s = _get_scenario(scenario_id, db)
    if s.status != "calculated":
        raise HTTPException(400, "Сначала выполните расчёт (status != 'calculated')")

    try:
        pdf_rel_path = generate_flow_report(s, db, baseline_id=baseline_id)
    except Exception as e:
        log.error("Report generation failed: %s", e, exc_info=True)
        raise HTTPException(500, f"Ошибка генерации отчёта: {e}")

    db.commit()
    return {"ok": True, "pdf_path": pdf_rel_path}


@router.get("/scenarios/{scenario_id}/report/download")
def download_report(scenario_id: int, db: Session = Depends(get_db)):
    """Скачать сгенерированный PDF-отчёт."""
    s = _get_scenario(scenario_id, db)
    pdf_path = (s.meta or {}).get("pdf_path")
    if not pdf_path:
        raise HTTPException(404, "Отчёт ещё не сгенерирован")

    import os
    if not os.path.exists(pdf_path):
        raise HTTPException(404, "Файл отчёта не найден на диске")

    from fastapi.responses import FileResponse
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=f"flow_report_{scenario_id}.pdf",
    )
