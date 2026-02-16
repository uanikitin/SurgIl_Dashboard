"""
Детекция периодов простоя и продувок.

Простой = непрерывный интервал, где p_tube < p_line.
Продувка (blowout) = простой длительностью > 30 минут.
"""
from __future__ import annotations

import pandas as pd

from .config import DowntimeConfig, DEFAULT_DOWNTIME


def detect_downtime_periods(
    df: pd.DataFrame,
    cfg: DowntimeConfig = DEFAULT_DOWNTIME,
) -> pd.DataFrame:
    """
    Группировка непрерывных периодов, где p_tube < p_line.

    Returns
    -------
    DataFrame: start, end, duration_min, duration_hours, is_blowout, interval_hours
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
                "is_blowout": dur > cfg.min_blowout_minutes,
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
            "is_blowout": dur > cfg.min_blowout_minutes,
        })

    result = pd.DataFrame(periods)

    if not result.empty:
        result["duration_hours"] = (result["duration_min"] / 60.0).round(2)
        result["interval_hours"] = (
            result["start"].diff().dt.total_seconds() / 3600.0
        ).round(2)

    return result
