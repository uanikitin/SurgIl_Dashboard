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
) -> dict:
    """
    Внутренняя функция: полный расчёт дебита для одной скважины.
    Возвращает dict с summary, chart data, downtime_periods.
    """
    from backend.services.flow_rate.data_access import (
        get_pressure_data,
        get_choke_mm,
    )
    from backend.services.flow_rate.cleaning import clean_pressure, smooth_pressure
    from backend.services.flow_rate.calculator import (
        calculate_flow_rate,
        calculate_cumulative,
        calculate_purge_loss,
    )
    from backend.services.flow_rate.downtime import detect_downtime_periods
    from backend.services.flow_rate.summary import build_summary
    from backend.services.flow_rate.config import FlowRateConfig

    # 1. Данные из БД
    df = get_pressure_data(well_id, dt_start, dt_end)
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"Нет данных давления для well_id={well_id} "
                   f"за период {dt_start}..{dt_end}",
        )

    choke = get_choke_mm(well_id)
    if choke is None:
        raise HTTPException(
            status_code=404,
            detail=f"Штуцер (choke_diam_mm) не найден для well_id={well_id}. "
                   f"Проверьте таблицу well_construction.",
        )

    # 2. Предобработка
    df = clean_pressure(df)
    if smooth:
        df = smooth_pressure(df)

    # 3. Расчёт дебита
    cfg = FlowRateConfig(multiplier=multiplier)
    df = calculate_flow_rate(df, choke, cfg)
    df = calculate_cumulative(df)
    df = calculate_purge_loss(df)

    # 4. Простои
    periods = detect_downtime_periods(df)

    # 5. Сводка
    summary = build_summary(df, periods, well_id, choke)

    # 6. Данные для графика (прореженные, макс ~2000 точек)
    step = max(1, len(df) // 2000)
    chart_df = df.iloc[::step]

    chart = {
        "timestamps": chart_df.index.strftime("%Y-%m-%dT%H:%M:%S").tolist(),
        "flow_rate": chart_df["flow_rate"].round(3).tolist(),
        "cumulative_flow": chart_df["cumulative_flow"].round(3).tolist(),
        "p_tube": chart_df["p_tube"].round(2).tolist(),
        "p_line": chart_df["p_line"].round(2).tolist(),
    }

    # 7. Периоды простоев
    dt_list = []
    if not periods.empty:
        for _, row in periods.iterrows():
            dt_list.append({
                "start": row["start"].isoformat(),
                "end": row["end"].isoformat(),
                "duration_min": row["duration_min"],
                "is_blowout": bool(row["is_blowout"]),
            })

    return {
        "summary": summary,
        "chart": chart,
        "downtime_periods": dt_list,
        "data_points": len(df),
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
    multiplier: float = Query(4.1, description="Калибровочный множитель"),
):
    """
    Полный расчёт дебита: summary + график + периоды простоев.
    """
    if start and end:
        dt_start, dt_end = start, end
    else:
        dt_end = datetime.utcnow().isoformat()
        dt_start = (datetime.utcnow() - timedelta(days=days)).isoformat()

    return _run_calculation(well_id, dt_start, dt_end, smooth, multiplier)


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
