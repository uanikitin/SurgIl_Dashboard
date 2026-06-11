"""
segment_analysis_module.py
==========================

Standalone модуль сегментного анализа газовых скважин + ПАВ-балл.

Извлечён из wells_web.py проекта «Анализ работы УзКорГаз» для
портирования в Сургил Дашборд.

См. ТЗ_СЕГМЕНТНЫЙ_АНАЛИЗ_ПОЛНОЕ.md для детального описания
алгоритма.

ЗАВИСИМОСТИ:
    - numpy
    - pandas

ОСНОВНЫЕ ТОЧКИ ВХОДА:
    - _segment_analysis(df) → dict
    - _segment_analysis_dual(df) → dict
    - _compute_pav_score(dual, sub) → dict

КОНФИГУРАЦИЯ:
    - SEGMENT_THRESHOLDS — словарь всех порогов, доступ через _segth(key)
"""
from __future__ import annotations
import numpy as np
import pandas as pd


# ════════════════════════════════════════════════════════════════════

# 1. Все настраиваемые пороги
# ════════════════════════════════════════════════════════════════════
SEGMENT_THRESHOLDS = {
    # Изменение Q между сегментами: событие фиксируется, если ИЛИ относительный
    # порог (%), ИЛИ абсолютный (тыс.м³/сут) превышен. Двойной порог нужен,
    # чтобы для низкодебитных скважин (Q≈5) не терять реальные изменения,
    # а для высокодебитных (Q≈100) не зашумлять отчёт на тривиальных %.
    "q_change_notable_pct":    10.0,   # % — порог «заметно»
    "q_change_notable_abs":     0.5,   # тыс.м³/сут — абсолютный минимум
    "q_change_significant_pct": 20.0,  # % — порог «существенно»
    "q_change_significant_abs": 2.0,   # тыс.м³/сут
    "q_change_dramatic_pct":   30.0,   # % — порог «резко» (📉/📈)

    # Детекция точек перелома
    "changepoint_threshold_pct":     15.0,  # порог скачка уровня (от медианы)
    "changepoint_final_filter_pct":   5.0,  # финальная фильтрация значимости
    "dip_drop_pct":                  25.0,  # короткий провал — порог падения
    "dip_recovery_pct":              25.0,  # порог восстановления
    "dip_recovery_window_days":       7,    # окно поиска восстановления
    "edge_margin_days":               2,    # минимальный отступ от края периода
    "merge_close_cp_days":            5,    # объединение близких переломов
    "min_segment_days":               3,    # минимальная длина сегмента

    # Кластеры простоев
    "shutdown_min_per_day":      600.0,   # мин/сут — порог problem_days
    "shutdown_full_stop":       1200.0,   # мин/сут — одиночный день = кластер
    "shutdown_cluster_min_days":    2,    # минимум дней для кластера
    "shutdown_cluster_merge_gap":   2,    # склейка кластеров с зазором ≤ 1 день
    "q_drop_for_problem_day_pct": 50.0,   # Q < X% медианы → problem_day

    # Эвристика «ремонт/КРС»
    "workover_min_days":         3,        # минимум дней простоя
    "workover_q_delta_pct":     30.0,      # |Q после − Q до| / |Q до| ≥ X %

    # Сравнение Q до простоя ↔ после простоя
    "preshutdown_planned_pct":  10.0,      # |Δ| < X% → плановая остановка
    "preshutdown_success_pct":  15.0,      # Δ ≥ X% → успешный ГТМ

    # Изменения давлений
    "dp_abs_threshold":          0.10,     # кгс/см² — минимальное значимое δΔP
    "dp_rel_threshold":          5.0,      # % — минимальное относительное δΔP
    "dp_drop_dramatic_pct":     20.0,      # % — для упоминания в описании
    "dp_change_in_event_pct":   15.0,      # % — для упоминания в события
    # ПРИОРИТЕТ давления в линии: рост P шлейфа считается ПЕРВИЧНОЙ
    # причиной снижения Q, даже если |δP_уст| > |δP_шл|. Физика: когда
    # линия «упирается», скважина теряет ΔP и Q, и устьевое тоже падает —
    # но это следствие, а не причина.
    "p_flowline_priority_threshold": 0.5,  # кгс/см² — порог «значимого» изменения P шлейфа

    # Детекция переломов по ДАВЛЕНИЯМ (v1.6). Срабатывает даже если Q не
    # изменился (типовой случай: смена режима в линии, скважина «упирается»
    # или разгружается, ΔP меняется — Q остаётся прежним, но это другой режим).
    "pressure_cp_abs_threshold":      1.0,  # кгс/см² — абсолютный скачок |mean_right − mean_left|
    "pressure_cp_rel_threshold_pct": 15.0,  # % от медианы — относительный скачок

    # Изменения простоев между сегментами
    "shutdown_change_significant": 200.0,  # мин/сут — для тега «простой ↑/↓»
    "shutdown_change_notable":      50.0,  # мин/сут — для упоминания в события

    # Плавный тренд внутри сегмента (НОВОЕ v1.4)
    "gradual_trend_min_days":       10,    # минимальная длина для отметки
    "gradual_trend_min_drift_pct":  10.0,  # минимальный |slope×days/mean×100|

    # Q_working влияние
    "q_working_softer_ratio":      0.5,    # |δQ_раб|/|δQ_общ| < X ⇒ простои влияют

    # ─── ПАВ-балл — пороги нормировки и весов признаков ───
    # Каждый признак ∈ [0..1]; финальный балл = base × penalty × 100, ∈ [0..100]
    # Положительные признаки (база):
    "pav_w_hysteresis":       0.15,  # вес: гистерезис ΔP (сильный сигнал)
    "pav_w_choke_paradox":    0.13,  # вес: Q↓ при штуцере↑/стабильном (явный внутренний фактор)
    "pav_w_choke_accel":      0.10,  # вес: ускоренный спад Q после увеличения штуцера
    "pav_w_mono_drift":       0.11,  # вес: монотонный спад Q раб по серии сегментов
    "pav_w_op_purges":        0.11,  # вес: операторские продувки (суточные сводки)
    "pav_w_uplift":           0.09,  # вес: Q после простоя > Q до (вынос жидкости)
    "pav_w_pw_decline":       0.08,  # вес: P уст ↓ при стабильной линии
    "pav_w_divergence":       0.07,  # вес: расхождение q_total vs q_working
    "pav_w_cyclicity":        0.05,  # вес: циклы накопление-продувка
    "pav_w_gradual":          0.04,  # вес: плавный спад Q внутри одного сегмента
    "pav_w_purges":           0.04,  # вес: короткие плановые простои
    "pav_w_dp_compression":   0.03,  # вес: ΔP сжимается (большая часть → гистерезис)
    # Нормировки положительных признаков (значение, при котором ось = 1)
    "pav_norm_hysteresis_pct":    40.0,  # % потери ΔP при возврате линии → 1.0
    "pav_norm_mono_drift_pct":    15.0,  # % монотонного спада Q раб за серию → 1.0
    "pav_norm_op_purges_per_30":   6.0,  # 6 операторских продувок / 30 дн → 1.0
    "pav_norm_uplift_count":       1.0,  # 1 случай выноса жидкости → 1.0 (порог)
    "pav_norm_gradual_drift":     20.0,  # % плавный спад → 1.0
    "pav_norm_pw_drop_total":      5.0,  # кгс/см² накопленного падения P уст
    "pav_norm_dp_drop_rel":        0.50, # 50% относительное сжатие ΔP за период
    "pav_norm_cycles":             3.0,  # циклов накопление-продувка
    "pav_norm_purges_per_30":      4.5,  # коротких простоев / 30 дн.
    "pav_norm_divergence_rel":     0.30, # ≥30% — действительно ненормальное расхождение (раньше 20% шумило)
    # Отрицательные признаки (штрафы)
    "pav_pen_pf_rise":             0.50,  # сила штрафа за рост P шлейфа
    "pav_pen_dom_shutdown":        0.40,  # сила штрафа за подавляющий простой
    "pav_pen_recent_workover":     0.30,  # сила штрафа за свежий ремонт
    "pav_pen_full_stability":      0.40,  # сила штрафа за полную стабильность
    "pav_norm_pf_rise_total":      3.0,   # кгс/см² накопленного роста P шл → штраф 1
    "pav_dom_shutdown_lo":         0.40,  # доля простоев, с которой начинаем штрафовать
    "pav_dom_shutdown_hi":         0.80,  # доля простоев, на которой штраф достигает 1
    "pav_recent_workover_days":   14,     # ремонт «свежий», если ≤ N дней назад
}

def _segth(key: str) -> float:
    """Доступ к порогу с поддержкой опечаток (ругается явным KeyError)."""
    if key not in SEGMENT_THRESHOLDS:
        raise KeyError(f"Неизвестный порог сегментного анализа: {key!r}")
    return SEGMENT_THRESHOLDS[key]


def _q_change_is_notable(change_pct: float | None, prev_q: float | None) -> bool:
    """ИЛИ-условие: относительный ИЛИ абсолютный порог. None → False."""
    if change_pct is None or prev_q is None or not np.isfinite(change_pct):
        return False
    abs_change = abs(change_pct) * abs(prev_q) / 100.0
    return (abs(change_pct) >= _segth("q_change_notable_pct")
            or abs_change >= _segth("q_change_notable_abs"))


def _q_change_is_significant(change_pct: float | None, prev_q: float | None) -> bool:
    if change_pct is None or prev_q is None or not np.isfinite(change_pct):
        return False
    abs_change = abs(change_pct) * abs(prev_q) / 100.0
    return (abs(change_pct) >= _segth("q_change_significant_pct")
            or abs_change >= _segth("q_change_significant_abs"))
# Порог классификации эпизода: ≤ этой длительности (в минутах) считается
# «коротким» (продувка); больше — «длинным» (ремонт/затяжной простой).
PURGE_EPISODE_MAX_MINUTES = 12 * 60
# Альфа для штрафа за рост P шлейфа: финальный балл умножается на

# ════════════════════════════════════════════════════════════════════
# 2. Линейная регрессия
# ════════════════════════════════════════════════════════════════════
def _linreg_slope(x: np.ndarray, y: np.ndarray) -> float:
    """Линейный тренд: коэффициент наклона (единицы y за 1 день). NaN если <2 точек."""
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 2:
        return float("nan")
    try:
        slope, _ = np.polyfit(x[mask], y[mask], 1)
        return float(slope)
    except Exception:
        return float("nan")


