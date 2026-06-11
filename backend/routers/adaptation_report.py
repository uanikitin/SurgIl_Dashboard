"""
Роутер отчёта об адаптации скважины.

Страница /adaptation-report — интерактивная настройка и просмотр PDF в iframe.
Данные редактируются (периоды этапов), PDF генерируется по кнопке.
"""
from __future__ import annotations

import logging
import tempfile
import time as time_module
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db import SessionLocal
from backend.deps import get_current_user
from backend.models.wells import Well
from backend.services.adaptation_report_service import (
    collect_report_data,
    collect_stage_stats,
    validate_stages,
    suggest_stages_from_events,
    compute_monthly_stats,
    _add_formatted_fields,
    _format_comparison,
    _fmt_num,
    DEFAULT_ADAPT_DURATION_DAYS,
)
from backend.services import customer_baseline_service as bsvc
from backend.models.wells import Well as _Well
from backend.config.status_registry import STATUS_BY_LABEL

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

# ─────── Persistent UI state (Этап D, sticky workspace) ───────
#
# Таблица создаётся IF NOT EXISTS при первом обращении (как well_daily).
# state — JSONB с любыми полями формы (даты, описания, активный tab).

_STATE_TABLE_INITIALIZED = False


def _ensure_state_table(db: Session) -> None:
    global _STATE_TABLE_INITIALIZED
    if _STATE_TABLE_INITIALIZED:
        return
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS adaptation_report_state (
            well_id    INTEGER PRIMARY KEY REFERENCES wells(id) ON DELETE CASCADE,
            state      JSONB   NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    db.commit()
    _STATE_TABLE_INITIALIZED = True


@router.get("/state/{well_id}")
def api_get_state(well_id: int, db: Session = Depends(get_db)):
    _ensure_state_table(db)
    row = db.execute(text("""
        SELECT state, updated_at FROM adaptation_report_state
        WHERE well_id = :wid
    """), {"wid": well_id}).fetchone()
    if not row:
        return {"well_id": well_id, "state": {}, "updated_at": None}
    return {
        "well_id": well_id,
        "state": row[0] or {},
        "updated_at": row[1].isoformat(timespec="seconds") if row[1] else None,
    }


class _StateBody(BaseModel):
    state: dict


@router.put("/state/{well_id}")
def api_put_state(well_id: int, body: _StateBody, db: Session = Depends(get_db)):
    _ensure_state_table(db)
    import json as _json
    db.execute(text("""
        INSERT INTO adaptation_report_state (well_id, state, updated_at)
        VALUES (:wid, CAST(:state AS JSONB), CURRENT_TIMESTAMP)
        ON CONFLICT (well_id) DO UPDATE
        SET state = EXCLUDED.state, updated_at = CURRENT_TIMESTAMP
    """), {"wid": well_id, "state": _json.dumps(body.state, default=str)})
    db.commit()
    return {"ok": True, "well_id": well_id}


@router.delete("/state/{well_id}")
def api_delete_state(well_id: int, db: Session = Depends(get_db)):
    _ensure_state_table(db)
    res = db.execute(
        text("DELETE FROM adaptation_report_state WHERE well_id = :wid"),
        {"wid": well_id},
    )
    db.commit()
    return {"ok": True, "deleted": res.rowcount > 0}


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
    data["all_stages"] = _load_all_stages(db, well_id)

    return _json_safe(data)


def _load_all_stages(db: Session, well_id: int) -> list[dict]:
    """История этапов из well_status (для отображения плиток в UI)."""
    rows = db.execute(text("""
        SELECT (dt_start AT TIME ZONE 'Asia/Tashkent')::timestamp AS dt_from,
               (dt_end   AT TIME ZONE 'Asia/Tashkent')::timestamp AS dt_to,
               status,
               note
        FROM well_status
        WHERE well_id = :wid
        ORDER BY dt_start ASC
    """), {"wid": well_id}).fetchall()

    today = datetime.now()
    out: list[dict] = []
    for r in rows:
        dt_from, dt_to, status, note = r[0], r[1], r[2], r[3]
        is_open = dt_to is None
        end_for_calc = dt_to or today
        duration_days = None
        if dt_from is not None:
            delta = end_for_calc - dt_from
            duration_days = round(delta.total_seconds() / 86400.0, 1)
        color = (STATUS_BY_LABEL.get(status) or {}).get("color") or "#6c757d"
        out.append({
            "status": status,
            "color": color,
            "dt_from": dt_from,
            "dt_to": dt_to,
            "is_open": is_open,
            "duration_days": duration_days,
            "note": (note or "").strip() or None,
        })
    return out


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
    # Глава «Анализ исходных данных»
    include_customer_chapter: bool = False
    customer_periods: list[dict] = []
    # Toggle разделов PDF — ключи: well_info, customer_data,
    # observation, adaptation, charts_compare, comparison.
    # Если ключ не передан → True (включён по умолчанию).
    sections: dict[str, bool] | None = None
    # Порог простоя ΔP (атм). Точка считается простоем если
    # (p_tube - p_line) < dp_threshold ИЛИ purge_flag.
    # Дефолт 0.1; диапазон 0.0..1.0.
    dp_threshold: float = 0.1
    # ── Режимы превью (главы / отдельный блок) ──
    # only_chapter — рендерить ТОЛЬКО одну главу (well_info | customer_data |
    # observation | adaptation | charts_compare | comparison). Все остальные
    # \BLOCK{ if include_sections.* } будут False, шаблон их пропустит.
    only_chapter: str | None = None
    # only_block_id — отфильтровать observation_blocks/adaptation_blocks до
    # одного блока с этим customer_report_block.id. Используется вместе с
    # only_chapter="observation"/"adaptation" для превью одной плитки.
    only_block_id: int | None = None

    @field_validator("obs_from", "obs_to", "adapt_from", "adapt_to", mode="before")
    @classmethod
    def _parse_date_str(cls, v):
        """Parse date string without time (e.g. '2026-02-02') as datetime."""
        if v is None:
            return v
        if isinstance(v, datetime):
            return v
        if isinstance(v, date):
            return datetime.combine(v, datetime.min.time())
        if isinstance(v, str):
            # Try ISO datetime first
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
                try:
                    return datetime.strptime(v, fmt)
                except ValueError:
                    continue
        return v  # Let Pydantic raise validation error

    @field_validator("optimal_from", "optimal_to", mode="before")
    @classmethod
    def _parse_optional_date_str(cls, v):
        """Parse optional date string without time as datetime."""
        if v is None:
            return v
        if isinstance(v, datetime):
            return v
        if isinstance(v, date):
            return datetime.combine(v, datetime.min.time())
        if isinstance(v, str):
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
                try:
                    return datetime.strptime(v, fmt)
                except ValueError:
                    continue
        return v


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
        include_customer_chapter=req.include_customer_chapter,
        customer_periods=req.customer_periods,
        dp_threshold=req.dp_threshold,
    )
    if data.get("ok") and req.with_charts:
        _attach_chart_urls(data)
    return _json_safe(data)


