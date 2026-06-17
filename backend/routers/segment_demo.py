"""Роутер для тестирования сегментного анализа временных рядов.

Демо-страница позволяет визуально работать с модулем анализа:
- Выбор скважины и периода
- Запуск анализа давления и дебита (данные с LoRa датчиков)
- Интерактивные графики Plotly
- Настройка порогов

Эндпоинты:
    GET  /segment-demo               — HTML-страница демо
    POST /api/segment-demo/analyze   — запуск анализа
    GET  /api/segment-demo/wells     — список скважин с данными давления
"""
from __future__ import annotations

import logging
import time as time_module
from datetime import date, datetime, timedelta
from typing import Optional, List

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.db import SessionLocal, engine as pg_engine
from backend.deps import get_current_user
from backend.services.timeseries_analyzer import (
    AnalyzerConfig,
    analyze_timeseries,
    SEGMENT_TYPE_LABELS,
    SEGMENT_TYPE_COLORS,
    format_segment_description,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/segment-demo", tags=["segment-demo"])
pages_router = APIRouter(tags=["segment-demo-pages"])

templates = Jinja2Templates(directory="backend/templates")
templates.env.globals["time"] = lambda: int(time_module.time())


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════════════════
#  Pydantic schemas
# ═══════════════════════════════════════════════════════════════════════

class AnalyzeRequest(BaseModel):
    """Запрос на анализ временного ряда."""
    well_id: int                  # ID скважины из таблицы wells
    date_from: str                # YYYY-MM-DD
    date_to: str                  # YYYY-MM-DD
    primary_series: str = "flow_rate"  # какой ряд анализировать
    # Опциональные настройки порогов
    changepoint_threshold_pct: Optional[float] = None
    min_segment_days: Optional[int] = None
    change_notable_pct: Optional[float] = None


class AnalyzeResponse(BaseModel):
    """Ответ с результатом анализа."""
    ok: bool
    well_id: int
    well_number: str
    date_from: str
    date_to: str
    n_points: int
    primary_series: str

    # Результаты анализа
    changepoints: List[int]
    segments: List[dict]
    anomaly_clusters: List[dict]

    # Данные для графиков
    chart_data: dict

    # Метки и цвета
    type_labels: dict
    type_colors: dict

    # Описания сегментов
    descriptions: List[str]

    # Использованная конфигурация
    config_used: dict


# ═══════════════════════════════════════════════════════════════════════
#  HTML-страница
# ═══════════════════════════════════════════════════════════════════════

def _fetch_wells_sync():
    """Синхронная загрузка списка скважин."""
    query = text("""
        SELECT w.id, w.number, w.name,
               MIN(pr.measured_at)::date as date_min,
               MAX(pr.measured_at)::date as date_max,
               COUNT(DISTINCT pr.measured_at::date) as n_days
        FROM wells w
        INNER JOIN pressure_raw pr ON pr.well_id = w.id
        WHERE pr.measured_at IS NOT NULL
        GROUP BY w.id, w.number, w.name
        HAVING COUNT(*) > 100
        ORDER BY w.number
    """)
    with pg_engine.connect() as conn:
        rows = conn.execute(query).fetchall()
    return [
        {"id": r[0], "number": r[1], "name": r[2] or "",
         "date_min": str(r[3]) if r[3] else None,
         "date_max": str(r[4]) if r[4] else None,
         "n_days": r[5] or 0}
        for r in rows
    ]


def _parse_date_flexible(date_str: str) -> date:
    """Парсинг даты в различных форматах: YYYY-MM-DD, YYYY-MM-DDTHH:MM, YYYY-MM-DD HH:MM."""
    if not date_str:
        raise ValueError("Пустая дата")
    # Убираем время (после T или пробела)
    date_part = date_str.split('T')[0].split(' ')[0].strip()
    return datetime.strptime(date_part, "%Y-%m-%d").date()


@pages_router.get("/segment-analysis", response_class=HTMLResponse)
def segment_analysis_page(
    request: Request,
    well_id: int,
    date_from: str,
    date_to: str,
    series: str = "flow_rate",
    sensitivity: int = 5,
):
    """Страница результатов анализа — всё рендерится на сервере."""
    from backend.services.flow_rate.full_pipeline import compute_full_flow
    from backend.services.flow_rate.data_access import get_well_info

    try:
        # Парсинг дат (поддержка разных форматов)
        try:
            d_from = _parse_date_flexible(date_from)
            d_to = _parse_date_flexible(date_to)
        except Exception as e:
            log.error(f"Date parse error: date_from={date_from!r}, date_to={date_to!r}, error={e}")
            return HTMLResponse(f"<h1>Ошибка формата даты: {e}</h1><p>date_from={date_from}, date_to={date_to}</p>", status_code=400)

        # Информация о скважине
        well_info = get_well_info(well_id)
        if not well_info:
            return HTMLResponse(f"<h1>Скважина {well_id} не найдена</h1>", status_code=404)

        well_number = str(well_info.get("number", well_id))

        # UTC времена для запроса
        d_from_dt = datetime.combine(d_from, datetime.min.time())
        d_to_dt = datetime.combine(d_to, datetime.min.time())
        utc_start = (d_from_dt - timedelta(hours=5)).isoformat()
        utc_end = (d_to_dt + timedelta(days=1) - timedelta(hours=5)).isoformat()

        try:
            full = compute_full_flow(well_id=well_id, dt_start=utc_start, dt_end=utc_end, smooth=True)
            df_raw = full["df"]
        except Exception as e:
            log.exception(f"compute_full_flow error for well {well_id}")
            return HTMLResponse(f"<h1>Ошибка загрузки данных: {e}</h1>", status_code=500)

        if df_raw.empty:
            return HTMLResponse(f"<h1>Нет данных за период {date_from} — {date_to}</h1>", status_code=404)

        # Агрегация в часовые
        df_daily = _aggregate_to_hourly(df_raw)

        # Маппинг
        col_map = {"flow_rate": "flow_rate_mean", "p_tube": "p_tube_mean", "p_line": "p_line_mean", "dp": "dp_mean"}
        series_labels = {"flow_rate": "Q дебит (тыс.м³/сут)", "p_tube": "P устьевое (кгс/см²)", "p_line": "P линии (кгс/см²)", "dp": "ΔP (кгс/см²)"}

        primary_col = col_map.get(series, series)
        series_label = series_labels.get(series, series)

        # Анализ с учётом чувствительности
        config = AnalyzerConfig(sensitivity=max(1, min(10, sensitivity)))
        config = config.apply_sensitivity()

        # Вторичные ряды для расчёта secondary_means (P_шлейф, P_устье, ΔP, простой)
        secondary_cols = []
        for col in ["flow_rate_mean", "p_tube_mean", "p_line_mean", "dp_mean", "downtime_min"]:
            if col in df_daily.columns and col != primary_col:
                secondary_cols.append(col)

        result = analyze_timeseries(
            df=df_daily,
            primary_column=primary_col,
            date_column="date",
            config=config,
            secondary_columns=secondary_cols,
        )

        if not result.ok:
            return HTMLResponse("<h1>Ошибка анализа</h1>", status_code=500)

        # Обогащаем сегменты полями давления и working_pct из secondary_means
        def enrich_segment_page(s):
            d = s.to_dict()
            sm = d.get("secondary_means") or {}
            # Маппинг: p_line_mean → mean_p_flowline, p_tube_mean → mean_p_wellhead
            d["mean_p_flowline"] = sm.get("p_line_mean")
            d["mean_p_wellhead"] = sm.get("p_tube_mean")
            # Если primary_series=dp, mean_dp берём из mean_value (primary column)
            if series == "dp":
                d["mean_dp"] = d.get("mean_value")
            else:
                d["mean_dp"] = sm.get("dp_mean")
            # working_pct = (1440 - downtime_min) / 1440 * 100
            downtime = sm.get("downtime_min")
            if downtime is not None:
                d["mean_shutdown"] = downtime
                d["working_pct"] = max(0, (1440 - downtime) / 1440 * 100)
            return d

        segments_enriched = [enrich_segment_page(s) for s in result.segments]

        # Описания
        unit_map = {"flow_rate": "тыс.м³/сут", "p_tube": "кгс/см²", "p_line": "кгс/см²", "dp": "кгс/см²"}
        descriptions = [format_segment_description(seg, unit_map.get(series, "")) for seg in result.segments]

        return templates.TemplateResponse("segment_result.html", {
            "request": request,
            "well_id": well_id,
            "well_number": well_number,
            "date_from": date_from,
            "date_to": date_to,
            "series": series,
            "series_label": series_label,
            "n_points": result.n_points,
            "changepoints": result.changepoints,
            "segments": segments_enriched,
            "descriptions": descriptions,
            "type_labels": SEGMENT_TYPE_LABELS,
            "type_colors": SEGMENT_TYPE_COLORS,
            "chart_data": {"dates": result.dates, "primary": {"name": series_label, "values": result.values}},
            "sensitivity": sensitivity,
        })

    except Exception as e:
        log.exception(f"segment_analysis_page error: well_id={well_id}, date_from={date_from}, date_to={date_to}")
        return HTMLResponse(f"<h1>Внутренняя ошибка</h1><pre>{e}</pre>", status_code=500)


@pages_router.get("/segment-demo", response_class=HTMLResponse)
def segment_demo_page(
    request: Request,
    well_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """Демо-страница сегментного анализа (без авторизации для теста)."""
    import json
    # Загружаем скважины на сервере, передаём в шаблон
    wells = _fetch_wells_sync()
    return templates.TemplateResponse(
        "segment_demo.html",
        {
            "request": request,
            "well_id": well_id or "",
            "date_from": date_from or "",
            "date_to": date_to or "",
            "username": "test",
            "wells_json": json.dumps(wells),
        },
    )


# ═══════════════════════════════════════════════════════════════════════
#  API endpoints
# ═══════════════════════════════════════════════════════════════════════

@router.get("/ping")
def api_ping():
    """Простой тест - без БД."""
    return {"ok": True, "msg": "pong", "time": datetime.now().isoformat()}


@router.get("/wells")
async def api_wells():
    """Список скважин с данными давления (LoRa датчиков)."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def fetch_wells():
        query = text("""
            SELECT
                w.id,
                w.number,
                w.name,
                MIN(pr.measured_at)::date as date_min,
                MAX(pr.measured_at)::date as date_max,
                COUNT(DISTINCT pr.measured_at::date) as n_days
            FROM wells w
            INNER JOIN pressure_raw pr ON pr.well_id = w.id
            WHERE pr.measured_at IS NOT NULL
            GROUP BY w.id, w.number, w.name
            HAVING COUNT(*) > 100
            ORDER BY w.number
        """)
        with pg_engine.connect() as conn:
            result = conn.execute(query)
            return result.fetchall()

    try:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as pool:
            rows = await loop.run_in_executor(pool, fetch_wells)

        wells_list = []
        for row in rows:
            wells_list.append({
                "id": row[0],
                "number": row[1],
                "name": row[2] or "",
                "date_min": str(row[3]) if row[3] else None,
                "date_max": str(row[4]) if row[4] else None,
                "n_days": row[5] or 0,
            })

        return {"ok": True, "wells": wells_list}

    except Exception as e:
        log.exception("wells error")
        return {"ok": False, "error": str(e), "wells": []}


@router.post("/analyze", response_model=AnalyzeResponse)
def api_analyze(
    req: AnalyzeRequest,
    db: Session = Depends(get_db),
):
    """Запуск сегментного анализа на данных скважины (давление + дебит)."""
    try:
        from backend.services.flow_rate.full_pipeline import compute_full_flow
        from backend.services.flow_rate.data_access import get_well_info

        # Парсинг дат (могут приходить как "2026-02-17" или "2026-02-17T00:00")
        d_from = datetime.fromisoformat(req.date_from.split('T')[0])
        d_to = datetime.fromisoformat(req.date_to.split('T')[0])

        # UTC времена для запроса
        utc_start = (d_from - timedelta(hours=5)).isoformat()
        utc_end = (d_to + timedelta(days=1) - timedelta(hours=5)).isoformat()

        # Информация о скважине
        well_info = get_well_info(req.well_id)
        if not well_info:
            raise HTTPException(
                status_code=404,
                detail=f"Скважина с ID {req.well_id} не найдена"
            )
        well_number = str(well_info.get("number", req.well_id))

        # Получаем данные через полный pipeline (как страница адаптации)
        try:
            full = compute_full_flow(
                well_id=req.well_id,
                dt_start=utc_start,
                dt_end=utc_end,
                smooth=True,
            )
        except ValueError as e:
            raise HTTPException(
                status_code=404,
                detail=str(e)
            )

        df_raw = full["df"]  # поминутные данные

        if df_raw.empty:
            raise HTTPException(
                status_code=404,
                detail=f"Нет данных для скважины {well_number} за период {req.date_from} — {req.date_to}"
            )

        # Агрегируем поминутные данные в дневные
        df_daily = _aggregate_to_hourly(df_raw)

        if df_daily.empty:
            raise HTTPException(
                status_code=404,
                detail=f"Нет дневных данных для анализа"
            )

        # Конфигурация анализа
        config = AnalyzerConfig()
        if req.changepoint_threshold_pct is not None:
            config.changepoint_threshold_pct = req.changepoint_threshold_pct
        if req.min_segment_days is not None:
            config.min_segment_days = req.min_segment_days
        if req.change_notable_pct is not None:
            config.change_notable_pct = req.change_notable_pct

        # Маппинг колонок для анализа
        col_map = {
            "flow_rate": "flow_rate_mean",
            "p_tube": "p_tube_mean",
            "p_line": "p_line_mean",
            "dp": "dp_mean",
            "downtime_min": "downtime_min",
        }

        primary_col = col_map.get(req.primary_series, req.primary_series)
        if primary_col not in df_daily.columns:
            raise HTTPException(
                status_code=400,
                detail=f"Колонка {req.primary_series} не найдена в данных"
            )

        # Вторичные ряды для визуализации
        secondary_cols = []
        for col in ["flow_rate_mean", "p_tube_mean", "p_line_mean", "dp_mean", "downtime_min"]:
            if col in df_daily.columns and col != primary_col:
                secondary_cols.append(col)

        # Колонка для детекции аномалий (простои)
        anomaly_col = "downtime_min" if "downtime_min" in df_daily.columns else None

        # Запуск анализа
        result = analyze_timeseries(
            df=df_daily,
            primary_column=primary_col,
            date_column="date",
            config=config,
            secondary_columns=secondary_cols,
            anomaly_column=anomaly_col,
        )

        if not result.ok:
            raise HTTPException(status_code=500, detail="Ошибка анализа данных")

        # Формирование данных для графиков
        chart_data = {
            "dates": result.dates,
            "primary": {
                "name": req.primary_series,
                "values": result.values,
            },
            "secondary": {},
        }

        # Маппинг имён для отображения
        display_names = {
            "flow_rate_mean": "Q (тыс.м³/сут)",
            "p_tube_mean": "P устьевое (кгс/см²)",
            "p_line_mean": "P линии (кгс/см²)",
            "dp_mean": "ΔP (кгс/см²)",
            "downtime_min": "Простой (мин/сут)",
        }

        for col, values in result.secondary_values.items():
            chart_data["secondary"][col] = {
                "name": display_names.get(col, col),
                "values": values,
            }

        # Формирование описаний
        unit_map = {
            "flow_rate": "тыс.м³/сут",
            "p_tube": "кгс/см²",
            "p_line": "кгс/см²",
            "dp": "кгс/см²",
            "downtime_min": "мин/сут",
        }
        unit = unit_map.get(req.primary_series, "")
        descriptions = [
            format_segment_description(seg, unit)
            for seg in result.segments
        ]

        # Обогащаем сегменты полями давления и working_pct из secondary_means
        def enrich_segment(s):
            d = s.to_dict()
            sm = d.get("secondary_means") or {}
            # Маппинг: p_line_mean → mean_p_flowline, p_tube_mean → mean_p_wellhead
            d["mean_p_flowline"] = sm.get("p_line_mean")
            d["mean_p_wellhead"] = sm.get("p_tube_mean")
            # Если primary_series=dp, mean_dp берём из mean_value (primary column)
            if req.primary_series == "dp":
                d["mean_dp"] = d.get("mean_value")
            else:
                d["mean_dp"] = sm.get("dp_mean")
            # working_pct = (1440 - downtime_min) / 1440 * 100
            downtime = sm.get("downtime_min")
            if downtime is not None:
                d["mean_shutdown"] = downtime
                d["working_pct"] = max(0, (1440 - downtime) / 1440 * 100)
            return d

        return AnalyzeResponse(
            ok=True,
            well_id=req.well_id,
            well_number=well_number,
            date_from=req.date_from,
            date_to=req.date_to,
            n_points=result.n_points,
            primary_series=req.primary_series,
            changepoints=result.changepoints,
            segments=[enrich_segment(s) for s in result.segments],
            anomaly_clusters=[c.to_dict() for c in result.anomaly_clusters],
            chart_data=chart_data,
            type_labels=SEGMENT_TYPE_LABELS,
            type_colors=SEGMENT_TYPE_COLORS,
            descriptions=descriptions,
            config_used=result.config_used,
        )

    except HTTPException:
        raise
    except Exception as e:
        log.exception("analyze error")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/series-options")
def api_series_options():
    """Список доступных временных рядов для анализа."""
    return {
        "ok": True,
        "options": [
            {"value": "flow_rate", "label": "Q дебит (тыс.м³/сут)"},
            {"value": "p_tube", "label": "P устьевое (кгс/см²)"},
            {"value": "p_line", "label": "P линии (кгс/см²)"},
            {"value": "dp", "label": "ΔP (кгс/см²)"},
            {"value": "downtime_min", "label": "Простой (мин/сут)"},
        ]
    }


# ═══════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════

def _aggregate_to_hourly(df: pd.DataFrame) -> pd.DataFrame:
    """
    Агрегация поминутных данных в ЧАСОВЫЕ.

    50 дней × 24 часа = 1200 точек (достаточно для детального анализа).
    """
    if df.empty:
        return pd.DataFrame()

    df = df.copy()

    # Вычисляем ΔP если не вычислено
    if "dp" not in df.columns and "p_tube" in df.columns and "p_line" in df.columns:
        df["dp"] = df["p_tube"] - df["p_line"]

    # Группируем по часу
    df["_hour"] = df.index.floor("h")
    groups = df.groupby("_hour")

    records = []
    for hour_ts, group in groups:
        n_points = len(group)

        flow_vals = group["flow_rate"].dropna() if "flow_rate" in group.columns else pd.Series()
        p_tube_vals = group["p_tube"].dropna() if "p_tube" in group.columns else pd.Series()
        p_line_vals = group["p_line"].dropna() if "p_line" in group.columns else pd.Series()
        dp_vals = group["dp"].dropna() if "dp" in group.columns else pd.Series()

        # Простой
        downtime_min = 0
        if "flow_rate" in group.columns:
            downtime_mask = (group["flow_rate"].isna()) | (group["flow_rate"] <= 0)
            downtime_min = int(downtime_mask.sum())

        records.append({
            "date": hour_ts.strftime("%Y-%m-%d %H:%M"),
            "flow_rate_mean": float(flow_vals.mean()) if len(flow_vals) > 0 else 0.0,
            "p_tube_mean": float(p_tube_vals.mean()) if len(p_tube_vals) > 0 else None,
            "p_line_mean": float(p_line_vals.mean()) if len(p_line_vals) > 0 else None,
            "dp_mean": float(dp_vals.mean()) if len(dp_vals) > 0 else None,
            "downtime_min": downtime_min,
            "n_points": n_points,
        })

    return pd.DataFrame(records)


def _aggregate_to_daily(df: pd.DataFrame) -> pd.DataFrame:
    """
    Агрегация поминутных данных в дневные (для совместимости).
    """
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    df["_date"] = df.index.date

    if "dp" not in df.columns and "p_tube" in df.columns and "p_line" in df.columns:
        df["dp"] = df["p_tube"] - df["p_line"]

    groups = df.groupby("_date")
    daily_records = []

    for day, group in groups:
        n_points = len(group)
        flow_vals = group["flow_rate"].dropna() if "flow_rate" in group.columns else pd.Series()
        p_tube_vals = group["p_tube"].dropna() if "p_tube" in group.columns else pd.Series()
        p_line_vals = group["p_line"].dropna() if "p_line" in group.columns else pd.Series()
        dp_vals = group["dp"].dropna() if "dp" in group.columns else pd.Series()

        downtime_min = 0
        if "flow_rate" in group.columns:
            downtime_mask = (group["flow_rate"].isna()) | (group["flow_rate"] <= 0)
            downtime_min = int(downtime_mask.sum())

        daily_records.append({
            "date": str(day),
            "flow_rate_mean": float(flow_vals.mean()) if len(flow_vals) > 0 else 0.0,
            "p_tube_mean": float(p_tube_vals.mean()) if len(p_tube_vals) > 0 else None,
            "p_line_mean": float(p_line_vals.mean()) if len(p_line_vals) > 0 else None,
            "dp_mean": float(dp_vals.mean()) if len(dp_vals) > 0 else None,
            "downtime_min": downtime_min,
            "n_points": n_points,
        })

    return pd.DataFrame(daily_records)


# ═══════════════════════════════════════════════════════════════════════
#  API для ручных корректировок
# ═══════════════════════════════════════════════════════════════════════

class ManualCPRequest(BaseModel):
    """Запрос на добавление/удаление точки перелома."""
    well_id: int
    date_from: str
    date_to: str
    series: str = "flow_rate"
    sensitivity: int = 5
    # Текущие CP (чтобы добавить/удалить)
    changepoints: List[int]
    # Действие: add или remove
    action: str  # "add" | "remove"
    # Индекс для добавления или удаления
    index: int


class RecalcSegmentRequest(BaseModel):
    """Запрос на пересчёт сегмента с параметрами."""
    well_id: int
    date_from: str
    date_to: str
    series: str = "flow_rate"
    # Границы сегмента
    start_idx: int
    end_idx: int
    # Отступы для тренда
    trim_left: int = 0
    trim_right: int = 0
    # Исключённые области [(start, end), ...]
    exclude_ranges: List[List[int]] = []


class UpdateCommentRequest(BaseModel):
    """Запрос на обновление комментария."""
    segment_num: int
    comment: str


@router.post("/manual-cp")
def api_manual_changepoint(req: ManualCPRequest):
    """Добавить или удалить точку перелома вручную."""
    try:
        new_cps = list(req.changepoints)

        if req.action == "add":
            if req.index not in new_cps:
                new_cps.append(req.index)
                new_cps.sort()
        elif req.action == "remove":
            if req.index in new_cps:
                new_cps.remove(req.index)

        # Пересчитываем сегменты с новыми CP
        from backend.services.flow_rate.full_pipeline import compute_full_flow

        d_from = datetime.fromisoformat(req.date_from.split('T')[0])
        d_to = datetime.fromisoformat(req.date_to.split('T')[0])
        utc_start = (d_from - timedelta(hours=5)).isoformat()
        utc_end = (d_to + timedelta(days=1) - timedelta(hours=5)).isoformat()

        full = compute_full_flow(well_id=req.well_id, dt_start=utc_start, dt_end=utc_end, smooth=True)
        df_raw = full["df"]
        df_hourly = _aggregate_to_hourly(df_raw)

        col_map = {"flow_rate": "flow_rate_mean", "p_tube": "p_tube_mean", "p_line": "p_line_mean", "dp": "dp_mean"}
        primary_col = col_map.get(req.series, req.series)

        series = df_hourly[primary_col].values.astype(float)
        dates = df_hourly["date"].astype(str).tolist()

        from backend.services.timeseries_analyzer import compute_segments, AnalyzerConfig

        config = AnalyzerConfig(sensitivity=req.sensitivity).apply_sensitivity()
        segments = compute_segments(series, new_cps, config, dates=dates)

        return {
            "ok": True,
            "changepoints": new_cps,
            "segments": [s.to_dict() for s in segments],
            "dates": dates,
            "values": [float(v) if np.isfinite(v) else None for v in series],
        }

    except Exception as e:
        log.exception("manual-cp error")
        return {"ok": False, "error": str(e)}


@router.post("/recalc-segment")
def api_recalc_segment(req: RecalcSegmentRequest):
    """Пересчитать тренд сегмента с отступами и исключениями."""
    try:
        from backend.services.flow_rate.full_pipeline import compute_full_flow
        from backend.services.timeseries_analyzer import recalculate_segment_trend, Segment

        d_from = datetime.fromisoformat(req.date_from.split('T')[0])
        d_to = datetime.fromisoformat(req.date_to.split('T')[0])
        utc_start = (d_from - timedelta(hours=5)).isoformat()
        utc_end = (d_to + timedelta(days=1) - timedelta(hours=5)).isoformat()

        full = compute_full_flow(well_id=req.well_id, dt_start=utc_start, dt_end=utc_end, smooth=True)
        df_raw = full["df"]
        df_hourly = _aggregate_to_hourly(df_raw)

        col_map = {"flow_rate": "flow_rate_mean", "p_tube": "p_tube_mean", "p_line": "p_line_mean", "dp": "dp_mean"}
        primary_col = col_map.get(req.series, req.series)

        series = df_hourly[primary_col].values.astype(float)

        # Создаём временный сегмент для пересчёта
        segment_data = series[req.start_idx:req.end_idx]
        valid_data = segment_data[np.isfinite(segment_data)]

        temp_segment = Segment(
            num=1,
            start_idx=req.start_idx,
            end_idx=req.end_idx,
            days=req.end_idx - req.start_idx,
            mean_value=float(np.mean(valid_data)) if len(valid_data) > 0 else 0.0,
            std_value=float(np.std(valid_data)) if len(valid_data) > 1 else 0.0,
            min_value=float(np.min(valid_data)) if len(valid_data) > 0 else 0.0,
            max_value=float(np.max(valid_data)) if len(valid_data) > 0 else 0.0,
            slope=0.0, intercept=0.0, r_squared=0.0
        )

        # Преобразуем exclude_ranges
        exclude_tuples = [(r[0], r[1]) for r in req.exclude_ranges if len(r) == 2]

        # Пересчитываем
        updated = recalculate_segment_trend(
            series,
            temp_segment,
            trim_left=req.trim_left,
            trim_right=req.trim_right,
            exclude_ranges=exclude_tuples if exclude_tuples else None
        )

        return {
            "ok": True,
            "segment": updated.to_dict(),
        }

    except Exception as e:
        log.exception("recalc-segment error")
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════
#  API: События скважины для отображения на графике
# ═══════════════════════════════════════════════════════════════════════

@router.get("/events")
def api_get_events(
    well_id: int = Query(...),
    date_from: str = Query(...),
    date_to: str = Query(...),
    db: Session = Depends(get_db)
):
    """Получить события скважины за период для отображения на графике."""
    try:
        from backend.models.events import Event
        from backend.models.wells import Well  # wells.py, не well.py!

        # Получаем ключ скважины для поиска в events.well
        # (так же как в app.py well_page)
        well = db.query(Well).filter(Well.id == well_id).first()
        if not well:
            return {"ok": False, "error": f"Скважина с id={well_id} не найдена в таблице wells"}

        # well_key - строка для фильтрации Event.well (как в app.py)
        if well.number:
            well_key = str(well.number)
        else:
            well_key = str(well.id)

        log.info(f"Events query: well_id={well_id}, well_key={well_key}, date_from={date_from}, date_to={date_to}")

        d_from = datetime.fromisoformat(date_from.split('T')[0])
        d_to = datetime.fromisoformat(date_to.split('T')[0]) + timedelta(days=1)

        # Запрашиваем события (Event.well - это строка well_key)
        events = db.query(Event).filter(
            Event.well == well_key,
            Event.event_time >= d_from,
            Event.event_time < d_to
        ).order_by(Event.event_time).all()

        log.info(f"Found {len(events)} events for well_key={well_key}")

        # Группируем по типам (формат как в app.py well_page)
        reagent_events = []    # timeline_injections
        other_events = []      # timeline_events (включая purge)

        for ev in events:
            et = (ev.event_type or "other").lower().strip()

            if et == "reagent" or ev.reagent:
                # Вброс реагента — формат timeline_injections
                reagent_events.append({
                    "t": ev.event_time.isoformat() if ev.event_time else None,
                    "reagent": ev.reagent,
                    "qty": float(ev.qty or 0.0),
                    "well": ev.well,
                    "description": ev.description or "",
                    "geo_status": ev.geo_status,
                })
            else:
                # Прочие события — формат timeline_events
                other_events.append({
                    "t": ev.event_time.isoformat() if ev.event_time else None,
                    "type": et,
                    "well": ev.well,
                    "reagent": ev.reagent,
                    "qty": float(ev.qty or 0.0) if ev.qty is not None else None,
                    "description": ev.description or "",
                    "p_tube": ev.p_tube,
                    "p_line": ev.p_line,
                    "purge_phase": ev.purge_phase,
                })

        # Подсчитываем purge отдельно для статистики
        purge_count = sum(1 for e in other_events if e.get("type") == "purge" or e.get("purge_phase"))

        return {
            "ok": True,
            "well_id": well_id,
            "well_key": well_key,
            "well_number": well.number,
            "date_from": str(d_from),
            "date_to": str(d_to),
            # Формат как в app.py well_page
            "timeline_injections": reagent_events,  # вбросы реагента
            "timeline_events": other_events,        # прочие события (purge, pressure, etc.)
            "total": len(events),
            "debug": {
                "query_well_key": well_key,
                "reagent_count": len(reagent_events),
                "purge_count": purge_count,
                "other_count": len(other_events),
            }
        }

    except Exception as e:
        log.exception("events fetch error")
        return {"ok": False, "error": str(e)}


@router.get("/events-debug")
def api_events_debug(
    well_id: int = Query(...),
    db: Session = Depends(get_db)
):
    """Диагностика: проверить какие события есть для скважины."""
    try:
        from backend.models.events import Event
        from backend.models.wells import Well

        # 1. Информация о скважине
        well = db.query(Well).filter(Well.id == well_id).first()
        well_info = {
            "id": well.id if well else None,
            "number": well.number if well else None,
            "name": well.name if well else None,
        } if well else None

        if not well:
            return {"ok": False, "error": f"Well id={well_id} not found", "well_info": None}

        # well_key - как в app.py
        if well.number:
            well_key = str(well.number)
        else:
            well_key = str(well.id)

        # 2. Последние 10 событий для этой скважины
        recent_events = db.query(Event).filter(
            Event.well == well_key
        ).order_by(Event.event_time.desc()).limit(10).all()

        recent_list = [{
            "id": e.id,
            "time": e.event_time.isoformat() if e.event_time else None,
            "type": e.event_type,
            "reagent": e.reagent,
            "qty": float(e.qty) if e.qty else None,
        } for e in recent_events]

        # 3. Общее количество событий
        total_count = db.query(Event).filter(Event.well == well_key).count()

        # 4. Количество по типам
        from sqlalchemy import func
        type_counts = db.query(
            Event.event_type, func.count(Event.id)
        ).filter(Event.well == well_key).group_by(Event.event_type).all()

        # 5. Все уникальные well в таблице events (для отладки)
        unique_wells = db.query(Event.well).distinct().limit(20).all()
        unique_wells_list = [w[0] for w in unique_wells]

        return {
            "ok": True,
            "well_info": well_info,
            "well_key": well_key,
            "total_events": total_count,
            "events_by_type": {t: c for t, c in type_counts},
            "recent_events": recent_list,
            "sample_wells_in_events_table": unique_wells_list,
        }

    except Exception as e:
        log.exception("events-debug error")
        return {"ok": False, "error": str(e)}
