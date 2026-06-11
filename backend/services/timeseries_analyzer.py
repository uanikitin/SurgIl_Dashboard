"""
timeseries_analyzer.py
======================

Универсальный модуль анализа временных рядов.
Вынесен из segment_analysis_module.py для переиспользования
в разных контекстах: давление, дебит, любые метрики.

ОСНОВНЫЕ ВОЗМОЖНОСТИ:
    - Детекция changepoints (точек перелома)
    - Сегментация временного ряда
    - Расчёт трендов (линейная регрессия)
    - Классификация типов сегментов
    - Детекция кластеров аномалий

ТОЧКИ ВХОДА:
    - analyze_timeseries(df, config) → dict
    - detect_changepoints(series, config) → list[int]
    - compute_segments(series, boundaries, config) → list[dict]

ЗАВИСИМОСТИ:
    - numpy
    - pandas
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple


# ════════════════════════════════════════════════════════════════════
# 1. Конфигурация анализа (пороги)
# ════════════════════════════════════════════════════════════════════

@dataclass
class AnalyzerConfig:
    """Конфигурация порогов для анализа временных рядов."""

    # ═══ Чувствительность детекции (1-10, где 1=грубо, 10=чувствительно) ═══
    sensitivity: int = 5  # дефолт - средняя чувствительность

    # Детекция changepoints (базовые значения, модифицируются sensitivity)
    changepoint_threshold_pct: float = 15.0   # скачок от медианы %
    merge_close_cp_days: int = 6              # объединение близких CP
    min_segment_days: int = 12                # минимальная длина сегмента (часов)
    edge_margin_days: int = 3                 # отступ от края периода

    # Требование к R² для подтверждения changepoint
    min_rsq_for_cp: float = 0.3               # минимальный R² тренда в сегменте

    # Классификация изменений
    change_notable_pct: float = 10.0          # порог "заметно"
    change_significant_pct: float = 20.0      # порог "существенно"
    change_dramatic_pct: float = 30.0         # порог "резко"

    # Тренды
    gradual_trend_min_days: int = 6           # минимум точек для тренда
    gradual_trend_min_drift_pct: float = 8.0  # минимальный дрейф %

    # Кластеры аномалий
    anomaly_threshold: float = 600.0          # порог аномалии
    cluster_min_days: int = 2                 # минимум дней в кластере
    cluster_merge_gap: int = 2                # склейка кластеров

    # Дополнительные метрики
    secondary_series_names: List[str] = field(default_factory=list)

    def apply_sensitivity(self) -> "AnalyzerConfig":
        """Применить sensitivity к порогам."""
        # sensitivity 1-10: 1=грубо (мало CP), 10=чувствительно (много CP)
        s = max(1, min(10, self.sensitivity))

        # Порог скачка: от 25% (s=1) до 8% (s=10)
        self.changepoint_threshold_pct = 25.0 - (s - 1) * 1.9

        # Минимальная длина сегмента: от 24 (s=1) до 6 (s=10) часов
        self.min_segment_days = int(24 - (s - 1) * 2)

        # Объединение близких CP: от 12 (s=1) до 3 (s=10)
        self.merge_close_cp_days = int(12 - (s - 1))

        return self


def default_config() -> AnalyzerConfig:
    """Создать конфигурацию с дефолтными значениями."""
    return AnalyzerConfig()


# ════════════════════════════════════════════════════════════════════
# 2. Детекция changepoints
# ════════════════════════════════════════════════════════════════════

def detect_changepoints(
    series: np.ndarray,
    config: AnalyzerConfig,
    secondary_series: Optional[np.ndarray] = None
) -> List[int]:
    """
    Детекция точек перелома во временном ряде.

    Двухэтапный алгоритм:
    1. Детекция скачков уровня (level shifts)
    2. Детекция разворотов тренда (trend reversals) - локальные экстремумы

    Args:
        series: основной временной ряд (numpy array)
        config: конфигурация порогов
        secondary_series: опциональный вторичный ряд для подтверждения CP

    Returns:
        Список индексов changepoints
    """
    n = len(series)
    if n < config.min_segment_days * 2:
        return []

    # Сглаживание (сильнее для низкой чувствительности)
    smooth_window = max(3, 15 - config.sensitivity)
    smoothed = _rolling_median(series, smooth_window)

    # Базовые статистики
    global_median = np.nanmedian(series)
    global_std = np.nanstd(series)
    if global_median == 0 or not np.isfinite(global_median):
        global_median = 1.0
    if global_std == 0 or not np.isfinite(global_std):
        global_std = global_median * 0.1

    threshold = global_median * config.changepoint_threshold_pct / 100.0

    # ═══ ЭТАП 1: Детекция скачков уровня ═══
    level_cps = _detect_level_shifts(smoothed, threshold, config)

    # ═══ ЭТАП 2: Детекция разворотов тренда (локальные max/min) ═══
    trend_cps = _detect_trend_reversals(smoothed, config)

    # Объединяем и сортируем
    all_cps = sorted(set(level_cps + trend_cps))

    # Фильтрация коротких сегментов
    filtered_cps = _filter_short_segments(all_cps, n, config.min_segment_days)

    # Объединение близких CP
    merged_cps = _merge_close_cps(filtered_cps, config.merge_close_cp_days)

    return merged_cps


def _detect_level_shifts(
    smoothed: np.ndarray,
    threshold: float,
    config: AnalyzerConfig
) -> List[int]:
    """Детекция скачков уровня."""
    n = len(smoothed)
    candidates = []
    half_window = max(6, config.min_segment_days // 2)

    for i in range(config.edge_margin_days, n - config.edge_margin_days):
        left_start = max(0, i - half_window)
        right_end = min(n, i + half_window)

        left_data = smoothed[left_start:i]
        right_data = smoothed[i:right_end]

        if len(left_data) < 3 or len(right_data) < 3:
            continue

        left_mean = np.nanmean(left_data)
        right_mean = np.nanmean(right_data)

        if not np.isfinite(left_mean) or not np.isfinite(right_mean):
            continue

        jump = abs(right_mean - left_mean)

        if jump >= threshold:
            candidates.append((i, jump))

    # Выбираем локальные максимумы
    return _select_local_maxima_simple(candidates, config.merge_close_cp_days)


def _detect_trend_reversals(
    smoothed: np.ndarray,
    config: AnalyzerConfig
) -> List[int]:
    """
    Детекция разворотов тренда - локальные максимумы и минимумы.
    Находит точки где тренд меняет направление (рост→падение или падение→рост).
    """
    n = len(smoothed)
    if n < 20:
        return []

    # Размер окна зависит от чувствительности
    # sensitivity 1 → window=20 (только крупные развороты)
    # sensitivity 10 → window=4 (мелкие развороты тоже)
    window = max(4, 22 - config.sensitivity * 2)
    half_w = window // 2

    reversals = []

    # Поиск локальных экстремумов через сравнение трендов слева и справа
    for i in range(window, n - window):
        local_region = smoothed[max(0, i - window):min(n, i + window + 1)]
        val = smoothed[i]

        if len(local_region) < 5:
            continue

        local_max = np.nanmax(local_region)
        local_min = np.nanmin(local_region)
        local_range = local_max - local_min

        if local_range < 0.5:  # слишком плоский участок
            continue

        # Порог значимости экстремума (% от локального диапазона)
        # sensitivity 10 → 10% достаточно
        # sensitivity 1 → 40% нужно
        threshold_pct = 0.45 - config.sensitivity * 0.035  # от 0.415 до 0.10

        left_vals = smoothed[max(0, i - half_w):i]
        right_vals = smoothed[i + 1:min(n, i + half_w + 1)]

        if len(left_vals) < 2 or len(right_vals) < 2:
            continue

        left_trend = (left_vals[-1] - left_vals[0]) if len(left_vals) > 0 else 0
        right_trend = (right_vals[-1] - right_vals[0]) if len(right_vals) > 0 else 0

        # Локальный максимум: рост слева, падение справа
        if val >= local_max - local_range * threshold_pct:
            if left_trend > 0 and right_trend < 0:
                reversals.append(i)

        # Локальный минимум: падение слева, рост справа
        elif val <= local_min + local_range * threshold_pct:
            if left_trend < 0 and right_trend > 0:
                reversals.append(i)

    return reversals


def _select_local_maxima_simple(candidates: List[Tuple[int, float]], min_gap: int) -> List[int]:
    """Простой выбор локальных максимумов."""
    if not candidates:
        return []

    sorted_cands = sorted(candidates, key=lambda x: -x[1])
    selected = []

    for idx, score in sorted_cands:
        too_close = any(abs(idx - s) < min_gap for s in selected)
        if not too_close:
            selected.append(idx)

    return sorted(selected)


def _quick_slope(data: np.ndarray) -> float:
    """Быстрый расчёт наклона для небольшого массива."""
    n = len(data)
    if n < 2:
        return 0.0
    x = np.arange(n, dtype=float)
    valid_mask = np.isfinite(data)
    if valid_mask.sum() < 2:
        return 0.0
    x_valid = x[valid_mask]
    y_valid = data[valid_mask]
    # Нормализованный slope
    mean_y = np.mean(y_valid)
    if mean_y == 0:
        return 0.0
    slope = np.polyfit(x_valid, y_valid, 1)[0]
    return slope / mean_y  # относительный slope


def _select_local_maxima(candidates: List[Tuple[int, float, float]], min_gap: int) -> List[int]:
    """Выбор локальных максимумов из кандидатов."""
    if not candidates:
        return []

    # Сортируем по score (убывание)
    sorted_cands = sorted(candidates, key=lambda x: -x[1])

    selected = []
    for idx, score, jump in sorted_cands:
        # Проверяем что не слишком близко к уже выбранным
        too_close = any(abs(idx - s) < min_gap for s in selected)
        if not too_close:
            selected.append(idx)

    return sorted(selected)


def _validate_changepoints(
    series: np.ndarray,
    cps: List[int],
    config: AnalyzerConfig
) -> List[int]:
    """
    Валидация changepoints: оставляем только те, что реально
    разделяют сегменты с разными характеристиками.
    """
    if not cps:
        return []

    n = len(series)
    validated = []
    boundaries = [0] + cps + [n]

    for i, cp in enumerate(cps):
        left_start = boundaries[i]
        left_end = cp
        right_start = cp
        right_end = boundaries[i + 2] if i + 2 < len(boundaries) else n

        left_data = series[left_start:left_end]
        right_data = series[right_start:right_end]

        left_valid = left_data[np.isfinite(left_data)]
        right_valid = right_data[np.isfinite(right_data)]

        if len(left_valid) < 2 or len(right_valid) < 2:
            continue

        # Критерий 1: разница средних
        left_mean = np.mean(left_valid)
        right_mean = np.mean(right_valid)
        global_mean = np.nanmean(series)
        mean_diff_pct = abs(right_mean - left_mean) / max(abs(global_mean), 0.01) * 100

        # Критерий 2: разница трендов
        left_slope, _, left_rsq = _linear_regression(left_data)
        right_slope, _, right_rsq = _linear_regression(right_data)

        # Нормализуем slopes относительно глобального среднего
        left_slope_norm = left_slope / max(abs(global_mean), 0.01)
        right_slope_norm = right_slope / max(abs(global_mean), 0.01)
        slope_diff = abs(right_slope_norm - left_slope_norm)

        # Критерий 3: смена направления тренда
        trend_reversal = (left_slope > 0.01 and right_slope < -0.01) or \
                        (left_slope < -0.01 and right_slope > 0.01)

        # Критерий 4: резкое изменение уровня (даже при схожих трендах)
        level_jump = abs(right_valid[0] - left_valid[-1]) if len(left_valid) > 0 and len(right_valid) > 0 else 0
        level_jump_pct = level_jump / max(abs(global_mean), 0.01) * 100

        # CP валиден если выполняется ЛЮБОЙ из критериев:
        threshold_pct = config.changepoint_threshold_pct

        mean_criterion = mean_diff_pct >= threshold_pct * 0.5
        slope_criterion = slope_diff >= 0.01  # 1% изменение slope в час
        jump_criterion = level_jump_pct >= threshold_pct * 0.8

        if mean_criterion or slope_criterion or trend_reversal or jump_criterion:
            validated.append(cp)

    return validated


def _rolling_median(series: np.ndarray, window: int) -> np.ndarray:
    """Скользящая медиана с центрированием."""
    n = len(series)
    result = np.empty(n)
    half = window // 2

    for i in range(n):
        start = max(0, i - half)
        end = min(n, i + half + 1)
        result[i] = np.nanmedian(series[start:end])

    return result


def _filter_short_segments(cps: List[int], n: int, min_days: int) -> List[int]:
    """Удаление CP, создающих слишком короткие сегменты."""
    if not cps:
        return []

    filtered = []
    prev = 0

    for cp in cps:
        if cp - prev >= min_days:
            filtered.append(cp)
            prev = cp

    # Проверка последнего сегмента
    if filtered and n - filtered[-1] < min_days:
        filtered.pop()

    return filtered


def _merge_close_cps(cps: List[int], merge_gap: int) -> List[int]:
    """Объединение близких changepoints (берём средний)."""
    if not cps:
        return []

    merged = []
    group = [cps[0]]

    for i in range(1, len(cps)):
        if cps[i] - cps[i-1] <= merge_gap:
            group.append(cps[i])
        else:
            merged.append(int(np.median(group)))
            group = [cps[i]]

    merged.append(int(np.median(group)))
    return merged


# ════════════════════════════════════════════════════════════════════
# 3. Сегментация и расчёт характеристик
# ════════════════════════════════════════════════════════════════════

@dataclass
class Segment:
    """Сегмент временного ряда."""
    num: int                     # порядковый номер
    start_idx: int               # индекс начала
    end_idx: int                 # индекс конца (exclusive)
    days: int                    # длительность

    # Характеристики основного ряда
    mean_value: float            # среднее значение
    std_value: float             # стандартное отклонение
    min_value: float             # минимум
    max_value: float             # максимум

    # Тренд (линейная регрессия)
    slope: float                 # наклон тренда
    intercept: float             # пересечение
    r_squared: float             # коэффициент детерминации

    # Изменение относительно предыдущего
    change_pct: Optional[float] = None
    change_abs: Optional[float] = None

    # Классификация
    segment_type: str = "stable"  # stable, rise, decline, sharp_rise, sharp_decline
    trend_direction: Optional[str] = None  # up, down, flat

    # Дополнительные метрики (для вторичных рядов)
    secondary_means: Dict[str, float] = field(default_factory=dict)
    secondary_slopes: Dict[str, float] = field(default_factory=dict)

    # Ручные корректировки
    comment: str = ""            # комментарий пользователя
    is_manual: bool = False      # создан вручную
    trim_left: int = 0           # отступ слева для тренда (часов)
    trim_right: int = 0          # отступ справа для тренда (часов)

    def to_dict(self) -> Dict[str, Any]:
        """Конвертация в словарь."""
        return {
            "num": self.num,
            "start_idx": self.start_idx,
            "end_idx": self.end_idx,
            "days": self.days,
            "mean_value": self.mean_value,
            "std_value": self.std_value,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "slope": self.slope,
            "intercept": self.intercept,
            "r_squared": self.r_squared,
            "change_pct": self.change_pct,
            "change_abs": self.change_abs,
            "segment_type": self.segment_type,
            "trend_direction": self.trend_direction,
            "secondary_means": self.secondary_means,
            "secondary_slopes": self.secondary_slopes,
            "comment": self.comment,
            "is_manual": self.is_manual,
            "trim_left": self.trim_left,
            "trim_right": self.trim_right,
        }


def compute_segments(
    series: np.ndarray,
    changepoints: List[int],
    config: AnalyzerConfig,
    dates: Optional[List[str]] = None,
    secondary_series: Optional[Dict[str, np.ndarray]] = None
) -> List[Segment]:
    """
    Разбиение ряда на сегменты по changepoints и расчёт характеристик.

    Args:
        series: основной временной ряд
        changepoints: список индексов changepoints
        config: конфигурация
        dates: опциональный список дат (для отладки)
        secondary_series: словарь {name: array} вторичных рядов

    Returns:
        Список сегментов с характеристиками
    """
    n = len(series)
    if n == 0:
        return []

    # Границы сегментов
    boundaries = [0] + changepoints + [n]
    segments = []
    prev_mean = None

    for i in range(len(boundaries) - 1):
        start = boundaries[i]
        end = boundaries[i + 1]

        segment_data = series[start:end]
        valid_data = segment_data[np.isfinite(segment_data)]

        if len(valid_data) == 0:
            continue

        # Базовые статистики
        mean_val = float(np.mean(valid_data))
        std_val = float(np.std(valid_data)) if len(valid_data) > 1 else 0.0
        min_val = float(np.min(valid_data))
        max_val = float(np.max(valid_data))

        # Линейная регрессия
        slope, intercept, r_sq = _linear_regression(segment_data)

        # Изменение относительно предыдущего сегмента
        change_pct = None
        change_abs = None
        if prev_mean is not None and prev_mean != 0:
            change_abs = mean_val - prev_mean
            change_pct = (change_abs / abs(prev_mean)) * 100

        # Классификация сегмента
        days = end - start
        seg_type = _classify_segment(change_pct, slope, mean_val, config, days)
        trend_dir = _classify_trend(slope, mean_val, days, config)

        # Вторичные ряды
        sec_means = {}
        sec_slopes = {}
        if secondary_series:
            for name, sec_arr in secondary_series.items():
                if start < len(sec_arr) and end <= len(sec_arr):
                    sec_data = sec_arr[start:end]
                    valid_sec = sec_data[np.isfinite(sec_data)]
                    if len(valid_sec) > 0:
                        sec_means[name] = float(np.mean(valid_sec))
                        sec_slope, _, _ = _linear_regression(sec_data)
                        sec_slopes[name] = sec_slope

        segment = Segment(
            num=len(segments) + 1,
            start_idx=start,
            end_idx=end,
            days=end - start,
            mean_value=mean_val,
            std_value=std_val,
            min_value=min_val,
            max_value=max_val,
            slope=slope,
            intercept=intercept,
            r_squared=r_sq,
            change_pct=change_pct,
            change_abs=change_abs,
            segment_type=seg_type,
            trend_direction=trend_dir,
            secondary_means=sec_means,
            secondary_slopes=sec_slopes,
        )

        segments.append(segment)
        prev_mean = mean_val

    return segments


def _linear_regression(
    data: np.ndarray,
    trim_left: int = 0,
    trim_right: int = 0,
    exclude_mask: Optional[np.ndarray] = None
) -> Tuple[float, float, float]:
    """
    Линейная регрессия для тренда с поддержкой отступов и исключений.

    Args:
        data: массив данных
        trim_left: сколько точек отступить слева
        trim_right: сколько точек отступить справа
        exclude_mask: маска исключённых точек (True = исключить)

    Returns:
        (slope, intercept, r_squared)
    """
    n = len(data)

    # Применяем отступы
    start_idx = min(trim_left, n - 2)
    end_idx = max(start_idx + 2, n - trim_right)

    if end_idx <= start_idx:
        start_idx = 0
        end_idx = n

    trimmed_data = data[start_idx:end_idx]

    # Создаём маску валидных данных
    valid_mask = np.isfinite(trimmed_data)

    # Применяем маску исключений если есть
    if exclude_mask is not None and len(exclude_mask) == len(data):
        trimmed_exclude = exclude_mask[start_idx:end_idx]
        valid_mask = valid_mask & ~trimmed_exclude

    valid_indices = np.where(valid_mask)[0]
    valid_values = trimmed_data[valid_mask]

    if len(valid_values) < 2:
        return 0.0, float(valid_values[0]) if len(valid_values) == 1 else 0.0, 0.0

    # Корректируем индексы с учётом отступа
    x = (valid_indices + start_idx).astype(float)
    y = valid_values

    # Простая линейная регрессия
    n_pts = len(x)
    sum_x = np.sum(x)
    sum_y = np.sum(y)
    sum_xy = np.sum(x * y)
    sum_x2 = np.sum(x * x)

    denom = n_pts * sum_x2 - sum_x * sum_x
    if abs(denom) < 1e-10:
        return 0.0, float(np.mean(y)), 0.0

    slope = (n_pts * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n_pts

    # R-squared
    y_pred = slope * x + intercept
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)

    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    return float(slope), float(intercept), float(max(0, min(1, r_squared)))


def recalculate_segment_trend(
    series: np.ndarray,
    segment: Segment,
    trim_left: int = 0,
    trim_right: int = 0,
    exclude_ranges: Optional[List[Tuple[int, int]]] = None
) -> Segment:
    """
    Пересчитать тренд сегмента с учётом отступов и исключённых областей.

    Args:
        series: полный временной ряд
        segment: сегмент для пересчёта
        trim_left: отступ слева (часов)
        trim_right: отступ справа (часов)
        exclude_ranges: список пар (start, end) для исключения

    Returns:
        Обновлённый сегмент
    """
    segment_data = series[segment.start_idx:segment.end_idx]

    # Создаём маску исключений
    exclude_mask = None
    if exclude_ranges:
        exclude_mask = np.zeros(len(segment_data), dtype=bool)
        for ex_start, ex_end in exclude_ranges:
            # Преобразуем глобальные индексы в локальные
            local_start = max(0, ex_start - segment.start_idx)
            local_end = min(len(segment_data), ex_end - segment.start_idx)
            if local_start < local_end:
                exclude_mask[local_start:local_end] = True

    # Пересчитываем регрессию
    slope, intercept, r_sq = _linear_regression(
        segment_data,
        trim_left=trim_left,
        trim_right=trim_right,
        exclude_mask=exclude_mask
    )

    # Пересчитываем статистики без исключённых точек
    valid_data = segment_data.copy()
    if exclude_mask is not None:
        valid_data = valid_data[~exclude_mask]
    valid_data = valid_data[np.isfinite(valid_data)]

    if len(valid_data) > 0:
        segment.mean_value = float(np.mean(valid_data))
        segment.std_value = float(np.std(valid_data)) if len(valid_data) > 1 else 0.0
        segment.min_value = float(np.min(valid_data))
        segment.max_value = float(np.max(valid_data))

    segment.slope = slope
    segment.intercept = intercept
    segment.r_squared = r_sq
    segment.trim_left = trim_left
    segment.trim_right = trim_right

    return segment


def _classify_segment(
    change_pct: Optional[float],
    slope: float,
    mean_val: float,
    config: AnalyzerConfig,
    days: int = 1
) -> str:
    """
    Классификация типа сегмента.

    ПРИОРИТЕТ: slope (тренд внутри сегмента) определяет тип.
    change_pct используется только если slope близок к нулю.
    """
    if change_pct is None:
        return "initial"

    # Вычисляем дрейф по slope: насколько значение изменится за период
    slope_drift_pct = 0.0
    if mean_val != 0 and days > 0:
        slope_drift_pct = (slope * days) / mean_val * 100.0

    # Порог минимального тренда — 5% за период
    MIN_TREND_PCT = 5.0

    # ПРАВИЛО 1: Slope показывает явный тренд → используем slope
    if abs(slope_drift_pct) >= MIN_TREND_PCT:
        if slope_drift_pct >= config.change_dramatic_pct:
            return "sharp_rise"
        elif slope_drift_pct <= -config.change_dramatic_pct:
            return "sharp_decline"
        elif slope_drift_pct > 0:
            return "rise"
        else:
            return "decline"

    # ПРАВИЛО 2: Slope слабый (<5%) — смотрим на изменение среднего относительно предыдущего сегмента
    abs_change = abs(change_pct)

    if abs_change < config.change_notable_pct:
        return "stable"
    elif change_pct >= config.change_dramatic_pct:
        return "sharp_rise"
    elif change_pct <= -config.change_dramatic_pct:
        return "sharp_decline"
    elif change_pct >= config.change_notable_pct:
        return "rise"
    elif change_pct <= -config.change_notable_pct:
        return "decline"

    return "stable"


def _classify_trend(
    slope: float,
    mean_val: float,
    days: int,
    config: AnalyzerConfig
) -> Optional[str]:
    """Классификация направления тренда внутри сегмента."""
    if days < config.gradual_trend_min_days:
        return None

    if mean_val == 0:
        return None

    # Дрейф за период: slope * days / mean * 100%
    drift_pct = abs(slope * days / mean_val * 100)

    if drift_pct < config.gradual_trend_min_drift_pct:
        return "flat"

    return "up" if slope > 0 else "down"


# ════════════════════════════════════════════════════════════════════
# 4. Детекция кластеров аномалий
# ════════════════════════════════════════════════════════════════════

@dataclass
class AnomalyCluster:
    """Кластер аномальных значений."""
    start_idx: int
    end_idx: int
    total_anomaly: float  # сумма/интенсивность аномалий

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_idx": self.start_idx,
            "end_idx": self.end_idx,
            "total_anomaly": self.total_anomaly,
        }


def detect_anomaly_clusters(
    series: np.ndarray,
    config: AnalyzerConfig
) -> List[AnomalyCluster]:
    """
    Детекция кластеров аномальных значений.

    Типичное применение: простои (shutdown_min > 600), выбросы давления.

    Args:
        series: временной ряд (например, shutdown_min или outlier_score)
        config: конфигурация

    Returns:
        Список кластеров аномалий
    """
    n = len(series)
    if n == 0:
        return []

    # Маска аномальных дней
    anomaly_mask = series >= config.anomaly_threshold

    # Поиск непрерывных кластеров
    clusters = []
    i = 0

    while i < n:
        if anomaly_mask[i]:
            start = i
            total = 0.0

            while i < n and anomaly_mask[i]:
                total += series[i]
                i += 1

            end = i

            if end - start >= config.cluster_min_days:
                clusters.append(AnomalyCluster(
                    start_idx=start,
                    end_idx=end,
                    total_anomaly=total
                ))
        else:
            i += 1

    # Склейка близких кластеров
    merged = _merge_close_clusters(clusters, config.cluster_merge_gap)

    return merged


def _merge_close_clusters(
    clusters: List[AnomalyCluster],
    merge_gap: int
) -> List[AnomalyCluster]:
    """Склейка близких кластеров."""
    if len(clusters) <= 1:
        return clusters

    merged = [clusters[0]]

    for cl in clusters[1:]:
        prev = merged[-1]

        if cl.start_idx - prev.end_idx <= merge_gap:
            # Объединяем
            merged[-1] = AnomalyCluster(
                start_idx=prev.start_idx,
                end_idx=cl.end_idx,
                total_anomaly=prev.total_anomaly + cl.total_anomaly
            )
        else:
            merged.append(cl)

    return merged


# ════════════════════════════════════════════════════════════════════
# 5. Главная точка входа
# ════════════════════════════════════════════════════════════════════

@dataclass
class AnalysisResult:
    """Результат анализа временного ряда."""
    ok: bool
    n_points: int

    # Основные результаты
    changepoints: List[int]
    segments: List[Segment]
    anomaly_clusters: List[AnomalyCluster]

    # Сырые данные (для визуализации)
    dates: List[str]
    values: List[float]
    secondary_values: Dict[str, List[float]]

    # Метаданные
    config_used: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "n_points": self.n_points,
            "changepoints": self.changepoints,
            "segments": [s.to_dict() for s in self.segments],
            "anomaly_clusters": [c.to_dict() for c in self.anomaly_clusters],
            "dates": self.dates,
            "values": self.values,
            "secondary_values": self.secondary_values,
            "config_used": self.config_used,
        }


def analyze_timeseries(
    df: pd.DataFrame,
    primary_column: str,
    date_column: str = "date",
    config: Optional[AnalyzerConfig] = None,
    secondary_columns: Optional[List[str]] = None,
    anomaly_column: Optional[str] = None
) -> AnalysisResult:
    """
    Полный анализ временного ряда.

    Args:
        df: DataFrame с данными
        primary_column: имя колонки основного ряда для анализа
        date_column: имя колонки с датами
        config: конфигурация (опционально, берётся дефолтная)
        secondary_columns: список колонок вторичных рядов
        anomaly_column: колонка для детекции кластеров аномалий

    Returns:
        AnalysisResult с полным результатом анализа
    """
    if config is None:
        config = default_config()

    # Валидация
    if df.empty or primary_column not in df.columns:
        return AnalysisResult(
            ok=False,
            n_points=0,
            changepoints=[],
            segments=[],
            anomaly_clusters=[],
            dates=[],
            values=[],
            secondary_values={},
            config_used={}
        )

    # Извлечение данных
    series = df[primary_column].values.astype(float)
    dates = df[date_column].astype(str).tolist() if date_column in df.columns else []
    n_points = len(series)

    # Вторичные ряды
    secondary_series = {}
    secondary_values = {}
    if secondary_columns:
        for col in secondary_columns:
            if col in df.columns:
                arr = df[col].values.astype(float)
                secondary_series[col] = arr
                secondary_values[col] = [
                    float(v) if np.isfinite(v) else None
                    for v in arr
                ]

    # 1. Детекция changepoints
    changepoints = detect_changepoints(series, config)

    # 2. Сегментация
    segments = compute_segments(
        series,
        changepoints,
        config,
        dates=dates,
        secondary_series=secondary_series
    )

    # 3. Детекция кластеров аномалий
    anomaly_clusters = []
    if anomaly_column and anomaly_column in df.columns:
        anomaly_series = df[anomaly_column].values.astype(float)
        anomaly_clusters = detect_anomaly_clusters(anomaly_series, config)

    # Формирование результата
    values = [float(v) if np.isfinite(v) else None for v in series]

    return AnalysisResult(
        ok=True,
        n_points=n_points,
        changepoints=changepoints,
        segments=segments,
        anomaly_clusters=anomaly_clusters,
        dates=dates,
        values=values,
        secondary_values=secondary_values,
        config_used={
            "changepoint_threshold_pct": config.changepoint_threshold_pct,
            "min_segment_days": config.min_segment_days,
            "change_notable_pct": config.change_notable_pct,
        }
    )


# ════════════════════════════════════════════════════════════════════
# 6. Утилиты для форматирования
# ════════════════════════════════════════════════════════════════════

SEGMENT_TYPE_LABELS = {
    "initial": "Начальный",
    "stable": "Стабильно",
    "rise": "Рост",
    "decline": "Снижение",
    "sharp_rise": "Резкий рост",
    "sharp_decline": "Резкое снижение",
}

SEGMENT_TYPE_COLORS = {
    "initial": "#9ca3af",
    "stable": "#22c55e",
    "rise": "#3b82f6",
    "decline": "#f59e0b",
    "sharp_rise": "#8b5cf6",
    "sharp_decline": "#ef4444",
}


def format_segment_description(segment: Segment, unit: str = "") -> str:
    """
    Формирование текстового описания сегмента.

    Args:
        segment: объект Segment
        unit: единица измерения (например "тыс.м³/сут")

    Returns:
        Человекочитаемое описание
    """
    type_label = SEGMENT_TYPE_LABELS.get(segment.segment_type, segment.segment_type)

    desc = f"Сегмент #{segment.num}: {type_label}"
    desc += f" ({segment.days} дн.)"
    desc += f" — среднее {segment.mean_value:.2f}"
    if unit:
        desc += f" {unit}"

    # Показываем дрейф по slope (изменение за период)
    if segment.mean_value != 0 and segment.days > 0:
        drift_pct = (segment.slope * segment.days) / segment.mean_value * 100.0
        if abs(drift_pct) >= 3.0:  # показываем только если заметно
            sign = "+" if drift_pct > 0 else ""
            desc += f", дрейф {sign}{drift_pct:.1f}%"

    return desc
