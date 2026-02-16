"""
Предобработка данных давления перед расчётом дебита.

Этапы:
1. Приведение к float, отрицательные/нули → NaN
2. Forward fill + backward fill
3. Сглаживание фильтром Савицкого-Голая (опционально)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def clean_pressure(df: pd.DataFrame) -> pd.DataFrame:
    """
    Базовая очистка: отрицательные → NaN, нули → NaN, ffill/bfill.
    """
    df = df.copy()
    for col in ("p_tube", "p_line"):
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        s = s.where(s > 0)          # отрицательные и нули → NaN
        df[col] = s
    df = df.ffill().bfill()
    return df


def smooth_pressure(
    df: pd.DataFrame,
    window: int = 17,
    polyorder: int = 3,
    passes: int = 2,
) -> pd.DataFrame:
    """
    Фильтр Савицкого-Голая (двойной проход).
    Сохраняет оригинальные данные в p_tube_raw / p_line_raw.

    Требует scipy. Если scipy не установлен — возвращает df без изменений.
    """
    try:
        from scipy.signal import savgol_filter
    except ImportError:
        return df

    df = df.copy()

    for col in ("p_tube", "p_line"):
        if col not in df.columns:
            continue

        df[f"{col}_raw"] = df[col].values.copy()

        values = df[col].values.astype(float)
        valid_count = int(np.sum(~np.isnan(values)))

        if valid_count < polyorder + 2:
            continue

        w = min(window, valid_count)
        if w % 2 == 0:
            w -= 1
        if w < polyorder + 2:
            continue

        # Интерполяция NaN перед фильтрацией
        s = pd.Series(values).interpolate(method="nearest").values

        for _ in range(passes):
            s = savgol_filter(s, window_length=w, polyorder=polyorder)
            s = np.clip(s, 0, None)

        df[col] = s

    return df
