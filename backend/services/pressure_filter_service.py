"""
pressure_filter_service — фильтрация и очистка временных рядов давлений.

Все фильтры работают с pandas Series, без зависимости от БД.
Вызывается из API endpoint /api/pressure/chart/{well_id} при включённых фильтрах.

Фильтры:
  1. flag_false_zeros   — убирает 0.0 (ложные нули датчиков)
  2. hampel_filter      — обнаружение и замена спайков (Hampel identifier)
  3. fill_gaps           — заполнение коротких пропусков (ffill / interpolation)
  4. filter_pressure_pair — координированная обработка пары p_tube + p_line
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 1. Убрать ложные нули
# ═══════════════════════════════════════════════════════════

def flag_false_zeros(series: pd.Series) -> tuple[pd.Series, int]:
    """
    Заменяет 0.0 на NaN — давление на скважине не может быть 0 атм.

    Returns:
        (очищенная Series, количество убранных нулей)
    """
    mask = series == 0.0
    count = int(mask.sum())
    if count > 0:
        series = series.copy()
        series[mask] = np.nan
    return series, count


# ═══════════════════════════════════════════════════════════
# 2. Hampel-фильтр (обнаружение спайков)
# ═══════════════════════════════════════════════════════════

def hampel_filter(
    series: pd.Series,
    window_half: int = 3,
    n_sigma: float = 3.5,
) -> tuple[pd.Series, int]:
    """
    Hampel identifier — робастный фильтр для обнаружения выбросов
    во временных рядах.

    Алгоритм:
      Для каждой точки x[i]:
        1. Берём окно [i - window_half, i + window_half]
        2. Вычисляем медиану окна (m)
        3. Вычисляем MAD = median(|x - m|) — робастная оценка разброса
        4. σ_est = 1.4826 * MAD (масштаб к нормальному распределению)
        5. Если |x[i] - m| > n_sigma * σ_est → спайк → заменить на m

    Преимущества Hampel:
      - Робастен к выбросам (использует медиану, а не среднее)
      - Не реагирует на продувки (плавный тренд не создаёт отклонения)
      - Ловит мгновенные спайки (1-2 точки)

    Args:
        series: временной ряд давления
        window_half: полуширина окна (полное окно = 2*window_half + 1)
        n_sigma: порог в единицах σ (MAD-based)

    Returns:
        (отфильтрованная Series, количество обнаруженных спайков)
    """
    if len(series) < 2 * window_half + 1:
        return series, 0

    result = series.copy()
    values = series.values.astype(float)
    n = len(values)
    spike_count = 0

    # Константа пересчёта MAD → σ для нормального распределения
    K = 1.4826

    for i in range(window_half, n - window_half):
        # Окно вокруг точки i
        window = values[max(0, i - window_half): i + window_half + 1]

        # Убираем NaN из окна
        valid = window[~np.isnan(window)]
        if len(valid) < 3:
            continue

        median_val = np.median(valid)
        mad = np.median(np.abs(valid - median_val))

        # Текущее значение
        current = values[i]
        if np.isnan(current):
            continue

        deviation = abs(current - median_val)

        # Минимальный абсолютный порог (атм).
        # Отклонения < min_abs_dev не считаются спайками,
        # даже если MAD очень мал. Это защищает от ложных срабатываний
        # на нормальном шуме квантования LoRa-датчиков (шаг 0.1 атм).
        min_abs_dev = 1.0  # атм

        if mad < 1e-10:
            # MAD ≈ 0 — почти все значения в окне одинаковые.
            # Используем абсолютный порог
            if deviation > min_abs_dev:
                result.iloc[i] = median_val
                spike_count += 1
        else:
            sigma_est = K * mad
            threshold = max(n_sigma * sigma_est, min_abs_dev)

            # Проверяем отклонение
            if deviation > threshold:
                result.iloc[i] = median_val
                spike_count += 1

    return result, spike_count


# ═══════════════════════════════════════════════════════════
# 3. Заполнение пропусков
# ═══════════════════════════════════════════════════════════

def fill_gaps(
    series: pd.Series,
    timestamps: pd.DatetimeIndex,
    mode: str = "none",
    max_gap_min: int = 10,
) -> tuple[pd.Series, int]:
    """
    Заполняет пропуски (NaN) во временном ряду.

    Modes:
      - "none"        — без заполнения
      - "ffill"       — forward-fill (последнее известное значение)
      - "interpolate" — линейная интерполяция

    max_gap_min:
      Не заполнять пропуски длиннее N минут.
      Для данных с шагом 1 мин: limit = max_gap_min.

    Returns:
        (заполненная Series, количество заполненных точек)
    """
    if mode == "none" or mode is None:
        return series, 0

    # Определяем типичный шаг данных для расчёта limit
    if len(timestamps) >= 2:
        # Медианный шаг в минутах
        diffs = pd.Series(timestamps).diff().dropna()
        median_step_min = diffs.median().total_seconds() / 60
        if median_step_min < 0.5:
            median_step_min = 1.0  # не менее 1 мин
        limit = max(1, int(max_gap_min / median_step_min))
    else:
        limit = max_gap_min  # fallback

    nan_before = int(series.isna().sum())
    result = series.copy()

    if mode == "ffill":
        result = result.ffill(limit=limit)
    elif mode == "interpolate":
        result = result.interpolate(method="linear", limit=limit, limit_direction="forward")
    else:
        log.warning("Unknown fill mode: %s, skipping", mode)
        return series, 0

    nan_after = int(result.isna().sum())
    filled = nan_before - nan_after

    return result, filled


# ═══════════════════════════════════════════════════════════
# 4. Координированная фильтрация пары давлений
# ═══════════════════════════════════════════════════════════

def filter_pressure_pair(
    p_tube: list,
    p_line: list,
    timestamps: list,
    filter_zeros: bool = False,
    filter_spikes: bool = False,
    fill_mode: str = "none",
    max_gap_min: int = 10,
) -> dict:
    """
    Применяет цепочку фильтров к паре давлений (p_tube, p_line).

    Порядок фильтрации:
      1. flag_false_zeros  — 0.0 → NaN
      2. hampel_filter     — спайки → медиана
      3. fill_gaps          — NaN → ffill/interpolate

    Args:
        p_tube: список значений давления устья (Ptr)
        p_line: список значений давления шлейфа (Pshl)
        timestamps: список временных меток (ISO строки или datetime)
        filter_zeros: убирать 0.0
        filter_spikes: применять Hampel-фильтр
        fill_mode: "none" | "ffill" | "interpolate"
        max_gap_min: максимальный пропуск для заполнения (мин)

    Returns:
        {
            "p_tube": list[float|None],
            "p_line": list[float|None],
            "timestamps": list[str],
            "stats": {
                "zeros_removed": int,
                "spikes_detected": int,
                "gaps_filled": int,
                "total_points": int,
            }
        }
    """
    # Конвертируем в pandas
    ts_index = pd.DatetimeIndex(pd.to_datetime(timestamps, format="mixed"))
    s_tube = pd.Series(p_tube, index=ts_index, dtype=float)
    s_line = pd.Series(p_line, index=ts_index, dtype=float)

    stats = {
        "zeros_removed": 0,
        "spikes_detected": 0,
        "gaps_filled": 0,
        "total_points": len(timestamps),
    }

    # ── Шаг 1: Убрать ложные нули ──
    if filter_zeros:
        s_tube, z1 = flag_false_zeros(s_tube)
        s_line, z2 = flag_false_zeros(s_line)
        stats["zeros_removed"] = z1 + z2

    # ── Шаг 2: Hampel-фильтр (спайки) ──
    if filter_spikes:
        s_tube, sp1 = hampel_filter(s_tube)
        s_line, sp2 = hampel_filter(s_line)
        stats["spikes_detected"] = sp1 + sp2

    # ── Шаг 3: Заполнение пропусков ──
    if fill_mode and fill_mode != "none":
        s_tube, f1 = fill_gaps(s_tube, ts_index, mode=fill_mode, max_gap_min=max_gap_min)
        s_line, f2 = fill_gaps(s_line, ts_index, mode=fill_mode, max_gap_min=max_gap_min)
        stats["gaps_filled"] = f1 + f2

    # Конвертируем обратно в списки (NaN → None)
    def to_list(s):
        return [None if pd.isna(v) else round(float(v), 3) for v in s]

    return {
        "p_tube": to_list(s_tube),
        "p_line": to_list(s_line),
        "timestamps": [str(t) for t in timestamps],
        "stats": stats,
    }


# ═══════════════════════════════════════════════════════════
# 5. Агрегация отфильтрованных данных по интервалу
# ═══════════════════════════════════════════════════════════

def aggregate_filtered(
    p_tube: list,
    p_line: list,
    timestamps: list,
    interval_min: int = 5,
) -> list[dict]:
    """
    Агрегирует отфильтрованные данные по временному интервалу.
    Аналог SQL GROUP BY bucket — но в pandas.

    Args:
        p_tube: отфильтрованный список давлений устья
        p_line: отфильтрованный список давлений шлейфа
        timestamps: список меток (ISO или datetime)
        interval_min: интервал агрегации в минутах

    Returns:
        Список dict совместимых с существующим форматом API:
        [{"t": ISO, "p_tube_avg": float, ...}, ...]
    """
    if not timestamps:
        return []

    ts_index = pd.DatetimeIndex(pd.to_datetime(timestamps, format="mixed"))
    df = pd.DataFrame({
        "p_tube": pd.array(p_tube, dtype="Float64"),
        "p_line": pd.array(p_line, dtype="Float64"),
    }, index=ts_index)

    # Resample по интервалу
    rule = f"{interval_min}min"
    agg = df.resample(rule).agg(
        p_tube_avg=("p_tube", "mean"),
        p_tube_min=("p_tube", "min"),
        p_tube_max=("p_tube", "max"),
        p_line_avg=("p_line", "mean"),
        p_line_min=("p_line", "min"),
        p_line_max=("p_line", "max"),
        cnt=("p_tube", "count"),  # считаем не-NaN точки
    )

    # Убираем интервалы без данных
    agg = agg.dropna(how="all", subset=["p_tube_avg", "p_line_avg"])

    import math

    def _safe(v):
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, 2)

    results = []
    for bucket_ts, row in agg.iterrows():
        # Пропускаем бакеты где оба давления None/NA
        tube_na = pd.isna(row["p_tube_avg"]) if row["p_tube_avg"] is not None else True
        line_na = pd.isna(row["p_line_avg"]) if row["p_line_avg"] is not None else True
        if tube_na and line_na:
            continue

        results.append({
            "t": bucket_ts.isoformat(),
            "p_tube_avg": _safe(row["p_tube_avg"]),
            "p_tube_min": _safe(row["p_tube_min"]),
            "p_tube_max": _safe(row["p_tube_max"]),
            "p_line_avg": _safe(row["p_line_avg"]),
            "p_line_min": _safe(row["p_line_min"]),
            "p_line_max": _safe(row["p_line_max"]),
            "count": int(row["cnt"]) if not pd.isna(row["cnt"]) else 0,
        })

    return results