def _linreg_full(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Возвращает (slope, intercept) для линейной регрессии."""
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 2:
        return (float("nan"), float("nan"))
    try:
        slope, intercept = np.polyfit(x[mask], y[mask], 1)
        return (float(slope), float(intercept))
    except Exception:
        return (float("nan"), float("nan"))



# ════════════════════════════════════════════════════════════════════
# 3. Кластеры простоев
# ════════════════════════════════════════════════════════════════════
def _find_shutdown_clusters(shutdown: np.ndarray, q_total: np.ndarray,
                            min_days: int | None = None,
                            shutdown_threshold_min: float | None = None,
                            q_drop_threshold_pct: float | None = None) -> list[tuple[int, int]]:
    """
    Находит кластеры простоев — периоды со значительным снижением работы.

    Критерии включения дня в кластер:
    - shutdown > shutdown_threshold_min (по умолчанию 600 мин = 10 часов)
    - ИЛИ Q_total < q_drop_threshold_pct% от медианы

    Args:
        shutdown: массив простоев в мин/сут
        q_total: массив Q общий
        min_days: минимум дней подряд для кластера
        shutdown_threshold_min: порог простоя в минутах
        q_drop_threshold_pct: порог падения Q в % от медианы

    Returns:
        Список кортежей (start_idx, end_idx) для каждого кластера
    """
    if min_days is None:
        min_days = int(_segth("shutdown_cluster_min_days"))
    if shutdown_threshold_min is None:
        shutdown_threshold_min = _segth("shutdown_min_per_day")
    if q_drop_threshold_pct is None:
        q_drop_threshold_pct = _segth("q_drop_for_problem_day_pct")

    n = len(shutdown)
    if n < min_days:
        return []

    shutdown = np.nan_to_num(shutdown, nan=0.0)
    q_total = np.asarray(q_total, dtype=float)

    # Медиана Q для определения "нормального" уровня
    q_median = np.nanmedian(q_total[np.isfinite(q_total)])
    if not np.isfinite(q_median) or q_median <= 0:
        q_median = 1.0
    q_threshold = q_median * q_drop_threshold_pct / 100.0

    # Маркируем "проблемные" дни
    problem_days = np.zeros(n, dtype=bool)
    for i in range(n):
        # Критерий 1: высокий простой
        if shutdown[i] >= shutdown_threshold_min:
            problem_days[i] = True
        # Критерий 2: Q сильно ниже медианы (и не NaN)
        elif np.isfinite(q_total[i]) and q_total[i] < q_threshold:
            problem_days[i] = True

    # Находим непрерывные последовательности проблемных дней
    clusters = []
    i = 0
    while i < n:
        if problem_days[i]:
            start = i
            while i < n and problem_days[i]:
                i += 1
            end = i  # end не включительно
            # Только если >= min_days
            if end - start >= min_days:
                clusters.append((start, end))
        else:
            i += 1

    # Дополнительно: одиночные дни полной остановки (≥shutdown_full_stop)
    # становятся обязательными 1-дневными кластерами даже без соседей
    full_stop_threshold = _segth("shutdown_full_stop")
    for i in range(n):
        if shutdown[i] >= full_stop_threshold:
            already_covered = any(start <= i < end for start, end in clusters)
            if not already_covered:
                clusters.append((i, i + 1))
    clusters.sort()

    # Сливаем соседние кластеры с зазором ≤ (merge_gap - 1) рабочих дней
    merge_gap = int(_segth("shutdown_cluster_merge_gap"))
    merged: list[tuple[int, int]] = []
    for start, end in clusters:
        if merged and start - merged[-1][1] <= merge_gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    return merged



# ════════════════════════════════════════════════════════════════════
# 4. Детекция переломов
# ════════════════════════════════════════════════════════════════════
def _detect_changepoints(y: np.ndarray, min_segment: int = 3,
                         threshold_pct: float = 15.0) -> list[int]:
    """
    Базовая детекция точек перелома (для обратной совместимости).
    """
    return _detect_changepoints_extended(y, None, min_segment, threshold_pct)


def _detect_level_shifts(series: np.ndarray, min_segment: int,
                          abs_threshold: float, rel_threshold_pct: float) -> set[int]:
    """Универсальный поиск скачков уровня для произвольного ряда (Q, P, ΔP).
    Возвращает множество индексов, где `|mean_right − mean_left| + 0.5|jump|`
    превышает порог И является локальным максимумом cost в окне ±min_segment.

    Порог = max(abs_threshold, |median| × rel_threshold_pct / 100).
    """
    s = np.asarray(series, dtype=float)
    mask = np.isfinite(s)
    if mask.sum() < min_segment * 2:
        return set()
    s_clean = s.copy()
    if not mask.all():
        nans = ~mask
        x_all = np.arange(len(s))
        s_clean[nans] = np.interp(x_all[nans], x_all[mask], s[mask])
    n = len(s_clean)
    med = float(np.nanmedian(np.abs(s_clean[mask])) or 1.0)
    threshold = max(abs_threshold, med * rel_threshold_pct / 100.0)

    cost = np.zeros(n)
    for i in range(min_segment, n - min_segment):
        left = s_clean[:i]
        right = s_clean[i:]
        mean_diff = abs(np.mean(right) - np.mean(left))
        jump = abs(s_clean[i] - s_clean[i - 1])
        cost[i] = mean_diff + jump * 0.5

    result: set[int] = set()
    for i in range(min_segment, n - min_segment):
        if cost[i] < threshold:
            continue
        ws = max(0, i - min_segment)
        we = min(n, i + min_segment + 1)
        if cost[i] == np.max(cost[ws:we]):
            result.add(i)
    return result


def _detect_changepoints_extended(y: np.ndarray, shutdown: np.ndarray | None = None,
                                   min_segment: int = 3, threshold_pct: float = 15.0,
                                   p_flowline: np.ndarray | None = None,
                                   dp: np.ndarray | None = None,
                                   choke: np.ndarray | None = None) -> list[int]:
    """
    Улучшенная детекция точек перелома с учётом простоев и волатильности.

    ВАРИАНТ D (гибрид):
    1. Сначала находим кластеры простоев (≥2 дней с >600 мин или Q < 50% медианы)
    2. Границы кластеров = обязательные точки перелома
    3. Внутри рабочих периодов — обычная детекция по Q

    Args:
        y: временной ряд значений Q
        shutdown: временной ряд простоев (мин/сут), опционально
        min_segment: минимальная длина сегмента между changepoints
        threshold_pct: порог изменения в % от медианы

    Returns:
        Список индексов точек перелома
    """
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(y)
    if mask.sum() < min_segment * 2:
        return []

    # Заменяем NaN на интерполированные значения
    y_clean = y.copy()
    if not mask.all():
        nans = ~mask
        x_all = np.arange(len(y))
        y_clean[nans] = np.interp(x_all[nans], x_all[mask], y[mask])

    n = len(y_clean)
    median_val = np.nanmedian(y_clean)
    if median_val == 0:
        median_val = 1.0

    threshold = median_val * threshold_pct / 100.0

    changepoints = set()

    # === 0. ВАРИАНТ D: Кластеры простоев как обязательные границы ===
    shutdown_clusters = []
    if shutdown is not None:
        shutdown_clusters = _find_shutdown_clusters(
            shutdown, y_clean,
            min_days=2,
            shutdown_threshold_min=600.0,
            q_drop_threshold_pct=50.0
        )
        # Добавляем границы кластеров как обязательные changepoints
        for start, end in shutdown_clusters:
            if start > 0:
                changepoints.add(start)
            if end < n:
                changepoints.add(end)

    # === 0b. СМЕНА ШТУЦЕРА = обязательная точка перелома ===
    # Любое изменение диаметра штуцера физически меняет режим скважины:
    # больше штуцер → больше Q, меньше → меньше. Поэтому такие даты
    # ОБЯЗАТЕЛЬНО должны быть границами сегментов — иначе мы будем
    # приписывать «накоплению жидкости» то, что было операционным
    # решением (или наоборот пропустим парадокс «Q ↓ при штуцере ↑»).
    choke_change_indices: set[int] = set()
    if choke is not None:
        choke_arr = np.asarray(choke, dtype=float)
        if len(choke_arr) == n:
            for i in range(1, n):
                a, b = choke_arr[i - 1], choke_arr[i]
                if np.isfinite(a) and np.isfinite(b) and a != b:
                    changepoints.add(i)
                    choke_change_indices.add(i)

    # === 1. Базовая детекция по скачку уровня ===
    cost_reduction = np.zeros(n)
    for i in range(min_segment, n - min_segment):
        left = y_clean[:i]
        right = y_clean[i:]
        mean_diff = abs(np.mean(right) - np.mean(left))
        jump = abs(y_clean[i] - y_clean[i-1]) if i > 0 else 0
        cost_reduction[i] = mean_diff + jump * 0.5

    for i in range(min_segment, n - min_segment):
        if cost_reduction[i] < threshold:
            continue
        window_start = max(0, i - min_segment)
        window_end = min(n, i + min_segment + 1)
        if cost_reduction[i] == np.max(cost_reduction[window_start:window_end]):
            changepoints.add(i)

    # === 2. Детекция коротких аномалий (провалов) ===
    dip_drop = _segth("dip_drop_pct")
    dip_rec = _segth("dip_recovery_pct")
    dip_win = int(_segth("dip_recovery_window_days"))
    for i in range(1, n - 1):
        if i >= 1:
            drop_pct = (y_clean[i] - y_clean[i-1]) / y_clean[i-1] * 100 if y_clean[i-1] != 0 else 0
            if drop_pct < -dip_drop:
                changepoints.add(i)
                for j in range(i + 1, min(i + 1 + dip_win, n)):
                    recovery_pct = (y_clean[j] - y_clean[i]) / y_clean[i] * 100 if y_clean[i] != 0 else 0
                    if recovery_pct > dip_rec:
                        changepoints.add(j)
                        break

    # === 3. Детекция по простоям (включая частые небольшие) ===
    if shutdown is not None:
        shutdown = np.asarray(shutdown, dtype=float)
        shutdown = np.nan_to_num(shutdown, nan=0.0)

        # 3a. Резкие скачки простоев
        for i in range(1, n):
            if shutdown[i] > 500 and (i == 0 or shutdown[i-1] < 100):
                changepoints.add(i)
            if i > 0 and shutdown[i-1] > 500 and shutdown[i] < 100:
                changepoints.add(i)

        # 3b. Детекция периодов с частыми простоями (нестабильная работа)
        window = 7  # окно 7 дней
        for i in range(window, n - window):
            # Считаем частоту простоев в окне до и после точки i
            before_window = shutdown[max(0, i-window):i]
            after_window = shutdown[i:min(n, i+window)]

            # Дни с простоями >30 мин
            days_with_shutdown_before = np.sum(before_window > 30)
            days_with_shutdown_after = np.sum(after_window > 30)

            # Если частота простоев резко изменилась (было мало, стало много или наоборот)
            freq_before = days_with_shutdown_before / len(before_window)
            freq_after = days_with_shutdown_after / len(after_window)

            # Начало нестабильного периода: было <30% дней с простоями, стало >60%
            if freq_before < 0.3 and freq_after > 0.6:
                changepoints.add(i)

            # Конец нестабильного периода: было >60% дней с простоями, стало <30%
            if freq_before > 0.6 and freq_after < 0.3:
                changepoints.add(i)

    # === 4. Детекция по волатильности (нестабильность Q) ===
    window = 7
    volatility = np.zeros(n)
    for i in range(window, n - window):
        # Коэффициент вариации в окне
        window_data = y_clean[i-window:i+window]
        if np.mean(window_data) != 0:
            volatility[i] = np.std(window_data) / np.mean(window_data) * 100  # CV в %

    # Средняя волатильность
    mean_volatility = np.mean(volatility[volatility > 0]) if np.any(volatility > 0) else 0

    for i in range(window + 1, n - window):
        # Резкое изменение волатильности
        vol_before = np.mean(volatility[max(0, i-window):i])
        vol_after = np.mean(volatility[i:min(n, i+window)])

        # Начало нестабильного периода: волатильность выросла в 2+ раза
        if vol_before > 0 and vol_after / vol_before > 2.0 and vol_after > mean_volatility * 1.5:
            changepoints.add(i)

        # Конец нестабильного периода: волатильность снизилась в 2+ раза
        if vol_after > 0 and vol_before / vol_after > 2.0 and vol_before > mean_volatility * 1.5:
            changepoints.add(i)

    # === 4b. ПЕРЕЛОМЫ ПО ДАВЛЕНИЯМ (v1.6) ===
    # Q может не двинуться, но скважина перешла в другой режим из-за
    # изменений в линии (P шл ↓/↑, ΔP скачок). Сканируем P шлейфа и ΔP
    # отдельно и тоже добавляем найденные точки.
    p_abs = _segth("pressure_cp_abs_threshold")
    p_rel = _segth("pressure_cp_rel_threshold_pct")
    pressure_cps: set[int] = set()
    if p_flowline is not None:
        pressure_cps |= _detect_level_shifts(p_flowline, min_segment, p_abs, p_rel)
    if dp is not None:
        pressure_cps |= _detect_level_shifts(dp, min_segment, p_abs, p_rel)
    changepoints |= pressure_cps

    # === 5. Сортируем и фильтруем ===
    edge = int(_segth("edge_margin_days"))
    merge_close = int(_segth("merge_close_cp_days"))
    final_filter_pct = _segth("changepoint_final_filter_pct")
    sorted_cp = sorted(changepoints)
    sorted_cp = [cp for cp in sorted_cp if cp >= edge and cp <= n - edge]

    # Объединяем близкие точки переломов
    filtered = []
    for cp in sorted_cp:
        if not filtered:
            filtered.append(cp)
        elif cp - filtered[-1] >= merge_close:
            filtered.append(cp)
        else:
            if cost_reduction[cp] > cost_reduction[filtered[-1]]:
                filtered[-1] = cp

    # === 6. Финальная фильтрация - убираем changepoints с незначительным изменением ===
    # Проверяем что изменение Q между сегментами > 5%
    final_filtered = []
    for i, cp in enumerate(filtered):
        # Считаем среднее до и после changepoint
        before_start = final_filtered[-1] if final_filtered else 0
        mean_before = np.mean(y_clean[before_start:cp])

        # Среднее после (до следующего changepoint или до конца)
        next_cp = filtered[i + 1] if i + 1 < len(filtered) else n
        mean_after = np.mean(y_clean[cp:next_cp])

        # Изменение в %
        if mean_before != 0:
            change_pct = abs(mean_after - mean_before) / mean_before * 100
        else:
            change_pct = 100 if mean_after != 0 else 0

        # Оставляем только если изменение > 5% ИЛИ это явная аномалия (простои)
        is_shutdown_event = False
        if shutdown is not None and cp < len(shutdown):
            # Проверяем был ли резкий скачок простоев около этой точки
            window_start = max(0, cp - 3)
            window_end = min(n, cp + 3)
            max_shutdown_near = np.max(shutdown[window_start:window_end])
            is_shutdown_event = max_shutdown_near > 300

        # Сохраняем перелом, если:
        #  • Q изменился значимо, ИЛИ
        #  • это явный простой-эпизод, ИЛИ
        #  • это перелом по давлению (Q мог не двинуться, а режим
        #    скважины поменялся за счёт линии или устьевого), ИЛИ
        #  • это смена штуцера (всегда обязательная граница).
        is_pressure_event = cp in pressure_cps
        is_choke_event    = cp in choke_change_indices
        if (change_pct > final_filter_pct or is_shutdown_event
                or is_pressure_event or is_choke_event):
            final_filtered.append(cp)

    # Подавляем точки перелома СТРОГО внутри кластера простоев: внутри
    # кластера один режим, дробить его на «простой A» и «простой B»
    # бессмысленно (даёт ложные «ремонты» из эвристики is_workover).
    if shutdown_clusters:
        final_filtered = [
            cp for cp in final_filtered
            if not any(start < cp < end for start, end in shutdown_clusters)
        ]

    return final_filtered



# ════════════════════════════════════════════════════════════════════
# 5. Построение сегментов + интерпретация
# ════════════════════════════════════════════════════════════════════
def _segment_trends_extended(dates: np.ndarray, q_total: np.ndarray, q_working: np.ndarray,
                              shutdown: np.ndarray, dp: np.ndarray,
                              changepoints: list[int],
                              shutdown_clusters: list[tuple[int, int]] | None = None,
                              p_wellhead: np.ndarray | None = None,
                              p_flowline: np.ndarray | None = None,
                              choke: np.ndarray | None = None) -> list[dict]:
    """
    Строит линейные тренды для каждого сегмента с расширенным анализом.

    Анализирует:
    - Q общий и Q рабочий
    - Простои (shutdown_min)
    - Перепад давления (ΔP)
    - Определяет причину изменения дебита
    - Маркирует сегменты как shutdown_cluster при совпадении с кластерами простоев

    Returns:
        Список словарей с полной информацией о каждом сегменте
    """
    segments = []
    boundaries = [0] + changepoints + [len(q_total)]
    shutdown_clusters = shutdown_clusters or []

    prev_seg = None
    first_seg_mean = None  # для отслеживания восстановления

    for i in range(len(boundaries) - 1):
        start_idx = boundaries[i]
        end_idx = boundaries[i + 1]

        seg_dates = dates[start_idx:end_idx]
        seg_q_total = q_total[start_idx:end_idx]
        seg_q_working = q_working[start_idx:end_idx]
        seg_shutdown = shutdown[start_idx:end_idx]
        seg_dp = dp[start_idx:end_idx]
        seg_p_wh = p_wellhead[start_idx:end_idx] if p_wellhead is not None else np.full_like(seg_dp, np.nan)
        seg_p_fl = p_flowline[start_idx:end_idx] if p_flowline is not None else np.full_like(seg_dp, np.nan)
        seg_choke = choke[start_idx:end_idx] if choke is not None else np.full_like(seg_dp, np.nan)

        if len(seg_q_total) < 2:
            continue

        # X в днях от начала сегмента
        x_days = (seg_dates - seg_dates[0]).astype('timedelta64[s]').astype(float) / 86400.0

        # Тренды
        slope_total, intercept_total = _linreg_full(x_days, seg_q_total)
        slope_working, _ = _linreg_full(x_days, seg_q_working)
        slope_dp, _ = _linreg_full(x_days, seg_dp)

        # Средние значения (защита от пустых срезов / all-NaN)
        def _nanmean_safe(a):
            arr = np.asarray(a, dtype=float)
            if arr.size == 0 or not np.isfinite(arr).any():
                return float("nan")
            return float(np.nanmean(arr))
        mean_q_total = _nanmean_safe(seg_q_total)
        mean_q_working = _nanmean_safe(seg_q_working)
        mean_shutdown = _nanmean_safe(seg_shutdown)
        if not np.isfinite(mean_shutdown):
            mean_shutdown = 0.0
        mean_dp = _nanmean_safe(seg_dp)
        mean_p_wh = float(np.nanmean(seg_p_wh)) if np.isfinite(seg_p_wh).any() else float("nan")
        mean_p_fl = float(np.nanmean(seg_p_fl)) if np.isfinite(seg_p_fl).any() else float("nan")
        # Штуцер: берём моду (наиболее частое значение), а не среднее —
        # промежуточный «средний» штуцер физически не существует.
        mean_choke_mm = float("nan")
        if np.isfinite(seg_choke).any():
            vals, counts = np.unique(seg_choke[np.isfinite(seg_choke)], return_counts=True)
            mean_choke_mm = float(vals[np.argmax(counts)])

        # Суммарный простой за сегмент
        total_shutdown = float(np.nansum(seg_shutdown))
        # Рабочее время в % (1440 мин = сутки)
        total_minutes = len(seg_q_total) * 1440
        working_pct = (total_minutes - total_shutdown) / total_minutes * 100 if total_minutes > 0 else 100

        # Изменения относительно предыдущего сегмента
        change_q_pct = None
        change_q_working_pct = None
        change_dp_pct = None
        change_shutdown = None
        change_choke = None

        if prev_seg is not None:
            if prev_seg["mean_q_total"] != 0:
                change_q_pct = (mean_q_total - prev_seg["mean_q_total"]) / prev_seg["mean_q_total"] * 100
            if prev_seg["mean_q_working"] != 0:
                change_q_working_pct = (mean_q_working - prev_seg["mean_q_working"]) / prev_seg["mean_q_working"] * 100
            if prev_seg["mean_dp"] != 0 and np.isfinite(prev_seg["mean_dp"]) and np.isfinite(mean_dp):
                change_dp_pct = (mean_dp - prev_seg["mean_dp"]) / abs(prev_seg["mean_dp"]) * 100
            change_shutdown = mean_shutdown - prev_seg["mean_shutdown"]
            prev_choke = prev_seg.get("mean_choke_mm")
            if (prev_choke is not None and np.isfinite(prev_choke)
                    and np.isfinite(mean_choke_mm)):
                change_choke = mean_choke_mm - prev_choke

        # Запоминаем первый сегмент для отслеживания восстановления
        if first_seg_mean is None:
            first_seg_mean = mean_q_total

        # Наблюдательное описание изменения режима:
        # 1) разложение δ(ΔP) = δP_устья − δP_шлейфа;
        # 2) сверка Q с ΔP;
        # 3) при рассогласовании — анализ через рабочее время.
        curr_for_cause = dict(
            mean_q_total=mean_q_total, mean_q_working=mean_q_working,
            mean_shutdown=mean_shutdown, mean_dp=mean_dp,
            mean_p_wellhead=mean_p_wh, mean_p_flowline=mean_p_fl,
            change_q_pct=change_q_pct, change_q_working_pct=change_q_working_pct,
            change_dp_pct=change_dp_pct, change_shutdown=change_shutdown,
        )
        cause = _determine_change_cause(curr_for_cause, prev_seg)

        # Проверяем восстановление к исходному уровню
        recovery_info = None
        if i > 1 and first_seg_mean is not None and first_seg_mean != 0:
            recovery_pct = (mean_q_total - first_seg_mean) / first_seg_mean * 100
            if abs(recovery_pct) < 15:  # восстановление если в пределах 15% от исходного
                recovery_info = f"Восстановление к исходному уровню ({recovery_pct:+.1f}% от начального)"

        # === ВАРИАНТ D: Проверяем принадлежность сегмента к кластеру простоев ===
        is_shutdown_cluster = False
        cluster_overlap_pct = 0.0
        for cl_start, cl_end in shutdown_clusters:
            # Проверяем пересечение сегмента с кластером
            overlap_start = max(start_idx, cl_start)
            overlap_end = min(end_idx, cl_end)
            if overlap_start < overlap_end:
                # Есть пересечение
                overlap_days = overlap_end - overlap_start
                seg_days = end_idx - start_idx
                overlap_pct = overlap_days / seg_days * 100 if seg_days > 0 else 0
                # Если сегмент на ≥70% совпадает с кластером - помечаем
                if overlap_pct >= 70:
                    is_shutdown_cluster = True
                    cluster_overlap_pct = overlap_pct
                    break

        seg_data = {
            "start_idx": start_idx,
            "end_idx": end_idx,
            "start_date": seg_dates[0],
            "end_date": seg_dates[-1],
            "days": len(seg_q_total),
            # Q общий
            "slope": slope_total,
            "intercept": intercept_total,
            "mean_q_total": mean_q_total,
            "change_q_pct": change_q_pct,
            # Q рабочий
            "slope_working": slope_working,
            "mean_q_working": mean_q_working,
            "change_q_working_pct": change_q_working_pct,
            # Простои
            "mean_shutdown": mean_shutdown,
            "total_shutdown": total_shutdown,
            "working_pct": working_pct,
            "change_shutdown": change_shutdown,
            # ΔP и компоненты давления
            "mean_dp": mean_dp,
            "slope_dp": slope_dp,
            "change_dp_pct": change_dp_pct,
            "mean_p_wellhead": mean_p_wh,
            "mean_p_flowline": mean_p_fl,
            # Штуцер (мода значений в сегменте)
            "mean_choke_mm": mean_choke_mm,
            "change_choke": change_choke,
            # Анализ
            "cause": cause,
            "recovery_info": recovery_info,
            # Кластер простоев (Вариант D)
            "is_shutdown_cluster": is_shutdown_cluster,
            "cluster_overlap_pct": cluster_overlap_pct,
            # Для совместимости
            "mean_level": mean_q_total,
            "change_pct": change_q_pct,
        }

        segments.append(seg_data)
        prev_seg = seg_data

    return segments


def _determine_change_cause(curr_seg: dict, prev_seg: dict | None) -> str | None:
    """
    Наблюдательное описание перехода между сегментами по физической схеме:

        1) Перепад: δ(ΔP) = δP_устья − δP_шлейфа.
           Раскладываем изменение ΔP на два числовых вклада (без ярлыка
           «доминирующий»); читатель сам делает вывод.
        2) Дебит vs перепад: проверяем, согласуется ли изменение Q
           по знаку с изменением ΔP.
        3) Рабочее время: если Q изменился, а ΔP «молчит», или знаки
           разные — поясняем за счёт чего (Q общ. vs Q раб., рост простоев).

    Никакая причина не утверждается категорично. Это «дельта-разбор», а не
    диагноз.
    """
    if prev_seg is None or curr_seg is None:
        return None

    # ── Пороги читаются из общего словаря SEGMENT_THRESHOLDS
    DP_ABS_THRESHOLD = _segth("dp_abs_threshold")
    DP_REL_THRESHOLD = _segth("dp_rel_threshold") / 100.0
    Q_REL_THRESHOLD  = _segth("q_change_notable_pct") / 100.0
    Q_WORKING_RATIO  = _segth("q_working_softer_ratio")
    SHUTDOWN_NOTABLE = _segth("shutdown_change_notable")
    SHUTDOWN_SIGNIF  = _segth("shutdown_change_significant")

    parts: list[str] = []

    # ─── 0) Главный фактор словами (первая фраза разбора) ───────────
    # Приоритет:
    # (i)  вход/выход длительной остановки — главный фактор «ремонт/простой»;
    # (ii) если |δP_шл| > |δP_уст| и знак противоречит Q — главный фактор «P шлейфа»;
    # (iii) если |δP_уст| > |δP_шл| — главный фактор «P устья»;
    # (iv) Q изменился, ΔP не изменился, |Δshutdown| ≥ 200 — фактор «простои».
    curr_cluster = curr_seg.get("is_shutdown_cluster", False)
    prev_cluster = prev_seg.get("is_shutdown_cluster", False)
    headline: str | None = None
    if curr_cluster and not prev_cluster:
        mean_sh = curr_seg.get("mean_shutdown", 0) or 0
        if curr_seg.get("is_workover"):
            headline = (
                f"Главный фактор — ремонт/КРС (простой ≈{mean_sh:.0f} мин/сут)"
            )
        else:
            headline = (
                f"Главный фактор — длительная остановка скважины "
                f"(простой ≈{mean_sh:.0f} мин/сут)"
            )
    elif prev_cluster and not curr_cluster:
        headline = "Главный фактор — возобновление работы после простоя"

    # ─── 1) Раскладываем изменение ΔP ──────────────────────────────
    prev_dp = prev_seg.get("mean_dp")
    curr_dp = curr_seg.get("mean_dp")
    prev_pw = prev_seg.get("mean_p_wellhead")
    curr_pw = curr_seg.get("mean_p_wellhead")
    prev_pf = prev_seg.get("mean_p_flowline")
    curr_pf = curr_seg.get("mean_p_flowline")

    have_dp = (prev_dp is not None and curr_dp is not None
               and np.isfinite(prev_dp) and np.isfinite(curr_dp))
    have_components = (
        prev_pw is not None and curr_pw is not None and prev_pf is not None and curr_pf is not None
        and np.isfinite(prev_pw) and np.isfinite(curr_pw)
        and np.isfinite(prev_pf) and np.isfinite(curr_pf)
    )

    dp_significant = False
    delta_dp = float("nan")
    delta_dp_pct = float("nan")
    delta_pw = float("nan")
    delta_pf = float("nan")

    if have_dp:
        delta_dp = curr_dp - prev_dp
        rel = abs(delta_dp) / abs(prev_dp) if prev_dp not in (0, None) else float("inf")
        dp_significant = (abs(delta_dp) >= DP_ABS_THRESHOLD or rel >= DP_REL_THRESHOLD)
        if abs(prev_dp) > 1e-9:
            delta_dp_pct = delta_dp / abs(prev_dp) * 100.0

    if have_components:
        delta_pw = curr_pw - prev_pw
        delta_pf = curr_pf - prev_pf

    if dp_significant:
        direction = "↑" if delta_dp > 0 else "↓"
        line = f"ΔP {direction} {delta_dp:+.2f} кгс/см²"
        if np.isfinite(delta_dp_pct):
            line += f" ({delta_dp_pct:+.0f}\\%)"  # \\% для LaTeX
        if have_components:
            # δ(ΔP) = δP_уст − δP_шл. Показываем оба вклада числами.
            line += (
                f" = δP устья {delta_pw:+.2f} − δP шлейфа {delta_pf:+.2f} кгс/см²"
            )
        parts.append(line)

    # Главный фактор по давлению.
    # ПРИОРИТЕТ ЛИНИИ (v1.5): если |δP_шл| ≥ p_flowline_priority_threshold
    # (по умолч. 0.5 кгс/см²) — P шлейфа считается главной причиной,
    # независимо от того, что |δP_уст| может быть больше. Физика:
    # рост противодавления в линии — первичен, падение P устья — следствие.
    # Только если изменение P шлейфа мало — главным становится P устья.
    if headline is None and have_components and dp_significant:
        line_threshold = _segth("p_flowline_priority_threshold")
        line_changed_signif = abs(delta_pf) >= line_threshold
        if line_changed_signif:
            # ─── Симметричное правило (для разработчика/ТЗ, не в текст отчёта) ───
            # P шлейфа — первичный фактор в обе стороны:
            #   P шл ↑  → ΔP ↓ → P уст ↓ → Q ↓   (всё следствия линии)
            #   P шл ↓  → ΔP ↑ → P уст ↑ → Q ↑   (всё следствия линии)
            # В отчёте показываем только короткую формулировку — без перечисления
            # каскадных следствий, потому что они избыточны для оператора.
            if delta_pf > 0:
                headline = (
                    f"Главный фактор — рост P шлейфа на {delta_pf:+.2f} кгс/см² "
                    "(рост противодавления в линии)"
                )
            else:
                headline = (
                    f"Главный фактор — снижение P шлейфа на {delta_pf:+.2f} кгс/см² "
                    "(разгрузка линии)"
                )
        else:
            if delta_pw < 0:
                headline = (
                    f"Главный фактор — снижение P устья на {delta_pw:+.2f} кгс/см² "
                    "(потеря устьевого давления; линия стабильна)"
                )
            elif delta_pw > 0:
                headline = (
                    f"Главный фактор — рост P устья на {delta_pw:+.2f} кгс/см² "
                    "(восстановление устьевого давления; линия стабильна)"
                )

    # ─── 2) Сверяем Q с ΔP ─────────────────────────────────────────
    change_q_pct = curr_seg.get("change_q_pct")
    change_q_working_pct = curr_seg.get("change_q_working_pct")
    change_shutdown = curr_seg.get("change_shutdown")
    mean_shutdown = curr_seg.get("mean_shutdown", 0) or 0
    prev_shutdown = prev_seg.get("mean_shutdown", 0) or 0

    q_changed = change_q_pct is not None and np.isfinite(change_q_pct) and abs(change_q_pct) >= Q_REL_THRESHOLD * 100

    # «Несинхронность» по любому из двух критериев (см. ответ пользователя)
    out_of_sync = False
    if q_changed:
        # (а) Q заметно изменился, ΔP — нет
        if not dp_significant:
            out_of_sync = True
        # (б) Разные знаки изменений
        elif np.isfinite(delta_dp) and (delta_dp * change_q_pct < 0):
            out_of_sync = True

    if q_changed and not out_of_sync:
        # Согласованное движение Q и ΔP
        if change_q_pct > 0 and delta_dp > 0:
            parts.append(
                f"Q вырос на {change_q_pct:+.0f}% согласованно с ростом ΔP"
            )
        elif change_q_pct < 0 and delta_dp < 0:
            parts.append(
                f"Q снизился на {change_q_pct:+.0f}% согласованно со снижением ΔP"
            )

    # ─── 3) Рабочее время / простой как остаточный фактор ─────────
    # Подключаем разбор, если Q не согласован с ΔP, либо если простои сильно меняются
    explain_by_work_time = out_of_sync or (
        change_shutdown is not None and abs(change_shutdown) >= SHUTDOWN_NOTABLE
    )
    if explain_by_work_time:
        q_working_softer = (
            change_q_working_pct is not None and np.isfinite(change_q_working_pct)
            and change_q_pct is not None
            and abs(change_q_pct) > 1e-6
            and abs(change_q_working_pct) < Q_WORKING_RATIO * abs(change_q_pct)
        )
        wt_bits: list[str] = []
        if change_shutdown is not None and abs(change_shutdown) >= 10:
            sign = "+" if change_shutdown > 0 else ""
            wt_bits.append(
                f"простои {prev_shutdown:.0f}→{mean_shutdown:.0f} мин/сут "
                f"({sign}{change_shutdown:.0f})"
            )
        if q_working_softer:
            wt_bits.append(
                "Q рабочий изменился слабее, чем Q общий — заметна доля влияния рабочего времени"
            )
        if not wt_bits and mean_shutdown >= 200:
            wt_bits.append(
                f"уровень простоев в сегменте высокий — {mean_shutdown:.0f} мин/сут"
            )
        if wt_bits:
            prefix = "Q не следует за ΔP" if out_of_sync else "Учёт рабочего времени"
            parts.append(prefix + ": " + "; ".join(wt_bits))

    # Резервный заголовок: Q изменился, ΔP стабилен, простои сдвинулись
    if headline is None and q_changed and not dp_significant and change_shutdown is not None:
        if abs(change_shutdown) >= SHUTDOWN_SIGNIF:
            sign = "+" if change_shutdown > 0 else ""
            headline = (
                f"Главный фактор — изменение режима простоев "
                f"({sign}{change_shutdown:.0f} мин/сут)"
            )

    if headline:
        parts.insert(0, headline)

    return ". ".join(parts) if parts else None


def _q_change_reason_phrase(curr_seg: dict, prev_seg: dict, direction: str) -> str:
    """Возвращает фразу «в связи с …» для канонического описания падения /
    роста Q. Приоритет: P шлейфа → P устья → ΔP → простои.

    direction: 'up' для роста Q, 'down' для падения.
    """
    prev_pf = prev_seg.get("mean_p_flowline")
    curr_pf = curr_seg.get("mean_p_flowline")
    prev_pw = prev_seg.get("mean_p_wellhead")
    curr_pw = curr_seg.get("mean_p_wellhead")
    line_threshold = _segth("p_flowline_priority_threshold")

    # 1) Приоритет P шлейфа (если есть и значимо изменилось)
    if (prev_pf is not None and curr_pf is not None
            and np.isfinite(prev_pf) and np.isfinite(curr_pf)):
        delta_pf = curr_pf - prev_pf
        if abs(delta_pf) >= line_threshold:
            # Для падения Q ожидаем рост P шл, для роста Q — снижение P шл
            if direction == "down" and delta_pf > 0:
                return f"в связи с ростом P шлейфа на {delta_pf:+.2f} кгс/см² (рост противодавления в линии)"
            if direction == "up" and delta_pf < 0:
                return f"в связи со снижением P шлейфа на {delta_pf:+.2f} кгс/см² (разгрузка линии)"
            # «Несогласованное» направление — всё равно фиксируем
            return f"одновременно с изменением P шлейфа на {delta_pf:+.2f} кгс/см²"

    # 2) P устья (если линия стабильна)
    if (prev_pw is not None and curr_pw is not None
            and np.isfinite(prev_pw) and np.isfinite(curr_pw)):
        delta_pw = curr_pw - prev_pw
        if abs(delta_pw) >= line_threshold:
            if direction == "down" and delta_pw < 0:
                return f"в связи со снижением P устья на {delta_pw:+.2f} кгс/см² (линия стабильна)"
            if direction == "up" and delta_pw > 0:
                return f"в связи с ростом P устья на {delta_pw:+.2f} кгс/см² (линия стабильна)"

    # 3) ΔP fallback
    change_dp_pct = curr_seg.get("change_dp_pct")
    if change_dp_pct is not None and np.isfinite(change_dp_pct) and abs(change_dp_pct) >= 15:
        if direction == "down" and change_dp_pct < 0:
            return f"в связи со снижением ΔP на {abs(change_dp_pct):.0f}%"
        if direction == "up" and change_dp_pct > 0:
            return f"в связи с увеличением ΔP на {change_dp_pct:.0f}%"

    # 4) Простои
    change_shutdown = curr_seg.get("change_shutdown")
    if change_shutdown is not None and abs(change_shutdown) >= 50:
        if direction == "down" and change_shutdown > 0:
            return f"в связи с увеличением простоев на {change_shutdown:.0f} мин/сут"
        if direction == "up" and change_shutdown < 0:
            return f"в связи с сокращением простоев на {abs(change_shutdown):.0f} мин/сут"

    return ""


def _changepoint_short_tag(curr_seg: dict, prev_seg: dict | None) -> str:
    """Короткая (1-2 слова) подпись для точки перелома: ставится у вертикальной
    штриховой линии на графике сегментного анализа. Приоритет диагнозов:
    ремонт → вход/выход кластера простоев → рост/падение простоев → δΔP →
    δQ (по абсолютному изменению)."""
    if prev_seg is None or curr_seg is None:
        return ""

    # Ремонт (на сегмент-кластер с пометкой is_workover)
    if curr_seg.get("is_workover"):
        return "🔧 ремонт"

    curr_cluster = curr_seg.get("is_shutdown_cluster", False)
    prev_cluster = prev_seg.get("is_shutdown_cluster", False)

    if curr_cluster and not prev_cluster:
        return "⏸ простой"
    if not curr_cluster and prev_cluster:
        return "▶ работа"

    change_shutdown = curr_seg.get("change_shutdown")
    if change_shutdown is None:
        # резерв для «стриппованных» сегментов из _segment_analysis
        prev_sh = prev_seg.get("mean_shutdown")
        curr_sh = curr_seg.get("mean_shutdown")
        if prev_sh is not None and curr_sh is not None:
            change_shutdown = curr_sh - prev_sh
    shutdown_signif = _segth("shutdown_change_significant")
    if change_shutdown is not None:
        if change_shutdown >= shutdown_signif:
            return "простой ↑"
        if change_shutdown <= -shutdown_signif:
            return "простой ↓"

    # Значимое изменение ΔP — компоненты P_уст / P_шл
    prev_dp = prev_seg.get("mean_dp")
    curr_dp = curr_seg.get("mean_dp")
    prev_pw = prev_seg.get("mean_p_wellhead")
    curr_pw = curr_seg.get("mean_p_wellhead")
    prev_pf = prev_seg.get("mean_p_flowline")
    curr_pf = curr_seg.get("mean_p_flowline")
    dp_abs = _segth("dp_abs_threshold")
    dp_rel = _segth("dp_rel_threshold") / 100.0
    if (prev_dp is not None and curr_dp is not None
            and np.isfinite(prev_dp) and np.isfinite(curr_dp)):
        delta_dp = curr_dp - prev_dp
        rel = abs(delta_dp) / abs(prev_dp) if prev_dp not in (0, None) else 0
        if abs(delta_dp) >= dp_abs or rel >= dp_rel:
            # Если есть компоненты — называем главную (приоритет P шлейфа)
            if (prev_pw is not None and curr_pw is not None
                    and prev_pf is not None and curr_pf is not None
                    and np.isfinite(prev_pw) and np.isfinite(curr_pw)
                    and np.isfinite(prev_pf) and np.isfinite(curr_pf)):
                delta_pw = curr_pw - prev_pw
                delta_pf = curr_pf - prev_pf
                line_threshold = _segth("p_flowline_priority_threshold")
                # ПРИОРИТЕТ P шлейфа: даже если |δP_уст| > |δP_шл|, при
                # значимом изменении в линии тег = P шл (физическая первичность)
                if abs(delta_pf) >= line_threshold:
                    return "P шл ↑" if delta_pf > 0 else "P шл ↓"
                return "P устья ↓" if delta_pw < 0 else "P устья ↑"
            return "ΔP ↑" if delta_dp > 0 else "ΔP ↓"

    # Резерв — само изменение Q (двойной порог: % ИЛИ абс.)
    change_q = curr_seg.get("change_q_pct")
    if change_q is None:
        change_q = curr_seg.get("change_pct")
    prev_q = prev_seg.get("mean_q_total") or prev_seg.get("mean_q")
    if _q_change_is_notable(change_q, prev_q):
        return f"Q {change_q:+.0f}%"

    return ""


def _segment_trends(dates: np.ndarray, y: np.ndarray,
                    changepoints: list[int]) -> list[dict]:
    """
    Строит линейные тренды для каждого сегмента между changepoints.
    Упрощённая версия для обратной совместимости.
    """
    segments = []
    boundaries = [0] + changepoints + [len(y)]

    prev_mean = None
    for i in range(len(boundaries) - 1):
        start_idx = boundaries[i]
        end_idx = boundaries[i + 1]

        seg_dates = dates[start_idx:end_idx]
        seg_y = y[start_idx:end_idx]

        if len(seg_y) < 2:
            continue

        x_days = (seg_dates - seg_dates[0]).astype('timedelta64[s]').astype(float) / 86400.0
        slope, intercept = _linreg_full(x_days, seg_y)
        mean_level = float(np.nanmean(seg_y))

        change_pct = None
        if prev_mean is not None and prev_mean != 0:
            change_pct = (mean_level - prev_mean) / prev_mean * 100

        segments.append({
            "start_idx": start_idx,
            "end_idx": end_idx,
            "start_date": seg_dates[0],
            "end_date": seg_dates[-1],
            "slope": slope,
            "intercept": intercept,
            "mean_level": mean_level,
            "change_pct": change_pct,
            "days": len(seg_y),
        })
        prev_mean = mean_level

    return segments



# ════════════════════════════════════════════════════════════════════
# 6. ⭐ Главная точка — _segment_analysis
# ════════════════════════════════════════════════════════════════════
def _segment_analysis(df: pd.DataFrame) -> dict:
    """
    Расширенный анализ сегментов с детектированием точек перелома.
    Учитывает простои, Q рабочий и ΔP для определения причин изменения дебита.
    """
    if df.empty or "q_gas_total" not in df.columns:
        return {"segments": [], "changepoints": [], "descriptions": [], "cp_descriptions": []}

    d = df.sort_values("date").copy()
    dates = d["date"].values
    q_total = d["q_gas_total"].to_numpy(dtype=float)
    q_working = d["q_gas_working"].to_numpy(dtype=float) if "q_gas_working" in d.columns else q_total.copy()
    shutdown = d["shutdown_min"].to_numpy(dtype=float) if "shutdown_min" in d.columns else np.zeros_like(q_total)
    # Вычисляем ΔP и сохраняем компоненты давления для декомпозиции
    if "p_wellhead" in d.columns and "p_flowline" in d.columns:
        p_wh = d["p_wellhead"].to_numpy(dtype=float)
        p_fl = d["p_flowline"].to_numpy(dtype=float)
        dp = p_wh - p_fl
    else:
        p_wh = np.full_like(q_total, np.nan)
        p_fl = np.full_like(q_total, np.nan)
        dp = np.full_like(q_total, np.nan)
    # Штуцер — обязательная точка перелома при изменении и параметр сегмента
    choke = (d["choke_mm"].to_numpy(dtype=float)
             if "choke_mm" in d.columns else None)

    # Детектируем точки перелома с учётом простоев + давлений + штуцера
    changepoints = _detect_changepoints_extended(
        q_total, shutdown, min_segment=3, threshold_pct=15.0,
        p_flowline=p_fl if np.isfinite(p_fl).any() else None,
        dp=dp if np.isfinite(dp).any() else None,
        choke=choke,
    )

    # Находим кластеры простоев для маркировки сегментов
    shutdown_clusters = _find_shutdown_clusters(
        shutdown, q_total,
        min_days=2,
        shutdown_threshold_min=600.0,
        q_drop_threshold_pct=50.0
    )

    # Строим сегменты с расширенным анализом (с компонентами давления + штуцером)
    segments = _segment_trends_extended(
        dates, q_total, q_working, shutdown, dp,
        changepoints, shutdown_clusters,
        p_wellhead=p_wh, p_flowline=p_fl,
        choke=choke,
    )

    # Эвристика «ремонт»: кластер простоев длиннее workover_min_days, после
    # которого Q отличается от Q до кластера на ≥ workover_q_delta_pct.
    # ВАЖНО: соседние сегменты обязаны быть РАБОЧИМИ.
    workover_min_days = int(_segth("workover_min_days"))
    workover_q_delta = _segth("workover_q_delta_pct")
    for idx, seg in enumerate(segments):
        seg["is_workover"] = False
        if not seg.get("is_shutdown_cluster"):
            continue
        if seg.get("days", 0) < workover_min_days:
            continue
        prev_seg = segments[idx - 1] if idx > 0 else None
        next_seg = segments[idx + 1] if idx + 1 < len(segments) else None
        if prev_seg is None or next_seg is None:
            continue
        if prev_seg.get("is_shutdown_cluster") or next_seg.get("is_shutdown_cluster"):
            continue
        prev_q = prev_seg["mean_q_total"]
        next_q = next_seg["mean_q_total"]
        if prev_q == 0:
            continue
        delta_pct = abs(next_q - prev_q) / abs(prev_q) * 100
        if delta_pct >= workover_q_delta:
            seg["is_workover"] = True

    # Детекция «плавного тренда» внутри длинного рабочего сегмента:
    # если длина ≥ gradual_trend_min_days И относительный дрейф за период
    # `|slope × (days-1) / mean × 100|` ≥ gradual_trend_min_drift_pct
    # — помечаем как gradual_trend (вниз/вверх). Это не отдельная точка
    # перелома, а характеристика самого сегмента: «плавное снижение Q
    # внутри стабильного режима» — типичный признак истощения / заводнения /
    # постепенного зарастания шлейфа.
    grad_min_days = int(_segth("gradual_trend_min_days"))
    grad_min_drift = _segth("gradual_trend_min_drift_pct")
    for seg in segments:
        seg["gradual_trend"] = None        # None | "down" | "up"
        seg["gradual_drift_pct"] = None    # дрейф за период, %
        if seg.get("is_shutdown_cluster"):
            continue
        if seg.get("days", 0) < grad_min_days:
            continue
        slope = seg.get("slope")
        mean_q = seg.get("mean_q_total")
        if slope is None or mean_q is None or not np.isfinite(slope) or mean_q == 0:
            continue
        span = max(1, seg["days"] - 1)
        drift_pct = slope * span / abs(mean_q) * 100.0
        if abs(drift_pct) >= grad_min_drift:
            seg["gradual_trend"] = "up" if drift_pct > 0 else "down"
            seg["gradual_drift_pct"] = drift_pct

    # Сравнение Q «до простоя ↔ после простоя»: для сегмента, идущего сразу
    # после кластера простоев, находим ПОСЛЕДНИЙ рабочий сегмент перед
    # цепочкой кластеров и сохраняем уровни + дельту + словесный вердикт.
    for idx, seg in enumerate(segments):
        if idx == 0 or seg.get("is_shutdown_cluster"):
            continue
        if not segments[idx - 1].get("is_shutdown_cluster"):
            continue
        # Идём назад через все подряд кластеры до первого рабочего сегмента
        j = idx - 1
        while j >= 0 and segments[j].get("is_shutdown_cluster"):
            j -= 1
        if j < 0:
            continue
        pre = segments[j]
        pre_q = pre.get("mean_q_total")
        curr_q = seg.get("mean_q_total")
        if pre_q is None or curr_q is None or pre_q == 0:
            continue
        delta_pct = (curr_q - pre_q) / abs(pre_q) * 100
        planned = _segth("preshutdown_planned_pct")
        success = _segth("preshutdown_success_pct")
        if abs(delta_pct) < planned:
            verdict = "возврат к прежнему режиму — плановая остановка без последствий для добычи"
        elif delta_pct >= success:
            verdict = (
                "дебит вырос относительно до-простойного режима — "
                "типичный признак успешного ГТМ/КРС"
            )
        elif delta_pct <= -success:
            # FIX-B: запрещены пластовые формулировки («ухудшение условий
            # притока/режима»). Заменяем на нейтральное наблюдение, требующее
            # сверки с журналом операций (см. feedback_diagnostic_interpretation_style).
            verdict = (
                "дебит снизился относительно до-простойного режима — "
                "фактический сдвиг режима эксплуатации после простоя; "
                "требуется сверка с журналом технологических операций"
            )
        else:
            verdict = "изменение дебита в пределах естественной вариативности"
        seg["preshutdown_q"] = pre_q
        seg["preshutdown_start"] = pre["start_date"]
        seg["preshutdown_end"] = pre["end_date"]
        seg["preshutdown_delta_pct"] = delta_pct
        seg["preshutdown_verdict"] = verdict

    # Пересчёт причины (cause) для кластеров и сегментов, идущих после них —
    # _determine_change_cause выше уже считал, но без знания is_workover.
    # Теперь повторно вызываем, чтобы headline «ремонт/КРС» попал в текст.
    for idx, seg in enumerate(segments):
        if idx == 0:
            continue
        curr_is_cluster = seg.get("is_shutdown_cluster")
        prev_is_cluster = segments[idx - 1].get("is_shutdown_cluster")
        if curr_is_cluster or prev_is_cluster:
            seg["cause"] = _determine_change_cause(seg, segments[idx - 1])

    # Формируем человекочитаемые описания сегментов
    descriptions = []
    cp_descriptions = []

    for i, seg in enumerate(segments):
        start_str = pd.Timestamp(seg["start_date"]).strftime("%d.%m")
        end_str = pd.Timestamp(seg["end_date"]).strftime("%d.%m")
        prev_seg = segments[i - 1] if i > 0 else None

        # Определяем тип сегмента
        is_first = (i == 0)
        is_shutdown_cluster = seg.get("is_shutdown_cluster", False)
        is_anomaly = seg["days"] <= 5 and seg.get("mean_shutdown", 0) > 200
        is_recovery = seg.get("recovery_info") is not None
        change_q = seg.get("change_q_pct")

        # Формируем описание
        # ВАРИАНТ D: Кластеры простоев описываем особым образом
        if is_shutdown_cluster and not is_first:
            # Сегмент выделен как кластер простоев
            label = "🔧 **Вероятный ремонт/КРС**" if seg.get("is_workover") else "**Период нестабильной работы/простоя**"
            desc = f"**{start_str}–{end_str}** ⏸ {label} ({seg['days']} дн.): "
            desc += f"Q общий={seg['mean_q_total']:.1f} тыс.м³/сут "
            desc += f"(простой ≈{seg['mean_shutdown']:.0f} мин/сут = {seg['mean_shutdown']/14.4:.0f}% времени)"
            if change_q is not None:
                desc += f". Q снизился на {abs(change_q):.0f}% относительно предыдущего периода"

        elif is_first:
            # Первый сегмент - описываем начальное состояние
            desc = f"**{start_str}–{end_str}**: Скважина работала "
            if seg["mean_shutdown"] < 30:
                desc += "стабильно "
            desc += f"с Q общий={seg['mean_q_total']:.1f}, Q рабочий={seg['mean_q_working']:.1f} тыс.м³/сут"
            if np.isfinite(seg["mean_dp"]):
                desc += f", ΔP={seg['mean_dp']:.1f} кгс/см²"
            if seg["mean_shutdown"] > 30:
                desc += f", простой {seg['mean_shutdown']:.0f} мин/сут"

        elif is_anomaly and change_q and change_q < -20:
            # Короткий период с падением - аномалия/простой
            desc = f"**{start_str}–{end_str}**: Резкое падение дебита на {abs(change_q):.0f}% "
            desc += f"(с {prev_seg['mean_q_total']:.1f} до {seg['mean_q_total']:.1f} тыс.м³/сут) "
            if seg["mean_shutdown"] > 200:
                desc += f"в связи с увеличением простоев до {seg['mean_shutdown']:.0f} мин/сут"
            elif seg.get("change_dp_pct") and seg["change_dp_pct"] < -20:
                desc += f"в связи со снижением ΔP на {abs(seg['change_dp_pct']):.0f}%"

        elif is_recovery or (change_q and change_q > 20 and prev_seg and (
                prev_seg.get("mean_shutdown", 0) > 200 or prev_seg.get("is_shutdown_cluster", False))):
            # Восстановление после аномалии или кластера простоев
            desc = f"**{start_str}–{end_str}**: Возобновление работы "
            if prev_seg and prev_seg.get("is_shutdown_cluster"):
                desc += "(выход из периода простоя). "
            else:
                desc += "(возврат к рабочим параметрам). "
            desc += f"Скважина работает с Q={seg['mean_q_total']:.1f} тыс.м³/сут"
            if seg["mean_shutdown"] < 30:
                desc += ", простоев нет"
            if np.isfinite(seg["mean_dp"]):
                desc += f", ΔP={seg['mean_dp']:.1f}"
            # Сравнение с до-простойным режимом (если известен)
            pre_q = seg.get("preshutdown_q")
            if pre_q is not None:
                pre_start = pd.Timestamp(seg["preshutdown_start"]).strftime("%d.%m")
                pre_end = pd.Timestamp(seg["preshutdown_end"]).strftime("%d.%m")
                delta = seg["preshutdown_delta_pct"]
                desc += (
                    f". До простоя ({pre_start}–{pre_end}) Q был {pre_q:.1f} тыс.м³/сут, "
                    f"после простоя — {seg['mean_q_total']:.1f} ({delta:+.0f}%). "
                    f"{seg['preshutdown_verdict'].capitalize()}"
                )

        elif change_q and change_q > 10:
            # Рост дебита
            desc = f"**{start_str}–{end_str}**: Увеличение дебита на {change_q:.0f}% "
            desc += f"(с {prev_seg['mean_q_total']:.1f} до {seg['mean_q_total']:.1f} тыс.м³/сут) "
            desc += _q_change_reason_phrase(seg, prev_seg, direction="up")

        elif change_q and change_q < -10:
            # Падение дебита
            desc = f"**{start_str}–{end_str}**: Снижение дебита на {abs(change_q):.0f}% "
            desc += f"(с {prev_seg['mean_q_total']:.1f} до {seg['mean_q_total']:.1f} тыс.м³/сут) "
            desc += _q_change_reason_phrase(seg, prev_seg, direction="down")

        else:
            # Стабильный период
            desc = f"**{start_str}–{end_str}**: Скважина работает "
            if seg["mean_shutdown"] < 30:
                desc += "стабильно "
            desc += f"с Q={seg['mean_q_total']:.1f} тыс.м³/сут"
            if np.isfinite(seg["mean_dp"]):
                desc += f", ΔP={seg['mean_dp']:.1f}"

        # Дописываем плавный тренд внутри сегмента (если есть).
        # ВАЖНО: не указываем «возможные причины» (истощение/заводнение/
        # зарастание) — это процессы месяцев и лет, на наших суточных данных
        # за несколько дней или недель такие выводы делать нельзя.
        # Просто фиксируем факт тренда.
        if seg.get("gradual_trend"):
            drift = seg["gradual_drift_pct"]
            direction = "снижение" if seg["gradual_trend"] == "down" else "рост"
            desc += (
                f". Внутри сегмента — плавн{'ое' if direction == 'снижение' else 'ый'} "
                f"{direction} Q на {abs(drift):.0f}% за {seg['days']} дн. "
                f"(тренд {seg['slope']:+.3f} тыс.м³/сут/сут)"
            )

        # Дописываем наблюдательный разбор (δΔP, согласованность Q/ΔP, рабочее время)
        cause_text = seg.get("cause")
        if cause_text:
            desc += f". Разбор: {cause_text}"

        descriptions.append(desc)

        # Каждая точка перелома → событие в «Ключевых событиях».
        # Формат: заголовок с эмодзи (как раньше) + причина после « — »
        # + опц. блок «до простоя ↔ после простоя» + опц. «Разбор: …».
        if not is_first:
            cp_date = pd.Timestamp(seg["start_date"]).strftime("%d.%m.%Y")
            prev_is_shutdown_cluster = prev_seg.get("is_shutdown_cluster", False) if prev_seg else False
            dramatic = _segth("q_change_dramatic_pct")
            notable  = _segth("q_change_notable_pct")
            prev_q = prev_seg["mean_q_total"]
            curr_q = seg["mean_q_total"]

            # Заголовок события — старый чистый формат
            if is_shutdown_cluster and not prev_is_shutdown_cluster:
                if seg.get("is_workover"):
                    event = (f"🔧 **{cp_date}**: Вероятный ремонт/КРС "
                             f"(простой ≈{seg['mean_shutdown']:.0f} мин/сут, {seg['days']} дн.)")
                else:
                    event = (f"⏸ **{cp_date}**: Остановка скважины / период простоя "
                             f"(Q {prev_q:.1f} → {curr_q:.1f} тыс.м³/сут, "
                             f"простой ≈{seg['mean_shutdown']:.0f} мин/сут)")
            elif not is_shutdown_cluster and prev_is_shutdown_cluster:
                event = (f"▶ **{cp_date}**: Возобновление работы после простоя "
                         f"(Q {curr_q:.1f} тыс.м³/сут")
                if seg["mean_shutdown"] < 30:
                    event += ", простоев нет"
                event += ")"
            elif change_q is not None and change_q <= -dramatic:
                event = (f"📉 **{cp_date}**: Резкое падение Q на {abs(change_q):.0f}% "
                         f"(с {prev_q:.1f} до {curr_q:.1f} тыс.м³/сут)")
            elif change_q is not None and _q_change_is_notable(change_q, prev_q) and change_q < 0:
                event = (f"↘ **{cp_date}**: Снижение Q на {abs(change_q):.0f}% "
                         f"(с {prev_q:.1f} до {curr_q:.1f} тыс.м³/сут)")
            elif change_q is not None and change_q >= dramatic:
                event = (f"📈 **{cp_date}**: Резкий рост Q на {change_q:.0f}% "
                         f"(с {prev_q:.1f} до {curr_q:.1f} тыс.м³/сут)")
            elif change_q is not None and _q_change_is_notable(change_q, prev_q) and change_q > 0:
                event = (f"↗ **{cp_date}**: Рост Q на {change_q:.0f}% "
                         f"(с {prev_q:.1f} до {curr_q:.1f} тыс.м³/сут)")
            else:
                # «Тихая» смена режима без значимого изменения Q
                short_tag = _changepoint_short_tag(seg, prev_seg) or "смена режима"
                event = (f"• **{cp_date}**: Смена режима ({short_tag}) "
                         f"(Q {prev_q:.1f} → {curr_q:.1f} тыс.м³/сут)")

            # Причина «— ...» (как в старом формате): простои / ΔP / давления
            reason_bits: list[str] = []
            sh_notable = _segth("shutdown_change_notable")
            dp_notable = _segth("dp_change_in_event_pct")
            if seg.get("change_shutdown") is not None and abs(seg["change_shutdown"]) >= sh_notable:
                sign = "+" if seg["change_shutdown"] > 0 else ""
                reason_bits.append(f"простои {sign}{seg['change_shutdown']:.0f} мин/сут")
            if (seg.get("change_dp_pct") is not None and np.isfinite(seg["change_dp_pct"])
                    and abs(seg["change_dp_pct"]) >= dp_notable):
                reason_bits.append(f"ΔP {seg['change_dp_pct']:+.0f}%")
            if reason_bits:
                event += " — " + ", ".join(reason_bits)

            # Сравнение с до-простойным режимом — для выхода из простоя
            if not is_shutdown_cluster and prev_is_shutdown_cluster and seg.get("preshutdown_q") is not None:
                delta = seg["preshutdown_delta_pct"]
                event += (
                    f". До простоя Q был {seg['preshutdown_q']:.1f}, "
                    f"после — {curr_q:.1f} ({delta:+.0f}%). "
                    f"{seg['preshutdown_verdict'].capitalize()}"
                )

            # Полный разбор причины (cause) — отдельной строкой
            cause_text = seg.get("cause")
            if cause_text:
                event += f". Разбор: {cause_text}"

            cp_descriptions.append(event)

    return {
        "segments": [
            {
                "num": i + 1,
                "start": pd.Timestamp(s["start_date"]).strftime("%d.%m.%Y"),
                "end": pd.Timestamp(s["end_date"]).strftime("%d.%m.%Y"),
                "days": s["days"],
                "mean_q": round(s["mean_q_total"], 2),
                "mean_q_working": round(s["mean_q_working"], 2),
                "slope": round(s["slope"], 3) if not np.isnan(s["slope"]) else None,
                "change_pct": round(s["change_q_pct"], 1) if s["change_q_pct"] is not None else None,
                "mean_shutdown": round(s["mean_shutdown"], 0),
                "working_pct": round(s["working_pct"], 1),
                "mean_dp": round(s["mean_dp"], 2) if np.isfinite(s["mean_dp"]) else None,
                "cause": s["cause"],
                "recovery_info": s["recovery_info"],
                # Вариант D: маркер кластера простоев
                "is_shutdown_cluster": s.get("is_shutdown_cluster", False),
                # Эвристика: вероятный ремонт/КРС
                "is_workover": s.get("is_workover", False),
                # v1.4: плавный тренд внутри сегмента
                "gradual_trend": s.get("gradual_trend"),
                "gradual_drift_pct": round(s["gradual_drift_pct"], 1) if s.get("gradual_drift_pct") is not None else None,
                # v1.3: сравнение с до-простойным режимом
                "preshutdown_q": round(s["preshutdown_q"], 2) if s.get("preshutdown_q") is not None else None,
                "preshutdown_delta_pct": round(s["preshutdown_delta_pct"], 1) if s.get("preshutdown_delta_pct") is not None else None,
                "preshutdown_verdict": s.get("preshutdown_verdict"),
                # Компоненты давления — нужны для гистерезис-анализа в PAV-балле
                "mean_p_wellhead": (round(s["mean_p_wellhead"], 2)
                                    if s.get("mean_p_wellhead") is not None
                                    and np.isfinite(s.get("mean_p_wellhead", float("nan")))
                                    else None),
                "mean_p_flowline": (round(s["mean_p_flowline"], 2)
                                    if s.get("mean_p_flowline") is not None
                                    and np.isfinite(s.get("mean_p_flowline", float("nan")))
                                    else None),
                "change_shutdown": s.get("change_shutdown"),
                # Штуцер — для PAV-парадокса и для описания
                "mean_choke_mm": (round(s["mean_choke_mm"], 1)
                                 if s.get("mean_choke_mm") is not None
                                 and np.isfinite(s.get("mean_choke_mm", float("nan")))
                                 else None),
                "change_choke": (round(s["change_choke"], 1)
                                if s.get("change_choke") is not None
                                and np.isfinite(s.get("change_choke", float("nan")))
                                else None),
            }
            for i, s in enumerate(segments)
        ],
        "changepoints": [
            {
                "date": pd.Timestamp(dates[cp]).strftime("%d.%m.%Y"),
                "idx": cp,
            }
            for cp in changepoints
        ],
        "descriptions": descriptions,
        "cp_descriptions": cp_descriptions,
    }



# ════════════════════════════════════════════════════════════════════
# 7. Детекторы признаков ПАВ
# ════════════════════════════════════════════════════════════════════
def _detect_dp_hysteresis(segments: list) -> dict | None:
    """Детектор гистерезиса ΔP.

    Ищет такую картину: за период ΔP значимо упал, при этом **P шлейфа
    вернулась к исходному уровню**, а **P устья не восстановилось**.

    Физический смысл: линия (внешний фактор) вернулась в норму, а
    устьевое давление и ΔP — нет. На коротком окне (недели–месяцы)
    пластовое давление не успевает падать; значит, что-то изменилось в
    стволе. Самая частая причина — **жидкость осела во время эпизода
    роста линии и не вышла обратно**. Это прямой сигнал ПАВ-кандидата.

    Возвращает dict с подробностями, если гистерезис обнаружен; None
    иначе. Поля dict: dp_first, dp_last, dp_loss_pct, pf_first, pf_last,
    pf_change, pw_first, pw_last, pw_loss.
    """
    if not segments or len(segments) < 3:
        return None
    # Берём первый РАБОЧИЙ сегмент (не кластер простоев) и последний
    # рабочий — чтобы избежать кластеров на краях, где давление измерено
    # в закрытой скважине и не сопоставимо с рабочими.
    first = next((s for s in segments if not s.get("is_shutdown_cluster")), None)
    last  = next((s for s in reversed(segments) if not s.get("is_shutdown_cluster")), None)
    if first is None or last is None or first is last:
        return None
    dp_a, dp_b = first.get("mean_dp"), last.get("mean_dp")
    pf_a, pf_b = first.get("mean_p_flowline"), last.get("mean_p_flowline")
    pw_a, pw_b = first.get("mean_p_wellhead"), last.get("mean_p_wellhead")
    if any(v is None for v in (dp_a, dp_b, pf_a, pf_b, pw_a, pw_b)):
        return None
    if dp_a <= 0:
        return None
    dp_loss_pct = (dp_a - dp_b) / dp_a * 100.0
    if dp_loss_pct < 20.0:
        return None
    pf_change = abs(pf_b - pf_a)
    if pf_change > 1.5:
        # Линия не вернулась к исходному → это потенциально внешний фактор,
        # а не гистерезис. Гистерезис требует возврата линии.
        return None
    pw_loss = pw_a - pw_b
    if pw_loss < 0.5:
        # Устье восстановилось — гистерезиса нет (всё ОК)
        return None
    return {
        "dp_first": dp_a, "dp_last": dp_b, "dp_loss_pct": dp_loss_pct,
        "pf_first": pf_a, "pf_last": pf_b, "pf_change": pf_change,
        "pw_first": pw_a, "pw_last": pw_b, "pw_loss": pw_loss,
        "first_period": f"{first.get('start')}–{first.get('end')}",
        "last_period":  f"{last.get('start')}–{last.get('end')}",
    }


def _detect_accelerated_decline_after_choke(segments: list) -> list[dict]:
    """Детектор: УВЕЛИЧИЛИ штуцер, а Q стал падать БЫСТРЕЕ, чем до этого.

    Физика признака:
    * Больший штуцер → выше скорость газа на устье → ожидаемо выше Q.
    * Но также выше скорость = больше воды, которую газ поднимает.
    * Если жидкости много, увеличенный поток её «вытаскивает», но скважина
      не справляется (вода накапливается обратно или конденсируется быстрее).
    * Результат: вместо ожидаемого роста Q — ускоренный спад.

    Это сильный косвенный признак ПАВ-кандидата: даже операционное действие
    (увеличение штуцера) не вытаскивает скважину из режима накопления.

    Возвращает список случаев с полями: period_pre, period_post,
    choke_pre, choke_post, rate_pre_pct_per_day, rate_post_pct_per_day,
    rate_delta_pct_per_day, days.
    """
    out: list[dict] = []
    for i in range(1, len(segments)):
        prev = segments[i - 1]
        curr = segments[i]
        if prev.get("is_shutdown_cluster") or curr.get("is_shutdown_cluster"):
            continue
        cc = curr.get("change_choke")
        if cc is None or cc <= 0:    # штуцер не увеличивался — не наш случай
            continue
        slope_a = prev.get("slope")
        slope_b = curr.get("slope")
        mean_a = prev.get("mean_q")
        mean_b = curr.get("mean_q")
        if any(v is None for v in (slope_a, slope_b, mean_a, mean_b)):
            continue
        if mean_a <= 0 or mean_b <= 0:
            continue
        # Нормированный темп спада: положительное число = Q падает
        rate_a = -slope_a / mean_a * 100  # % в день
        rate_b = -slope_b / mean_b * 100
        if rate_b <= 0:
            continue                # Q после увеличения не падает — норма
        # Темп спада после увеличения штуцера должен быть значимо выше
        if rate_b - rate_a < 0.3:
            continue                # ≥ 0.3 %/день дополнительного спада
        days = curr.get("days") or 0
        if days < 5:
            continue                # слишком короткий сегмент — ненадёжно
        out.append({
            "period_pre":   f"{prev['start']}–{prev['end']}",
            "period_post":  f"{curr['start']}–{curr['end']}",
            "choke_pre":    prev.get("mean_choke_mm"),
            "choke_post":   curr.get("mean_choke_mm"),
            "rate_pre_pct_per_day":   rate_a,
            "rate_post_pct_per_day":  rate_b,
            "rate_delta_pct_per_day": rate_b - rate_a,
            "days": days,
        })
    return out


def _detect_operator_purges(df: pd.DataFrame) -> dict:
    """Детектор «операторских продувок» в суточных данных.

    Контекст наших данных (важно!):
    * Источник — суточные сводки геологов (не круглосуточный мониторинг).
    * Простой в колонке `shutdown_min` оператор проставляет САМ, когда
      проводит работу на скважине.
    * Многодневный полный простой (≥ 3 дней при ~1440 мин/сут) — почти
      наверняка ремонт / КРС, не наша задача.
    * Однодневный изолированный или 2-дневный простой 30–1200 мин/сут —
      почти наверняка продувка от жидкости (плановая или внеплановая).

    Следовательно: множество коротких изолированных простоев = оператор
    регулярно борется с накоплением жидкости. Это **прямой косвенный
    признак** ПАВ-кандидата — кому-то приходится вручную её удалять.

    Возвращает dict:
      n_purges:           число дней с признаками продувки
      purges_per_30:      нормированная частота (на 30 дней периода)
      total_purge_minutes: суммарная длительность продувок, мин
    """
    if "shutdown_min" not in df.columns or df.empty:
        return {"n_purges": 0, "purges_per_30": 0.0, "total_purge_minutes": 0}

    d = df.sort_values("date").copy()
    sh = d["shutdown_min"].fillna(0).to_numpy(dtype=float)
    n = len(sh)
    if n == 0:
        return {"n_purges": 0, "purges_per_30": 0.0, "total_purge_minutes": 0}

    # «Кластерный день» — день внутри многодневной (≥ 2 дней подряд)
    # серии с простоем ≥ 600 мин/сут ИЛИ отдельный день полной остановки
    # (≥ 1200 мин/сут). Это всё, что НЕ операторская продувка.
    cluster_day = np.zeros(n, dtype=bool)
    i = 0
    while i < n:
        if sh[i] >= 1200:
            cluster_day[i] = True
            i += 1
        elif sh[i] >= 600:
            start = i
            while i < n and sh[i] >= 600:
                i += 1
            if i - start >= 2:
                cluster_day[start:i] = True
        else:
            i += 1

    # Операторская продувка: 30 ≤ shutdown < 1200 мин/сут И день не
    # принадлежит многодневному кластеру.
    purge_days = (sh >= 30) & (sh < 1200) & (~cluster_day)
    n_purges = int(purge_days.sum())
    total_minutes = int(sh[purge_days].sum())
    purges_per_30 = (n_purges * 30.0 / n) if n > 0 else 0.0
    return {
        "n_purges":            n_purges,
        "purges_per_30":       purges_per_30,
        "total_purge_minutes": total_minutes,
    }


def _detect_q_working_baseline_drift(segments: list) -> dict | None:
    """Детектор медленного монотонного спада Q рабочего на длинном участке.

    `gradual_trend` в `_segment_trends_extended` ищет спад ВНУТРИ одного
    сегмента (порог 10 %). Но накопление жидкости часто разворачивается
    плавно через **несколько подряд рабочих сегментов**, каждый из
    которых сам по себе ниже порога — а в сумме это явный медленный
    спад фактического дебита.

    Алгоритм:
      * Находит все «забеги» — серии подряд НЕ-кластерных сегментов.
      * В каждом забеге считает спад Q рабочего: `(q_first − q_last) / q_first`.
      * Проверяет монотонность: каждый следующий сегмент не выше
        предыдущего более чем на 10 %.
      * Возвращает лучшую (с наибольшим спадом) серию длиной ≥ 14 дней
        со спадом ≥ 5 %.

    Это **сигнал ПАВ-кандидата**: при стабильном паспортном Q общем
    Q рабочий «тает» — типовой признак накопления жидкости в стволе.
    """
    runs: list[list] = []
    current: list = []
    for s in segments:
        if s.get("is_shutdown_cluster"):
            if len(current) >= 2:
                runs.append(current)
            current = []
        else:
            current.append(s)
    if len(current) >= 2:
        runs.append(current)

    best = None
    for run in runs:
        total_days = sum(int(s.get("days", 0) or 0) for s in run)
        if total_days < 14:
            continue
        q_first = run[0].get("mean_q_working")
        q_last  = run[-1].get("mean_q_working")
        if q_first is None or q_last is None or q_first <= 0:
            continue
        drift_pct = (q_first - q_last) / q_first * 100.0
        if drift_pct < 5.0:
            continue
        # Монотонность: ни один следующий не выше предыдущего более чем на 10 %
        monotonic = True
        for i in range(1, len(run)):
            pq = run[i - 1].get("mean_q_working") or 0
            cq = run[i].get("mean_q_working") or 0
            if pq > 0 and cq > pq * 1.10:
                monotonic = False
                break
        if not monotonic:
            continue
        cand = {
            "drift_pct":  drift_pct,
            "days":       total_days,
            "q_first":    q_first,
            "q_last":     q_last,
            "n_segments": len(run),
            "period_start": run[0].get("start"),
            "period_end":   run[-1].get("end"),
        }
        if best is None or drift_pct > best["drift_pct"]:
            best = cand
    return best


def _detect_post_shutdown_uplift(segments: list) -> list:
    """Детектор «выноса жидкости» после простоя.

    Ищет случаи, когда Q рабочий ПОСЛЕ простоя выше, чем Q рабочий ДО
    простоя на ≥ 10 %. Это типовой признак удачной продувки: за время
    стоянки скважина «отлежалась», жидкость осела и при перезапуске её
    вынесло вместе с газом — рабочий дебит вырос относительно прежнего.

    Возвращает список dict (по одному на каждый случай) с полями:
    period_before, q_before, period_shutdown, period_after, q_after,
    uplift_pct.
    """
    out = []
    for i, seg in enumerate(segments):
        if seg.get("is_shutdown_cluster") or i == 0:
            continue
        if not segments[i - 1].get("is_shutdown_cluster"):
            continue
        # Найти последний рабочий сегмент перед цепочкой кластеров
        j = i - 1
        while j >= 0 and segments[j].get("is_shutdown_cluster"):
            j -= 1
        if j < 0:
            continue
        before = segments[j]
        q_b = before.get("mean_q_working")
        q_a = seg.get("mean_q_working")
        if q_b is None or q_a is None or q_b <= 0:
            continue
        uplift = (q_a - q_b) / q_b
        if uplift >= 0.10:
            out.append({
                "period_before": f"{before.get('start')}–{before.get('end')}",
                "q_before": q_b,
                "period_shutdown": f"{segments[i - 1].get('start')}–{segments[i - 1].get('end')}",
                "period_after": f"{seg.get('start')}–{seg.get('end')}",
                "q_after": q_a,
                "uplift_pct": uplift * 100.0,
            })
    return out


def _detect_load_purge_cycles(segments: list) -> int:
    """Считает циклы «накопление-продувка-восстановление» в списке сегментов.

    Паттерн:
        [рабочий сегмент с Q ↓]
      → [короткий простой 1-5 дней]
      → [рабочий сегмент с восстановлением Q до прежнего уровня]

    Признаки:
      • для рабочего сегмента «с Q ↓» — gradual_trend="down" ИЛИ change_pct < -10
      • для простоя — is_shutdown_cluster=True И days ≤ 5
      • для восстановления — preshutdown_verdict содержит «возврат к прежнему»

    Если паттерн встречается ≥ 1 раз — это уже сигнал; ≥ 2 раз — устойчивый
    цикл накопления жидкости (типичный признак ПАВ-кандидата).
    """
    if not segments or len(segments) < 3:
        return 0
    n = 0
    for i in range(2, len(segments)):
        curr = segments[i]
        if curr.get("is_shutdown_cluster"):
            continue
        verdict = curr.get("preshutdown_verdict") or ""
        if "возврат к прежнему" not in verdict:
            continue
        prev = segments[i - 1]
        if not (prev.get("is_shutdown_cluster") and prev.get("days", 0) <= 5):
            continue
        # Идём назад через все подряд кластеры до первого рабочего
        j = i - 2
        before = None
        while j >= 0:
            if not segments[j].get("is_shutdown_cluster"):
                before = segments[j]
                break
            j -= 1
        if before is None:
            continue
        # «Снижение Q» — либо плавный тренд вниз, либо change_pct < -10
        had_decline = (
            before.get("gradual_trend") == "down" or
            (before.get("change_pct") is not None and before["change_pct"] < -10)
        )
        if had_decline:
            n += 1
    return n



# ════════════════════════════════════════════════════════════════════
# 8. ⭐ _segment_analysis_dual + _compute_pav_score
# ════════════════════════════════════════════════════════════════════
def _segment_analysis_dual(df: pd.DataFrame) -> dict:
    """Параллельный сегментный анализ на ДВУХ Q-кривых: q_gas_total
    (расчётная / паспортная) и q_gas_working (фактическая суточная).

    Возвращает оба результата + сравнение переломов:
    * `primary`        — анализ на q_gas_total
    * `working`        — анализ на q_gas_working (внутри swap-колонок)
    * `common_dates`   — даты переломов, найденные В ОБОИХ анализах
    * `only_total`     — переломы только в q_total (возможно бумажные)
    * `only_working`   — переломы только в q_working (реальные сдвиги,
                        не отражённые в паспортном расчёте)

    Расхождение между анализами — диагностический сигнал: если в q_working
    есть переломы, которых нет в q_total — фактическая работа отклоняется
    от расчёта (накопление жидкости / износ штуцера / 2-фазный режим).
    """
    primary = _segment_analysis(df)
    if "q_gas_working" not in df.columns or df["q_gas_working"].isna().all():
        # Нет второй кривой — параллельный анализ невозможен
        return {
            "primary": primary,
            "working": None,
            "common_dates": [],
            "only_total":   [cp["date"] for cp in primary.get("changepoints", [])],
            "only_working": [],
        }
    # Подменяем колонки, чтобы _segment_analysis обработал q_working как
    # «первичный» ряд (по нему детектируется ПЕРЕЛОМ). Структура выхода
    # та же; field naming в коде остаётся «q_total/q_working» — это просто
    # внутренние имена потока, semantically в working-анализе они меняются.
    df_swap = df.copy()
    df_swap["q_gas_total"]   = df["q_gas_working"]
    df_swap["q_gas_working"] = df["q_gas_total"]
    working = _segment_analysis(df_swap)

    t_dates = {cp["date"] for cp in primary.get("changepoints", [])}
    w_dates = {cp["date"] for cp in working.get("changepoints", [])}
    return {
        "primary":      primary,
        "working":      working,
        "common_dates": sorted(t_dates & w_dates),
        "only_total":   sorted(t_dates - w_dates),
        "only_working": sorted(w_dates - t_dates),
    }


# ─────────────────── ПАВ-балл и сценарий ───────────────────────────
# Эта часть заменяет «Розу причин нестабильности». Цель: одна цифра 0..100,
# оценивающая, насколько скважина — кандидат на закачку ПАВ для удаления
# жидкости из ствола. Расчёт идёт ПОВЕРХ выхода сегментного анализа.

def _compute_pav_score(dual: dict, sub: pd.DataFrame) -> dict:
    """Считает балл ПАВ-кандидата и формирует обоснование.

    Returns dict:
        score: float 0..100 | None (None если нет данных по давлению)
        confidence: "высокая" | "средняя" | "низкая" | "n/a"
        scenario: str — короткая метка сценария
        recommendation: str — рекомендуемое действие
        signs_pro: list[(text, strength 0..1)] — признаки за ПАВ
        signs_con: list[(text, strength 0..1)] — контраргументы (штрафы)
        breakdown: dict — все промежуточные значения (для отладки/ТЗ)
    """
    primary = (dual or {}).get("primary", {}) or {}
    segments = primary.get("segments", []) or []

    has_pw = ("p_wellhead" in sub.columns and sub["p_wellhead"].notna().any())
    has_pf = ("p_flowline" in sub.columns and sub["p_flowline"].notna().any())
    if not (has_pw and has_pf):
        return {
            "score": None,
            "confidence": "n/a",
            "scenario": "Нет данных по давлению",
            "recommendation": "Невозможно оценить — отсутствуют замеры давлений",
            "signs_pro": [],
            "signs_con": [],
            "breakdown": {},
        }
    if not segments:
        return {
            "score": 0.0,
            "confidence": "низкая",
            "scenario": "Недостаточно данных",
            "recommendation": "Период слишком короткий для сегментного анализа",
            "signs_pro": [],
            "signs_con": [],
            "breakdown": {},
        }

    total_days = sum(int(s.get("days", 0) or 0) for s in segments) or 1
    line_thresh = _segth("p_flowline_priority_threshold")

    # === ПОЛОЖИТЕЛЬНЫЕ ПРИЗНАКИ (за ПАВ-кандидата) ===

    # 1. Плавный спад Q — max |drift| из down-трендов
    declines = [abs(s["gradual_drift_pct"]) for s in segments
                if s.get("gradual_trend") == "down"
                and s.get("gradual_drift_pct") is not None]
    max_decline = max(declines) if declines else 0.0
    s_gradual = min(1.0, max_decline / _segth("pav_norm_gradual_drift"))

    # 2. P уст падение при стабильной линии — сумма |δP_уст| по переходам
    sum_pw_drop = 0.0
    n_pw_stable_drops = 0
    for i in range(1, len(segments)):
        prev, curr = segments[i - 1], segments[i]
        prev_pf = (prev.get("mean_p_flowline") if "mean_p_flowline" in prev
                   else None)
        curr_pf = (curr.get("mean_p_flowline") if "mean_p_flowline" in curr
                   else None)
        prev_pw = (prev.get("mean_p_wellhead") if "mean_p_wellhead" in prev
                   else None)
        curr_pw = (curr.get("mean_p_wellhead") if "mean_p_wellhead" in curr
                   else None)
        # «mean_p_*» лежит в расширенном выходе, но в стриппованном — нет.
        # Если нет — пропускаем (поле просто отсутствует).
        if (prev_pf is None or curr_pf is None
                or prev_pw is None or curr_pw is None):
            continue
        if abs(curr_pf - prev_pf) >= line_thresh:
            continue
        d_pw = curr_pw - prev_pw
        if d_pw < -0.5:
            sum_pw_drop += abs(d_pw)
            n_pw_stable_drops += 1
    s_pw_decline = min(1.0, sum_pw_drop / _segth("pav_norm_pw_drop_total"))

    # 3. ΔP сжимается — деактивирован, если активен гистерезис ΔP
    # (они описывают ОДНО явление — сжатие ΔP не вернувшееся к норме).
    # Применяется только когда: ΔP упал, линия стабильна, но гистерезиса
    # как такового нет (P устья не упало значимо). Редкий случай.
    dp_vals = [s["mean_dp"] for s in segments
               if s.get("mean_dp") is not None and np.isfinite(s["mean_dp"])]
    s_dp_compress = 0.0
    dp_first, dp_last = None, None
    if len(dp_vals) >= 2 and dp_vals[0] > 0:
        dp_first, dp_last = dp_vals[0], dp_vals[-1]
        rel_drop = (dp_first - dp_last) / abs(dp_first)
        if rel_drop > 0:
            pf_first_v = next((s.get("mean_p_flowline") for s in segments
                              if s.get("mean_p_flowline") is not None), None)
            pf_last_v = next((s.get("mean_p_flowline") for s in reversed(segments)
                             if s.get("mean_p_flowline") is not None), None)
            if pf_first_v is not None and pf_last_v is not None:
                pf_change = pf_last_v - pf_first_v
                if abs(pf_change) < 1.0:
                    s_dp_compress = min(1.0, rel_drop / _segth("pav_norm_dp_drop_rel"))
                else:
                    s_dp_compress = min(1.0, rel_drop / _segth("pav_norm_dp_drop_rel")) * 0.3
            else:
                s_dp_compress = min(1.0, rel_drop / _segth("pav_norm_dp_drop_rel"))

    # 4. Цикличность накопление-продувка
    n_cycles = _detect_load_purge_cycles(segments)
    s_cyclicity = min(1.0, n_cycles / _segth("pav_norm_cycles"))

    # 5. Короткие простои (продувки)
    short_clusters = sum(1 for s in segments
                         if s.get("is_shutdown_cluster")
                         and s.get("days", 0) <= 5)
    purges_per_30 = short_clusters * 30.0 / total_days
    s_purges = min(1.0, max(0.0, purges_per_30 / _segth("pav_norm_purges_per_30")))

    # 6. Расхождение q_total vs q_working (среднее относительное).
    # Это расхождение паспортного и фактического дебита — реальный сигнал
    # 2-фазности / накопления жидкости. Но: если расхождение «техническое»
    # (большая часть периода в простое — Q общий усреднён на 0, Q раб — нет),
    # то сигнал не диагностический, и его суппрессируем.
    s_divergence = 0.0
    rel_div_pct = 0.0
    if "q_gas_working" in sub.columns and "q_gas_total" in sub.columns:
        qt = sub["q_gas_total"].fillna(0.0)
        qw = sub["q_gas_working"].fillna(0.0)
        mask = (qt > 0) & (qw > 0)
        if mask.any():
            rel_div = (qt - qw).abs() / qt
            rel_div_pct = float(rel_div[mask].mean())
            s_divergence = min(1.0, rel_div_pct / _segth("pav_norm_divergence_rel"))
            # Если подавляющая часть периода в простое — расхождение неинформативно
            shutdown_share_quick = sum(int(s.get("days", 0) or 0) for s in segments
                                       if s.get("is_shutdown_cluster")) / total_days
            if shutdown_share_quick > 0.40:
                s_divergence *= 0.3

    # 7. ГИСТЕРЕЗИС ΔP — самый сильный сигнал. Линия вернулась, ΔP и
    # устье — нет → жидкость осела во время эпизода роста линии.
    hyster = _detect_dp_hysteresis(segments)
    s_hysteresis = 0.0
    if hyster:
        s_hysteresis = min(1.0, hyster["dp_loss_pct"] / _segth("pav_norm_hysteresis_pct"))
        # Если активен гистерезис — DP-сжатие ОТКЛЮЧАЕМ ПОЛНОСТЬЮ
        # (они описывают одно явление — нельзя дважды засчитывать).
        s_dp_compress = 0.0

    # 8. ВЫНОС ЖИДКОСТИ — Q после простоя выше, чем Q до простоя.
    # Это признак удачной продувки: жидкость вышла, скважина заработала лучше.
    uplifts = _detect_post_shutdown_uplift(segments)
    s_uplift = min(1.0, len(uplifts) / _segth("pav_norm_uplift_count"))

    # 9. МОНОТОННЫЙ СПАД Q РАБОЧЕГО на длинном забеге сегментов.
    # Накопление жидкости часто разворачивается медленно через несколько
    # подряд сегментов — каждый сам по себе ниже порога gradual_trend (10 %),
    # а в сумме спад 8–15 % за месяц. Это «классика» признака накопления.
    mono = _detect_q_working_baseline_drift(segments)
    s_mono = 0.0
    if mono:
        s_mono = min(1.0, mono["drift_pct"] / _segth("pav_norm_mono_drift_pct"))

    # 10. ОПЕРАТОРСКИЕ ПРОДУВКИ — изолированные дни с простоем 30..1200 мин.
    # В наших суточных сводках простой проставляется ВРУЧНУЮ оператором,
    # когда он сам проводит работы. Короткий разовый простой ≈ продувка
    # от жидкости. Множество таких событий = оператор регулярно борется
    # с жидкостью, это сильный косвенный признак ПАВ-кандидата.
    op = _detect_operator_purges(sub)
    n_op = op["n_purges"]
    ops_per_30 = op["purges_per_30"]
    s_op_purges = min(1.0, ops_per_30 / _segth("pav_norm_op_purges_per_30"))

    # 11. ПАРАДОКС «Q↓ при штуцере↑» — сильный признак, что скважина
    # теряет дебит из-за ВНУТРЕННЕГО фактора. Физика: больше штуцер →
    # должно быть больше Q. Если штуцер вырос (>0, СТРОГО), а Q упал —
    # парадокс. ВАЖНО: стабильный штуцер (cc==0) НЕ парадокс, это просто
    # обычное падение Q без оператор. решений — учитывается другими
    # признаками.
    paradox_cases = []
    n_choke_explained = 0
    for k in range(1, len(segments)):
        prev_s = segments[k - 1]
        curr_s = segments[k]
        if prev_s.get("is_shutdown_cluster") or curr_s.get("is_shutdown_cluster"):
            continue
        cc = curr_s.get("change_choke")
        qc = curr_s.get("change_pct")
        if cc is None or qc is None:
            continue
        # Парадокс ТОЛЬКО при УВЕЛИЧЕНИИ штуцера на ≥ 0.5 мм
        if cc >= 0.5 and qc <= -15:
            paradox_cases.append({
                "period": f"{curr_s.get('start')}–{curr_s.get('end')}",
                "choke_prev": prev_s.get("mean_choke_mm"),
                "choke_curr": curr_s.get("mean_choke_mm"),
                "change_choke": cc,
                "change_q":     qc,
            })
        elif (cc >= 0.5 and qc > 5) or (cc <= -0.5 and qc < -5):
            n_choke_explained += 1
    n_paradox = len(paradox_cases)
    s_choke_paradox = min(1.0, float(n_paradox))

    # 12. УСКОРЕННЫЙ СПАД Q ПОСЛЕ УВЕЛИЧЕНИЯ ШТУЦЕРА.
    # Физически: больший штуцер → больше скорость газа → ожидаемо
    # больше Q. Но также: больше воды, поднятой газом. Если жидкости
    # много — скважина не справляется, и Q падает БЫСТРЕЕ, чем до
    # увеличения. Это сильный косвенный признак ПАВ-кандидата:
    # операционное действие не вытаскивает скважину из режима накопления.
    accel = _detect_accelerated_decline_after_choke(segments)
    n_accel = len(accel)
    s_choke_accel = min(1.0, float(n_accel))  # один случай → ось 1.0

    # === БАЗА ===
    base = (
        _segth("pav_w_hysteresis")     * s_hysteresis
        + _segth("pav_w_choke_paradox") * s_choke_paradox
        + _segth("pav_w_choke_accel")  * s_choke_accel
        + _segth("pav_w_mono_drift")   * s_mono
        + _segth("pav_w_op_purges")    * s_op_purges
        + _segth("pav_w_uplift")       * s_uplift
        + _segth("pav_w_gradual")      * s_gradual
        + _segth("pav_w_pw_decline")   * s_pw_decline
        + _segth("pav_w_dp_compression") * s_dp_compress
        + _segth("pav_w_cyclicity")    * s_cyclicity
        + _segth("pav_w_purges")       * s_purges
        + _segth("pav_w_divergence")   * s_divergence
    )

    # === ОТРИЦАТЕЛЬНЫЕ ПРИЗНАКИ (штрафы) ===

    # P1: ВНЕШНИЙ ФАКТОР линии — макс. отклонение P шлейфа от исходного.
    # ВАЖНО: если активен гистерезис, эпизод роста линии УЖЕ объяснён как
    # причина накопления (через признак 7). Повторно штрафовать = двойной
    # счёт. Поэтому при активном гистерезисе штраф снижается в 3 раза:
    # линия вернулась — претензия не к ней, а к скважине.
    pf_series = [s.get("mean_p_flowline") for s in segments
                 if s.get("mean_p_flowline") is not None]
    s_pf_rise = 0.0
    max_pf_excursion = 0.0
    if len(pf_series) >= 2:
        pf_baseline = pf_series[0]
        for v in pf_series[1:]:
            excursion = max(0.0, v - pf_baseline)
            if excursion > max_pf_excursion:
                max_pf_excursion = excursion
        s_pf_rise = min(1.0, max_pf_excursion / _segth("pav_norm_pf_rise_total"))
        if s_hysteresis > 0.3:
            s_pf_rise *= 0.33

    # P2: подавляющий простой
    shutdown_days = sum(s["days"] for s in segments if s.get("is_shutdown_cluster"))
    shutdown_share = shutdown_days / total_days
    lo = _segth("pav_dom_shutdown_lo")
    hi = _segth("pav_dom_shutdown_hi")
    s_dom_shutdown = 0.0
    if shutdown_share > lo:
        s_dom_shutdown = min(1.0, (shutdown_share - lo) / max(0.001, hi - lo))

    # P3: свежий ремонт
    s_recent_workover = 0.0
    recent_thresh = int(_segth("pav_recent_workover_days"))
    last_data_date = None
    try:
        last_data_date = pd.to_datetime(segments[-1]["end"], format="%d.%m.%Y")
    except Exception:
        pass
    if last_data_date is not None:
        for s in reversed(segments):
            if not s.get("is_workover"):
                continue
            try:
                end_d = pd.to_datetime(s["end"], format="%d.%m.%Y")
                days_since = (last_data_date - end_d).days
                if days_since <= recent_thresh:
                    s_recent_workover = 1.0
            except Exception:
                pass
            break

    # P4: полная стабильность. Скважина считается стабильной, если:
    # — нет плавного спада ни в одном сегменте,
    # — нет ни одного резкого падения Q (change_pct ≤ −20),
    # — нет ни одного кластера простоев (is_shutdown_cluster),
    # — среднее working_pct по сегментам ≥ 90 %,
    # — ни одного сильного сигнала жидкости (hysteresis/uplift/mono/parallax) не активно.
    s_full_stab = 0.0
    no_decline = not any(s.get("gradual_trend") == "down" for s in segments)
    no_dramatic_drop = not any((s.get("change_pct") or 0) <= -20 for s in segments)
    no_clusters = not any(s.get("is_shutdown_cluster") for s in segments)
    avg_working = sum(s.get("working_pct", 100) for s in segments) / max(1, len(segments))
    no_strong_signal = (s_hysteresis < 0.3 and s_uplift < 0.3 and s_mono < 0.3
                        and s_choke_paradox < 0.3 and s_choke_accel < 0.3)
    if no_decline and no_dramatic_drop and no_clusters and avg_working >= 90 and no_strong_signal:
        s_full_stab = 1.0

    penalty = (
        (1 - _segth("pav_pen_pf_rise")        * s_pf_rise)
        * (1 - _segth("pav_pen_dom_shutdown") * s_dom_shutdown)
        * (1 - _segth("pav_pen_recent_workover") * s_recent_workover)
        * (1 - _segth("pav_pen_full_stability")  * s_full_stab)
    )

    # Базовый балл
    raw_score = base * penalty * 100.0

    # БОНУС СОГЛАСОВАННОСТИ: чем больше РАЗНОРОДНЫХ сильных сигналов
    # жидкости активно одновременно, тем выше уверенность диагноза. Один
    # сильный сигнал ≠ два + три, потому что разные физические явления
    # подтверждают одно и то же.
    strong_signals = sum(1 for v in (s_hysteresis, s_mono, s_op_purges,
                                     s_uplift, s_choke_paradox, s_choke_accel,
                                     s_pw_decline) if v >= 0.5)
    if strong_signals >= 3:
        raw_score += 20
    elif strong_signals == 2:
        raw_score += 10

    score = max(0.0, min(100.0, raw_score))

    # === Сборка признаков для отображения ===
    pro = []
    if s_hysteresis >= 0.05 and hyster:
        pro.append((
            f"ΔP не восстановился после эпизода роста линии: было {hyster['dp_first']:.1f}, "
            f"стало {hyster['dp_last']:.1f} кгс/см² (−{hyster['dp_loss_pct']:.0f}%). "
            f"P шлейфа вернулась ({hyster['pf_first']:.1f}→{hyster['pf_last']:.1f}), "
            f"но P устья — нет ({hyster['pw_first']:.1f}→{hyster['pw_last']:.1f}, −{hyster['pw_loss']:.1f}). "
            f"Признак накопления жидкости в стволе",
            s_hysteresis))
    if s_uplift >= 0.05 and uplifts:
        for u in uplifts[:2]:  # показываем максимум 2 случая, остальные сводим
            pro.append((
                f"После простоя ({u['period_shutdown']}) Q рабочий вырос: "
                f"{u['q_before']:.1f} → {u['q_after']:.1f} тыс.м³/сут (+{u['uplift_pct']:.0f}%) — "
                f"вероятный вынос жидкости",
                s_uplift))
        if len(uplifts) > 2:
            pro.append((f"…и ещё {len(uplifts) - 2} аналогичных случаев", s_uplift))
    if s_mono >= 0.05 and mono:
        pro.append((
            f"Монотонный спад Q рабочего {mono['period_start']}–{mono['period_end']} "
            f"({mono['days']} дн., {mono['n_segments']} сегм.): "
            f"{mono['q_first']:.1f} → {mono['q_last']:.1f} тыс.м³/сут "
            f"(−{mono['drift_pct']:.0f}%) — медленное накопление жидкости",
            s_mono))
    if s_op_purges >= 0.05 and n_op > 0:
        pro.append((
            f"Операторские продувки: {n_op} дней с изолированным простоем "
            f"(~{ops_per_30:.1f}/30 дн., суммарно {op['total_purge_minutes']} мин). "
            f"В наших суточных сводках простой проставляется ВРУЧНУЮ оператором — "
            f"короткие разовые остановки = регулярные продувки от жидкости, "
            f"оператор уже фактически борется с накоплением",
            s_op_purges))
    if s_choke_paradox >= 0.05 and paradox_cases:
        for pc in paradox_cases[:2]:
            arrow = "↑" if pc["change_choke"] > 0 else "→"
            pro.append((
                f"Парадокс {pc['period']}: штуцер "
                f"{pc['choke_prev']:.0f}{arrow}{pc['choke_curr']:.0f} мм "
                f"(больше или стабильный), а Q ↓{abs(pc['change_q']):.0f} %. "
                f"Физически дебит должен был вырасти/остаться — обратное "
                f"указывает на внутренний фактор (накопление жидкости)",
                s_choke_paradox))
        if len(paradox_cases) > 2:
            pro.append((f"…и ещё {len(paradox_cases) - 2} аналогичных случаев",
                       s_choke_paradox))
    if s_choke_accel >= 0.05 and accel:
        for ac in accel[:2]:
            pro.append((
                f"Ускоренный спад при увеличенном штуцере "
                f"{ac['period_post']}: штуцер {ac['choke_pre']:.0f}→"
                f"{ac['choke_post']:.0f} мм, темп спада Q вырос с "
                f"{ac['rate_pre_pct_per_day']:+.1f} до "
                f"{ac['rate_post_pct_per_day']:+.1f} %/день "
                f"(+{ac['rate_delta_pct_per_day']:.1f}). "
                f"Больше газа → больше воды поднимается; скважина не "
                f"справляется с притоком жидкости",
                s_choke_accel))
        if len(accel) > 2:
            pro.append((f"…и ещё {len(accel) - 2} аналогичных случаев",
                       s_choke_accel))
    if s_gradual >= 0.05:
        pro.append((f"Плавный спад Q — макс. {max_decline:.0f}% за самый длинный сегмент", s_gradual))
    if s_pw_decline >= 0.05:
        pro.append((f"P устья падает при стабильной линии — суммарно −{sum_pw_drop:.1f} кгс/см² по {n_pw_stable_drops} переходам", s_pw_decline))
    if s_dp_compress >= 0.05 and dp_first is not None:
        pro.append((f"ΔP сжимается за период: {dp_first:.1f} → {dp_last:.1f} кгс/см² (учтена только часть, не объяснённая линией)", s_dp_compress))
    if s_cyclicity >= 0.05:
        pro.append((f"Циклы «накопление-продувка»: {n_cycles} за период", s_cyclicity))
    if s_purges >= 0.05:
        pro.append((f"Короткие простои (продувки): {short_clusters} за период (~{purges_per_30:.1f} / 30 дн.)", s_purges))
    if s_divergence >= 0.05:
        pro.append((f"Расхождение Q общий vs Q рабочий — в среднем {rel_div_pct*100:.0f}%", s_divergence))
    # Сортируем по силе убывания
    pro.sort(key=lambda x: -x[1])

    con = []
    if s_pf_rise >= 0.05:
        con.append((f"Эпизод роста P шлейфа (внешний фактор): макс. отклонение +{max_pf_excursion:.1f} кгс/см² от исходного уровня", s_pf_rise))
    if s_dom_shutdown >= 0.05:
        con.append((f"Подавляющий простой: {shutdown_share*100:.0f}% времени в кластерах — это не ПАВ-задача", s_dom_shutdown))
    if s_recent_workover >= 0.05:
        con.append((f"Свежий ремонт/КРС в последние {recent_thresh} дней — подождать стабилизации", s_recent_workover))
    if s_full_stab >= 0.05:
        con.append((f"Скважина работает полностью стабильно — вмешательство не требуется", s_full_stab))
    if n_choke_explained > 0:
        # Это не «штраф» в строгом смысле — это контекст: часть изменений
        # Q объясняется штуцером, а не накоплением жидкости.
        con.append((
            f"{n_choke_explained} переходов(а) Q объясняются сменой штуцера "
            f"(оператор сам менял режим) — учтено при оценке",
            min(1.0, n_choke_explained / 3.0)))
    con.sort(key=lambda x: -x[1])

    # === SCENARIO / RECOMMENDATION ===
    # Тон сценариев изменён: алгоритм НЕ выносит окончательный вердикт,
    # а маркирует «вероятный кандидат / маловероятный / разобрать вручную».
    # Финальное решение остаётся за инженером по обоснованию.
    liquid_signal = max(s_hysteresis, s_uplift, s_mono, s_op_purges,
                        s_choke_paradox, s_choke_accel,
                        (s_gradual + s_pw_decline + s_cyclicity + s_purges) / 4.0)
    if s_dom_shutdown >= 0.5:
        scenario = "ПРЕИМУЩЕСТВЕННЫЙ ПРОСТОЙ"
        recommendation = (
            "ПАВ маловероятен — большую часть времени скважина в простое. "
            "Оценить причину остановок, возможно требуется ремонт"
        )
    elif s_recent_workover >= 0.5:
        scenario = "СВЕЖИЙ РЕМОНТ / КРС"
        recommendation = (
            f"Подождать стабилизации режима (последний ремонт в окне {recent_thresh} дн.). "
            "Оценка ПАВ-кандидатности преждевременна"
        )
    elif score >= 35 and (
            s_hysteresis >= 0.5 or s_uplift >= 0.5 or s_mono >= 0.5
            or s_op_purges >= 0.5 or s_choke_paradox >= 0.5
            or s_choke_accel >= 0.5):
        scenario = "ВЕРОЯТНЫЙ КАНДИДАТ ПОД ПАВ (накопление жидкости)"
        recommendation = (
            "Признаки накопления жидкости обнаружены (см. список ниже). "
            "Рекомендуется рассмотреть закачку ПАВ. Финальное решение — за инженером"
        )
    elif s_full_stab >= 0.5:
        scenario = "СТАБИЛЬНАЯ РАБОТА"
        recommendation = "Вмешательство не требуется"
    elif s_pf_rise >= 0.5 and liquid_signal < 0.20 and any(
            (s.get("change_pct") or 0) <= -10 for s in segments):
        # ВНЕШНИЙ ФАКТОР засчитываем только если Q ДЕЙСТВИТЕЛЬНО падал.
        # Без падения Q колебания линии — не диагноз, а просто факт.
        scenario = "ВНЕШНИЙ ФАКТОР — давление в линии"
        recommendation = (
            "Падение Q объясняется ростом противодавления в линии (ДКС / ГСП), "
            "признаков жидкости в стволе не обнаружено — ПАВ маловероятен"
        )
    elif liquid_signal >= 0.25:
        scenario = "ВОЗМОЖНЫЕ ПРИЗНАКИ НАКОПЛЕНИЯ ЖИДКОСТИ"
        recommendation = (
            "Сигналы слабые, но согласованные — стоит включить в шорт-лист "
            "и наблюдать или проверить продувкой"
        )
    elif score >= 25:
        scenario = "СМЕШАННЫЙ РЕЖИМ"
        recommendation = (
            "Признаки противоречивы; нужен ручной разбор по таблице ниже"
        )
    else:
        scenario = "НЕТ ВЫРАЖЕННЫХ ПРИЗНАКОВ ПАВ-КАНДИДАТА"
        recommendation = (
            "Явных «жидкостных» признаков не обнаружено за выбранный период. "
            "Возможно, расширить период анализа или рассмотреть другие сценарии"
        )

    # === CONFIDENCE ===
    n_strong_pro = sum(1 for _, v in pro if v >= 0.30)
    n_strong_con = sum(1 for _, v in con if v >= 0.30)
    if total_days >= 30 and n_strong_pro >= 3 and n_strong_con <= 1:
        confidence = "высокая"
    elif total_days >= 15 and n_strong_pro >= 1:
        confidence = "средняя"
    else:
        confidence = "низкая"

    return {
        "score": score,
        "confidence": confidence,
        "scenario": scenario,
        "recommendation": recommendation,
        "signs_pro": pro,
        "signs_con": con,
        "breakdown": {
            "base": base, "penalty": penalty,
            "s_hysteresis": s_hysteresis, "s_mono": s_mono,
            "s_op_purges": s_op_purges, "s_uplift": s_uplift,
            "s_choke_paradox": s_choke_paradox, "s_choke_accel": s_choke_accel,
            "s_gradual": s_gradual, "s_pw_decline": s_pw_decline,
            "s_dp_compress": s_dp_compress, "s_cyclicity": s_cyclicity,
            "s_purges": s_purges, "s_divergence": s_divergence,
            "s_pf_rise": s_pf_rise, "s_dom_shutdown": s_dom_shutdown,
            "s_recent_workover": s_recent_workover, "s_full_stab": s_full_stab,
            "n_cycles": n_cycles, "shutdown_share": shutdown_share,
            "total_days": total_days,
        },
    }


# ─────────────────── Роза причин нестабильности (LEGACY) ─────────────
# Оставлена в файле для совместимости имён, но НЕ ИСПОЛЬЗУЕТСЯ в новом
# отчёте — заменена на _v2_pav_card_page (ПАВ-карточка).
# Дополняет «Розу стабильности» (которая отвечает «насколько нестабильна
# скважина»). Эта роза отвечает «почему» — какие факторы вызвали проблемы
# в данном периоде. Источник — результат `_segment_analysis(df)`.

CAUSES_KEYS = (
    "shutdown", "workover", "pf_events",
    "pw_events", "drops_dramatic", "gradual_decline",
)
CAUSES_LABELS = {
    "shutdown":        "Простои",
    "workover":        "Ремонт / КРС",
    "pf_events":       "P шлейфа — события",
    "pw_events":       "P устья — события",
    "drops_dramatic":  "Резкие падения Q",
    "gradual_decline": "Плавный спад Q",
}
CAUSES_LABELS_SHORT = {
    "shutdown":        "Простои %",
    "workover":        "Ремонт",
    "pf_events":       "P шл соб.",
    "pw_events":       "P уст соб.",
    "drops_dramatic":  "Падения Q",
    "gradual_decline": "Спад Q",
}
# Нормировочные «точки 100»: при таком значении сырой метрики ось упирается
# в 100. Меньше — пропорционально.
CAUSES_NORMS = {
    "shutdown":         100.0,  # % времени в простоях
    "workover":           1.0,  # ремонтов на 30 дней
    "pf_events":          4.0,  # событий P-шлейфа на 30 дней
    "pw_events":          4.0,  # событий P-устья на 30 дней
    "drops_dramatic":     2.0,  # резких падений Q на 30 дней
    "gradual_decline":   30.0,  # |drift_pct| (% за период)
}


