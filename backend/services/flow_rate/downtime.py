"""
Детекция периодов простоя скважины.

Простой = непрерывный интервал, где (p_tube - p_line) < dp_threshold
(газ не поступает в шлейф или поступает на грани — недостаточный перепад).
Дебит = 0, потерь нет.

Параметр `dp_threshold` (атм) — настраиваемая граница «работа vs простой»:
  0.0 → старое поведение (строго p_tube < p_line)
  0.1 → дефолт (рабочий перепад должен быть хотя бы 0.1 атм)
  0.2/0.3 → жёстче, больше часов в простое

Если `include_purge=True` и в df есть колонка `purge_flag` — продувки
(`purge_flag == 1`) объединяются в простой по логике OR.

Ручные аннотации с фронта в эту функцию не передаются — они применяются
в UI поверх результата (шаг 3 wizard).
"""
from __future__ import annotations

import pandas as pd


def detect_downtime_periods(
    df: pd.DataFrame,
    dp_threshold: float = 0.1,
    include_purge: bool = True,
) -> pd.DataFrame:
    """
    Группировка непрерывных периодов простоя.

    mask = (p_tube - p_line < dp_threshold) [OR purge_flag == 1, если include_purge]

    Returns
    -------
    DataFrame: start, end, duration_min, duration_hours, interval_hours
    Пустой DataFrame если простоев нет.
    """
    dp = df["p_tube"] - df["p_line"]
    mask = dp < dp_threshold
    if include_purge and "purge_flag" in df.columns:
        mask = mask | (df["purge_flag"].fillna(0).astype(bool))

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
