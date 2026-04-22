"""
Роутер отчёта об адаптации скважины.

Страница /adaptation-report — интерактивная настройка и просмотр PDF в iframe.
Данные редактируются (периоды этапов), PDF генерируется по кнопке.
"""
from __future__ import annotations

import logging
import time as time_module
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db import SessionLocal
from backend.deps import get_current_user
from backend.models.wells import Well
from backend.services.adaptation_report_service import (
    collect_report_data,
    validate_stages,
    suggest_stages_from_events,
    compute_monthly_stats,
    _add_formatted_fields,
    _format_comparison,
    _fmt_num,
    DEFAULT_ADAPT_DURATION_DAYS,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/adaptation-report", tags=["adaptation-report"])
pages_router = APIRouter(tags=["adaptation-report-pages"])

templates = Jinja2Templates(directory="backend/templates")
templates.env.globals["time"] = lambda: int(time_module.time())


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
#  API-эндпоинты
# ═══════════════════════════════════════════════════════════════════

@router.get("/wells")
def list_wells(db: Session = Depends(get_db)):
    """Список ВСЕХ скважин для выпадающего списка.

    Возвращает все скважины с пометкой наличия этапов (obs_count, adapt_count).
    Для скважин без этапов даты можно задать вручную.
    """
    rows = db.execute(text("""
        SELECT w.id, w.number, w.name,
               COUNT(*) FILTER (WHERE ws.status = 'Наблюдение') AS obs_cnt,
               COUNT(*) FILTER (WHERE ws.status = 'Адаптация') AS adapt_cnt
        FROM wells w
        LEFT JOIN well_status ws ON ws.well_id = w.id
        GROUP BY w.id, w.number, w.name
        ORDER BY w.number
    """)).fetchall()

    return {
        "wells": [
            {
                "id": r[0],
                "number": r[1],
                "name": r[2] or f"Скв {r[1]}",
                "obs_count": int(r[3] or 0),
                "adapt_count": int(r[4] or 0),
            }
            for r in rows
        ]
    }


def _to_static_url(abs_path: str | None) -> str | None:
    """Преобразовать абсолютный путь в TEMP_DIR в URL для статики."""
    if not abs_path:
        return None
    p = Path(abs_path)
    # TEMP_DIR = backend/static/generated/temp/ → URL /static/generated/temp/...
    parts = p.parts
    if "static" in parts:
        idx = parts.index("static")
        rel = "/".join(parts[idx + 1:])
        return f"/static/{rel}"
    return None


def _attach_chart_urls(data: dict) -> dict:
    """Заменить абсолютные пути графиков на /static/ URL."""
    for stage_key in ("observation", "adaptation", "optimal_regime"):
        stage = data.get(stage_key) or {}
        if not stage:
            continue
        for k in (
            "pressure_chart_path", "dp_chart_path",
            "flow_chart_path", "combined_chart_path",
        ):
            if k in stage:
                stage[k] = _to_static_url(stage.get(k))
    return data


@router.get("/stage-data")
def get_stage_data(
    well_id: int = Query(...),
    with_charts: bool = Query(False, description="Render PNG charts"),
    with_reagent: bool = Query(False, description="Include reagent effectiveness"),
    adapt_days: int = Query(
        DEFAULT_ADAPT_DURATION_DAYS,
        description="Длительность адаптации для events-подсказки",
    ),
    db: Session = Depends(get_db),
):
    """Вернуть всё что нужно для предзаполнения формы:

    - `stages_from_status`: даты из well_status (если есть)
    - `stages_from_events`: подсказка по событиям (установка оборуд. + вбросы)
    - `source_used`: какой источник использован по умолчанию
      ('status' | 'events' | 'manual')
    - `observation`/`adaptation`/`comparison` — статистика (если получилось)

    Источник по умолчанию: status > events > manual (пусто).
    Фронт может переключить выбор пользователя.
    """
    # 1. Попробовать автодетект из well_status
    v = validate_stages(db, well_id)
    def _iso(dt):
        if dt is None:
            return None
        if isinstance(dt, datetime):
            return dt.isoformat(timespec="minutes")
        return dt.isoformat()

    stages_from_status = None
    if v.ok:
        stages_from_status = {
            "obs_from": _iso(v.obs_from),
            "obs_to": _iso(v.obs_to),
            "adapt_from": _iso(v.adapt_from),
            "adapt_to": _iso(v.adapt_to),
            "warnings": v.warnings,
        }

    # 2. Подсказка по событиям (всегда считаем)
    ev = suggest_stages_from_events(db, well_id, default_adapt_days=adapt_days)
    stages_from_events = None
    if ev.get("obs_from") or ev.get("adapt_from"):
        stages_from_events = {
            "obs_from": _iso(ev.get("obs_from")),
            "obs_to": _iso(ev.get("obs_to")),
            "adapt_from": _iso(ev.get("adapt_from")),
            "adapt_to": _iso(ev.get("adapt_to")),
            "rationale": ev.get("rationale", {}),
            "default_adapt_days": ev.get("default_adapt_days"),
        }

    # 3. Выбор дефолтного источника
    if stages_from_status:
        source_used = "status"
    elif stages_from_events and stages_from_events.get("adapt_from"):
        source_used = "events"
    else:
        source_used = "manual"

    # 4. Собираем статистику по выбранному источнику (если даты есть)
    if source_used == "status":
        data = collect_report_data(
            db, well_id,
            render_charts=with_charts,
            include_reagent_effectiveness=with_reagent,
        )
    elif source_used == "events":
        data = collect_report_data(
            db, well_id,
            obs_from=ev.get("obs_from"), obs_to=ev.get("obs_to"),
            adapt_from=ev.get("adapt_from"), adapt_to=ev.get("adapt_to"),
            render_charts=with_charts,
            include_reagent_effectiveness=with_reagent,
        )
    else:
        # Нет источника — только well-инфо
        well = db.query(Well).filter(Well.id == well_id).first()
        data = {"ok": False, "error": "Нет данных для автодетекта этапов"}
        if well:
            row = db.execute(text("""
                SELECT choke_diam_mm, horizon FROM well_construction
                WHERE well_no = :wno
                ORDER BY data_as_of DESC NULLS LAST LIMIT 1
            """), {"wno": str(well.number)}).fetchone()
            data["well"] = {
                "id": well.id,
                "number": str(well.number),
                "name": well.name,
                "horizon": str(row[1]) if row and row[1] else None,
                "choke_mm": float(row[0]) if row and row[0] else None,
            }

    if data.get("ok"):
        _attach_chart_urls(data)

    # Добавляем метаинформацию об источниках
    data["stages_from_status"] = stages_from_status
    data["stages_from_events"] = stages_from_events
    data["source_used"] = source_used

    return _json_safe(data)


class CustomDatesRequest(BaseModel):
    well_id: int
    obs_from: datetime
    obs_to: datetime
    adapt_from: datetime
    adapt_to: datetime
    with_charts: bool = True
    with_reagent: bool = True
    obs_description: str | None = None
    adapt_description: str | None = None
    # Оптимальный режим
    optimal_mode: str = "auto"           # "auto" | "manual" | "off"
    optimal_window_days: int = 3
    optimal_from: datetime | None = None
    optimal_to: datetime | None = None


@router.post("/compute")
def compute_with_custom_dates(
    req: CustomDatesRequest,
    db: Session = Depends(get_db),
):
    """Пересчитать статистику с явно заданными датами.

    По умолчанию рендерит графики и считает эффективность реагентов.
    """
    data = collect_report_data(
        db, req.well_id,
        obs_from=req.obs_from, obs_to=req.obs_to,
        adapt_from=req.adapt_from, adapt_to=req.adapt_to,
        render_charts=req.with_charts,
        include_reagent_effectiveness=req.with_reagent,
        obs_description_override=req.obs_description,
        adapt_description_override=req.adapt_description,
        optimal_mode=req.optimal_mode,
        optimal_window_days=req.optimal_window_days,
        optimal_from=req.optimal_from,
        optimal_to=req.optimal_to,
    )
    if data.get("ok") and req.with_charts:
        _attach_chart_urls(data)
    return _json_safe(data)


@router.get("/monthly-stats")
def get_monthly_stats(
    well_id: int = Query(...),
    months_back: int = Query(24, description="Глубина истории в месяцах"),
    db: Session = Depends(get_db),
):
    """Помесячная статистика скважины (Q mean/median/min/max, тренд, события)."""
    months = compute_monthly_stats(db, well_id, months_back=months_back)
    return {"well_id": well_id, "months": _json_safe(months)}


@router.get("/preview-pdf")
def preview_pdf(
    well_id: int = Query(...),
    obs_from: datetime | None = Query(None),
    obs_to: datetime | None = Query(None),
    adapt_from: datetime | None = Query(None),
    adapt_to: datetime | None = Query(None),
    obs_description: str | None = Query(None),
    adapt_description: str | None = Query(None),
    optimal_mode: str = Query("auto"),
    optimal_window_days: int = Query(3),
    optimal_from: datetime | None = Query(None),
    optimal_to: datetime | None = Query(None),
    db: Session = Depends(get_db),
):
    """Сгенерировать PDF (с графиками) и вернуть как inline-файл для iframe.

    Если даты заданы — используются они. Иначе автодетект из well_status.
    """
    from datetime import datetime as _dt
    from backend.services.daily_report_service import (
        _ensure_dirs, _get_latex_env, _compile_latex, _tex_escape,
    )
    from backend.services.adaptation_report_service import KUNGRAD_OFFSET

    _ensure_dirs()

    data = collect_report_data(
        db, well_id,
        obs_from=obs_from, obs_to=obs_to,
        adapt_from=adapt_from, adapt_to=adapt_to,
        render_charts=True,
        obs_description_override=obs_description,
        adapt_description_override=adapt_description,
        optimal_mode=optimal_mode,
        optimal_window_days=optimal_window_days,
        optimal_from=optimal_from,
        optimal_to=optimal_to,
    )
    if not data.get("ok"):
        raise HTTPException(status_code=400, detail=data.get("error"))

    observation = _add_formatted_fields(dict(data["observation"]))
    adaptation = _add_formatted_fields(dict(data["adaptation"]))
    comparison = _format_comparison(data["comparison"])

    optimal_regime = None
    comparison_optimal = None
    if data.get("optimal_regime"):
        optimal_regime = _add_formatted_fields(dict(data["optimal_regime"]))
    if data.get("comparison_optimal"):
        comparison_optimal = _format_comparison(data["comparison_optimal"])

    well_ctx = dict(data["well"])
    well_ctx["name"] = _tex_escape(well_ctx.get("name") or "")
    well_ctx["horizon"] = _tex_escape(str(well_ctx.get("horizon") or "---"))
    well_ctx["choke_mm"] = _fmt_num(well_ctx.get("choke_mm"), 1)

    now_kungrad = _dt.utcnow() + KUNGRAD_OFFSET
    context = {
        "doc_number": f"PREVIEW-{well_id}",
        "generated_at": now_kungrad.strftime("%d.%m.%Y %H:%M"),
        "well": well_ctx,
        "work_start_date": None,
        "equipment_acts": [],
        "observation": observation,
        "adaptation": adaptation,
        "optimal_regime": optimal_regime,
        "comparison": comparison,
        "comparison_optimal": comparison_optimal,
        "conclusions": [_tex_escape(c) for c in (data.get("conclusions") or [])],
        "warnings": [_tex_escape(w) for w in (data.get("warnings") or [])],
    }

    env = _get_latex_env()
    template = env.get_template("adaptation_report.tex")
    latex_source = template.render(**context)

    base_name = f"adaptation_preview_{well_id}"
    pdf_path = _compile_latex(latex_source, base_name)

    # Очистка PNG
    stages_to_clean = [observation, adaptation]
    if optimal_regime:
        stages_to_clean.append(optimal_regime)
    for stage in stages_to_clean:
        for key in (
            "pressure_chart_path", "dp_chart_path",
            "flow_chart_path", "combined_chart_path",
        ):
            p = stage.get(key)
            if p:
                Path(p).unlink(missing_ok=True)

    response = FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"adaptation_well_{well_id}.pdf",
    )
    # inline чтобы открывался в iframe, а не скачивался
    response.headers["Content-Disposition"] = (
        f'inline; filename="adaptation_well_{well_id}.pdf"'
    )
    return response


@router.get("/validate")
def validate_well_stages(
    well_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Быстрая проверка этапов."""
    v = validate_stages(db, well_id)
    return {
        "ok": v.ok,
        "error": v.error,
        "warnings": v.warnings,
        "obs_from": v.obs_from.isoformat() if v.obs_from else None,
        "obs_to": v.obs_to.isoformat() if v.obs_to else None,
        "adapt_from": v.adapt_from.isoformat() if v.adapt_from else None,
        "adapt_to": v.adapt_to.isoformat() if v.adapt_to else None,
        "obs_note": v.obs_note,
        "adapt_note": v.adapt_note,
    }


# ═══════════════════════════════════════════════════════════════════
#  HTML-страница
# ═══════════════════════════════════════════════════════════════════

@pages_router.get("/adaptation-report", response_class=HTMLResponse)
def adaptation_report_page(
    request: Request,
    current_user: str = Depends(get_current_user),
):
    """Страница настройки и просмотра отчёта об адаптации."""
    return templates.TemplateResponse(
        "adaptation_report.html",
        {
            "request": request,
            "current_user": current_user,
            "is_admin": request.session.get("is_admin", False),
        },
    )
