"""
pressure_filter_service — фильтрация и очистка временных рядов давлений.

Все фильтры работают с pandas Series, без зависимости от БД.
Вызывается из API endpoint /api/pressure/chart/{well_id} при включённых фильтрах.

Фильтры:
  1. flag_false_zeros   — убирает 0.0 (ложные нули датчиков)
  2. hampel_filter      — обнаружение и замена спайков (Hampel identifier)
  2a. instant_spike_filter — обнаружение мгновенных скачков |ΔP| > порог
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
    во временных рядах (векторизованная реализация).

    Алгоритм:
      Для каждой точки x[i]:
        1. Берём окно [i - window_half, i + window_half]
        2. Вычисляем медиану окна (m)
        3. Вычисляем MAD = median(|x - m|) — робастная оценка разброса
        4. σ_est = 1.4826 * MAD (масштаб к нормальному распределению)
        5. Если |x[i] - m| > n_sigma * σ_est → спайк → заменить на m

    Args:
        series: временной ряд давления
        window_half: полуширина окна (полное окно = 2*window_half + 1)
        n_sigma: порог в единицах σ (MAD-based)

    Returns:
        (отфильтрованная Series, количество обнаруженных спайков)
    """
    window_size = 2 * window_half + 1
    if len(series) < window_size:
        return series, 0

    K = 1.4826          # MAD → σ scale factor
    min_abs_dev = 1.0   # минимальный абсолютный порог (атм)

    # Rolling median и MAD — vectorized через pandas
    rolling_median = series.rolling(window=window_size, center=True, min_periods=3).median()

    # MAD = median(|x - median|) — нужен .apply, но на маленьком окне это быстро
    # Альтернатива: вычислить отклонение и применить rolling median к нему
    deviation = (series - rolling_median).abs()
    rolling_mad = deviation.rolling(window=window_size, center=True, min_periods=3).median()

    # Порог: max(n_sigma * K * MAD, min_abs_dev)
    sigma_est = K * rolling_mad
    threshold = sigma_est.clip(lower=min_abs_dev / n_sigma) * n_sigma

    # Спайки: отклонение от медианы больше порога
    is_spike = deviation > threshold
    # Не считаем NaN спайками
    is_spike = is_spike & series.notna() & rolling_median.notna()

    spike_count = int(is_spike.sum())
    result = series.copy()
    if spike_count > 0:
        result[is_spike] = rolling_median[is_spike]

    return result, spike_count


# ═══════════════════════════════════════════════════════════
# 2a. Детектор мгновенных скачков (|ΔP| > порог)
# ═══════════════════════════════════════════════════════════

