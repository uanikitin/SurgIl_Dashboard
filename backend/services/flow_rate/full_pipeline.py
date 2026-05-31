"""
Полный конвейер расчёта дебита газа — единый источник истины.

Это та же логика, что используется страницей скважины и `/api/flow-rate/calculate`.
Использовать ВЕЗДЕ, где требуется наш дебит (адаптация, оптимизация, отчёты),
чтобы избежать расхождений между страницами.

Pipeline:
  1. get_pressure_data()       — сырые точки давления (UTC)
  2. clean_pressure()          — очистка
  3. apply_masks() (verified)  — наложение верифицированных масок
  4. UTC → Кунград (+5h)       — для отображения
  5. smooth_pressure()         — фильтр Савицкого-Голая (по запросу)
  6. calculate_flow_rate()     — мгновенный дебит (Q=0 при p_tube ≤ p_line)
  7. calculate_purge_loss()    — потери при стравливании (предварительно)
  8. PurgeDetector.detect()    — детекция циклов продувок
  9. recalculate_purge_loss_with_cycles() — пересчёт потерь только в venting
 10. calculate_cumulative()    — накопленный дебит
 11. detect_downtime_periods() — простои (p_tube < p_line)
 12. build_summary()           — сводные показатели по точкам

Возвращает {df, summary, downtime_periods, purge_cycles}.
DataFrame `df` — поминутные точки ПОСЛЕ всех преобразований; индекс — Кунградское время.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from backend.services.flow_rate.cleaning import DEFAULT_MAX_FILL_MIN

log = logging.getLogger(__name__)


def compute_full_flow(
    well_id: int,
    dt_start: str | datetime,
    dt_end: str | datetime,
    *,
    smooth: bool = True,
    max_fill_min: int = DEFAULT_MAX_FILL_MIN,
    multiplier: float = 4.1,
    C1: float = 2.919,
    C2: float = 4.654,
    C3: float = 286.95,
    critical_ratio: float = 0.5,
    exclude_periods: str = "",
    dp_threshold: float = 0.1,
) -> dict:
    """
    Полный расчёт дебита для одной скважины за период.

    Параметры
    ---------
    well_id : int
    dt_start, dt_end : str | datetime
        Время в UTC (ISO-строка или naive datetime). Внутри индекс
        DataFrame после возврата будет в Кунградском времени (+5h).
    smooth : bool
        Применять ли фильтр Савицкого-Голая.
    max_fill_min : int
        Порог заполнения пропусков давления (минут). Короткие дыри
        интерполируются, длиннее — остаются NaN. По умолчанию 20. См.
        clean_pressure. Регулируется в дашборде (страница скважины).

    Возвращает
    ----------
    dict со следующими ключами:
      - df : pd.DataFrame — поминутные точки после всех преобразований
                            (индекс — Кунградское время). Колонки:
                            flow_rate, cumulative_flow, p_tube, p_line,
                            purge_flag, purge_loss_per_min, cumulative_purge_loss
      - summary : dict — результат build_summary (median_flow_rate, mean_flow_rate,
                         cumulative_flow, actual_avg_flow, utilization_pct,
                         downtime_hours, median_p_tube/p_line/dp, ...)
      - downtime_periods : pd.DataFrame — найденные периоды простоев
      - purge_cycles : list — обнаруженные циклы продувок
      - data_points : int

    Бросает
    -------
    ValueError если нет данных давления или штуцера.
    """
    # Импорты делаются внутри функции, чтобы избежать цикличных импортов
    # на уровне модулей.
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

    # Нормализация входных дат к ISO-строкам
    if isinstance(dt_start, datetime):
        dt_start_iso = dt_start.isoformat()
    else:
        dt_start_iso = dt_start
    if isinstance(dt_end, datetime):
        dt_end_iso = dt_end.isoformat()
    else:
        dt_end_iso = dt_end

    # 1. Сырые точки давления (UTC)
    df = get_pressure_data(well_id, dt_start_iso, dt_end_iso)
    if df.empty:
        raise ValueError(
            f"Нет данных давления для well_id={well_id} "
            f"за период {dt_start_iso}..{dt_end_iso}"
        )

    choke = get_choke_mm(well_id)
    if choke is None:
        raise ValueError(
            f"Штуцер (choke_diam_mm) не найден для well_id={well_id}. "
            f"Проверьте таблицу well_construction."
        )

    # 2. Очистка + заполнение коротких пропусков (≤ max_fill_min мин)
    df = clean_pressure(df, max_fill_min=max_fill_min)

    # 3. Verified-маски (ДО сдвига UTC → Кунград)
    try:
        from backend.services.pressure_mask_service import (
            load_active_masks, apply_masks as _apply_masks,
        )
        ms = datetime.fromisoformat(dt_start_iso) if isinstance(dt_start_iso, str) else dt_start_iso
        me = datetime.fromisoformat(dt_end_iso) if isinstance(dt_end_iso, str) else dt_end_iso
        masks = load_active_masks(well_id, ms, me)
        if masks:
            df, _mc = _apply_masks(df, masks)
            log.info(
                "[full_pipeline] well=%d applied %d pressure masks, %d points",
                well_id, len(masks), _mc,
            )
    except Exception as e:
        log.warning("[full_pipeline] failed to apply pressure masks: %s", e)

    # 4. UTC → Кунград (+5h) для отображения и согласования с events
    df.index = df.index + timedelta(hours=5)

    # 5. Сглаживание (опционально)
    if smooth:
        df = smooth_pressure(df)

    # 6. Расчёт дебита
    cfg = FlowRateConfig(
        multiplier=multiplier,
        C1=C1, C2=C2, C3=C3,
        critical_ratio=critical_ratio,
    )
    df = calculate_flow_rate(df, choke, cfg)

    # 7. Предварительный расчёт потерь
    df = calculate_purge_loss(df)

    # 8. Детекция продувок
    exclude_ids = set()
    if exclude_periods:
        exclude_ids = {s.strip() for s in exclude_periods.split(",") if s.strip()}

    events_df = get_purge_events(well_id, dt_start_iso, dt_end_iso)
    detector = PurgeDetector()
    purge_cycles = detector.detect(df, events_df, exclude_ids)

    # 9. Пересчёт потерь только в фазах venting
    df = recalculate_purge_loss_with_cycles(df, purge_cycles)

    # 10. Накопленный дебит
    df = calculate_cumulative(df)

    # 11. Простои: (dp < dp_threshold) OR purge_flag — единое условие.
    periods = detect_downtime_periods(df, dp_threshold=dp_threshold, include_purge=True)

    # 11a. Обнулить flow_rate в периодах простоя (согласованность с красными зонами).
    # Простой = когда ΔP < порога, скважина физически не работает → дебит = 0.
    import numpy as np
    dp = df["p_tube"] - df["p_line"]
    downtime_mask = dp < dp_threshold
    if "purge_flag" in df.columns:
        downtime_mask = downtime_mask | (df["purge_flag"].fillna(0).astype(bool))
    df["flow_rate"] = np.where(downtime_mask, 0.0, df["flow_rate"].values)

    # 11b. Пересчитать накопленный дебит после обнуления.
    df = calculate_cumulative(df)

    # 12. Сводка по точкам
    summary = build_summary(
        df, periods, well_id, choke, purge_cycles,
        dp_threshold=dp_threshold,
    )

    return {
        "df": df,
        "summary": summary,
        "downtime_periods": periods,
        "purge_cycles": purge_cycles,
        "data_points": len(df),
        "choke_mm": choke,
    }


def build_chart_payload(
    df: pd.DataFrame,
    *,
    max_points: int = 2000,
) -> dict:
    """
    Построить компактный payload для графика на фронте — прореженные точки.
    Используется в `/api/flow-rate/calculate` для chart-блока.
    """
    if df.empty:
        return {
            "timestamps": [], "flow_rate": [], "cumulative_flow": [],
            "p_tube": [], "p_line": [],
        }
    step = max(1, len(df) // max_points)
    chart_df = df.iloc[::step]
    return {
        "timestamps": chart_df.index.strftime("%Y-%m-%dT%H:%M:%S").tolist(),
        "flow_rate": chart_df["flow_rate"].round(3).tolist(),
        "cumulative_flow": chart_df["cumulative_flow"].round(3).tolist(),
        "p_tube": chart_df["p_tube"].round(2).tolist(),
        "p_line": chart_df["p_line"].round(2).tolist(),
    }


def downtime_periods_to_list(periods: pd.DataFrame) -> list[dict]:
    """Сериализовать periods в список dict для JSON-ответа."""
    if periods.empty:
        return []
    return [
        {
            "start": row["start"].isoformat(),
            "end": row["end"].isoformat(),
            "duration_min": row["duration_min"],
        }
        for _, row in periods.iterrows()
    ]
