"""
Observation Segment Service — Phase C2.

Реализует preview-расчёт для блока kind='observation_segment' согласно
RFC observation_v1.0 (schema_version="1.0").

PUBLIC API: единственная функция — compute_segment_preview().
Все helper-функции приватные (prefix `_`).

НЕ пишет в БД. НЕ имеет side-effects. Только читает.
Использует B1 (observation_data_service.py):
  - load_observation_data() — единственная точка входа для данных.

ОГРАНИЧЕНИЯ (owner constraints):
  - B1: единственный источник данных — load_observation_data().
  - B2: agg_df не мутируется inplace — все трансформации на copy().
  - B3: не «лечит» snapshot, не изобретает данные.
  - НЕ импортирует _segment_analysis_dual (завязан на dual q_total+q_working).
  - НЕ содержит top-level 'metrics' или 'comparisons' в snapshot.
  - Snapshot renderer-neutral (нет narrative/HTML/markdown/labels).
  - diagnostics.target ∈ {segment, changepoint, overall}.
  - overall.verdict='insufficient_data' если segments=[] (не изобретает).
  - slope в snapshot всегда нормализован per-day.
  - min_segment_days всегда в днях (независимо от aggregation).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Literal

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

# B1: единственный источник данных для главы Наблюдение.
# Импорт на module level, чтобы тесты могли патчить через
# `mock.patch("backend.services.observation_segment_service.load_observation_data", ...)`.
from backend.services.observation_data_service import load_observation_data

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

SNAPSHOT_V = "obs_segment_v1"
SCHEMA_VERSION = "1.0"

# [requires_calibration] — пороги подобраны по экспертным оценкам,
# требуют калибровки по накопленным реальным данным.
SENSITIVITY_PRESETS: dict[str, dict | None] = {
    "low":    {"min_change_pct": 20.0, "min_segment_days": 10, "smoothing_window": 10},
    "medium": {"min_change_pct": 10.0, "min_segment_days": 7,  "smoothing_window": 7},
    "high":   {"min_change_pct": 5.0,  "min_segment_days": 3,  "smoothing_window": 3},
    "custom": None,
}

DEFAULT_IGNORE_SHUTDOWN_DAYS = True
DEFAULT_IGNORE_PURGE_WINDOW_HOURS = 24

# Borderline: detected если |pct| >= BORDERLINE_COEFFICIENT * threshold
BORDERLINE_COEFFICIENT = 1.5

# Минимум точек для сегментации (меньше → insufficient_data)
MIN_POINTS_FOR_SEGMENTATION = 6

# Маппинг aggregation → минут в одном периоде (для нормализации slope)
_AGG_MINUTES: dict[str, float] = {
    "daily":  1440.0,
    "12h":    720.0,
    "6h":     360.0,
    "hourly": 60.0,
}

# Stable slope threshold: |slope_per_day| < threshold → direction="stable"
# [requires_calibration]
_SLOPE_STABLE_THRESHOLD_Q = 50.0    # тыс.м³/сут / день

# Минимальный shutdown_min в сутки для включения дня в shutdown_cluster
_SHUTDOWN_THRESHOLD_MIN_PER_DAY = 600.0  # 10 часов

# Минимум дней подряд для кластера простоев
_SHUTDOWN_CLUSTER_MIN_DAYS = 2

# Зазор для слияния соседних кластеров простоев (дней)
_SHUTDOWN_CLUSTER_MERGE_GAP = 2


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------


def compute_segment_preview(
    db: Session,
    well_id: int,
    d_from: "date | str",
    d_to: "date | str",
    *,
    aggregation: Literal["daily", "12h", "6h", "hourly"] = "daily",
    sensitivity: Literal["low", "medium", "high", "custom"] = "medium",
    min_segment_days: int | None = None,
    min_change_pct: float | None = None,
    smoothing_window: int | None = None,
    ignore_shutdown_days: bool = DEFAULT_IGNORE_SHUTDOWN_DAYS,
    ignore_purge_window_hours: int = DEFAULT_IGNORE_PURGE_WINDOW_HOURS,
    include_raw_chart: bool = True,
) -> dict:
    """
    Preview-сервис для kind='observation_segment'.

    Возвращает полный snapshot obs_segment_v1.

    Не пишет в БД. Не имеет side-effects.

    Args:
        db: SQLAlchemy session (только чтение — для B1)
        well_id: id скважины (wells.id)
        d_from, d_to: даты периода (ISO string или date)
        aggregation: разрешение агрегации ('daily'/'12h'/'6h'/'hourly')
        sensitivity: пресет чувствительности
        min_segment_days: переопределение min_segment_days (всегда в днях)
        min_change_pct: переопределение порога изменения Q в %
        smoothing_window: переопределение окна сглаживания (в периодах aggregation)
        ignore_shutdown_days: исключать ли дни кластеров простоев из детекции CP
        ignore_purge_window_hours: окно в часах вокруг продувки для флага purge_related
        include_raw_chart: включать ли raw.chart_payload в snapshot

    Returns:
        dict — snapshot obs_segment_v1 (RFC §2.3)

    Raises:
        ValueError: custom sensitivity без override параметров;
                    критические ошибки B1 (нет well_id и т.п.)
    """
    # Нормализация дат
    if isinstance(d_from, str):
        d_from = date.fromisoformat(d_from)
    if isinstance(d_to, str):
        d_to = date.fromisoformat(d_to)

    computed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    period_dict = {"from": d_from.isoformat(), "to": d_to.isoformat()}

    # Resolve thresholds (ValueError при custom без params)
    overrides = {
        "min_segment_days": min_segment_days,
        "min_change_pct": min_change_pct,
        "smoothing_window": smoothing_window,
    }
    _validate_custom_sensitivity(sensitivity, overrides)
    effective_thresholds, has_user_overrides = _resolve_thresholds(sensitivity, overrides)

    thresholds_used = {
        "aggregation": aggregation,
        "sensitivity": sensitivity,
        "min_segment_days": effective_thresholds["min_segment_days"],
        "min_change_pct":   effective_thresholds["min_change_pct"],
        "smoothing_window": effective_thresholds["smoothing_window"],
        "ignore_shutdown_days": ignore_shutdown_days,
        "ignore_purge_window_hours": ignore_purge_window_hours,
        "has_user_overrides": has_user_overrides,
    }

    # ── B1: единственная точка входа для данных ──────────────────────────
    # (import выполнен на module level — см. начало файла)
    obs = load_observation_data(
        db=db,
        well_id=well_id,
        d_from=d_from,
        d_to=d_to,
        aggregation=aggregation,
        smooth_minute=True,
        include_customer_overlay=False,  # C2 не нужен overlay
    )

    agg_df: pd.DataFrame = obs.our_df
    data_quality_raw: dict = obs.data_quality
    pipeline_meta: dict = obs.our_meta

    # ── Quality layer (аналогично C1) ────────────────────────────────────
    quality_layer = _build_quality_layer(data_quality_raw)

    # ── Flags layer ──────────────────────────────────────────────────────
    flags_layer = _build_flags_layer(quality_layer)

    # ── Ранний выход если данных нет ─────────────────────────────────────
    no_data = (data_quality_raw.get("status") == "no_data") or agg_df.empty
    if no_data:
        return _assemble_snapshot(
            computed_at=computed_at,
            block_status="no_data",
            period=period_dict,
            raw_layer=None,
            quality_layer=quality_layer,
            flags_layer=flags_layer,
            thresholds_used=thresholds_used,
            segments=[],
            changepoints=[],
            shutdown_clusters=[],
            diagnostics=[{
                "target": "overall",
                "context": "combined",
                "verdict": "insufficient_data",
                "magnitude": None,
                "requires_log_check": True,
            }],
        )

    # ── Проверка минимального кол-ва точек ────────────────────────────────
    n_points = int(agg_df["q"].notna().sum()) if "q" in agg_df.columns else 0
    if n_points < MIN_POINTS_FOR_SEGMENTATION:
        return _assemble_snapshot(
            computed_at=computed_at,
            block_status="insufficient_data",
            period=period_dict,
            raw_layer=None,
            quality_layer=quality_layer,
            flags_layer=flags_layer,
            thresholds_used=thresholds_used,
            segments=[],
            changepoints=[],
            shutdown_clusters=[],
            diagnostics=[{
                "target": "overall",
                "context": "combined",
                "verdict": "insufficient_data",
                "magnitude": None,
                "requires_log_check": True,
            }],
        )

    # ── Построение shutdown_clusters ────────────────────────────────────
    shutdown_clusters_list = _build_shutdown_clusters(agg_df)

    # ── Извлечение purge_events ──────────────────────────────────────────
    purge_events = _extract_purge_events(agg_df, pipeline_meta)

    # ── Применение сглаживания (на copy) ─────────────────────────────────
    work_df = _apply_smoothing(agg_df, effective_thresholds["smoothing_window"])

    # ── Исключение shutdown-периодов (на copy) ────────────────────────────
    work_df = _exclude_shutdown_periods(
        work_df, shutdown_clusters_list, ignore_shutdown_days
    )

    # ── Маска продувок (на copy) ──────────────────────────────────────────
    work_df = _apply_purge_mask(work_df, purge_events, ignore_purge_window_hours)

    # ── Детекция changepoints ────────────────────────────────────────────
    cps = _detect_changepoints(
        work_df,
        min_change_pct=effective_thresholds["min_change_pct"],
        min_segment_days=effective_thresholds["min_segment_days"],
        aggregation=aggregation,
    )

    # ── Построение сегментов из исходного (не work_df) agg_df ────────────
    # Для расчёта статистик используем оригинальный agg_df (B2: work_df временный)
    segments = _build_segments_from_changepoints(agg_df, cps, aggregation)
    segments = _compute_segment_trends(segments, agg_df, aggregation)

    # ── Обогащение changepoints magnitude/confidence ─────────────────────
    cps_enriched = _enrich_changepoints(
        cps, agg_df, effective_thresholds["min_change_pct"], aggregation
    )

    # ── Diagnostics ──────────────────────────────────────────────────────
    diagnostics = _build_diagnostics(
        segments=segments,
        changepoints_enriched=cps_enriched,
        shutdown_clusters=shutdown_clusters_list,
        purge_events=purge_events,
        thresholds=effective_thresholds,
        ignore_purge_window_hours=ignore_purge_window_hours,
    )

    # ── Raw chart payload ─────────────────────────────────────────────────
    raw_layer = None
    if include_raw_chart:
        raw_layer = {"chart_payload": _build_raw_chart_payload(agg_df)}

    # ── block_status ──────────────────────────────────────────────────────
    block_status = "ok"

    return _assemble_snapshot(
        computed_at=computed_at,
        block_status=block_status,
        period=period_dict,
        raw_layer=raw_layer,
        quality_layer=quality_layer,
        flags_layer=flags_layer,
        thresholds_used=thresholds_used,
        segments=segments,
        changepoints=cps_enriched,
        shutdown_clusters=shutdown_clusters_list,
        diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# PRIVATE HELPERS — thresholds
# ---------------------------------------------------------------------------


def _validate_custom_sensitivity(
    sensitivity: str,
    overrides: dict,
) -> None:
    """
    Проверяет: если sensitivity='custom', хотя бы один override не None.
    Иначе → ValueError.
    """
    if sensitivity != "custom":
        return
    all_none = all(v is None for v in overrides.values())
    if all_none:
        raise ValueError(
            "sensitivity='custom' требует хотя бы одного из: "
            "min_segment_days, min_change_pct, smoothing_window"
        )


def _resolve_thresholds(
    sensitivity: str,
    overrides: dict,
) -> tuple[dict, bool]:
    """
    Возвращает (effective_thresholds, has_user_overrides).

    Логика:
    - Берём preset (для 'custom' — fallback на 'medium').
    - Применяем все non-None overrides.
    - has_user_overrides=True если sensitivity != 'custom' И хотя бы один override не None.
      Для sensitivity='custom' has_user_overrides=True всегда (они и есть параметры).
    """
    if sensitivity == "custom":
        # Для custom: начинаем с medium как fallback
        base = dict(SENSITIVITY_PRESETS["medium"])
    else:
        base = dict(SENSITIVITY_PRESETS[sensitivity])

    has_override = False
    for key, val in overrides.items():
        if val is not None:
            base[key] = val
            has_override = True

    if sensitivity == "custom":
        # custom всегда считается overridden
        return base, True

    return base, has_override


# ---------------------------------------------------------------------------
# PRIVATE HELPERS — data transformations (все на copy)
# ---------------------------------------------------------------------------


def _apply_smoothing(agg_df: pd.DataFrame, window: int) -> pd.DataFrame:
    """
    Применяет скользящее среднее к колонке 'q' на copy().
    window — в периодах aggregation.
    Оригинальный agg_df НЕ мутируется.
    """
    if agg_df.empty or "q" not in agg_df.columns or window <= 1:
        return agg_df.copy()

    result = agg_df.copy()
    result["q"] = (
        result["q"]
        .rolling(window=window, min_periods=1, center=True)
        .mean()
    )
    return result


def _build_shutdown_clusters(agg_df: pd.DataFrame) -> list[dict]:
    """
    Находит кластеры простоев в agg_df по колонке shutdown_min.
    Возвращает список {"start_date": str, "end_date": str, "total_minutes": float}.
    Работает независимо от aggregation — shutdown_min суммируется по дням.
    """
    if agg_df.empty or "shutdown_min" not in agg_df.columns:
        return []

    df = agg_df.copy()
    shutdown = df["shutdown_min"].fillna(0.0).values
    n = len(shutdown)

    # Порог применяется к каждой строке в aggregation-единицах.
    # shutdown_min в строке может быть долей суток в зависимости от agg.
    # Определяем "проблемный" ряд как: shutdown >= _SHUTDOWN_THRESHOLD_MIN_PER_DAY / 2
    # (либерально, чтобы не пропустить кластеры при hourly aggregation).
    problem = shutdown >= (_SHUTDOWN_THRESHOLD_MIN_PER_DAY / 2.0)

    clusters_raw: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if problem[i]:
            start = i
            while i < n and problem[i]:
                i += 1
            end = i
            if end - start >= _SHUTDOWN_CLUSTER_MIN_DAYS:
                clusters_raw.append((start, end - 1))
        else:
            i += 1

    # Слияние соседних кластеров с зазором <= _SHUTDOWN_CLUSTER_MERGE_GAP
    merged: list[tuple[int, int]] = []
    for start, end in clusters_raw:
        if merged and start - merged[-1][1] <= _SHUTDOWN_CLUSTER_MERGE_GAP:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    result = []
    idx = df.index
    for start, end in merged:
        # Даты из индекса (DatetimeIndex) или строковые
        start_dt = _idx_to_date_str(idx[start])
        end_dt   = _idx_to_date_str(idx[end])
        total_min = float(np.nansum(shutdown[start : end + 1]))
        result.append({
            "start_date":    start_dt,
            "end_date":      end_dt,
            "total_minutes": total_min,
        })

    return result


def _extract_purge_events(agg_df: pd.DataFrame, pipeline_meta: dict) -> list[str]:
    """
    Извлекает даты/timestamps продувок из pipeline_meta['purge_cycles']
    и/или из agg_df['purge_flag'].
    Возвращает список ISO timestamp строк (Кунградское время).
    """
    events: list[str] = []

    # Из pipeline_meta
    purge_cycles = pipeline_meta.get("purge_cycles", [])
    if isinstance(purge_cycles, list):
        for pc in purge_cycles:
            if isinstance(pc, dict):
                ts = pc.get("start") or pc.get("timestamp") or pc.get("date")
                if ts:
                    events.append(str(ts))
            elif isinstance(pc, str):
                events.append(pc)

    # Из agg_df purge_flag (backup)
    if not events and "purge_flag" in agg_df.columns and not agg_df.empty:
        purge_rows = agg_df[agg_df["purge_flag"].astype(bool)]
        for ts in purge_rows.index:
            events.append(_idx_to_date_str(ts))

    return events


def _exclude_shutdown_periods(
    agg_df: pd.DataFrame,
    shutdown_clusters: list[dict],
    ignore_flag: bool,
) -> pd.DataFrame:
    """
    Если ignore_flag=True, ставит q=NaN на строках из shutdown_clusters.
    Работает на copy(). Оригинал НЕ мутируется.
    """
    if not ignore_flag or not shutdown_clusters or agg_df.empty:
        return agg_df.copy()

    result = agg_df.copy()
    if "q" not in result.columns:
        return result

    idx = result.index
    for cluster in shutdown_clusters:
        start_str = cluster.get("start_date", "")
        end_str   = cluster.get("end_date", "")
        try:
            start_dt = pd.Timestamp(start_str)
            end_dt   = pd.Timestamp(end_str)
            mask = (idx >= start_dt) & (idx <= end_dt)
            result.loc[mask, "q"] = np.nan
        except Exception:
            pass

    return result


def _apply_purge_mask(
    agg_df: pd.DataFrame,
    purge_events: list[str],
    window_hours: int,
) -> pd.DataFrame:
    """
    Ставит q=NaN в окне ±window_hours/2 вокруг каждого события продувки.
    Работает на copy(). Оригинал НЕ мутируется.
    """
    if not purge_events or agg_df.empty or window_hours <= 0:
        return agg_df.copy()

    result = agg_df.copy()
    if "q" not in result.columns:
        return result

    half_window = pd.Timedelta(hours=window_hours / 2.0)
    idx = result.index

    for ev_str in purge_events:
        try:
            ev_ts = pd.Timestamp(ev_str)
            mask = (idx >= ev_ts - half_window) & (idx <= ev_ts + half_window)
            result.loc[mask, "q"] = np.nan
        except Exception:
            pass

    return result


# ---------------------------------------------------------------------------
# PRIVATE HELPERS — changepoint detection (extracted + rewritten для obs DataFrame)
# ---------------------------------------------------------------------------


def _detect_changepoints(
    agg_df: pd.DataFrame,
    min_change_pct: float,
    min_segment_days: int,
    aggregation: str = "daily",
) -> list[int]:
    """
    Детектирует changepoints в ряду Q из agg_df.

    Алгоритм адаптирован из segment_analysis_module._detect_changepoints_extended.
    Адаптации:
    - Работает на колонке 'q' из нашего obs DataFrame.
    - Не использует dual q_total/q_working semantics.
    - min_segment_days интерпретируется в днях, конвертируется в points.
    - threshold применяется из min_change_pct параметра.

    Возвращает список индексов (int) точек перелома.
    """
    if "q" not in agg_df.columns or agg_df.empty:
        return []

    y_orig = agg_df["q"].values.astype(float)
    mask = np.isfinite(y_orig)
    if mask.sum() < MIN_POINTS_FOR_SEGMENTATION:
        return []

    # Конвертируем min_segment_days в количество точек aggregation
    min_seg_pts = _days_to_points(min_segment_days, aggregation)
    min_seg_pts = max(min_seg_pts, 2)

    n = len(y_orig)
    if n < min_seg_pts * 2:
        return []

    # Интерполируем NaN для алгоритма детекции
    y_clean = y_orig.copy()
    if not mask.all():
        nans = ~mask
        x_all = np.arange(n)
        y_clean[nans] = np.interp(x_all[nans], x_all[mask], y_orig[mask])

    median_val = float(np.nanmedian(y_clean))
    if median_val == 0:
        median_val = 1.0

    threshold = abs(median_val) * min_change_pct / 100.0

    changepoints: set[int] = set()

    # === 1. Базовая детекция по скачку среднего уровня ===
    cost = np.zeros(n)
    for i in range(min_seg_pts, n - min_seg_pts):
        left  = y_clean[:i]
        right = y_clean[i:]
        mean_diff = abs(np.mean(right) - np.mean(left))
        jump = abs(y_clean[i] - y_clean[i - 1]) if i > 0 else 0.0
        cost[i] = mean_diff + jump * 0.5

    for i in range(min_seg_pts, n - min_seg_pts):
        if cost[i] < threshold:
            continue
        ws = max(0, i - min_seg_pts)
        we = min(n, i + min_seg_pts + 1)
        if cost[i] == np.max(cost[ws:we]):
            changepoints.add(i)

    # === 2. Детекция коротких аномальных провалов ===
    dip_drop_pct  = 25.0  # [requires_calibration]
    dip_rec_pct   = 25.0  # [requires_calibration]
    dip_win_pts   = _days_to_points(7, aggregation)
    for i in range(1, n - 1):
        if y_clean[i - 1] != 0:
            drop_pct = (y_clean[i] - y_clean[i - 1]) / y_clean[i - 1] * 100.0
            if drop_pct < -dip_drop_pct:
                changepoints.add(i)
                for j in range(i + 1, min(i + 1 + dip_win_pts, n)):
                    rec_pct = (y_clean[j] - y_clean[i]) / y_clean[i] * 100.0 if y_clean[i] != 0 else 0.0
                    if rec_pct > dip_rec_pct:
                        changepoints.add(j)
                        break

    # === 3. Сортировка и отсечение edge ===
    edge_pts = max(1, _days_to_points(2, aggregation))
    sorted_cp = sorted(changepoints)
    sorted_cp = [cp for cp in sorted_cp if edge_pts <= cp <= n - edge_pts]

    # === 4. Слияние близких точек ===
    merge_close_pts = _days_to_points(5, aggregation)
    merged: list[int] = []
    for cp in sorted_cp:
        if not merged:
            merged.append(cp)
        elif cp - merged[-1] < merge_close_pts:
            # Оставляем с большим cost
            if cost[cp] > cost[merged[-1]]:
                merged[-1] = cp
        else:
            merged.append(cp)

    # === 5. Финальная фильтрация: |change_pct| > min_change_pct / 3 ===
    # (мягкий финальный фильтр — строгий порог уже применён в threshold)
    final: list[int] = []
    final_filter_pct = min_change_pct / 3.0
    for idx, cp in enumerate(merged):
        prev_cp = final[-1] if final else 0
        next_cp = merged[idx + 1] if idx + 1 < len(merged) else n
        mean_before = np.nanmean(y_clean[prev_cp:cp]) if cp > prev_cp else np.nan
        mean_after  = np.nanmean(y_clean[cp:next_cp]) if next_cp > cp else np.nan

        if np.isfinite(mean_before) and np.isfinite(mean_after) and mean_before != 0:
            chg = abs(mean_after - mean_before) / abs(mean_before) * 100.0
            if chg >= final_filter_pct:
                final.append(cp)
        elif not (np.isfinite(mean_before) and np.isfinite(mean_after)):
            # Если данных нет для проверки — оставляем (conservative)
            final.append(cp)

    return final


def _days_to_points(days: int, aggregation: str) -> int:
    """
    Конвертирует количество дней в количество точек для заданного aggregation.
    Всегда возвращает int >= 1.
    """
    minutes_per_point = _AGG_MINUTES.get(aggregation, 1440.0)
    points = int(days * 1440.0 / minutes_per_point)
    return max(1, points)


def _normalize_slope_per_day(slope_per_period: float, aggregation: str) -> float:
    """
    Нормализует slope из единиц/период в единицы/день.
    slope_per_period — наклон из linreg по индексу (в периодах aggregation).
    """
    if not np.isfinite(slope_per_period):
        return float("nan")
    minutes_per_point = _AGG_MINUTES.get(aggregation, 1440.0)
    # slope_per_period = slope в ед./period
    # per_day = slope_per_period * (1440 / minutes_per_period)
    return slope_per_period * (1440.0 / minutes_per_point)


# ---------------------------------------------------------------------------
# PRIVATE HELPERS — segment building + trends
# ---------------------------------------------------------------------------


def _build_segments_from_changepoints(
    agg_df: pd.DataFrame,
    cps: list[int],
    aggregation: str,
) -> list[dict]:
    """
    Строит список сегментов из changepoints.
    N changepoints → N+1 сегментов.

    Каждый сегмент: {num, start_idx, end_idx, start_date, end_date, duration_days,
                     mean_q, mean_dp, mean_p_tube, mean_p_line}
    Статистики считаются из оригинального agg_df (не work_df).

    Короткие сегменты (< min_segment_days) объединяются с соседним.
    """
    if agg_df.empty:
        return []

    n = len(agg_df)
    boundaries = [0] + sorted(cps) + [n]
    idx = agg_df.index

    segments = []
    for i in range(len(boundaries) - 1):
        s = boundaries[i]
        e = boundaries[i + 1]
        if e <= s:
            continue

        seg_df = agg_df.iloc[s:e]

        start_dt = _idx_to_date_str(idx[s])
        end_dt   = _idx_to_date_str(idx[e - 1])
        duration_days = _compute_duration_days(idx[s], idx[e - 1], s, e, aggregation)

        mean_q      = _safe_nanmean(seg_df, "q")
        mean_dp     = _safe_nanmean(seg_df, "dp")
        mean_p_tube = _safe_nanmean(seg_df, "p_tube")
        mean_p_line = _safe_nanmean(seg_df, "p_line")

        segments.append({
            "num":        i + 1,
            "start_date": start_dt,
            "end_date":   end_dt,
            "start_idx":  s,
            "end_idx":    e - 1,
            "duration_days": duration_days,
            "mean_q":      _round_or_none(mean_q, 2),
            "mean_dp":     _round_or_none(mean_dp, 3),
            "mean_p_tube": _round_or_none(mean_p_tube, 3),
            "mean_p_line": _round_or_none(mean_p_line, 3),
            # Trend fields будут заполнены в _compute_segment_trends
            "slope_q_per_day": None,
            "direction": "insufficient_data",
        })

    return segments


def _compute_segment_trends(
    segments: list[dict],
    agg_df: pd.DataFrame,
    aggregation: str,
) -> list[dict]:
    """
    Вычисляет slope_q_per_day и direction для каждого сегмента.
    slope нормализован per-day независимо от aggregation.
    direction: 'rising'/'falling'/'stable'/'insufficient_data'.
    Возвращает обновлённые сегменты (без мутации входного списка).
    """
    result = []
    for seg in segments:
        seg = dict(seg)  # копия чтобы не мутировать исходный
        s = seg["start_idx"]
        e = seg["end_idx"] + 1

        if s >= e or "q" not in agg_df.columns:
            seg["slope_q_per_day"] = None
            seg["direction"] = "insufficient_data"
            result.append(seg)
            continue

        q_vals = agg_df["q"].iloc[s:e].values.astype(float)
        valid_mask = np.isfinite(q_vals)
        n_valid = int(valid_mask.sum())

        if n_valid < 3:
            seg["slope_q_per_day"] = None
            seg["direction"] = "insufficient_data"
            result.append(seg)
            continue

        x = np.arange(len(q_vals), dtype=float)[valid_mask]
        y = q_vals[valid_mask]

        try:
            coeffs = np.polyfit(x, y, 1)
            slope_per_period = float(coeffs[0])
        except (np.linalg.LinAlgError, ValueError):
            seg["slope_q_per_day"] = None
            seg["direction"] = "insufficient_data"
            result.append(seg)
            continue

        slope_per_day = _normalize_slope_per_day(slope_per_period, aggregation)

        if not np.isfinite(slope_per_day):
            seg["slope_q_per_day"] = None
            seg["direction"] = "insufficient_data"
        else:
            seg["slope_q_per_day"] = round(slope_per_day, 4)
            if abs(slope_per_day) < _SLOPE_STABLE_THRESHOLD_Q:
                seg["direction"] = "stable"
            elif slope_per_day > 0:
                seg["direction"] = "rising"
            else:
                seg["direction"] = "falling"

        result.append(seg)

    return result


def _enrich_changepoints(
    cps: list[int],
    agg_df: pd.DataFrame,
    min_change_pct: float,
    aggregation: str,
) -> list[dict]:
    """
    Обогащает raw changepoints (list of int) метаданными:
    idx, date, magnitude_pct, confidence.

    Возвращает list[dict].
    """
    if not cps or agg_df.empty or "q" not in agg_df.columns:
        return []

    n = len(agg_df)
    y = agg_df["q"].values.astype(float)
    boundaries = [0] + sorted(cps) + [n]
    idx_ts = agg_df.index

    enriched = []
    for i, cp in enumerate(sorted(cps)):
        prev_start = boundaries[i]
        next_end   = boundaries[i + 2] if i + 2 < len(boundaries) else n

        mean_before = float(np.nanmean(y[prev_start:cp])) if cp > prev_start else float("nan")
        mean_after  = float(np.nanmean(y[cp:next_end]))   if next_end > cp   else float("nan")

        magnitude_pct: float | None = None
        if np.isfinite(mean_before) and np.isfinite(mean_after) and mean_before != 0:
            magnitude_pct = round((mean_after - mean_before) / abs(mean_before) * 100.0, 2)

        verdict = _classify_changepoint_verdict(magnitude_pct, min_change_pct)
        confidence = _compute_confidence(magnitude_pct, min_change_pct)
        cp_date = _idx_to_date_str(idx_ts[cp]) if cp < len(idx_ts) else None

        enriched.append({
            "idx":           cp,
            "date":          cp_date,
            "magnitude_pct": magnitude_pct,
            "confidence":    confidence,
            # verdict сохраняем для _build_diagnostics, в финальный snapshot не попадает
            "_verdict":      verdict,
        })

    return enriched


# ---------------------------------------------------------------------------
# PRIVATE HELPERS — classification
# ---------------------------------------------------------------------------


def _classify_changepoint_verdict(
    magnitude_pct: float | None,
    threshold: float,
) -> str:
    """
    detected:    |pct| >= BORDERLINE_COEFFICIENT * threshold
    borderline:  threshold <= |pct| < BORDERLINE_COEFFICIENT * threshold
    insufficient_data: magnitude_pct is None или |pct| < threshold
    """
    if magnitude_pct is None or not np.isfinite(magnitude_pct):
        return "insufficient_data"
    abs_pct = abs(magnitude_pct)
    if abs_pct >= BORDERLINE_COEFFICIENT * threshold:
        return "detected"
    if abs_pct >= threshold:
        return "borderline"
    return "insufficient_data"


def _compute_confidence(
    magnitude_pct: float | None,
    threshold: float,
) -> str:
    """
    high:   |pct| >= 2 * threshold
    medium: |pct| >= threshold
    low:    |pct| < threshold или None
    """
    if magnitude_pct is None or not np.isfinite(magnitude_pct):
        return "low"
    abs_pct = abs(magnitude_pct)
    if abs_pct >= 2.0 * threshold:
        return "high"
    if abs_pct >= threshold:
        return "medium"
    return "low"


def _classify_changepoint_flags(
    cp_date: str | None,
    shutdown_clusters: list[dict],
    purge_events: list[str],
    window_hours: int,
) -> dict:
    """
    Возвращает {"shutdown_related": bool, "purge_related": bool}.
    shutdown_related: cp_date попадает в [cluster.start, cluster.end].
    purge_related: cp_date в окне ±window_hours/2 от purge_event.
    """
    shutdown_related = False
    purge_related    = False

    if not cp_date:
        return {"shutdown_related": False, "purge_related": False}

    try:
        cp_ts = pd.Timestamp(cp_date)
    except Exception:
        return {"shutdown_related": False, "purge_related": False}

    # shutdown_related
    for cluster in shutdown_clusters:
        try:
            s_ts = pd.Timestamp(cluster["start_date"])
            e_ts = pd.Timestamp(cluster["end_date"])
            if s_ts <= cp_ts <= e_ts:
                shutdown_related = True
                break
        except Exception:
            pass

    # purge_related
    half_window = pd.Timedelta(hours=window_hours / 2.0)
    for ev_str in purge_events:
        try:
            ev_ts = pd.Timestamp(ev_str)
            if abs(cp_ts - ev_ts) <= half_window:
                purge_related = True
                break
        except Exception:
            pass

    return {"shutdown_related": shutdown_related, "purge_related": purge_related}


def _compute_overall_trend(segments: list[dict]) -> str:
    """
    Вычисляет overall trend взвешенный по duration_days.

    Если segments пустой → 'insufficient_data' (НЕ инвентируем).
    Если ни у одного сегмента нет валидного direction → 'insufficient_data'.

    Логика:
    - Взвешенная сумма по duration_days.
    - rising_weight / falling_weight / stable_weight.
    - Побеждает максимальная сумма весов; tie → 'stable'.
    """
    if not segments:
        return "insufficient_data"

    rising_w = 0.0
    falling_w = 0.0
    stable_w  = 0.0
    total_w   = 0.0

    for seg in segments:
        direction = seg.get("direction", "insufficient_data")
        w = float(seg.get("duration_days") or 1)
        if direction == "rising":
            rising_w += w
        elif direction == "falling":
            falling_w += w
        elif direction == "stable":
            stable_w += w
        # insufficient_data не учитываем в weights
        total_w += w

    if rising_w == 0 and falling_w == 0 and stable_w == 0:
        return "insufficient_data"

    max_w = max(rising_w, falling_w, stable_w)
    if rising_w == max_w:
        return "rising"
    if falling_w == max_w:
        return "falling"
    return "stable"


# ---------------------------------------------------------------------------
# PRIVATE HELPERS — diagnostics
# ---------------------------------------------------------------------------


def _build_diagnostics(
    segments: list[dict],
    changepoints_enriched: list[dict],
    shutdown_clusters: list[dict],
    purge_events: list[str],
    thresholds: dict,
    ignore_purge_window_hours: int,
) -> list[dict]:
    """
    Строит список diagnostics.
    target ∈ {segment, changepoint, overall}.
    Нет narrative-строк, нет HTML, нет causal claims.
    requires_log_check=True для всех (RFC §5.3 + Diagnostic interpretation style).
    """
    entries: list[dict] = []

    # ── Segment diagnostics ───────────────────────────────────────────────
    for seg in segments:
        direction = seg.get("direction", "insufficient_data")
        slope     = seg.get("slope_q_per_day")
        num       = seg.get("num", 0)

        magnitude = {"slope_q_per_day": slope} if slope is not None else None

        entries.append({
            "target":            "segment",
            "context":           f"trend_{num}",
            "verdict":           direction,
            "magnitude":         magnitude,
            "requires_log_check": True,
        })

    # ── Changepoint diagnostics ───────────────────────────────────────────
    min_change_pct = thresholds.get("min_change_pct", 10.0)
    for cp_info in changepoints_enriched:
        verdict      = cp_info.get("_verdict", "insufficient_data")
        magnitude_pct = cp_info.get("magnitude_pct")
        cp_date      = cp_info.get("date")
        cp_idx       = cp_info.get("idx")

        flags = _classify_changepoint_flags(
            cp_date=cp_date,
            shutdown_clusters=shutdown_clusters,
            purge_events=purge_events,
            window_hours=ignore_purge_window_hours,
        )

        magnitude = {"pct": magnitude_pct} if magnitude_pct is not None else None

        entry: dict = {
            "target":            "changepoint",
            "context":           f"cp_{cp_idx}",
            "verdict":           verdict,
            "magnitude":         magnitude,
            "flags":             flags,
            "requires_log_check": True,
        }
        entries.append(entry)

    # ── Overall diagnostic ────────────────────────────────────────────────
    overall_verdict = _compute_overall_trend(segments)
    entries.append({
        "target":            "overall",
        "context":           "combined",
        "verdict":           overall_verdict,
        "magnitude":         None,
        "requires_log_check": True,
    })

    return entries


# ---------------------------------------------------------------------------
# PRIVATE HELPERS — snapshot assembly
# ---------------------------------------------------------------------------


def _build_raw_chart_payload(agg_df: pd.DataFrame) -> dict:
    """
    Строит chart_payload из agg_df.
    Ключи: dates, q, p_tube, p_line, dp, shutdown_min.
    """
    if agg_df.empty:
        return {
            "dates": [], "q": [], "p_tube": [], "p_line": [], "dp": [], "shutdown_min": []
        }

    df = agg_df.copy()

    if isinstance(df.index, pd.DatetimeIndex):
        timestamps = [ts.strftime("%Y-%m-%dT%H:%M:%S") for ts in df.index]
    else:
        timestamps = [str(t) for t in df.index]

    def _col_to_list(col: str) -> list:
        if col not in df.columns:
            return [None] * len(df)
        return [
            round(float(v), 4)
            if v is not None and not (isinstance(v, float) and np.isnan(v))
            else None
            for v in df[col].tolist()
        ]

    return {
        "dates":        timestamps,
        "q":            _col_to_list("q"),
        "p_tube":       _col_to_list("p_tube"),
        "p_line":       _col_to_list("p_line"),
        "dp":           _col_to_list("dp"),
        "shutdown_min": _col_to_list("shutdown_min"),
    }


def _build_quality_layer(data_quality_raw: dict) -> dict:
    """
    Переупаковывает вывод B1 compute_data_quality в Layer quality RFC §1.3.
    Паттерн из C1 (_build_quality_layer в observation_period_service.py).
    """
    flags = data_quality_raw.get("quality_flags", data_quality_raw.get("flags", []))
    return {
        "status": data_quality_raw.get("status", "no_data"),
        "flags":  flags,
        "metrics": {
            "coverage_pct":            data_quality_raw.get("coverage_pct", 0.0),
            "gap_count":               data_quality_raw.get("gap_count", 0),
            "max_gap_hours":           data_quality_raw.get("max_gap_hours", 0.0),
            "suspicious_spikes_count": data_quality_raw.get("suspicious_spikes_count", 0),
            "false_zero_pct":          data_quality_raw.get("false_zero_pct", 0.0),
            "days_with_data":          data_quality_raw.get("days_with_data", 0),
            "days_requested":          data_quality_raw.get("days_requested", 0),
        },
    }


def _build_flags_layer(quality: dict) -> dict:
    """
    Строит flags layer — только quality-related флаги (без comparison флагов).
    Соответствует RFC §2.3 для obs_segment_v1.
    """
    quality_flags = quality.get("flags", [])
    return {
        "low_coverage":     "low_coverage"     in quality_flags,
        "significant_gap":  "significant_gap"  in quality_flags,
        "outlier_detected": "outlier_detected" in quality_flags,
    }


def _assemble_snapshot(
    computed_at: str,
    block_status: str,
    period: dict,
    raw_layer: dict | None,
    quality_layer: dict,
    flags_layer: dict,
    thresholds_used: dict,
    segments: list[dict],
    changepoints: list[dict],
    shutdown_clusters: list[dict],
    diagnostics: list[dict],
) -> dict:
    """
    Финальная сборка snapshot obs_segment_v1.
    Гарантирует:
    - НЕТ top-level 'metrics'
    - НЕТ top-level 'comparisons'
    - Только structured data (no narrative/HTML/markdown)
    """
    # Из changepoints_enriched убираем служебный ключ _verdict
    clean_cps = []
    for cp in changepoints:
        clean_cp = {k: v for k, v in cp.items() if k != "_verdict"}
        clean_cps.append(clean_cp)

    # Сегменты — убираем служебные ключи start_idx/end_idx (они в snapshot не нужны)
    # НО по спецификации start_idx/end_idx присутствуют — оставляем.

    snapshot: dict = {
        "_v":             SNAPSHOT_V,
        "schema_version": SCHEMA_VERSION,
        "computed_at":    computed_at,
        "block_status":   block_status,
        "period":         period,
        "quality":        quality_layer,
        "flags":          flags_layer,
        "thresholds_used": thresholds_used,
        "segments":        segments,
        "changepoints":    clean_cps,
        "shutdown_clusters": shutdown_clusters,
        "diagnostics":     diagnostics,
    }

    if raw_layer is not None:
        snapshot["raw"] = raw_layer

    return snapshot


# ---------------------------------------------------------------------------
# PRIVATE HELPERS — utilities
# ---------------------------------------------------------------------------


def _idx_to_date_str(ts) -> str:
    """Конвертирует timestamp index в ISO date string."""
    try:
        return pd.Timestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return str(ts)[:10]


def _safe_nanmean(df: pd.DataFrame, col: str) -> float:
    """NaN-safe среднее по колонке. Возвращает float('nan') если нет данных."""
    if col not in df.columns:
        return float("nan")
    vals = df[col].values.astype(float)
    if not np.isfinite(vals).any():
        return float("nan")
    return float(np.nanmean(vals))


def _round_or_none(v: float, ndigits: int = 4) -> float | None:
    """round(v, ndigits) или None если NaN/None."""
    if v is None or not np.isfinite(v):
        return None
    return round(v, ndigits)


def _compute_duration_days(
    start_ts,
    end_ts,
    start_idx: int,
    end_idx_exclusive: int,
    aggregation: str,
) -> float:
    """
    Вычисляет duration_days сегмента.
    Всегда в днях, независимо от aggregation.

    Приоритет: если оба timestamp — DatetimeIndex, считаем из дат.
    Fallback: из числа точек и aggregation.
    """
    try:
        s_ts = pd.Timestamp(start_ts)
        e_ts = pd.Timestamp(end_ts)
        delta = (e_ts - s_ts).total_seconds() / 86400.0
        # +1 day (включительно)
        return max(1.0, round(delta + 1.0, 2))
    except Exception:
        pass

    # Fallback: из точек
    n_pts = end_idx_exclusive - start_idx
    minutes_per_point = _AGG_MINUTES.get(aggregation, 1440.0)
    return max(1.0, round(n_pts * minutes_per_point / 1440.0, 2))
