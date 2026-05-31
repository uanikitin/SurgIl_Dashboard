"""
Предобработка данных давления перед расчётом дебита.

Этапы:
1. Приведение к float, отрицательные/нули → NaN
2. Заполнение КОРОТКИХ пропусков интерполяцией (≤ max_fill_min мин);
   длинные дыры остаются NaN (их нельзя достоверно восстановить)
3. Сглаживание фильтром Савицкого-Голая (опционально)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Порог заполнения пропусков давления по умолчанию (минуты).
# Пропуск датчика (false-zero/непереданное значение) ≤ порога заполняется
# интерполяцией — значение физически было. Дыра длиннее порога остаётся NaN:
# заполнять её = фабриковать данные. Серии false-zero датчика SMOD-PT-60
# наблюдались до ~16 мин, поэтому дефолт 20 покрывает их с запасом.
# Регулируется в дашборде (страница скважины → панель коэффициентов дебита).
DEFAULT_MAX_FILL_MIN = 20


def clean_pressure(
    df: pd.DataFrame,
    max_fill_min: int = DEFAULT_MAX_FILL_MIN,
) -> pd.DataFrame:
    """
    Базовая очистка давления + заполнение коротких пропусков.

    1. Отрицательные и нули → NaN (давление на скважине не может быть ≤ 0).
    2. Короткие пропуски (пропавшая передача датчика — значение физически
       было) заполняются интерполяцией по времени, но НЕ длиннее
       ``max_fill_min`` минут. Более длинные дыры остаются NaN — достоверно
       восстановить их нельзя, заполнение = фабрикация данных.

    Параметры
    ---------
    max_fill_min : int
        Порог заполнения пропусков в минутах. Дыры ≤ порога — интерполяция;
        длиннее — остаются NaN. ``0`` или меньше → без лимита (историческое
        поведение: ffill/bfill заполняет всё). По умолчанию
        ``DEFAULT_MAX_FILL_MIN`` (20). Регулируется в дашборде.
    """
    df = df.copy()
    for col in ("p_tube", "p_line"):
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        df[col] = s.where(s > 0)    # отрицательные и нули → NaN

    if max_fill_min is None or max_fill_min <= 0:
        # Без лимита — историческое поведение (заполнить любую дыру).
        return df.ffill().bfill()

    # Интерполяция только КОРОТКИХ внутренних дыр (≤ max_fill_min точек ≈ минут
    # при поминутном шаге). limit_area="inside" не трогает края.
    method = "time" if isinstance(df.index, pd.DatetimeIndex) else "linear"
    df = df.interpolate(method=method, limit=int(max_fill_min), limit_area="inside")
    # Края — короткий ffill/bfill в пределах того же лимита; длинные хвосты NaN.
    df = df.ffill(limit=int(max_fill_min)).bfill(limit=int(max_fill_min))
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
