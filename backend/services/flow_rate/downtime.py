"""
Детекция периодов простоя скважины.

Простой = непрерывный интервал, где p_tube < p_line (газ не поступает в шлейф).
Дебит = 0, потерь нет.

Продувки детектируются отдельно в purge_detector.py.
"""
from __future__ import annotations

import pandas as pd


def detect_downtime_periods(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Группировка непрерывных периодов, где p_tube < p_line.

    Returns
    -------
    DataFrame: start, end, duration_min, duration_hours, interval_hours
    Пустой DataFrame если простоев нет.
    """
    mask = df["p_tube"] < df["p_line"]

    periods: list[dict] = []
    start = None

    for i in range(len(df)):
        if mask.iloc[i] and start is None:
            start = df.index[i]
        elif not mask.iloc[i] and start is not None:
            end = df.index[i]
            dur = (end - start).total_seconds() / 60.0
            periods.append({
                "start": start,
                "end": end,
                "duration_min": round(dur, 1),
            })
            start = None

    # Если последний период не закрыт
    if start is not None:
        end = df.index[-1]
        dur = (end - start).total_seconds() / 60.0
        periods.append({
            "start": start,
            "end": end,
            "duration_min": round(dur, 1),
        })

    result = pd.DataFrame(periods)

    if not result.empty:
        result["duration_hours"] = (result["duration_min"] / 60.0).round(2)
        result["interval_hours"] = (
            result["start"].diff().dt.total_seconds() / 3600.0
        ).round(2)

    return result