# ═══════════════════════════════════════════════════════════════════
#  Customer baseline endpoints (для главы «Анализ исходных данных»)
# ═══════════════════════════════════════════════════════════════════

def _is_admin(request: Request) -> bool:
    return bool(request.session.get("is_admin", False))


@router.get("/baselines")
def api_baselines_list(
    well_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Список baseline-ов для скважины."""
    return {"well_id": well_id, "baselines": bsvc.list_baselines(db, well_id)}


class PeriodAnalysisRequest(BaseModel):
    well_id: int
    period_from: date
    period_to: date
    description: str | None = None
    # Ширина окна для поиска наилучшего подпериода (detect_optimal_windows).
    # По умолчанию 3 сут.; 0 или None — модуль best_window не считается.
    window_days: int = 3


@router.post("/source-analysis")
def api_source_analysis(
    req: PeriodAnalysisRequest,
    db: Session = Depends(get_db),
):
    """Полный анализ периода по данным заказчика (well_daily) + доп. блоки
    по нашим данным (pressure_raw/events): измерения по датчикам, продувки,
    вбросы реагента, наиболее эффективное окно.
    """
    well = db.query(_Well).filter(_Well.id == req.well_id).first()
    if not well:
        raise HTTPException(404, "Скважина не найдена")
    data = bsvc.compute_period_analysis(
        db, str(well.number), req.period_from, req.period_to, req.description,
        well_id=req.well_id,
        window_days=req.window_days,
    )
    # Добавим сравнение с baseline-ами
    baselines = bsvc.list_baselines(db, req.well_id)
    data["baselines_comparison"] = bsvc.compare_to_baselines(data, baselines)
    data["well"] = {"id": well.id, "number": str(well.number), "name": well.name}
    return _json_safe(data)


class BaselineCreateRequest(BaseModel):
    well_id: int
    name: str
    period_from: date
    period_to: date
    source: str = "customer"
    notes: str | None = None
    is_pinned: bool = False
    precomputed_stats: dict | None = None  # для source='observation'


@router.post("/baselines")
def api_baseline_create(
    req: BaselineCreateRequest,
    request: Request,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Создать baseline (только админ)."""
    if not _is_admin(request):
        raise HTTPException(403, "Сохранение baseline доступно только администратору")
    try:
        bl = bsvc.save_baseline(
            db,
            well_id=req.well_id,
            name=req.name.strip() or "Базовый",
            period_from=req.period_from,
            period_to=req.period_to,
            source=req.source,
            notes=req.notes,
            created_by=current_user,
            is_pinned=req.is_pinned,
            precomputed_stats=req.precomputed_stats,
        )
        return {"ok": True, "baseline": bl}
    except ValueError as e:
        raise HTTPException(400, str(e))


class ObservationBaselineRequest(BaseModel):
    well_id: int
    obs_from: datetime
    obs_to: datetime
    name: str = "Этап наблюдения"
    notes: str | None = None
    is_pinned: bool = True


@router.post("/baselines/observation")
def api_baseline_observation(
    req: ObservationBaselineRequest,
    request: Request,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Зафиксировать baseline по этапу наблюдения (НАШИ данные через
    pressure_raw → flow_rate pipeline). Источник = 'observation'.

    В отличие от /baselines (source='customer' по сводке заказчика),
    этот baseline считается из реальных датчиков LoRa за период наблюдения.
    """
    if not _is_admin(request):
        raise HTTPException(403, "Сохранение baseline доступно только администратору")
    try:
        bl = bsvc.save_observation_baseline(
            db,
            well_id=req.well_id,
            name=req.name.strip() or "Этап наблюдения",
            obs_from=req.obs_from,
            obs_to=req.obs_to,
            notes=req.notes,
            created_by=current_user,
            is_pinned=req.is_pinned,
        )
        return {"ok": True, "baseline": bl}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class BaselineUpdateRequest(BaseModel):
    name: str | None = None
    notes: str | None = None
    is_pinned: bool | None = None


@router.patch("/baselines/{baseline_id}")
def api_baseline_update(
    baseline_id: int,
    req: BaselineUpdateRequest,
    request: Request,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Обновить метаданные baseline (только админ)."""
    if not _is_admin(request):
        raise HTTPException(403, "Изменение baseline доступно только администратору")
    bl = bsvc.update_baseline(
        db, baseline_id,
        name=req.name, notes=req.notes, is_pinned=req.is_pinned,
    )
    if not bl:
        raise HTTPException(404, "Baseline не найден")
    return {"ok": True, "baseline": bl}


@router.delete("/baselines/{baseline_id}")
def api_baseline_delete(
    baseline_id: int,
    request: Request,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Удалить baseline (только админ)."""
    if not _is_admin(request):
        raise HTTPException(403, "Удаление baseline доступно только администратору")
    if not bsvc.delete_baseline(db, baseline_id):
        raise HTTPException(404, "Baseline не найден")
    return {"ok": True}


class ResolvePeriodRequest(BaseModel):
    anchor: date
    preset: str  # week | month | calendar | custom
    n_days: int | None = None
    direction: str = "before"  # before | after
    include_anchor: bool = False


@router.get("/our-segment")
def api_our_segment(
    well_id: int = Query(...),
    date_from: date = Query(...),
    date_to: date = Query(...),
    db: Session = Depends(get_db),
):
    """Наши данные после масок (pressure_raw → clean → masks) за произвольный период.

    Используется в шаге 4 для наложения наших данных на выбранный участок
    данных заказчика.

    Возвращает chart_data (массив часовых точек) + сводные статистики
    (Q ср/мед/cum, ΔP мед, P_tube/P_line мед, working/downtime hours).
    """
    well = db.query(_Well).filter(_Well.id == well_id).first()
    if not well:
        raise HTTPException(404, "Скважина не найдена")
    if date_from > date_to:
        raise HTTPException(400, "date_from > date_to")
    # Штуцер: читаем из well_construction по номеру скважины (как в compute_monthly_stats),
    # т.к. у Well нет поля choke_mm — оно в well_construction.choke_diam_mm
    try:
        row = db.execute(text("""
            SELECT choke_diam_mm FROM well_construction
            WHERE well_no = :wno
            ORDER BY data_as_of DESC NULLS LAST LIMIT 1
        """), {"wno": str(well.number)}).fetchone()
        choke_mm = float(row[0]) if row and row[0] else None
        if choke_mm is not None and choke_mm <= 0:
            choke_mm = None
    except Exception:
        choke_mm = None
    stats = collect_stage_stats(
        db, well, choke_mm, date_from, date_to,
        render_charts=False, chart_tag="",
    )
    cd = stats.get("chart_data") or []
    return {
        "ok": True,
        "well_id": well_id,
        "well_number": str(well.number),
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "duration_days": stats.get("duration_days"),
        "duration_label": stats.get("duration_label"),
        "chart_data": cd,
        "events_for_chart": stats.get("events_for_chart") or [],
        "stats": {
            "p_tube_median": stats.get("p_tube_median"),
            "p_line_median": stats.get("p_line_median"),
            "dp_median":     stats.get("dp_median"),
            "flow_median":   stats.get("flow_median"),
            "flow_avg":      stats.get("flow_avg"),
            "flow_cumulative": stats.get("flow_cumulative"),
            "hours_with_data": stats.get("hours_with_data"),
            "working_hours":   stats.get("working_hours"),
            "downtime_hours":  stats.get("downtime_hours"),
            "utilization_pct": stats.get("utilization_pct"),
            "purge_count":     stats.get("purge_count"),
        },
    }


@router.post("/resolve-period")
def api_resolve_period(req: ResolvePeriodRequest):
    """Разрешить (anchor, preset) → (period_from, period_to). Чистая функция."""
    try:
        pf, pt = bsvc.resolve_period(
            req.anchor, req.preset,
            n_days=req.n_days,
            direction=req.direction,
            include_anchor=req.include_anchor,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"period_from": pf.isoformat(), "period_to": pt.isoformat(),
            "days": (pt - pf).days + 1}


@router.get("/monthly-stats")
def get_monthly_stats(
    well_id: int = Query(...),
    months_back: int = Query(24, description="Глубина истории в месяцах"),
    db: Session = Depends(get_db),
):
    """Помесячная статистика скважины (Q mean/median/min/max, тренд, события)."""
    months = compute_monthly_stats(db, well_id, months_back=months_back)
    return {"well_id": well_id, "months": _json_safe(months)}


@router.get("/timeline")
def api_timeline(
    well_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Временная шкала скважины для конструктора периода на вкладке «Исходные данные».

    Возвращает диапазоны и опорные точки, нужные UI для отрисовки таймлайна:
    - customer_data: первый/последний день в well_daily + кол-во дней
    - our_data: первый/последний час в pressure_hourly
    - stages: все записи well_status (наблюдение / адаптация / оптимизация / ...) с датами и цветами
    - today: текущая дата (анкер «сегодня»)
    """
    well = db.query(_Well).filter(_Well.id == well_id).first()
    if not well:
        raise HTTPException(404, "Скважина не найдена")

    # Берём well.number через прямой SQL — не через ORM. ORM может вернуть
    # Decimal('30') или 30.0; events.well/well_daily.well — VARCHAR '30'.
    # str(Decimal('30')) даёт '30' но Numeric может давать '30.00' → не совпадёт.
    wn_row = db.execute(text(
        "SELECT number FROM wells WHERE id = :wid"
    ), {"wid": well_id}).fetchone()
    well_number_raw = wn_row[0] if wn_row else well.number
    # Нормализация: '30.0' → '30', '30.00' → '30'
    try:
        wn_str = str(int(float(well_number_raw)))
    except Exception:
        wn_str = str(well_number_raw).strip()

    errors: list[dict] = []

    def _rollback_on_error(step: str, exc: Exception) -> None:
        """Каждое исключение делает транзакцию aborted в Postgres —
        rollback нужен ДО любого следующего запроса, иначе каскад из
        InFailedSqlTransaction. Все sub-queries здесь независимы.
        """
        log.exception("timeline: %s failed", step)
        errors.append({"step": step, "error": str(exc)})
        try:
            db.rollback()
        except Exception:
            pass

    # Диапазон well_daily (данные заказчика)
    customer = {"first_date": None, "last_date": None, "days_count": 0}
    try:
        from backend.services import customer_daily_service as csvc
        csvc.ensure_table(db)
        row = db.execute(text("""
            SELECT MIN(date), MAX(date), COUNT(*)
            FROM well_daily WHERE well = :wn
        """), {"wn": wn_str}).fetchone()
        if row and row[0]:
            customer = {
                "first_date": row[0].isoformat(),
                "last_date": row[1].isoformat(),
                "days_count": int(row[2] or 0),
            }
    except Exception as e:
        _rollback_on_error("well_daily", e)

    # Диапазон pressure_raw (наши датчики). Используем pressure_raw, а не
    # pressure_hourly — потому что pressure_hourly это агрегаты, которые
    # могут быть пустыми, даже если raw полный. Все рабочие модули
    # (daily_report, adaptation, flow_rate) читают из pressure_raw.
    our = {"first_date": None, "last_date": None, "rows": 0}
    try:
        from backend.services.adaptation_report_service import KUNGRAD_OFFSET
        row = db.execute(text("""
            SELECT MIN(measured_at), MAX(measured_at), COUNT(*)
            FROM pressure_raw WHERE well_id = :wid
        """), {"wid": well_id}).fetchone()
        if row and row[0]:
            # measured_at — UTC; для отображения переводим в локальное Asia/Tashkent
            our = {
                "first_date": (row[0] + KUNGRAD_OFFSET).date().isoformat(),
                "last_date":  (row[1] + KUNGRAD_OFFSET).date().isoformat(),
                "rows":       int(row[2] or 0),
            }
    except Exception as e:
        _rollback_on_error("pressure_raw", e)

    # Этапы из well_status (все записи, не только последние).
    # Пробуем сначала с AT TIME ZONE (если dt_start TIMESTAMPTZ), при ошибке
    # fallback на простой SELECT (если поле уже timestamp without tz).
    stages: list[dict] = []
    try:
        try:
            rows = db.execute(text("""
                SELECT status,
                       (dt_start AT TIME ZONE 'Asia/Tashkent')::timestamp,
                       (dt_end   AT TIME ZONE 'Asia/Tashkent')::timestamp,
                       note
                FROM well_status
                WHERE well_id = :wid
                ORDER BY dt_start
            """), {"wid": well_id}).fetchall()
        except Exception as e_tz:
            log.warning("timeline well_status tz cast failed, fallback: %s", e_tz)
            try:
                db.rollback()
            except Exception:
                pass
            rows = db.execute(text("""
                SELECT status, dt_start, dt_end, note
                FROM well_status
                WHERE well_id = :wid
                ORDER BY dt_start
            """), {"wid": well_id}).fetchall()
        for r in rows:
            label = r[0]
            color = STATUS_BY_LABEL.get(label, {}).get("color") or "#6b7280"
            stages.append({
                "label": label,
                "dt_start": r[1].isoformat() if r[1] else None,
                "dt_end":   r[2].isoformat() if r[2] else None,
                "color":    color,
                "note":     r[3] or "",
                "source":   "status",
            })
    except Exception as e:
        _rollback_on_error("well_status", e)

    # Ключевые события из events (для отображения как маркеры на шкале)
    events: list[dict] = []
    equip_first = None
    reagent_first = None
    try:
        # Установка оборудования (первый equip)
        row = db.execute(text("""
            SELECT MIN(event_time) FROM events
            WHERE well = :wno AND event_type = 'equip'
        """), {"wno": wn_str}).fetchone()
        if row and row[0]:
            equip_first = row[0]
            events.append({
                "kind":  "equip",
                "label": "Установка оборудования",
                "dt":    equip_first.isoformat() if hasattr(equip_first, 'isoformat') else str(equip_first),
                "color": "#0288d1",
                "icon":  "🔧",
            })
        # Первый вброс реагента
        row = db.execute(text("""
            SELECT MIN(event_time) FROM events
            WHERE well = :wno AND event_type = 'reagent'
        """), {"wno": wn_str}).fetchone()
        if row and row[0]:
            reagent_first = row[0]
            events.append({
                "kind":  "reagent_first",
                "label": "Первый вброс",
                "dt":    reagent_first.isoformat() if hasattr(reagent_first, 'isoformat') else str(reagent_first),
                "color": "#2e7d32",
                "icon":  "💧",
            })
    except Exception as e:
        _rollback_on_error("events", e)

    # Виртуальные этапы из событий — если в well_status пусто.
    # Логика как в suggest_stages_from_events (упрощённая):
    #   Наблюдение: equip_first .. (reagent_first - 1 день)
    #   Адаптация:  reagent_first .. today
    if not stages and equip_first:
        if reagent_first:
            obs_to = reagent_first
            obs_color = STATUS_BY_LABEL.get("Наблюдение", {}).get("color") or "#1565c0"
            stages.append({
                "label":    "Наблюдение (по событиям)",
                "dt_start": equip_first.isoformat() if hasattr(equip_first, 'isoformat') else str(equip_first),
                "dt_end":   obs_to.isoformat() if hasattr(obs_to, 'isoformat') else str(obs_to),
                "color":    obs_color,
                "note":     "Виртуальный этап: well_status пуст, восстановлено из events",
                "source":   "events",
            })
            adapt_color = STATUS_BY_LABEL.get("Адаптация", {}).get("color") or "#f57c00"
            stages.append({
                "label":    "Адаптация (по событиям)",
                "dt_start": reagent_first.isoformat() if hasattr(reagent_first, 'isoformat') else str(reagent_first),
                "dt_end":   None,
                "color":    adapt_color,
                "note":     "Виртуальный этап: well_status пуст, восстановлено из events",
                "source":   "events",
            })
        else:
            obs_color = STATUS_BY_LABEL.get("Наблюдение", {}).get("color") or "#1565c0"
            stages.append({
                "label":    "Наблюдение (по событиям)",
                "dt_start": equip_first.isoformat() if hasattr(equip_first, 'isoformat') else str(equip_first),
                "dt_end":   None,
                "color":    obs_color,
                "note":     "Виртуальный этап: well_status пуст, события вбросов не найдены",
                "source":   "events",
            })

    return {
        "ok": True,
        "well_id": well_id,
        "well_number": wn_str,
        "well_number_raw": str(well_number_raw),
        "today": date.today().isoformat(),
        "customer_data": customer,
        "our_data": our,
        "stages": stages,
        "events": events,
        "errors": errors,
    }


_SECTION_KEYS = (
    "well_info", "customer_data", "observation",
    "adaptation", "charts_compare", "comparison",
)


def _normalize_sections(
    sections: dict[str, bool] | None,
    only_chapter: str | None = None,
) -> dict[str, bool]:
    """Пользовательский dict → полный dict с дефолтами True для всех 6 глав.

    Помимо 6 канонических chapter-level ключей пропускает любые ATOM-ключи
    (`well_summary_table`, `obs_combined_chart`, ...), сохраняя их булевы
    значения. Это нужно для атомарной сборки PDF в шаге 7 wizard-а: каждая
    галочка → отдельный ключ → LaTeX-гейт `\\BLOCK{ if include_sections.KEY }`.
    Незаполненные ключи трактуются как True (блок включён по умолчанию).

    Параметр only_chapter (режим превью одной главы): если задан и совпадает
    с одним из канонических ключей — все 6 глав = False, только выбранная True.
    Атомарные ATOM-ключи передаются как есть (нужны для гейтов внутри главы).
    """
    src = sections or {}
    if only_chapter and only_chapter in _SECTION_KEYS:
        out: dict[str, bool] = {k: False for k in _SECTION_KEYS}
        out[only_chapter] = True
        # ATOM-флаги (например, gating подразделов §3) пропускаем как есть —
        # чтобы превью главы повторяло те же подгалочки, что и финальный PDF.
        for k, v in src.items():
            if k not in out:
                out[k] = bool(v)
        return out
    out = {k: bool(src.get(k, True)) for k in _SECTION_KEYS}
    # Дополнительно: пропускаем все остальные ключи, не входящие в дефолтную
    # 6-ку — это атомарные блок-флаги от UI шага 7. Не валидируем имена, чтобы
    # каталог можно было расширять только в JS + LaTeX без правок здесь.
    for k, v in src.items():
        if k not in out:
            out[k] = bool(v)
    return out


def _build_pdf_response(
    db, well_id: int, *,
    obs_from=None, obs_to=None, adapt_from=None, adapt_to=None,
    obs_description=None, adapt_description=None,
    optimal_mode="auto", optimal_window_days=3,
    optimal_from=None, optimal_to=None,
    include_customer_chapter=False, customer_periods=None,
    sections=None,
    with_charts: bool = True,
    with_reagent: bool = True,
    only_chapter: str | None = None,
    only_block_id: int | None = None,
):
    """Общая реализация генерации preview-PDF.

    Режимы превью:
      • only_chapter — компилировать ТОЛЬКО одну главу из 6 канонических.
        Через include_sections все остальные ставятся False, шаблон их
        пропустит. xelatex всё равно проходит весь файл, но контента
        других глав в нём нет → секунды вместо десятков секунд.
      • only_block_id — отфильтровать observation_blocks/adaptation_blocks
        до ровно одной плитки. Используется вместе с only_chapter для
        превью отдельного блока на странице наблюдения/адаптации.
    """
    from datetime import datetime as _dt
    from backend.services.daily_report_service import (
        _ensure_dirs, _get_latex_env, _compile_latex, _tex_escape,
    )
    from backend.services.adaptation_report_service import KUNGRAD_OFFSET

    _ensure_dirs()

    # Эфемерный каталог для PNG главы 2 (плитки заказчика, сравнения,
    # розы). Автоматически удаляется на выходе из with — PNG живут только
    # на время одной сборки PDF и не накапливаются в static/generated/temp.
    # См. TZ §0 п.1: «snapshot — единственное хранимое представление,
    # PNG генерируются on-the-fly per request».
    chapter2_tmp = tempfile.TemporaryDirectory(prefix="adapt_ch2_")
    chapter2_dir = Path(chapter2_tmp.name)

    try:
        data = collect_report_data(
            db, well_id,
            obs_from=obs_from, obs_to=obs_to,
            adapt_from=adapt_from, adapt_to=adapt_to,
            render_charts=with_charts,
            include_reagent_effectiveness=with_reagent,
            obs_description_override=obs_description,
            adapt_description_override=adapt_description,
            optimal_mode=optimal_mode,
            optimal_window_days=optimal_window_days,
            optimal_from=optimal_from,
            optimal_to=optimal_to,
            include_customer_chapter=include_customer_chapter,
            customer_periods=customer_periods,
            # Гейты быстрого превью — пропускают тяжёлые расчёты глав, которые
            # не идут в результирующий PDF.
            fast_chapter_only=only_chapter,
            fast_block_only=(only_block_id is not None),
            chart_dir=chapter2_dir,
        )
        if not data.get("ok"):
            raise HTTPException(status_code=400, detail=data.get("error"))
        return _finalize_pdf_response(
            db, data, well_id,
            with_charts=with_charts,
            only_chapter=only_chapter,
            only_block_id=only_block_id,
            sections=sections,
        )
    finally:
        # TemporaryDirectory.cleanup() удаляет все PNG главы 2 разом —
        # неважно, успешно ли скомпилировался PDF или была ошибка.
        try:
            chapter2_tmp.cleanup()
        except Exception:
            log.exception("chapter2 temp cleanup failed: %s", chapter2_dir)


def _finalize_pdf_response(
    db, data, well_id: int, *,
    with_charts: bool, only_chapter: str | None,
    only_block_id: int | None, sections: dict | None,
):
    """Финализация PDF после collect_report_data (вынесено из _build_pdf_response
    чтобы избежать вложенного finally с raise HTTPException).
    """
    from datetime import datetime as _dt
    from backend.services.daily_report_service import (
        _get_latex_env, _compile_latex, _tex_escape,
    )
    from backend.services.adaptation_report_service import KUNGRAD_OFFSET

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

    # Сравнение этапа наблюдения с baseline (для §3) — экранируем имя
    obs_vs_baseline = data.get("obs_vs_baseline")
    if obs_vs_baseline:
        bl = obs_vs_baseline.get("baseline") or {}
        if bl.get("name"):
            bl["name"] = _tex_escape(str(bl["name"]))
        cust = obs_vs_baseline.get("customer") or {}
        if cust.get("baseline_name"):
            cust["baseline_name"] = _tex_escape(str(cust["baseline_name"]))

    # Глава «Анализ исходных данных»
    customer_chapter = data.get("customer_chapter")
    if customer_chapter:
        for p in customer_chapter.get("periods", []):
            if p.get("description"):
                p["description_tex"] = _tex_escape(p["description"])
            else:
                p["description_tex"] = None

            # Динамические тексты (период-сводка, помесячные описания,
            # подписи графиков) формируются Python-кодом и могут содержать
            # %, _, & — экранируем для LaTeX (xelatex).
            if p.get("period_summary"):
                p["period_summary"] = _tex_escape(p["period_summary"])
            for d in (p.get("month_descriptions") or []):
                if d.get("label"):
                    d["label"] = _tex_escape(d["label"])
                if d.get("text"):
                    d["text"] = _tex_escape(d["text"])
            # Помесячные блоки UzKor+UniTool (для §«Развёрнутое описание»).
            # Структура: [{label, uzkor:{text}, unitool:{available, days, lines, compat_text}}].
            for mb in (p.get("month_descriptions_blocks") or []):
                if mb.get("label"):
                    mb["label"] = _tex_escape(mb["label"])
                uz = mb.get("uzkor") or {}
                if uz.get("text"):
                    uz["text"] = _tex_escape(uz["text"])
                ut = mb.get("unitool") or {}
                if ut:
                    ut["lines"] = [_tex_escape(s) for s in (ut.get("lines") or [])]
                    if ut.get("compat_text"):
                        ut["compat_text"] = _tex_escape(ut["compat_text"])
            caps = p.get("chart_captions") or {}
            for _k, _v in list(caps.items()):
                if _v:
                    caps[_k] = _tex_escape(_v)
            # chart_grid_rows: [[{key, path, label, caption}, ...], ...].
            # Эскейпим caption отдельно (label — статический ru-текст без спецсимволов).
            for _row in (p.get("chart_grid_rows") or []):
                for _ch in _row:
                    if _ch.get("caption"):
                        _ch["caption"] = _tex_escape(_ch["caption"])

    # Прикреплённые блоки для §3 (наблюдение) и §4 (адаптация)
    from backend.services.adaptation_report_service import (
        _format_observation_block, _format_adaptation_block,
        _format_segment_analysis_for_observation,
        _format_segment_comparison_for_observation,
    )

    obs_blocks_raw = data.get("observation_blocks") or []
    adapt_blocks_raw = data.get("adaptation_blocks") or []

    # Превью одного блока: фильтруем до ровно одного по customer_report_block.id.
    # only_block_id может ссылаться как на observation_, так и на adaptation_-блок.
    if only_block_id is not None:
        obs_blocks_raw = [
            b for b in obs_blocks_raw if b.get("block_id") == only_block_id
        ]
        adapt_blocks_raw = [
            b for b in adapt_blocks_raw if b.get("block_id") == only_block_id
        ]

    # B2-блоки (is_b2=true) исключаем из §3.4 — они уже показаны в §3.3 (b2_tile).
    # Исключение 1: превью конкретного блока (only_block_id) — тогда показываем.
    # Исключение 2: если b2_tile=None (baseline не зафиксирован в БД), НЕ фильтруем —
    #               иначе B2-блок не появится нигде (ни в §3.3, ни в §3.4).
    def _is_b2_block(b: dict) -> bool:
        snap = b.get("data_snapshot") or {}
        return snap.get("is_b2", False)

    obs_blocks_for_pdf = obs_blocks_raw
    has_b2_tile = data.get("b2_tile") is not None
    if only_block_id is None and has_b2_tile:
        # Полный отчёт + есть b2_tile: исключаем B2-блоки (они в §3.3)
        obs_blocks_for_pdf = [b for b in obs_blocks_raw if not _is_b2_block(b)]
    # Иначе: показываем все блоки включая B2 (fallback если b2_tile не создан)

    # Выбор форматтера по kind блока
    def _format_obs_block(b: dict) -> dict:
        kind = b.get("kind")
        if kind == "segment_analysis":
            return _format_segment_analysis_for_observation(b, render_charts=with_charts)
        if kind == "segment_comparison":
            return _format_segment_comparison_for_observation(b, render_charts=with_charts)
        return _format_observation_block(b, render_charts=with_charts)

    observation_blocks_fmt = [_format_obs_block(b) for b in obs_blocks_for_pdf]
    adaptation_blocks_fmt = [
        _format_adaptation_block(b, render_charts=with_charts, db=db)
        for b in adapt_blocks_raw
    ]

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
        "adapt_vs_baseline": data.get("adapt_vs_baseline"),
        "obs_vs_baseline": data.get("obs_vs_baseline"),
        "conclusions": [_tex_escape(c) for c in (data.get("conclusions") or [])],
        "warnings": [_tex_escape(w) for w in (data.get("warnings") or [])],
        "customer_chapter": customer_chapter,
        "observation_blocks": observation_blocks_fmt,
        "adaptation_blocks": adaptation_blocks_fmt,
        "reagent_effectiveness": data.get("reagent_effectiveness"),
        # Плитки утверждённых baseline (для §2 / §3 по ТЗ)
        "b2_tile": data.get("b2_tile"),
        "b1_tile": data.get("b1_tile"),
        # observation_chapter_latex убран — observation-блоки рендерятся через observation_blocks в §3.4
        "include_sections": _normalize_sections(
            {**(sections or {}),
             # ATOM-флаг для шаблона: при превью одного блока скрыть все
             # подразделы главы кроме §3.4 / §4.7+ (Прикреплённые блоки).
             **({"only_attached_blocks": True} if only_block_id is not None else {})},
            only_chapter=only_chapter,
        ),
    }

    env = _get_latex_env()
    template = env.get_template("adaptation_report.tex")
    latex_source = template.render(**context)

    # Имя файла различает превью главы / блока / полного отчёта — чтобы
    # параллельные превью не затирали друг друга в TEMP_DIR.
    if only_block_id is not None:
        base_name = f"adaptation_preview_{well_id}_block_{only_block_id}"
    elif only_chapter:
        base_name = f"adaptation_preview_{well_id}_chap_{only_chapter}"
    else:
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
    # PNG карты §1
    map_path = (well_ctx.get("map_chart_path") if isinstance(well_ctx, dict) else None)
    if map_path:
        Path(map_path).unlink(missing_ok=True)
    # PNG главы 2 (плитки заказчика, сравнения, розы) — НЕ чистим вручную:
    # они лежат в эфемерном TemporaryDirectory, который удаляется внешним
    # try/finally в _build_pdf_response.
    # PNG сравнения участков в §4.7 (adaptation_comparison)
    for _ab in adaptation_blocks_fmt:
        _cp = _ab.get("chart_path")
        if _cp:
            Path(_cp).unlink(missing_ok=True)
    # PNG графиков §3.4 (observation_analysis): 3 PNG на каждый блок —
    # давления, ΔP, Q. Рендерятся live через render_baseline_tile_charts.
    # Для segment_analysis — PNG из render_segment_analysis (в _chart_paths).
    for _ob in observation_blocks_fmt:
        for _key in ("chart_pressures", "chart_dp", "chart_flow"):
            _p = _ob.get(_key)
            if _p:
                Path(_p).unlink(missing_ok=True)
        # segment_analysis: cleanup PNG графиков из render_segment_analysis
        for _cp in (_ob.get("_chart_paths") or []):
            if _cp:
                Path(_cp).unlink(missing_ok=True)

    response = FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"adaptation_well_{well_id}.pdf",
    )
    response.headers["Content-Disposition"] = (
        f'inline; filename="adaptation_well_{well_id}.pdf"'
    )
    return response


@router.get("/preview-pdf")
def preview_pdf_get(
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
    """GET-вариант preview-PDF (без главы заказчика)."""
    return _build_pdf_response(
        db, well_id,
        obs_from=obs_from, obs_to=obs_to,
        adapt_from=adapt_from, adapt_to=adapt_to,
        obs_description=obs_description, adapt_description=adapt_description,
        optimal_mode=optimal_mode, optimal_window_days=optimal_window_days,
        optimal_from=optimal_from, optimal_to=optimal_to,
    )


@router.post("/preview-pdf")
def preview_pdf_post(
    req: CustomDatesRequest,
    db: Session = Depends(get_db),
):
    """POST-вариант preview-PDF (с поддержкой главы «Анализ исходных данных»)."""
    return _build_pdf_response(
        db, req.well_id,
        obs_from=req.obs_from, obs_to=req.obs_to,
        adapt_from=req.adapt_from, adapt_to=req.adapt_to,
        obs_description=req.obs_description,
        adapt_description=req.adapt_description,
        optimal_mode=req.optimal_mode,
        optimal_window_days=req.optimal_window_days,
        optimal_from=req.optimal_from,
        optimal_to=req.optimal_to,
        include_customer_chapter=req.include_customer_chapter,
        customer_periods=req.customer_periods,
        sections=req.sections,
        with_charts=req.with_charts,
        with_reagent=req.with_reagent,
        only_chapter=req.only_chapter,
        only_block_id=req.only_block_id,
    )


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
    """Страница настройки и просмотра отчёта об адаптации (старая версия)."""
    return templates.TemplateResponse(
        "adaptation_report.html",
        {
            "request": request,
            "current_user": current_user,
            "is_admin": request.session.get("is_admin", False),
        },
    )


@pages_router.get("/adaptation-report/wizard", response_class=HTMLResponse)
def adaptation_wizard_page(
    request: Request,
    current_user: str = Depends(get_current_user),
):
    """Новый мастер сборки отчёта (параллельный flow, не трогает старую страницу)."""
    return templates.TemplateResponse(
        "adaptation_wizard.html",
        {
            "request": request,
            "current_user": current_user,
            "is_admin": request.session.get("is_admin", False),
        },
    )
