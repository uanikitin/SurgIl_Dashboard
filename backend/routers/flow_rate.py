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
) -> dict:
    """
    Внутренняя функция: полный расчёт дебита для одной скважины.

    Pipeline:
    1. get_pressure_data()        — данные давления
    2. get_choke_mm()             — диаметр штуцера
    3. clean + smooth             — предобработка
    4. calculate_flow_rate()      — мгновенный дебит (Q=0 при p_tube≤p_line)
    5. calculate_purge_loss()     — потери при стравливании (предварительно)
    6. get_purge_events()         — маркеры продувок из events
    7. PurgeDetector.detect()     — детекция циклов продувок
    8. recalculate_purge_loss()   — пересчёт: потери ТОЛЬКО в фазах venting
    9. calculate_cumulative()     — накопленный дебит (после пересчёта)
    10. detect_downtime_periods() — простои (p_tube < p_line)
    11. build_summary()           — сводные показатели
    """
    from backend.services.flow_rate.data_access import (
        get_pressure_data,
        get_choke_mm,
        get_purge_events,
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
    from backend.services.flow_rate.purge_detector import (
        PurgeDetector,
        recalculate_purge_loss_with_cycles,
    )

    # 1. Данные из БД (measured_at в UTC)
    df = get_pressure_data(well_id, dt_start, dt_end)
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"Нет данных давления для well_id={well_id} "
                   f"за период {dt_start}..{dt_end}",
        )

    # UTC → Кунград (+5ч) для отображения на графиках
    df.index = df.index + timedelta(hours=5)

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

    # 3. Расчёт дебита (Q=0 при p_tube≤p_line — уже встроено)
    cfg = FlowRateConfig(
        multiplier=multiplier,
        C1=C1,
        C2=C2,
        C3=C3,
        critical_ratio=critical_ratio,
    )
    df = calculate_flow_rate(df, choke, cfg)

    # 4. Предварительный расчёт потерь (весь p_tube<p_line — будет скорректирован)
    df = calculate_purge_loss(df)

    # 5. Детекция продувок
    exclude_ids = set()
    if exclude_periods:
        exclude_ids = {s.strip() for s in exclude_periods.split(",") if s.strip()}

    events_df = get_purge_events(well_id, dt_start, dt_end)
    detector = PurgeDetector()
    purge_cycles = detector.detect(df, events_df, exclude_ids)

    # 6. Пересчёт потерь: ТОЛЬКО в фазах venting обнаруженных продувок
    df = recalculate_purge_loss_with_cycles(df, purge_cycles)

    # 7. Накопленный дебит (после пересчёта потерь)
    df = calculate_cumulative(df)

    # 8. Простои (p_tube < p_line)
    periods = detect_downtime_periods(df)

    # 9. Сводка (расширенная)
    summary = build_summary(df, periods, well_id, choke, purge_cycles)

    # 10. Данные для графика (прореженные, макс ~2000 точек)
    step = max(1, len(df) // 2000)
    chart_df = df.iloc[::step]

    chart = {
        "timestamps": chart_df.index.strftime("%Y-%m-%dT%H:%M:%S").tolist(),
        "flow_rate": chart_df["flow_rate"].round(3).tolist(),
        "cumulative_flow": chart_df["cumulative_flow"].round(3).tolist(),
        "p_tube": chart_df["p_tube"].round(2).tolist(),
        "p_line": chart_df["p_line"].round(2).tolist(),
    }

    # 11. Периоды простоев
    dt_list = []
    if not periods.empty:
        for _, row in periods.iterrows():
            dt_list.append({
                "start": row["start"].isoformat(),
                "end": row["end"].isoformat(),
                "duration_min": row["duration_min"],
            })

    # 12. Циклы продувок
    purge_list = [c.to_dict() for c in purge_cycles]

    return {
        "summary": summary,
        "chart": chart,
        "downtime_periods": dt_list,
        "purge_cycles": purge_list,
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
    multiplier: float = Query(4.1, description="Калибровочный множитель M"),
    C1: float = Query(2.919, description="Коэффициент C1"),
    C2: float = Query(4.654, description="Коэффициент C2"),
    C3: float = Query(286.95, description="Коэффициент C3"),
    critical_ratio: float = Query(0.5, description="Критическое отношение давлений"),
    exclude_periods: str = Query("", description="ID продувок для исключения (через запятую)"),
):
    """
    Полный расчёт дебита: summary + график + продувки + простои.
    Все коэффициенты формулы можно передать через query-параметры.
    """
    if start and end:
        dt_start, dt_end = start, end
    else:
        dt_end = datetime.utcnow().isoformat()
        dt_start = (datetime.utcnow() - timedelta(days=days)).isoformat()

    return _run_calculation(
        well_id, dt_start, dt_end, smooth,
        multiplier=multiplier, C1=C1, C2=C2, C3=C3,
        critical_ratio=critical_ratio,
        exclude_periods=exclude_periods,
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
