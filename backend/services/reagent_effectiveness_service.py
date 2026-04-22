"""
Анализ эффективности реагента.

Строит интервалы реагентного воздействия (ИРВ) между вбросами,
рассчитывает метрики M1-M6, Score и шкалу эффективности.

Использует существующие модули:
- flow_rate.calculator — расчёт Q из давления
- flow_rate.data_access — чтение pressure_raw, choke_mm
- pressure_mask_service — маски давления
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import text

from backend.db import engine as pg_engine
from backend.services.flow_rate.calculator import (
    calculate_flow_rate,
    calculate_cumulative,
)
from backend.services.flow_rate.config import FlowRateConfig, DEFAULT_FLOW
from backend.services.flow_rate.data_access import get_pressure_data, get_choke_mm
from backend.services.pressure_mask_service import load_active_masks, apply_masks

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Конфигурация анализа (значения по умолчанию, переопределяются из API)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReagentAnalysisConfig:
    """Параметры анализа эффективности реагента."""
    grace_period_min: float = 30.0        # минуты после вброса — игнорировать
    pre_window_hours: float = 2.0         # часы до вброса — baseline ΔP
    merge_window_hours: float = 4.0       # группировка вбросов ближе этого интервала
    max_window_days: float = 7.0          # макс. длина ИРВ
    dp_effect_threshold: float = 0.3      # порог прироста ΔP для M4 (кгс/см²)
    min_utilisation_pct: float = 50.0     # мин. M5 для включения в Score
    smoothing_window_min: int = 30        # окно скользящего среднего для M4 (мин)


DEFAULT_CONFIG = ReagentAnalysisConfig()

# ---------------------------------------------------------------------------
# Типы результатов
# ---------------------------------------------------------------------------

@dataclass
class ReagentInjection:
    """Одно событие вброса (или группа объединённых)."""
    event_ids: list[int]
    event_time: datetime          # время первого вброса
    reagent: str
    qty: Optional[float]          # суммарное количество (None если не указано)
    p_tube: Optional[float]       # давление в момент первого вброса
    p_line: Optional[float]
    merged_count: int = 1         # сколько вбросов объединено


@dataclass
class DPPhases:
    """Фазовый анализ кривой ΔP внутри ИРВ."""
    # Фаза роста
    peak_dp: Optional[float] = None           # пик ΔP (кгс/см²)
    time_to_peak_min: Optional[float] = None  # минуты от t_start до пика
    rise_rate: Optional[float] = None         # скорость роста (кгс/см²/ч)
    # Фаза затухания
    decay_slope: Optional[float] = None       # наклон линейного тренда (кгс/см²/ч), отрицательный
    decay_start_dp: Optional[float] = None    # ΔP в начале затухания
    decay_end_dp: Optional[float] = None      # ΔP в конце затухания
    # Момент возврата к baseline
    time_to_baseline_hours: Optional[float] = None  # часы до возврата к baseline (None = не вернулся)


@dataclass
class IRVMetrics:
    """Метрики интервала реагентного воздействия."""
    # M1 — Накопленный дебит за ИРВ (тыс. м³)
    q_cumulative: Optional[float] = None
    # M2 — Накопленный дебит на единицу реагента (тыс. м³/шт)
    q_per_unit: Optional[float] = None
    # M3 — Прирост ΔP относительно baseline (кгс/см²)
    dp_gain: Optional[float] = None
    # M4 — Продолжительность эффекта (часы)
    effect_duration_hours: Optional[float] = None
    # M5 — Коэффициент использования времени (%)
    utilisation_pct: Optional[float] = None
    # Фазовый анализ ΔP
    phases: Optional[DPPhases] = None
    # Вспомогательные
    baseline_dp: Optional[float] = None
    avg_dp: Optional[float] = None
    avg_flow_rate: Optional[float] = None
    data_points: int = 0
    invalid_reason: Optional[str] = None


@dataclass
class IRVResult:
    """Полный результат по одному ИРВ."""
    injection: ReagentInjection
    t_start: datetime             # начало ИРВ (после grace)
    t_end: datetime               # конец ИРВ
    duration_hours: float
    choke_mm: Optional[float]
    metrics: IRVMetrics
    # Для графиков — сырые данные (не сериализуются в JSON)
    pressure_df: Optional[pd.DataFrame] = field(default=None, repr=False)


@dataclass
class ReagentScore:
    """Агрегированный Score по паре (скважина, реагент, штуцер)."""
    reagent: str
    choke_mm: Optional[float]
    irv_count: int                # всего ИРВ
    valid_irv_count: int          # ИРВ с M5 >= порога
    score: float                  # 0..1
    level: int                    # 1..5
    level_name: str
    median_q_per_unit: Optional[float]
    median_dp_gain: Optional[float]
    median_effect_hours: Optional[float]
    degradation_slope: Optional[float]  # M6
    flags: list[str]


# ---------------------------------------------------------------------------
# Получение вбросов реагента из events
# ---------------------------------------------------------------------------

def _get_reagent_injections(
    well_id: int,
    period_start: datetime,
    period_end: datetime,
) -> list[dict]:
    """
    Вбросы реагента из events для скважины за период.
    """
    query = text("""
        SELECT e.id, e.event_time, e.reagent, e.qty, e.p_tube, e.p_line
        FROM events e
        JOIN wells w ON e.well = w.number::text
        WHERE w.id = :well_id
          AND e.event_type = 'reagent'
          AND e.event_time BETWEEN :start AND :end
          AND e.reagent IS NOT NULL
        ORDER BY e.event_time
    """)
    with pg_engine.connect() as conn:
        rows = conn.execute(
            query,
            {"well_id": well_id, "start": period_start, "end": period_end},
        ).fetchall()
    return [
        {
            "id": r[0],
            "event_time": r[1],
            "reagent": r[2],
            "qty": float(r[3]) if r[3] is not None else None,
            "p_tube": r[4],
            "p_line": r[5],
        }
        for r in rows
    ]


def _get_purge_times(
    well_id: int,
    period_start: datetime,
    period_end: datetime,
) -> list[datetime]:
    """
    Времена начала продувок (purge_phase='start' или первая запись).
    """
    query = text("""
        SELECT e.event_time
        FROM events e
        JOIN wells w ON e.well = w.number::text
        WHERE w.id = :well_id
          AND e.event_type = 'purge'
          AND e.event_time BETWEEN :start AND :end
        ORDER BY e.event_time
    """)
    with pg_engine.connect() as conn:
        rows = conn.execute(
            query,
            {"well_id": well_id, "start": period_start, "end": period_end},
        ).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Группировка близких вбросов
# ---------------------------------------------------------------------------

def _merge_injections(
    injections: list[dict],
    merge_window: timedelta,
) -> list[ReagentInjection]:
    """
    Объединяет вбросы с интервалом < merge_window в один.

    Правила:
    - qty суммируется
    - reagent: если одинаковый — он; если разные — 'комбинированный'
    - event_time = время первого вброса в группе
    - p_tube/p_line = из первого вброса
    """
    if not injections:
        return []

    groups: list[list[dict]] = []
    current_group: list[dict] = [injections[0]]

    for inj in injections[1:]:
        prev = current_group[-1]
        gap = inj["event_time"] - prev["event_time"]
        if gap < merge_window:
            current_group.append(inj)
        else:
            groups.append(current_group)
            current_group = [inj]
    groups.append(current_group)

    result = []
    for group in groups:
        first = group[0]
        reagents = set(g["reagent"] for g in group)
        reagent_name = first["reagent"] if len(reagents) == 1 else "комбинированный"

        qtys = [g["qty"] for g in group if g["qty"] is not None]
        total_qty = sum(qtys) if qtys else None

        result.append(ReagentInjection(
            event_ids=[g["id"] for g in group],
            event_time=first["event_time"],
            reagent=reagent_name,
            qty=total_qty,
            p_tube=first["p_tube"],
            p_line=first["p_line"],
            merged_count=len(group),
        ))
    return result


# ---------------------------------------------------------------------------
# Построение ИРВ
# ---------------------------------------------------------------------------

def _build_irv_boundaries(
    injections: list[ReagentInjection],
    purge_times: list[datetime],
    cfg: ReagentAnalysisConfig,
    period_end: datetime,
) -> list[tuple[ReagentInjection, datetime, datetime]]:
    """
    Для каждого вброса определяет (injection, t_start, t_end).

    t_end = min(следующий вброс, ближайшая продувка после t_start, period_end, t_start + max_window)
    """
    grace = timedelta(minutes=cfg.grace_period_min)
    max_dur = timedelta(days=cfg.max_window_days)
    boundaries = []

    for i, inj in enumerate(injections):
        t_start = inj.event_time + grace

        # Следующий вброс
        t_next_inj = injections[i + 1].event_time if i + 1 < len(injections) else period_end

        # Ближайшая продувка ПОСЛЕ t_start
        t_next_purge = period_end
        for pt in purge_times:
            if pt > inj.event_time + timedelta(minutes=5):  # не тот же момент
                t_next_purge = pt
                break

        t_end = min(t_next_inj, t_next_purge, t_start + max_dur, period_end)

        if t_end <= t_start:
            continue

        boundaries.append((inj, t_start, t_end))

    return boundaries


# ---------------------------------------------------------------------------
# Фазовый анализ кривой ΔP
# ---------------------------------------------------------------------------

def _analyze_dp_phases(
    df_irv: pd.DataFrame,
    baseline_dp: Optional[float],
    cfg: ReagentAnalysisConfig,
) -> DPPhases:
    """
    Анализирует кривую ΔP внутри ИРВ: рост → пик → затухание → возврат к baseline.
    """
    phases = DPPhases()

    dp_series = df_irv[["p_tube", "p_line"]].dropna()
    if dp_series.empty or len(dp_series) < 5:
        return phases

    dp_raw = dp_series["p_tube"] - dp_series["p_line"]

    # Сглаженная кривая ΔP (скользящее среднее)
    window = min(cfg.smoothing_window_min, len(dp_raw))
    dp_smooth = dp_raw.rolling(window=window, min_periods=1, center=True).mean()

    t0 = dp_smooth.index[0]

    # --- Пик ΔP ---
    peak_idx = dp_smooth.idxmax()
    phases.peak_dp = round(float(dp_smooth[peak_idx]), 3)
    phases.time_to_peak_min = round((peak_idx - t0).total_seconds() / 60.0, 1)

    # --- Скорость роста (от начала до пика) ---
    if phases.time_to_peak_min and phases.time_to_peak_min > 1:
        dp_start = float(dp_smooth.iloc[0])
        rise = phases.peak_dp - dp_start
        hours_to_peak = phases.time_to_peak_min / 60.0
        phases.rise_rate = round(rise / hours_to_peak, 3) if hours_to_peak > 0 else None

    # --- Фаза затухания (от пика до конца) ---
    dp_after_peak = dp_smooth.loc[peak_idx:]
    if len(dp_after_peak) >= 5:
        # Линейный тренд затухания
        t_sec = np.array([(t - peak_idx).total_seconds() for t in dp_after_peak.index], dtype=float)
        t_hours = t_sec / 3600.0
        y = dp_after_peak.values.astype(float)

        if t_hours[-1] > 0:
            coeffs = np.polyfit(t_hours, y, 1)
            phases.decay_slope = round(float(coeffs[0]), 4)  # кгс/см²/ч

        phases.decay_start_dp = round(float(dp_after_peak.iloc[0]), 3)
        phases.decay_end_dp = round(float(dp_after_peak.iloc[-1]), 3)

    # --- Время возврата к baseline ---
    if baseline_dp is not None:
        below_baseline = dp_smooth.loc[peak_idx:] <= baseline_dp + cfg.dp_effect_threshold * 0.5
        if below_baseline.any():
            first_below = below_baseline[below_baseline].index[0]
            phases.time_to_baseline_hours = round(
                (first_below - t0).total_seconds() / 3600.0, 2
            )

    return phases


# ---------------------------------------------------------------------------
# Сегментация ΔP кривой: рост / плато / спад
# ---------------------------------------------------------------------------

def _detect_segments(
    df_irv: pd.DataFrame,
    event_time: datetime,
    min_segment_min: int = 30,
    slope_threshold: float = 0.1,
) -> list[dict]:
    """
    Определяет участки кривой ΔP: рост, плато, спад.

    Алгоритм:
    1. Агрессивное сглаживание (медианный фильтр 60 мин + среднее 20 мин)
       — убивает краткосрочные спайки
    2. Скользящий наклон в окне 30 мин (линейная регрессия)
    3. Классификация каждой точки: rise / plateau / decay по порогу
    4. Группировка последовательных одинаковых классов в сегменты
    5. Сегменты короче min_segment_min объединяются с соседями

    Параметры:
        slope_threshold — кгс/см²/ч, граница между плато и ростом/спадом

    Возвращает список сегментов:
        {
            "type": "rise" | "plateau" | "decay",
            "start_hours": float,    # часы от вброса
            "end_hours": float,
            "duration_hours": float,
            "slope": float,           # кгс/см²/ч (средний наклон)
            "start_dp": float,
            "end_dp": float,
        }
    """
    dp_raw = df_irv[["p_tube", "p_line"]].dropna()
    if len(dp_raw) < 30:
        return []

    dp = dp_raw["p_tube"] - dp_raw["p_line"]

    # Ресэмпл на 1 мин
    try:
        dp_1m = dp.resample("1min").mean().interpolate(limit=10)
    except Exception:
        dp_1m = dp
    if len(dp_1m) < 30:
        return []

    # Агрессивное сглаживание: медианный фильтр 60 мин (убивает спайки),
    # затем среднее 20 мин (сглаживает)
    dp_smooth = dp_1m.rolling(window=60, min_periods=10, center=True).median()
    dp_smooth = dp_smooth.rolling(window=20, min_periods=5, center=True).mean()
    dp_smooth = dp_smooth.dropna()
    if len(dp_smooth) < 30:
        return []

    # Скользящий наклон: окно 30 мин
    win = 30
    slopes = np.zeros(len(dp_smooth))
    y_arr = dp_smooth.values
    for i in range(len(dp_smooth)):
        s = max(0, i - win // 2)
        e = min(len(dp_smooth), i + win // 2 + 1)
        if e - s < 5:
            slopes[i] = 0.0
            continue
        seg = y_arr[s:e]
        x_h = np.arange(len(seg)) / 60.0  # часы
        slopes[i] = np.polyfit(x_h, seg, 1)[0]

    # Классификация
    classes = np.where(
        slopes > slope_threshold, 1,
        np.where(slopes < -slope_threshold, -1, 0)
    )

    # Группировка в сегменты
    raw_segs = []
    cur_cls = classes[0]
    s_idx = 0
    for i in range(1, len(classes)):
        if classes[i] != cur_cls:
            raw_segs.append((s_idx, i, cur_cls))
            cur_cls = classes[i]
            s_idx = i
    raw_segs.append((s_idx, len(classes), cur_cls))

    # Фильтр коротких сегментов: сливаем с соседним более длинным
    filtered: list[tuple[int, int, int]] = []
    for seg in raw_segs:
        s, e, c = seg
        duration_min = e - s  # т.к. 1 мин = 1 точка
        if duration_min < min_segment_min and filtered:
            # Слить с предыдущим (расширить его конец)
            ps, pe, pc = filtered[-1]
            filtered[-1] = (ps, e, pc)
        elif duration_min < min_segment_min and len(raw_segs) > 1:
            # Пока пропускаем — может объединиться со следующим
            pass
        else:
            filtered.append((s, e, c))

    if not filtered:
        return []

    # Ещё раз: если после слияния остался очень короткий в начале — объединить
    merged: list[tuple[int, int, int]] = []
    for s, e, c in filtered:
        if merged and (e - s) < min_segment_min:
            ps, pe, pc = merged[-1]
            merged[-1] = (ps, e, pc)
        else:
            merged.append((s, e, c))

    # Формируем результат
    times = dp_smooth.index
    type_map = {1: "rise", -1: "decay", 0: "plateau"}
    result = []
    for s, e, c in merged:
        if e <= s:
            continue
        t_start = times[s]
        t_end = times[min(e - 1, len(times) - 1)]
        dur_h = (t_end - t_start).total_seconds() / 3600.0
        if dur_h < 0.1:
            continue

        seg_y = y_arr[s:e]
        if len(seg_y) >= 2:
            x_h = np.arange(len(seg_y)) / 60.0
            slope = float(np.polyfit(x_h, seg_y, 1)[0])
        else:
            slope = 0.0

        result.append({
            "type": type_map[int(c)],
            "start_hours": round((t_start - event_time).total_seconds() / 3600.0, 3),
            "end_hours": round((t_end - event_time).total_seconds() / 3600.0, 3),
            "duration_hours": round(dur_h, 2),
            "slope": round(slope, 4),
            "start_dp": round(float(seg_y[0]), 3),
            "end_dp": round(float(seg_y[-1]), 3),
        })
    return result


# ---------------------------------------------------------------------------
# Подготовка ΔP кривой для графика (прореженная)
# ---------------------------------------------------------------------------

def _build_dp_curve(
    df_irv: pd.DataFrame,
    event_time: datetime,
    smoothing_window: int = 15,
    max_points: int = 500,
) -> Optional[dict]:
    """
    Строит кривую ΔP для графика.
    Возвращает {hours_from_injection: [...], dp: [...], dp_smooth: [...]}.
    Ось X — часы от момента вброса (может быть отрицательной для pre-window).
    """
    dp_series = df_irv[["p_tube", "p_line"]].dropna()
    if dp_series.empty:
        return None

    dp_raw = dp_series["p_tube"] - dp_series["p_line"]
    dp_smooth = dp_raw.rolling(window=smoothing_window, min_periods=1, center=True).mean()

    # Прореживание если слишком много точек
    step = max(1, len(dp_raw) // max_points)
    dp_raw_s = dp_raw.iloc[::step]
    dp_smooth_s = dp_smooth.iloc[::step]

    hours = [(t - event_time).total_seconds() / 3600.0 for t in dp_raw_s.index]

    return {
        "hours_from_injection": [round(h, 3) for h in hours],
        "dp": [_safe_float(v) for v in dp_raw_s.values],
        "dp_smooth": [_safe_float(v) for v in dp_smooth_s.values],
    }


# ---------------------------------------------------------------------------
# Расчёт метрик для одного ИРВ
# ---------------------------------------------------------------------------

def _compute_irv_metrics(
    well_id: int,
    inj: ReagentInjection,
    t_start: datetime,
    t_end: datetime,
    choke_mm: Optional[float],
    flow_cfg: FlowRateConfig,
    cfg: ReagentAnalysisConfig,
) -> tuple[IRVMetrics, Optional[pd.DataFrame]]:
    """
    Рассчитывает M1-M5 для одного ИРВ.
    Возвращает (metrics, pressure_df для графиков).
    """
    metrics = IRVMetrics()

    duration_hours = (t_end - t_start).total_seconds() / 3600.0

    # --- Получаем данные давления: pre-window + ИРВ ---
    pre_start = inj.event_time - timedelta(hours=cfg.pre_window_hours)
    df_full = get_pressure_data(
        well_id,
        start=pre_start.isoformat(),
        end=t_end.isoformat(),
    )
    if df_full.empty:
        metrics.invalid_reason = "нет данных давления"
        return metrics, None

    # --- Применяем маски давления ---
    masks = load_active_masks(well_id, pre_start, t_end, verified_only=True)
    if masks:
        df_full, _ = apply_masks(df_full, masks)

    # --- Baseline ΔP (pre-window) ---
    pre_end = inj.event_time
    df_pre = df_full.loc[:pre_end]
    df_pre_valid = df_pre.dropna(subset=["p_tube", "p_line"])
    df_pre_valid = df_pre_valid[
        (df_pre_valid["p_tube"] > df_pre_valid["p_line"])
        & ((df_pre_valid["p_tube"] - df_pre_valid["p_line"]) > 0.1)
    ]
    if not df_pre_valid.empty:
        baseline_dp = float(np.median(df_pre_valid["p_tube"] - df_pre_valid["p_line"]))
    else:
        baseline_dp = None
    metrics.baseline_dp = baseline_dp

    # --- Данные ИРВ (после grace) ---
    df_irv = df_full.loc[t_start:t_end]
    if df_irv.empty:
        metrics.invalid_reason = "нет данных давления в интервале ИРВ"
        return metrics, df_full

    # Фильтрация: p_tube > p_line AND ΔP > 0.1 AND оба NOT NULL
    df_valid = df_irv.dropna(subset=["p_tube", "p_line"])
    df_valid = df_valid[
        (df_valid["p_tube"] > df_valid["p_line"])
        & ((df_valid["p_tube"] - df_valid["p_line"]) > 0.1)
    ]

    metrics.data_points = len(df_valid)
    if df_valid.empty:
        metrics.invalid_reason = "нет валидных точек (после фильтрации ΔP)"
        return metrics, df_full

    # --- M5: Коэффициент использования ---
    total_minutes = duration_hours * 60.0
    # «Рабочие минуты» = количество валидных точек (≈1 точка/мин)
    working_minutes = len(df_valid)
    metrics.utilisation_pct = min(100.0, (working_minutes / max(total_minutes, 1)) * 100.0)

    # --- M3: Прирост ΔP ---
    dp_values = (df_valid["p_tube"] - df_valid["p_line"]).values
    avg_dp = float(np.median(dp_values))
    metrics.avg_dp = avg_dp
    if baseline_dp is not None:
        metrics.dp_gain = round(avg_dp - baseline_dp, 3)

    # --- M4: Продолжительность эффекта ---
    # Ищем максимальный непрерывный блок, где ΔP >= baseline + threshold.
    # Реагент может подействовать не сразу → ищем первое превышение,
    # потом считаем до момента падения ниже порога.
    if baseline_dp is not None:
        threshold = baseline_dp + cfg.dp_effect_threshold
        dp_series = df_irv[["p_tube", "p_line"]].dropna()
        if not dp_series.empty:
            dp_rolling = (
                (dp_series["p_tube"] - dp_series["p_line"])
                .rolling(window=cfg.smoothing_window_min, min_periods=1)
                .mean()
            )
            above = dp_rolling >= threshold

            if above.any():
                # Находим первый момент превышения порога
                first_above_idx = above[above].index[0]
                # От этого момента ищем первое падение ниже порога
                after_rise = above.loc[first_above_idx:]
                below_after = after_rise[~after_rise]
                if len(below_after) > 0:
                    end_effect = below_after.index[0]
                else:
                    end_effect = dp_rolling.index[-1]
                effect_minutes = (end_effect - first_above_idx).total_seconds() / 60.0
                metrics.effect_duration_hours = round(effect_minutes / 60.0, 2)
            else:
                metrics.effect_duration_hours = 0.0

    # --- Фазовый анализ ΔP ---
    metrics.phases = _analyze_dp_phases(df_irv, baseline_dp, cfg)

    # --- M1: Накопленный дебит ---
    if choke_mm is not None and choke_mm > 0:
        df_calc = calculate_flow_rate(df_valid.copy(), choke_mm, flow_cfg)
        df_calc = calculate_cumulative(df_calc)
        q_cum = float(df_calc["cumulative_flow"].iloc[-1])
        metrics.q_cumulative = round(q_cum, 4)
        metrics.avg_flow_rate = round(float(df_calc["flow_rate"].mean()), 4)

        # --- M2: Q_cum / qty ---
        if inj.qty is not None and inj.qty > 0:
            metrics.q_per_unit = round(q_cum / inj.qty, 4)

    return metrics, df_full


# ---------------------------------------------------------------------------
# M6: Тренд деградации
# ---------------------------------------------------------------------------

def _compute_degradation(
    irv_results: list[IRVResult],
    min_count: int = 5,
) -> Optional[float]:
    """
    Линейный тренд M2 по порядковому номеру вброса.
    Возвращает наклон (отрицательный = деградация) или None если мало данных.
    """
    values = [
        r.metrics.q_per_unit
        for r in irv_results
        if r.metrics.q_per_unit is not None
        and r.metrics.utilisation_pct is not None
        and r.metrics.utilisation_pct >= 50.0
    ]
    if len(values) < min_count:
        return None

    x = np.arange(len(values), dtype=float)
    y = np.array(values, dtype=float)
    # Линейная регрессия: y = a*x + b
    coeffs = np.polyfit(x, y, 1)
    slope = float(coeffs[0])
    return round(slope, 6)


# ---------------------------------------------------------------------------
# Score и шкала
# ---------------------------------------------------------------------------

LEVEL_NAMES = {
    5: "Высокая",
    4: "Хорошая",
    3: "Средняя",
    2: "Слабая",
    1: "Не работает",
}


def _score_to_level(score: float) -> int:
    if score >= 0.80:
        return 5
    if score >= 0.60:
        return 4
    if score >= 0.40:
        return 3
    if score >= 0.20:
        return 2
    return 1


def _normalize_values(values: list[float]) -> list[float]:
    """Нормализация в [0, 1]."""
    if not values:
        return []
    vmin, vmax = min(values), max(values)
    rng = vmax - vmin
    if rng < 1e-9:
        return [0.5] * len(values)
    return [(v - vmin) / rng for v in values]


def _compute_scores(
    irv_by_reagent: dict[str, list[IRVResult]],
    cfg: ReagentAnalysisConfig,
) -> list[ReagentScore]:
    """
    Рассчитывает Score для каждого реагента на основе его ИРВ.
    """
    # Собираем медианные значения по каждому реагенту
    reagent_medians: dict[str, dict] = {}
    for reagent, irvs in irv_by_reagent.items():
        valid = [
            r for r in irvs
            if r.metrics.utilisation_pct is not None
            and r.metrics.utilisation_pct >= cfg.min_utilisation_pct
        ]
        m2_vals = [r.metrics.q_per_unit for r in valid if r.metrics.q_per_unit is not None]
        m3_vals = [r.metrics.dp_gain for r in valid if r.metrics.dp_gain is not None]
        m4_vals = [r.metrics.effect_duration_hours for r in valid if r.metrics.effect_duration_hours is not None]

        reagent_medians[reagent] = {
            "valid_count": len(valid),
            "total_count": len(irvs),
            "m2_median": float(np.median(m2_vals)) if m2_vals else None,
            "m3_median": float(np.median(m3_vals)) if m3_vals else None,
            "m4_median": float(np.median(m4_vals)) if m4_vals else None,
            "m2_vals": m2_vals,
        }

    # Нормализация M3 и M4 по всем реагентам
    all_m3 = [v["m3_median"] for v in reagent_medians.values() if v["m3_median"] is not None]
    all_m4 = [v["m4_median"] for v in reagent_medians.values() if v["m4_median"] is not None]
    all_m2 = [v["m2_median"] for v in reagent_medians.values() if v["m2_median"] is not None]

    norm_m3 = dict(zip(
        [r for r, v in reagent_medians.items() if v["m3_median"] is not None],
        _normalize_values(all_m3),
    ))
    norm_m4 = dict(zip(
        [r for r, v in reagent_medians.items() if v["m4_median"] is not None],
        _normalize_values(all_m4),
    ))
    norm_m2 = dict(zip(
        [r for r, v in reagent_medians.items() if v["m2_median"] is not None],
        _normalize_values(all_m2),
    ))

    results = []
    for reagent, irvs in irv_by_reagent.items():
        med = reagent_medians[reagent]
        flags: list[str] = []

        # Score
        s_m2 = norm_m2.get(reagent, 0.0) * 0.5
        s_m3 = norm_m3.get(reagent, 0.0) * 0.3
        s_m4 = norm_m4.get(reagent, 0.0) * 0.2
        score = round(s_m2 + s_m3 + s_m4, 4)

        # Флаги
        if med["valid_count"] < 5:
            flags.append("мало данных")
        if med["m2_vals"] and len(med["m2_vals"]) >= 3:
            cv = float(np.std(med["m2_vals"]) / max(np.mean(med["m2_vals"]), 1e-9))
            if cv > 0.5:
                flags.append("нестабильный")

        degradation = _compute_degradation(irvs)
        if degradation is not None and degradation < 0:
            m2_med = med["m2_median"] or 1.0
            if abs(degradation) > 0.1 * abs(m2_med):
                flags.append("деградация")

        # Определяем choke_mm (одинаковый для всех ИРВ в группе)
        choke = irvs[0].choke_mm if irvs else None

        level = _score_to_level(score)
        results.append(ReagentScore(
            reagent=reagent,
            choke_mm=choke,
            irv_count=med["total_count"],
            valid_irv_count=med["valid_count"],
            score=score,
            level=level,
            level_name=LEVEL_NAMES[level],
            median_q_per_unit=med["m2_median"],
            median_dp_gain=med["m3_median"],
            median_effect_hours=med["m4_median"],
            degradation_slope=degradation,
            flags=flags,
        ))

    # Сортируем по Score убыванию
    results.sort(key=lambda s: s.score, reverse=True)
    return results


# ---------------------------------------------------------------------------
# Основная функция: полный анализ
# ---------------------------------------------------------------------------

def analyze_reagent_effectiveness(
    well_id: int,
    period_start: datetime,
    period_end: datetime,
    cfg: ReagentAnalysisConfig = DEFAULT_CONFIG,
    include_pressure_data: bool = False,
) -> dict:
    """
    Полный анализ эффективности реагентов для скважины.

    Возвращает dict:
    {
        "well_id": int,
        "period": {"start": str, "end": str},
        "config": {...},
        "choke_mm": float | None,
        "injections_total": int,
        "merged_injections": int,
        "irv_results": [...],   # список ИРВ с метриками
        "scores": [...],        # Score по реагентам
        "best_reagent": {...},  # лучший реагент
        "warnings": [...],
    }
    """
    warnings: list[str] = []

    # --- 1. Получаем штуцер ---
    choke_mm = get_choke_mm(well_id)
    if choke_mm is None:
        warnings.append("Штуцер не найден — M1/M2 (дебит) не будут рассчитаны")

    # --- 2. Получаем вбросы реагента ---
    raw_injections = _get_reagent_injections(well_id, period_start, period_end)
    if not raw_injections:
        return {
            "well_id": well_id,
            "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
            "config": _cfg_to_dict(cfg),
            "choke_mm": choke_mm,
            "injections_total": 0,
            "merged_injections": 0,
            "irv_results": [],
            "scores": [],
            "best_reagent": None,
            "warnings": ["Нет вбросов реагента за выбранный период"],
        }

    # --- 3. Группируем близкие вбросы ---
    merge_window = timedelta(hours=cfg.merge_window_hours)
    injections = _merge_injections(raw_injections, merge_window)

    # --- 4. Получаем продувки ---
    purge_times = _get_purge_times(well_id, period_start, period_end)

    # --- 5. Строим границы ИРВ ---
    boundaries = _build_irv_boundaries(injections, purge_times, cfg, period_end)

    # --- 6. FlowRateConfig из baseline сценария ---
    flow_cfg = _get_flow_config(well_id)

    # --- 7. Рассчитываем метрики для каждого ИРВ ---
    irv_results: list[IRVResult] = []
    for inj, t_start, t_end in boundaries:
        duration_hours = (t_end - t_start).total_seconds() / 3600.0
        metrics, df = _compute_irv_metrics(
            well_id, inj, t_start, t_end,
            choke_mm, flow_cfg, cfg,
        )
        irv_results.append(IRVResult(
            injection=inj,
            t_start=t_start,
            t_end=t_end,
            duration_hours=round(duration_hours, 2),
            choke_mm=choke_mm,
            metrics=metrics,
            pressure_df=df if include_pressure_data else None,
        ))

    # --- 8. Группируем ИРВ по реагенту и считаем Score ---
    irv_by_reagent: dict[str, list[IRVResult]] = {}
    for irv in irv_results:
        r = irv.injection.reagent
        irv_by_reagent.setdefault(r, []).append(irv)

    scores = _compute_scores(irv_by_reagent, cfg)

    best = scores[0] if scores else None

    # --- 9. Предупреждения ---
    no_qty_count = sum(1 for irv in irv_results if irv.injection.qty is None)
    if no_qty_count > 0:
        pct = no_qty_count / len(irv_results) * 100
        if pct > 30:
            warnings.append(
                f"{no_qty_count}/{len(irv_results)} вбросов ({pct:.0f}%) без указания количества — M2 ненадёжен"
            )

    if best and best.score < 0.40:
        warnings.append("Ни один реагент не превышает Score 0.40 — рекомендуется пересмотр программы реагентов")

    return {
        "well_id": well_id,
        "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
        "config": _cfg_to_dict(cfg),
        "choke_mm": choke_mm,
        "injections_total": len(raw_injections),
        "merged_injections": len(injections),
        "irv_results": [_irv_to_dict(irv) for irv in irv_results],
        "scores": [_score_to_dict(s) for s in scores],
        "best_reagent": _score_to_dict(best) if best else None,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Детальный анализ одного ИРВ (для popup / детальной страницы)
# ---------------------------------------------------------------------------

def get_irv_detail(
    well_id: int,
    event_time: datetime,
    cfg: ReagentAnalysisConfig = DEFAULT_CONFIG,
) -> Optional[dict]:
    """
    Детальный анализ ИРВ начиная от конкретного вброса.
    Включает данные давления и дебита для графиков.
    """
    # Находим вброс и окружающие события
    window_start = event_time - timedelta(hours=cfg.pre_window_hours + 1)
    window_end = event_time + timedelta(days=cfg.max_window_days)

    raw_injections = _get_reagent_injections(well_id, window_start, window_end)
    if not raw_injections:
        return None

    merge_window = timedelta(hours=cfg.merge_window_hours)
    injections = _merge_injections(raw_injections, merge_window)

    # Ищем вброс, содержащий event_time
    target_inj = None
    for inj in injections:
        if abs((inj.event_time - event_time).total_seconds()) < 60:
            target_inj = inj
            break
    if target_inj is None:
        return None

    purge_times = _get_purge_times(well_id, window_start, window_end)
    boundaries = _build_irv_boundaries(injections, purge_times, cfg, window_end)

    choke_mm = get_choke_mm(well_id)
    flow_cfg = _get_flow_config(well_id)

    for inj, t_start, t_end in boundaries:
        if inj is target_inj:
            metrics, df = _compute_irv_metrics(
                well_id, inj, t_start, t_end,
                choke_mm, flow_cfg, cfg,
            )
            result = _irv_to_dict(IRVResult(
                injection=inj,
                t_start=t_start,
                t_end=t_end,
                duration_hours=round((t_end - t_start).total_seconds() / 3600.0, 2),
                choke_mm=choke_mm,
                metrics=metrics,
            ))

            # Добавляем данные для графиков
            if df is not None and not df.empty:
                chart_df = df.copy()
                chart_df["dp"] = chart_df["p_tube"] - chart_df["p_line"]
                if choke_mm and choke_mm > 0:
                    chart_df = calculate_flow_rate(chart_df, choke_mm, flow_cfg)
                    chart_df = calculate_cumulative(chart_df)

                result["chart_data"] = {
                    "timestamps": [t.isoformat() for t in chart_df.index],
                    "p_tube": [_safe_float(v) for v in chart_df["p_tube"]],
                    "p_line": [_safe_float(v) for v in chart_df["p_line"]],
                    "dp": [_safe_float(v) for v in chart_df["dp"]],
                    "flow_rate": [_safe_float(v) for v in chart_df.get("flow_rate", [])],
                    "cumulative_flow": [_safe_float(v) for v in chart_df.get("cumulative_flow", [])],
                    "event_time": inj.event_time.isoformat(),
                    "t_start": t_start.isoformat(),
                    "t_end": t_end.isoformat(),
                    "baseline_dp": metrics.baseline_dp,
                }

                # ΔP кривая (ось X = часы от вброса)
                dp_curve = _build_dp_curve(df, inj.event_time, cfg.smoothing_window_min)
                if dp_curve:
                    result["dp_curve"] = dp_curve

                # Сегменты (рост/плато/спад) внутри ИРВ
                df_irv_only = df.loc[t_start:t_end]
                segments = _detect_segments(df_irv_only, inj.event_time)
                result["segments"] = segments

            return result

    return None


# ---------------------------------------------------------------------------
# Overlay: ΔP кривые всех ИРВ одного реагента наложены (X=часы от вброса)
# ---------------------------------------------------------------------------

def get_overlay_data(
    well_id: int,
    period_start: datetime,
    period_end: datetime,
    reagent_name: Optional[str] = None,
    cfg: ReagentAnalysisConfig = DEFAULT_CONFIG,
) -> dict:
    """
    Собирает ΔP кривые для каждого ИРВ.
    Ось X — часы от вброса (0 = момент вброса).
    Каждая кривая — отдельный dataset для overlay графика.

    Возвращает:
    {
        "reagent": str,
        "curves": [
            {
                "label": "дд.мм HH:MM (N л)",
                "event_time": str,
                "hours": [...],
                "dp_smooth": [...],
                "baseline_dp": float,
                "phases": {...}
            }, ...
        ]
    }
    """
    raw_injections = _get_reagent_injections(well_id, period_start, period_end)
    if not raw_injections:
        return {"reagent": reagent_name, "curves": []}

    merge_window = timedelta(hours=cfg.merge_window_hours)
    injections = _merge_injections(raw_injections, merge_window)

    # Фильтр по реагенту
    if reagent_name:
        injections = [inj for inj in injections if inj.reagent == reagent_name]
    if not injections:
        return {"reagent": reagent_name, "curves": []}

    purge_times = _get_purge_times(well_id, period_start, period_end)
    boundaries = _build_irv_boundaries(injections, purge_times, cfg, period_end)

    curves = []
    for inj, _t_start, t_end in boundaries:
        # Получаем данные: от момента вброса (не от pre-window)
        df = get_pressure_data(
            well_id,
            start=inj.event_time.isoformat(),
            end=t_end.isoformat(),
        )
        if df.empty:
            continue

        masks = load_active_masks(well_id, inj.event_time, t_end, verified_only=True)
        if masks:
            df, _ = apply_masks(df, masks)

        dp_curve = _build_dp_curve(df, inj.event_time, cfg.smoothing_window_min, max_points=300)
        if not dp_curve:
            continue

        # Baseline
        pre_start = inj.event_time - timedelta(hours=cfg.pre_window_hours)
        df_pre = get_pressure_data(well_id, start=pre_start.isoformat(), end=inj.event_time.isoformat())
        baseline = None
        if not df_pre.empty:
            df_pv = df_pre.dropna(subset=["p_tube", "p_line"])
            df_pv = df_pv[(df_pv["p_tube"] > df_pv["p_line"]) & ((df_pv["p_tube"] - df_pv["p_line"]) > 0.1)]
            if not df_pv.empty:
                baseline = round(float(np.median(df_pv["p_tube"] - df_pv["p_line"])), 3)

        # Phases
        phases = _analyze_dp_phases(df, baseline, cfg)

        label_parts = [inj.event_time.strftime("%d.%m %H:%M")]
        if inj.qty is not None:
            label_parts.append(f"{inj.qty} л")

        curves.append({
            "label": " | ".join(label_parts),
            "event_time": inj.event_time.isoformat(),
            "hours": dp_curve["hours_from_injection"],
            "dp_smooth": dp_curve["dp_smooth"],
            "baseline_dp": baseline,
            "phases": _phases_to_dict(phases),
        })

    return {"reagent": reagent_name or (injections[0].reagent if injections else ""), "curves": curves}


# ---------------------------------------------------------------------------
# Хелперы: сериализация
# ---------------------------------------------------------------------------

def _safe_float(v) -> Optional[float]:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return round(float(v), 4)


def _phases_to_dict(p: DPPhases) -> dict:
    return {
        "peak_dp": p.peak_dp,
        "time_to_peak_min": p.time_to_peak_min,
        "rise_rate": p.rise_rate,
        "decay_slope": p.decay_slope,
        "decay_start_dp": p.decay_start_dp,
        "decay_end_dp": p.decay_end_dp,
        "time_to_baseline_hours": p.time_to_baseline_hours,
    }


def _cfg_to_dict(cfg: ReagentAnalysisConfig) -> dict:
    return {
        "grace_period_min": cfg.grace_period_min,
        "pre_window_hours": cfg.pre_window_hours,
        "merge_window_hours": cfg.merge_window_hours,
        "max_window_days": cfg.max_window_days,
        "dp_effect_threshold": cfg.dp_effect_threshold,
        "min_utilisation_pct": cfg.min_utilisation_pct,
    }


def _irv_to_dict(irv: IRVResult) -> dict:
    m = irv.metrics
    return {
        "event_ids": irv.injection.event_ids,
        "event_time": irv.injection.event_time.isoformat(),
        "reagent": irv.injection.reagent,
        "qty": irv.injection.qty,
        "merged_count": irv.injection.merged_count,
        "t_start": irv.t_start.isoformat(),
        "t_end": irv.t_end.isoformat(),
        "duration_hours": irv.duration_hours,
        "choke_mm": irv.choke_mm,
        "metrics": {
            "q_cumulative": m.q_cumulative,
            "q_per_unit": m.q_per_unit,
            "dp_gain": m.dp_gain,
            "effect_duration_hours": m.effect_duration_hours,
            "utilisation_pct": round(m.utilisation_pct, 1) if m.utilisation_pct else None,
            "baseline_dp": m.baseline_dp,
            "avg_dp": m.avg_dp,
            "avg_flow_rate": m.avg_flow_rate,
            "data_points": m.data_points,
            "invalid_reason": m.invalid_reason,
            "phases": _phases_to_dict(m.phases) if m.phases else None,
        },
    }


def _score_to_dict(s: ReagentScore) -> dict:
    return {
        "reagent": s.reagent,
        "choke_mm": s.choke_mm,
        "irv_count": s.irv_count,
        "valid_irv_count": s.valid_irv_count,
        "score": s.score,
        "level": s.level,
        "level_name": s.level_name,
        "median_q_per_unit": s.median_q_per_unit,
        "median_dp_gain": s.median_dp_gain,
        "median_effect_hours": s.median_effect_hours,
        "degradation_slope": s.degradation_slope,
        "flags": s.flags,
    }


def _get_flow_config(well_id: int) -> FlowRateConfig:
    """
    Пытается загрузить параметры из baseline flow_scenario.
    Если нет — возвращает DEFAULT_FLOW.
    """
    query = text("""
        SELECT c1, c2, c3, multiplier, critical_ratio
        FROM flow_scenario
        WHERE well_id = :well_id
          AND is_baseline = true
          AND status = 'calculated'
          AND deleted_at IS NULL
        ORDER BY period_end DESC
        LIMIT 1
    """)
    with pg_engine.connect() as conn:
        row = conn.execute(query, {"well_id": well_id}).fetchone()
    if row is None:
        return DEFAULT_FLOW
    return FlowRateConfig(
        C1=float(row[0]),
        C2=float(row[1]),
        C3=float(row[2]),
        multiplier=float(row[3]),
        critical_ratio=float(row[4]),
    )