def instant_spike_filter(
    series: pd.Series,
    max_delta: float = 5.0,
) -> tuple[pd.Series, int]:
    """
    Обнаруживает мгновенные скачки давления между соседними точками.

    Если |P[i] - P[i-1]| > max_delta И |P[i] - P[i+1]| > max_delta,
    то P[i] — одиночный спайк (заменяется средним соседей).

    Если скачок устойчивый (P[i] и P[i+1] на одном уровне) —
    это реальное изменение давления (продувка, переключение), не трогаем.

    Args:
        series: временной ряд давления
        max_delta: порог скачка (атм), по умолчанию 5.0

    Returns:
        (отфильтрованная Series, количество обнаруженных спайков)
    """
    if len(series) < 3 or max_delta <= 0:
        return series, 0

    values = series.values.astype(float)

    # Vectorized: сдвиги на 1 назад и вперёд
    prev_vals = np.roll(values, 1)
    next_vals = np.roll(values, -1)

    # Границы не проверяем
    prev_vals[0] = np.nan
    next_vals[-1] = np.nan

    delta_prev = np.abs(values - prev_vals)
    delta_next = np.abs(values - next_vals)

    # Спайк: резкий скачок от обоих соседей, все три не NaN
    is_spike = (
        (delta_prev > max_delta)
        & (delta_next > max_delta)
        & ~np.isnan(values)
        & ~np.isnan(prev_vals)
        & ~np.isnan(next_vals)
    )

    spike_count = int(is_spike.sum())
    if spike_count > 0:
        replaced = values.copy()
        replaced[is_spike] = (prev_vals[is_spike] + next_vals[is_spike]) / 2.0
        return pd.Series(replaced, index=series.index, name=series.name), spike_count

    return series, spike_count


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
    spike_threshold: float = 0.0,
) -> dict:
    """
    Применяет цепочку фильтров к паре давлений (p_tube, p_line).

    Порядок фильтрации:
      0. range filter     — ≤0 или >85 → NaN (всегда)
      1. flag_false_zeros — 0.0 → NaN
      2. hampel_filter    — спайки → медиана (статистический)
      2a. instant_spike   — |ΔP| > порог → среднее соседей (мгновенные скачки)
      3. fill_gaps        — NaN → ffill/interpolate

    Args:
        p_tube: список значений давления устья (Ptr)
        p_line: список значений давления шлейфа (Pshl)
        timestamps: список временных меток (ISO строки или datetime)
        filter_zeros: убирать 0.0
        filter_spikes: применять Hampel-фильтр
        fill_mode: "none" | "ffill" | "interpolate"
        max_gap_min: максимальный пропуск для заполнения (мин)
        spike_threshold: порог мгновенного скачка (атм), 0 = выключен

    Returns:
        {
            "p_tube": list[float|None],
            "p_line": list[float|None],
            "timestamps": list[str],
            "stats": {
                "zeros_removed": int,
                "spikes_detected": int,
                "instant_spikes": int,
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
        "out_of_range": 0,
        "zeros_removed": 0,
        "spikes_detected": 0,
        "instant_spikes": 0,
        "gaps_filled": 0,
        "total_points": len(timestamps),
    }

    # ── Шаг 0: Диапазонный фильтр (≤0 или >85 → NaN) — ВСЕГДА ──
    _P_MAX = 85.0
    for s, label in [(s_tube, "tube"), (s_line, "line")]:
        invalid = (s <= 0) | (s > _P_MAX)
        cnt = int(invalid.sum())
        if cnt > 0:
            s[invalid] = np.nan
            stats["out_of_range"] += cnt
    # Обновляем ссылки после in-place изменений
    s_tube = s_tube
    s_line = s_line

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

    # ── Шаг 2a: Мгновенные скачки |ΔP| > порог ──
    if spike_threshold > 0:
        s_tube, isp1 = instant_spike_filter(s_tube, max_delta=spike_threshold)
        s_line, isp2 = instant_spike_filter(s_line, max_delta=spike_threshold)
        stats["instant_spikes"] = isp1 + isp2

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

    if agg.empty:
        return []

    # Vectorized: округляем и заменяем NaN/inf на None
    for col in ("p_tube_avg", "p_tube_min", "p_tube_max",
                "p_line_avg", "p_line_min", "p_line_max"):
        s = agg[col].round(2)
        # replace inf with NaN, then NaN → None happens via .where
        agg[col] = s.replace([np.inf, -np.inf], np.nan)

    agg["cnt"] = agg["cnt"].fillna(0).astype(int)
    agg.index = agg.index.strftime("%Y-%m-%dT%H:%M:%S")

    # Bulk convert to list of dicts — much faster than iterrows
    records = agg.reset_index().rename(columns={"index": "t"}).to_dict("records")

    results = []
    for r in records:
        # Skip buckets where both pressures are NaN
        if pd.isna(r.get("p_tube_avg")) and pd.isna(r.get("p_line_avg")):
            continue
        results.append({
            "t": r["t"],
            "p_tube_avg": None if pd.isna(r["p_tube_avg"]) else r["p_tube_avg"],
            "p_tube_min": None if pd.isna(r["p_tube_min"]) else r["p_tube_min"],
            "p_tube_max": None if pd.isna(r["p_tube_max"]) else r["p_tube_max"],
            "p_line_avg": None if pd.isna(r["p_line_avg"]) else r["p_line_avg"],
            "p_line_min": None if pd.isna(r["p_line_min"]) else r["p_line_min"],
            "p_line_max": None if pd.isna(r["p_line_max"]) else r["p_line_max"],
            "count": r["cnt"],
        })

    return results
