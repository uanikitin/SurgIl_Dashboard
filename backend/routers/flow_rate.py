"""
/api/flow-rate/* — API расчёта дебита газа.

Полностью изолирован от остальных роутеров Dashboard.
Подключается в app.py одной строкой:
    app.include_router(flow_rate_router)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Query, HTTPException

router = APIRouter(prefix="/api/flow-rate", tags=["flow-rate"])
log = logging.getLogger(__name__)


def _run_calculation(
    well_id: int,
    dt_start: str,
    dt_end: str,
    smooth: bool = True,
    multiplier: float = 4.1,
    C1: float = 2.919,
    C2: float = 4.654,
    C3: float = 286.95,
    critical_ratio: float = 0.5,
    exclude_periods: str = "",
    dp_threshold: float = 0.1,
) -> dict:
    """
    Тонкая обёртка над `compute_full_flow` — единым источником истины.
    Используется только в этом роутере; для прямого использования из
    других сервисов импортируйте `compute_full_flow` напрямую.

    Возвращает {summary, chart, downtime_periods, purge_cycles, data_points}.
    """
    from backend.services.flow_rate.full_pipeline import (
        compute_full_flow, build_chart_payload, downtime_periods_to_list,
    )

    try:
        result = compute_full_flow(
            well_id, dt_start, dt_end,
            smooth=smooth,
            multiplier=multiplier, C1=C1, C2=C2, C3=C3,
            critical_ratio=critical_ratio,
            exclude_periods=exclude_periods,
            dp_threshold=dp_threshold,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {
        "summary": result["summary"],
        "chart": build_chart_payload(result["df"]),
        "downtime_periods": downtime_periods_to_list(result["downtime_periods"]),
        "purge_cycles": [c.to_dict() for c in result["purge_cycles"]],
        "data_points": result["data_points"],
    }


# ──────────────────── endpoints ────────────────────


@router.get("/calculate/{well_id}")
def api_calculate(
    well_id: int,
    start: Optional[str] = Query(
        None, description="Начало периода ISO: 2025-01-01",
    ),
    end: Optional[str] = Query(
        None, description="Конец периода ISO: 2025-02-01",
    ),
    days: int = Query(30, ge=1, le=365),
    smooth: bool = Query(True, description="Фильтр Савицкого-Голая"),
    multiplier: float = Query(4.1, description="Калибровочный множитель M"),
    C1: float = Query(2.919, description="Коэффициент C1"),
    C2: float = Query(4.654, description="Коэффициент C2"),
    C3: float = Query(286.95, description="Коэффициент C3"),
    critical_ratio: float = Query(0.5, description="Критическое отношение давлений"),
    exclude_periods: str = Query("", description="ID продувок для исключения (через запятую)"),
    dp_threshold: float = Query(0.1, ge=0.0, le=2.0,
        description="Порог простоя ΔP (атм). Точка считается простоем если "
                    "(p_tube - p_line) < dp_threshold ИЛИ purge_flag."),
):
    """
    Полный расчёт дебита: summary + график + продувки + простои.
    Все коэффициенты формулы можно передать через query-параметры.
    """
    if start and end:
        # Фронтенд передаёт время в Кунграде (UTC+5) — конвертируем в UTC
        try:
            kungrad_offset = timedelta(hours=5)
            dt_start = (datetime.fromisoformat(start) - kungrad_offset).isoformat()
            dt_end = (datetime.fromisoformat(end) - kungrad_offset).isoformat()
        except ValueError:
            raise HTTPException(400, "Invalid start/end format. Use ISO: 2025-01-01T08:00:00")
    else:
        dt_end = datetime.utcnow().isoformat()
        dt_start = (datetime.utcnow() - timedelta(days=days)).isoformat()

    return _run_calculation(
        well_id, dt_start, dt_end, smooth,
        multiplier=multiplier, C1=C1, C2=C2, C3=C3,
        critical_ratio=critical_ratio,
        exclude_periods=exclude_periods,
        dp_threshold=dp_threshold,
    )


@router.get("/summary/{well_id}")
def api_summary(
    well_id: int,
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
):
    """Только сводные показатели (без графика). Быстрый endpoint."""
    if start and end:
        dt_start, dt_end = start, end
    else:
        dt_end = datetime.utcnow().isoformat()
        dt_start = (datetime.utcnow() - timedelta(days=days)).isoformat()

    result = _run_calculation(well_id, dt_start, dt_end)
    return result["summary"]


@router.get("/wells")
def api_wells_with_pressure(
    days: int = Query(7, ge=1, le=90),
):
    """
    Список скважин, у которых есть данные давления за последние N дней.
    Без расчёта дебита — только список для UI.
    """
    from backend.services.flow_rate.data_access import list_wells_with_pressure
    return list_wells_with_pressure(days)


# ──────────────────── Segment Analysis ────────────────────


def _linear_trend(values: list, duration_hours: float, threshold=None) -> dict | None:
    """
    Линейная регрессия Y(t) = a + b*t.

    Returns: slope_per_day, intercept, r_squared, direction, hours_to_zero,
             hours_to_threshold.
    """
    import numpy as np

    valid = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(valid) < 10:
        return None

    idx = np.array([x[0] for x in valid], dtype=float)
    vals = np.array([x[1] for x in valid])

    n_total = len(values) if len(values) > 1 else 1
    t_hours = idx * (duration_hours / n_total)

    coeffs = np.polyfit(t_hours, vals, 1)
    slope_h = float(coeffs[0])
    intercept = float(coeffs[1])
    slope_day = slope_h * 24.0

    predicted = np.polyval(coeffs, t_hours)
    ss_res = float(np.sum((vals - predicted) ** 2))
    ss_tot = float(np.sum((vals - np.mean(vals)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

    last_t = float(t_hours[-1])

    # Прогноз к нулю
    hours_to_zero = None
    if slope_h < 0 and intercept > 0:
        t_zero = -intercept / slope_h
        if t_zero > last_t:
            hours_to_zero = round(t_zero - last_t, 1)

    # Прогноз к порогу
    hours_to_threshold = None
    if threshold is not None and abs(slope_h) > 1e-9:
        t_thresh = (threshold - intercept) / slope_h
        if t_thresh > last_t:
            hours_to_threshold = round(t_thresh - last_t, 1)

    direction = "up" if slope_day > 0.001 else "down" if slope_day < -0.001 else "flat"

    return {
        "slope_per_day": round(slope_day, 6),
        "intercept": round(intercept, 4),
        "r_squared": round(r2, 4),
        "direction": direction,
        "hours_to_zero": hours_to_zero,
        "hours_to_threshold": hours_to_threshold,
        "threshold": threshold,
    }


def _compute_segment_stats(
    well_id: int, start: str, end: str,
    threshold_flow=None, threshold_dp=None, threshold_p_tube=None,
) -> dict:
    """Расчёт статистики произвольного участка."""
    import statistics

    # Если timestamps naive (без TZ) → это Кунград (+5h), конвертируем в UTC
    dt_s = datetime.fromisoformat(start)
    dt_e = datetime.fromisoformat(end)
    if dt_s.tzinfo is None:
        dt_s = dt_s - timedelta(hours=5)
        dt_e = dt_e - timedelta(hours=5)
        start = dt_s.isoformat()
        end = dt_e.isoformat()

    result = _run_calculation(well_id, start, end)
    chart = result["chart"]
    summary = result["summary"]
    purge_cycles = result.get("purge_cycles", [])
    downtime_periods = result.get("downtime_periods", [])

    flow_vals = [v for v in chart["flow_rate"] if v is not None]
    p_tube_vals = [v for v in chart["p_tube"] if v is not None]
    p_line_vals = [v for v in chart["p_line"] if v is not None]
    dp_vals = [
        t - l
        for t, l in zip(chart["p_tube"], chart["p_line"])
        if t is not None and l is not None
    ]

    def safe_stats(vals):
        if not vals:
            return {"mean": None, "median": None, "min": None, "max": None}
        return {
            "mean": round(sum(vals) / len(vals), 4),
            "median": round(statistics.median(vals), 4),
            "min": round(min(vals), 4),
            "max": round(max(vals), 4),
        }

    flow_s = safe_stats(flow_vals)
    dt_start = datetime.fromisoformat(start)
    dt_end = datetime.fromisoformat(end)
    duration_hours = (dt_end - dt_start).total_seconds() / 3600

    downtime_hours = sum(d["duration_min"] for d in downtime_periods) / 60
    purge_count = len([p for p in purge_cycles if not p.get("excluded")])

    # Потери от простоев (условные) = downtime_hours * median_flow / 24
    loss_vs_median = None
    if flow_s["median"] and downtime_hours > 0:
        loss_vs_median = round(downtime_hours * flow_s["median"] / 24, 4)

    # Эффективный суточный дебит = cumulative / T_days
    duration_days = duration_hours / 24.0
    cum = summary.get("cumulative_flow")
    effective_daily = round(cum / duration_days, 4) if cum and duration_days > 0 else None

    # Тренд-анализ (линейная регрессия)
    # Маска: только рабочие точки (Q > 0) — исключаем продувки и простои
    flow_raw = chart["flow_rate"]
    working = [v is not None and v > 0 for v in flow_raw]
    flow_for_trend = [v if ok else None for v, ok in zip(flow_raw, working)]

    trend_flow = _linear_trend(flow_for_trend, duration_hours, threshold_flow)
    trend_dp = _linear_trend(
        [(t - l) if (ok and t is not None and l is not None) else None
         for t, l, ok in zip(chart["p_tube"], chart["p_line"], working)],
        duration_hours, threshold_dp,
    )
    trend_p_tube = _linear_trend(
        [v if (ok and v is not None) else None
         for v, ok in zip(chart["p_tube"], working)],
        duration_hours, threshold_p_tube,
    )

    return {
        "mean_flow": flow_s["mean"],
        "median_flow": flow_s["median"],
        "min_flow": flow_s["min"],
        "max_flow": flow_s["max"],
        "effective_daily": effective_daily,
        "mean_p_tube": safe_stats(p_tube_vals)["mean"],
        "min_p_tube": safe_stats(p_tube_vals)["min"],
        "max_p_tube": safe_stats(p_tube_vals)["max"],
        "mean_p_line": safe_stats(p_line_vals)["mean"],
        "min_p_line": safe_stats(p_line_vals)["min"],
        "max_p_line": safe_stats(p_line_vals)["max"],
        "mean_dp": safe_stats(dp_vals)["mean"],
        "min_dp": safe_stats(dp_vals)["min"],
        "max_dp": safe_stats(dp_vals)["max"],
        "cumulative_flow": cum,
        "duration_hours": round(duration_hours, 2),
        "purge_count": purge_count,
        "purge_loss_total": summary.get("purge_loss_total"),
        "purge_loss_daily": summary.get("purge_loss_daily_avg"),
        "utilization_pct": summary.get("utilization_pct"),
        "downtime_count": len(downtime_periods),
        "downtime_hours": round(downtime_hours, 2),
        "loss_vs_median": loss_vs_median,
        "data_points": result.get("data_points", 0),
        "trend_flow": trend_flow,
        "trend_dp": trend_dp,
        "trend_p_tube": trend_p_tube,
    }


@router.post("/segment-stats")
async def api_segment_stats(request_data: dict):
    """Расчёт статистики участка без сохранения (preview)."""
    well_id = request_data.get("well_id")
    start = request_data.get("start")
    end = request_data.get("end")
    if not well_id or not start or not end:
        raise HTTPException(400, "well_id, start, end required")
    return _compute_segment_stats(
        well_id, start, end,
        threshold_flow=request_data.get("threshold_flow"),
        threshold_dp=request_data.get("threshold_dp"),
        threshold_p_tube=request_data.get("threshold_p_tube"),
    )


@router.post("/segments")
async def api_create_segment(request_data: dict):
    """Создать сегмент: расчёт статистики + сохранение в БД."""
    from sqlalchemy.orm import Session
    from backend.db import SessionLocal
    from backend.models.flow_segment import FlowSegment

    well_id = request_data.get("well_id")
    name = request_data.get("name", "Участок")
    start = request_data.get("start")
    end = request_data.get("end")
    if not well_id or not start or not end:
        raise HTTPException(400, "well_id, start, end required")

    stats = _compute_segment_stats(well_id, start, end)

    db: Session = SessionLocal()
    try:
        seg = FlowSegment(
            well_id=well_id,
            name=name,
            dt_start=datetime.fromisoformat(start),
            dt_end=datetime.fromisoformat(end),
            stats=stats,
        )
        db.add(seg)
        db.commit()
        db.refresh(seg)
        return {
            "id": seg.id,
            "name": seg.name,
            "dt_start": seg.dt_start.isoformat(),
            "dt_end": seg.dt_end.isoformat(),
            "stats": seg.stats,
        }
    finally:
        db.close()


@router.get("/segments")
def api_list_segments(well_id: int = Query(...)):
    """Список сохранённых сегментов для скважины."""
    from sqlalchemy.orm import Session
    from backend.db import SessionLocal
    from backend.models.flow_segment import FlowSegment

    db: Session = SessionLocal()
    try:
        rows = (
            db.query(FlowSegment)
            .filter(FlowSegment.well_id == well_id)
            .order_by(FlowSegment.dt_start)
            .all()
        )
        return [
            {
                "id": r.id,
                "name": r.name,
                "dt_start": r.dt_start.isoformat(),
                "dt_end": r.dt_end.isoformat(),
                "stats": r.stats,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()


@router.post("/segments/compare-report")
async def api_segment_compare_report(request_data: dict):
    """Генерация PDF сравнительного отчёта по выбранным участкам."""
    from backend.db import SessionLocal
    from backend.models.wells import Well

    well_id = request_data.get("well_id")
    segment_inputs = request_data.get("segments", [])
    if not well_id or not segment_inputs:
        raise HTTPException(400, "well_id and segments required")

    db = SessionLocal()
    try:
        well = db.query(Well).filter(Well.id == well_id).first()
        well_number = str(well.number) if well else str(well_id)
        well_name = well.name if well else ""

        segments_for_report = []
        for seg_in in segment_inputs:
            dt_start = seg_in.get("dt_start")
            dt_end = seg_in.get("dt_end")
            name = seg_in.get("name", "Участок")
            stats = seg_in.get("stats", {})

            try:
                calc_result = _run_calculation(well_id, dt_start, dt_end)
                chart_data = calc_result.get("chart", {})
            except Exception as e:
                log.warning("Failed to calculate segment %s: %s", name, e)
                chart_data = {}

            segments_for_report.append({
                "name": name,
                "start_iso": dt_start,
                "end_iso": dt_end,
                "stats": stats,
                "timestamps": chart_data.get("timestamps", []),
                "flow_rate": chart_data.get("flow_rate", []),
                "cumulative_flow": chart_data.get("cumulative_flow", []),
                "p_tube": chart_data.get("p_tube", []),
                "p_line": chart_data.get("p_line", []),
            })

        from backend.services.flow_rate.segment_report_service import generate_segment_comparison_report
        pdf_rel_path = generate_segment_comparison_report(
            segments=segments_for_report,
            well_number=well_number,
            well_name=well_name or "",
        )

        return {"ok": True, "pdf_url": pdf_rel_path}

    except Exception as e:
        log.error("Segment compare report failed: %s", e, exc_info=True)
        raise HTTPException(500, f"Ошибка генерации отчёта: {e}")
    finally:
        db.close()


@router.delete("/segments/{segment_id}")
def api_delete_segment(segment_id: int):
    """Удалить сегмент."""
    from sqlalchemy.orm import Session
    from backend.db import SessionLocal
    from backend.models.flow_segment import FlowSegment

    db: Session = SessionLocal()
    try:
        seg = db.query(FlowSegment).filter(FlowSegment.id == segment_id).first()
        if not seg:
            raise HTTPException(404, "Segment not found")
        db.delete(seg)
        db.commit()
        return {"ok": True}
    finally:
        db.close()
