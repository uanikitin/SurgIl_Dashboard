"""API «Конструктор сравнения»: CRUD для ComparisonSet/ComparisonCurve
плюс эндпоинт построения готового набора кривых для UI.

Источники данных и формат ответов — см. `comparison_service.py`.
Эндпоинты:
    GET    /api/comparison/sets?well_id=X         — список наборов скважины
    POST   /api/comparison/sets                   — создать набор
    GET    /api/comparison/sets/{set_id}          — набор + curves (плоско)
    PATCH  /api/comparison/sets/{set_id}          — изменить метаданные набора
    DELETE /api/comparison/sets/{set_id}          — удалить набор (вместе с curves)
    POST   /api/comparison/sets/{set_id}/curves   — добавить кривую
    PATCH  /api/comparison/curves/{curve_id}      — изменить кривую
    DELETE /api/comparison/curves/{curve_id}      — удалить кривую
    POST   /api/comparison/sets/{set_id}/render   — построить точки всех кривых
                                                    (для Plotly в UI)
"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db import SessionLocal
from backend.services import comparison_service as svc

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/comparison", tags=["comparison"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ───────────────────────── Pydantic schemas ─────────────────────────

class SetCreate(BaseModel):
    well_id: int
    name: str
    description: str | None = None
    x_axis_mode: str = "offset"  # 'offset' | 'date'
    in_report: bool = True


class SetUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    x_axis_mode: str | None = None
    in_report: bool | None = None
    sort_order: int | None = None


class CurveCreate(BaseModel):
    source: str             # 'customer'|'our_pressure'|'our_flow'|'baseline'
    metric: str             # 'q_total'|'q_working'|'dp'|'p_wellhead'|'p_flowline'
    label: str
    baseline_id: int | None = None
    period_from: date | None = None
    period_to: date | None = None
    color: str | None = None
    description: str | None = None


class CurveUpdate(BaseModel):
    source: str | None = None
    metric: str | None = None
    label: str | None = None
    baseline_id: int | None = None
    period_from: date | None = None
    period_to: date | None = None
    color: str | None = None
    description: str | None = None
    order_index: int | None = None


# ─────────────────────────────── Sets ───────────────────────────────

@router.get("/sets")
def api_list_sets(
    well_id: int = Query(...),
    db: Session = Depends(get_db),
):
    return {"well_id": well_id, "sets": svc.list_sets(db, well_id)}


@router.post("/sets")
def api_create_set(req: SetCreate, db: Session = Depends(get_db)):
    try:
        s = svc.create_set(
            db,
            well_id=req.well_id,
            name=req.name,
            description=req.description,
            x_axis_mode=req.x_axis_mode,
            in_report=req.in_report,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "set": svc.set_with_curves(db, s.id)}


@router.get("/sets/{set_id}")
def api_get_set(set_id: int, db: Session = Depends(get_db)):
    payload = svc.set_with_curves(db, set_id)
    if not payload:
        raise HTTPException(404, "ComparisonSet не найден")
    return payload


@router.patch("/sets/{set_id}")
def api_update_set(set_id: int, req: SetUpdate, db: Session = Depends(get_db)):
    try:
        s = svc.update_set(
            db, set_id,
            name=req.name, description=req.description,
            x_axis_mode=req.x_axis_mode, in_report=req.in_report,
            sort_order=req.sort_order,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not s:
        raise HTTPException(404, "ComparisonSet не найден")
    return {"ok": True, "set": svc.set_with_curves(db, set_id)}


@router.delete("/sets/{set_id}")
def api_delete_set(set_id: int, db: Session = Depends(get_db)):
    if not svc.delete_set(db, set_id):
        raise HTTPException(404, "ComparisonSet не найден")
    return {"ok": True, "deleted_id": set_id}


# ─────────────────────────────── Curves ─────────────────────────────

@router.post("/sets/{set_id}/curves")
def api_add_curve(set_id: int, req: CurveCreate, db: Session = Depends(get_db)):
    try:
        c = svc.add_curve(
            db, set_id,
            source=req.source, metric=req.metric, label=req.label,
            baseline_id=req.baseline_id,
            period_from=req.period_from, period_to=req.period_to,
            color=req.color, description=req.description,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "curve": svc.curve_to_dict(c)}


@router.patch("/curves/{curve_id}")
def api_update_curve(
    curve_id: int, req: CurveUpdate, db: Session = Depends(get_db),
):
    try:
        c = svc.update_curve(
            db, curve_id,
            source=req.source, metric=req.metric, label=req.label,
            baseline_id=req.baseline_id,
            period_from=req.period_from, period_to=req.period_to,
            color=req.color, description=req.description,
            order_index=req.order_index,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not c:
        raise HTTPException(404, "ComparisonCurve не найдена")
    return {"ok": True, "curve": svc.curve_to_dict(c)}


@router.delete("/curves/{curve_id}")
def api_delete_curve(curve_id: int, db: Session = Depends(get_db)):
    if not svc.delete_curve(db, curve_id):
        raise HTTPException(404, "ComparisonCurve не найдена")
    return {"ok": True, "deleted_id": curve_id}


# ────────────────────────── Render (для UI) ─────────────────────────

@router.post("/sets/{set_id}/render")
def api_render_set(set_id: int, db: Session = Depends(get_db)):
    """Построить все кривые набора и вернуть готовую структуру для графика.

    UI-фронт получает массив `curves[]` с полями `x`, `dates`, `values`,
    `baseline_value` (для горизонтальных линий), `label`, `color`, etc.
    """
    s = svc.get_set(db, set_id)
    if not s:
        raise HTTPException(404, "ComparisonSet не найден")
    return svc.build_set(db, s)


# ─────────────── Сопоставление LoRa / УзКорГаз ────────────────────

class SensorCustomerRequest(BaseModel):
    well_number: str
    period_from: date
    period_to: date


@router.post("/sensor-customer/compute")
def api_sensor_customer_compute(
    req: SensorCustomerRequest,
    db: Session = Depends(get_db),
):
    """Сопоставление данных мониторинга LoRa с суточными сводками УзКорГаз.

    Возвращает:
        - curves: кривые для Plotly (sensor_q, customer_q, sensor_dp, customer_dp)
        - daily_diff: таблица посуточного сравнения с Δ
        - summary: сводная статистика (средние, %, дни)
        - conclusion: текстовое заключение
    """
    if req.period_from > req.period_to:
        raise HTTPException(400, "period_from > period_to")
    return svc.build_sensor_customer_comparison(
        db,
        well_number=req.well_number,
        period_from=req.period_from,
        period_to=req.period_to,
    )
