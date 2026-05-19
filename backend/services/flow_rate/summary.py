"""
Формирование итоговых показателей эффективности скважины.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from .purge_detector import PurgeCycle


def build_summary(
    df: pd.DataFrame,
    downtime_periods: pd.DataFrame,
    well_id: int,
    choke_mm: float,
    purge_cycles: list[PurgeCycle] | None = None,
    dp_threshold: float = 0.1,
) -> dict:
    """
    Сводные показатели за один расчётный период.

    Ожидает в df:
      flow_rate, cumulative_flow, purge_loss_per_min,
      purge_flag, p_tube, p_line

    Returns: dict с ключевыми метриками.
    """
    if df.empty:
        return {"well_id": well_id, "error": "no data"}

    T_obs = (df.index.max() - df.index.min()).total_seconds() / 86400.0
    if T_obs <= 0:
        return {"well_id": well_id, "error": "zero observation period"}

    q = df["flow_rate"].values

    # Медианный фильтр для статистики (убрать шум)
    try:
        from scipy.signal import medfilt
        filtered = medfilt(q, kernel_size=5)
    except ImportError:
        filtered = q

    median_flow = float(pd.Series(filtered).median())
    mean_flow = float(pd.Series(filtered).mean())
    q1_flow = float(pd.Series(filtered).quantile(0.25))
    q3_flow = float(pd.Series(filtered).quantile(0.75))
    cum_flow = float(df["cumulative_flow"].iloc[-1])
    actual_avg = cum_flow / T_obs

    # ─── Простои: legacy-метрика (через purge_flag, ТОЛЬКО продувки) ───
    # Оставляем для обратной совместимости (поле downtime_hours_purge_flag в return).
    # Для UI/КИВ используется downtime_total_hours (через detect_downtime_periods),
    # которое учитывает и продувки, и реальный простой p_tube < p_line + dp_threshold.
    import numpy as np
    time_idx = pd.to_datetime(df.index)
    dt_sec = np.diff(time_idx.asi8 // 10**9, prepend=time_idx.asi8[0] // 10**9)
    dt_sec[0] = 0.0
    dt_min_arr = dt_sec / 60.0
    dt_min = float((df["purge_flag"].values * dt_min_arr).sum())
    dt_hours = dt_min / 60.0
    dt_days = dt_hours / 24.0

    # Потери при простоях (скважина не работала)
    downtime_loss = dt_days * median_flow
    downtime_loss_daily = downtime_loss / T_obs if T_obs > 0 else 0.0

    # Потери при продувках (стравливание в атмосферу)
    purge_total = float(df["cumulative_purge_loss"].iloc[-1])
    purge_daily = float((df["purge_loss_per_min"].values * dt_min_arr).sum()) / T_obs if T_obs > 0 else 0.0

    # Суммарные потери
    total_loss_daily = downtime_loss_daily + purge_daily

    # Коэффициент потерь
    loss_pct = (
        (downtime_loss + purge_total) / cum_flow * 100.0
        if cum_flow > 0 else 0.0
    )

    # Эффективный суточный дебит = календарный
    # (cumulative уже НЕ содержит атмосферных потерь: Q=0 при p_tube < p_line)
    effective_flow = actual_avg

    # КИВ — коэффициент использования времени.
    # Считается от ФАКТИЧЕСКОГО простоя (downtime_periods, см. ниже),
    # который объединяет (dp < threshold) OR purge_flag.
    # Старая метрика по purge_flag оставлена в downtime_hours для совместимости.
    total_minutes = float(dt_min_arr.sum())

    # Прогноз прироста от ТППАВ
    forecast_gain = q3_flow - median_flow

    # Медианы и средние давлений (по точкам после масок)
    # ΔP считается поточечно с clip(0): max(p_tube - p_line, 0).
    median_p_tube = float(df["p_tube"].median())
    median_p_line = float(df["p_line"].median())
    median_dp = max(median_p_tube - median_p_line, 0.0)
    mean_p_tube = float(df["p_tube"].mean())
    mean_p_line = float(df["p_line"].mean())
    mean_dp = float((df["p_tube"] - df["p_line"]).clip(lower=0).mean())

    # Статистика простоев — единый источник для UI и красных зон на графиках.
    # downtime_periods приходит из detect_downtime_periods(df, dp_threshold=...,
    # include_purge=True), т.е. уже учитывает (dp < threshold) OR purge_flag.
    total_periods = 0
    downtime_total_hours = 0.0
    if not downtime_periods.empty:
        total_periods = len(downtime_periods)
        downtime_total_hours = float(downtime_periods["duration_min"].sum() / 60.0)

    # КИВ от downtime_total_hours
    downtime_total_minutes = downtime_total_hours * 60.0
    utilization_pct = (
        (total_minutes - downtime_total_minutes) / total_minutes * 100.0
        if total_minutes > 0 else 0.0
    )

    # ═══════ Метрики по продувкам ═══════
    purge_venting_count = 0
    purge_venting_hours = 0.0
    purge_buildup_hours = 0.0
    purge_marker_count = 0
    purge_algorithm_count = 0

    if purge_cycles:
        active_cycles = [c for c in purge_cycles if not c.excluded]
        purge_venting_count = len(active_cycles)
        purge_venting_hours = sum(c.venting_duration_min for c in active_cycles) / 60.0
        purge_buildup_hours = sum(c.buildup_duration_min for c in active_cycles) / 60.0
        purge_marker_count = sum(1 for c in active_cycles if c.source == "marker")
        purge_algorithm_count = sum(1 for c in active_cycles if c.source == "algorithm")

    result = {
        "well_id": well_id,
        "observation_days": round(T_obs, 2),
        "choke_mm": choke_mm,
        # Дебит
        "median_flow_rate": round(median_flow, 3),
        "mean_flow_rate": round(mean_flow, 3),
        "q1_flow_rate": round(q1_flow, 3),
        "q3_flow_rate": round(q3_flow, 3),
        "cumulative_flow": round(cum_flow, 3),
        "actual_avg_flow": round(actual_avg, 3),
        # Простои — единый источник: (dp < dp_threshold) OR purge_flag
        "dp_threshold": dp_threshold,
        "downtime_total_hours": round(downtime_total_hours, 2),
        "downtime_minutes": dt_min,                # legacy: ТОЛЬКО purge_flag
        "downtime_hours": round(dt_hours, 2),      # legacy
        "downtime_days": round(dt_days, 3),
        # Потери при продувках (стравливание)
        "purge_time_hours": round(dt_hours, 2),
        "purge_loss_total": round(purge_total, 4),
        "purge_loss_daily_avg": round(purge_daily, 4),
        # Потери при простоях
        "downtime_loss": round(downtime_loss, 3),
        "downtime_loss_daily": round(downtime_loss_daily, 4),
        # Итого
        "total_loss_daily": round(total_loss_daily, 4),
        "loss_coefficient_pct": round(loss_pct, 2),
        "effective_flow_rate": round(effective_flow, 3),
        "utilization_pct": round(utilization_pct, 1),
        "forecast_gain_tppav": round(forecast_gain, 3),
        # Давление
        "median_p_tube": round(median_p_tube, 2),
        "median_p_line": round(median_p_line, 2),
        "median_dp": round(median_dp, 2),
        "mean_p_tube": round(mean_p_tube, 2),
        "mean_p_line": round(mean_p_line, 2),
        "mean_dp": round(mean_dp, 2),
        # Простои
        "total_downtime_periods": total_periods,
        # Продувки (детекция)
        "purge_venting_count": purge_venting_count,
        "purge_venting_hours": round(purge_venting_hours, 2),
        "purge_buildup_hours": round(purge_buildup_hours, 2),
        "purge_marker_count": purge_marker_count,
        "purge_algorithm_count": purge_algorithm_count,
    }

    return result
