"""
pressure_mask_service.py — Применение масок коррекции давления.

Маски НЕ изменяют pressure_raw. Коррекции применяются in-memory к DataFrame.
Используется как в chart pipeline, так и в flow rate pipeline.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import text

from backend.db import engine as pg_engine

log = logging.getLogger(__name__)


# ──────────────────── Загрузка масок из БД ────────────────────


def load_active_masks(
    well_id: int,
    dt_start: datetime,
    dt_end: datetime,
) -> list:
    """
    Загружает активные маски для скважины, пересекающиеся с периодом.
    Возвращает список объектов-словарей (не ORM, чтобы не тянуть сессию).
    """
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT id, well_id, problem_type, affected_sensor,
                       correction_method, dt_start, dt_end,
                       manual_delta_p, reason
                FROM pressure_mask
                WHERE well_id = :well_id
                  AND is_active = true
                  AND dt_start < :period_end
                  AND dt_end > :period_start
                ORDER BY dt_start
            """),
            {
                "well_id": well_id,
                "period_start": dt_start,
                "period_end": dt_end,
            },
        ).fetchall()

    masks = []
    for r in rows:
        masks.append({
            "id": r[0],
            "well_id": r[1],
            "problem_type": r[2],
            "affected_sensor": r[3],
            "correction_method": r[4],
            "dt_start": r[5],
            "dt_end": r[6],
            "manual_delta_p": r[7],
            "reason": r[8],
        })
    return masks


# ──────────────────── Применение масок ────────────────────


def apply_masks(
    df: pd.DataFrame,
    masks: list[dict],
) -> tuple[pd.DataFrame, int]:
    """
    Применяет маски коррекции к DataFrame давления.

    Parameters
    ----------
    df : DataFrame с индексом measured_at (UTC), колонками p_tube, p_line.
    masks : список масок (dict) из load_active_masks().

    Returns
    -------
    (corrected_df, total_corrected_points)

    Оригинальный df не изменяется (делается копия).
    """
    if not masks or df.empty:
        return df, 0

    df = df.copy()
    total_corrected = 0

    for mask in masks:
        affected = mask["affected_sensor"]  # 'p_tube' or 'p_line'
        method = mask["correction_method"]
        dt_start = mask["dt_start"]
        dt_end = mask["dt_end"]

        # Маска по времени
        time_mask = (df.index >= dt_start) & (df.index <= dt_end)
        n_affected = int(time_mask.sum())
        if n_affected == 0:
            continue

        total_corrected += n_affected

        if method == "median_1d":
            _apply_median(df, time_mask, affected, dt_start, window_days=1)

        elif method == "median_3d":
            _apply_median(df, time_mask, affected, dt_start, window_days=3)

        elif method == "delta_reconstruct":
            _apply_delta_reconstruct(
                df, time_mask, affected, dt_start,
                manual_delta_p=mask.get("manual_delta_p"),
            )

        elif method == "interpolate":
            df.loc[time_mask, affected] = np.nan
            df[affected] = df[affected].interpolate(method="linear")

        elif method == "exclude":
            df.loc[time_mask, affected] = np.nan

        log.debug(
            "mask %s: method=%s sensor=%s %d points",
            mask["id"], method, affected, n_affected,
        )

    # Заполняем оставшиеся NaN (от exclude / interpolate на краях)
    for col in ("p_tube", "p_line"):
        if col in df.columns:
            df[col] = df[col].ffill().bfill()

    return df, total_corrected


def _apply_median(
    df: pd.DataFrame,
    time_mask: pd.Series,
    affected: str,
    dt_start: datetime,
    window_days: int,
) -> None:
    """Заменяет affected_sensor медианой за window_days ДО начала проблемы."""
    window_start = dt_start - timedelta(days=window_days)
    pre_data = df.loc[
        (df.index >= window_start) & (df.index < dt_start),
        affected,
    ]
    if pre_data.empty or pre_data.isna().all():
        # Нет данных до проблемы — fallback к общей медиане
        median_val = df[affected].median()
    else:
        median_val = pre_data.median()

    if pd.notna(median_val):
        df.loc[time_mask, affected] = median_val


def _apply_delta_reconstruct(
    df: pd.DataFrame,
    time_mask: pd.Series,
    affected: str,
    dt_start: datetime,
    manual_delta_p: Optional[float] = None,
) -> None:
    """
    Восстанавливает плохой датчик через хороший + медиана ΔP.

    ΔP = p_tube - p_line (всегда).
    Если affected='p_line' → p_line = p_tube - median_ΔP
    Если affected='p_tube' → p_tube = p_line + median_ΔP
    """
    if manual_delta_p is not None:
        median_dp = manual_delta_p
    else:
        # Считаем медиану ΔP за 1 день ДО проблемы
        pre_start = dt_start - timedelta(days=1)
        pre_data = df.loc[
            (df.index >= pre_start) & (df.index < dt_start)
        ]
        if pre_data.empty:
            log.warning("delta_reconstruct: no pre-data, skipping mask")
            return
        dp = pre_data["p_tube"] - pre_data["p_line"]
        median_dp = dp.median()
        if pd.isna(median_dp):
            log.warning("delta_reconstruct: median_dp is NaN, skipping mask")
            return

    good_col = "p_tube" if affected == "p_line" else "p_line"

    if affected == "p_line":
        df.loc[time_mask, "p_line"] = df.loc[time_mask, good_col] - median_dp
    else:
        df.loc[time_mask, "p_tube"] = df.loc[time_mask, good_col] + median_dp


# ──────────────────── Авто-детекция аномалий ────────────────────


def detect_anomalies(
    well_id: int,
    days: int = 30,
    dp_threshold_sigma: float = 3.0,
    min_duration_min: int = 30,
) -> list[dict]:
    """
    Базовая эвристика: ищет участки с аномальным ΔP.

    Алгоритм:
    1. Загрузить почасовые данные
    2. Рассчитать ΔP = p_tube - p_line
    3. Скользящая медиана ΔP (окно 6 часов)
    4. MAD (median absolute deviation)
    5. Участки где |ΔP - rolling_median| > threshold * MAD > min_duration
    6. Определить affected_sensor по направлению отклонения

    Returns
    -------
    list of {dt_start, dt_end, affected_sensor, confidence, suggested_method, dp_deviation}
    """
    dt_end = datetime.utcnow()
    dt_start = dt_end - timedelta(days=days)

    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT measured_at,
                       NULLIF(p_tube, 0) AS p_tube,
                       NULLIF(p_line, 0) AS p_line
                FROM pressure_raw
                WHERE well_id = :well_id
                  AND measured_at >= :start
                  AND measured_at <= :end
                ORDER BY measured_at
            """),
            {"well_id": well_id, "start": dt_start, "end": dt_end},
        ).fetchall()

    if len(rows) < 100:
        return []

    df = pd.DataFrame(rows, columns=["measured_at", "p_tube", "p_line"])
    df["measured_at"] = pd.to_datetime(df["measured_at"])
    df = df.set_index("measured_at").sort_index()

    # Ресэмплинг до 5-минутного интервала для скорости
    df = df.resample("5min").median().dropna(how="all")
    if df.empty:
        return []

    df["dp"] = df["p_tube"] - df["p_line"]

    # Скользящая медиана ΔP (окно 6 часов = 72 точки при 5 мин)
    window = 72
    df["dp_median"] = df["dp"].rolling(window=window, center=True, min_periods=10).median()
    df["dp_dev"] = (df["dp"] - df["dp_median"]).abs()

    # MAD — robust estimator of standard deviation
    global_mad = df["dp_dev"].median() * 1.4826  # scale to σ
    if global_mad < 0.1:
        global_mad = 0.1  # минимальный порог

    threshold = dp_threshold_sigma * global_mad

    # Маска аномалий
    df["is_anomaly"] = df["dp_dev"] > threshold

    # Группировка последовательных аномалий
    anomalies = []
    in_anomaly = False
    start_idx = None

    for idx, row in df.iterrows():
        if row["is_anomaly"] and not in_anomaly:
            in_anomaly = True
            start_idx = idx
        elif not row["is_anomaly"] and in_anomaly:
            in_anomaly = False
            duration = (idx - start_idx).total_seconds() / 60
            if duration >= min_duration_min:
                anomalies.append((start_idx, idx))

    # Закрыть последнюю аномалию
    if in_anomaly and start_idx is not None:
        last_idx = df.index[-1]
        duration = (last_idx - start_idx).total_seconds() / 60
        if duration >= min_duration_min:
            anomalies.append((start_idx, last_idx))

    # Определить affected_sensor для каждой аномалии
    results = []
    for a_start, a_end in anomalies:
        segment = df.loc[a_start:a_end]
        pre_start = a_start - timedelta(hours=6)
        pre_data = df.loc[pre_start:a_start]

        if pre_data.empty:
            continue

        pre_tube_median = pre_data["p_tube"].median()
        pre_line_median = pre_data["p_line"].median()
        seg_tube_median = segment["p_tube"].median()
        seg_line_median = segment["p_line"].median()

        tube_change = abs(seg_tube_median - pre_tube_median) if pd.notna(pre_tube_median) and pd.notna(seg_tube_median) else 0
        line_change = abs(seg_line_median - pre_line_median) if pd.notna(pre_line_median) and pd.notna(seg_line_median) else 0

        if tube_change > line_change:
            affected = "p_tube"
        else:
            affected = "p_line"

        # Confidence: насколько один датчик изменился больше другого
        total_change = tube_change + line_change
        if total_change > 0:
            confidence = round(max(tube_change, line_change) / total_change, 2)
        else:
            confidence = 0.5

        duration_hours = (a_end - a_start).total_seconds() / 3600
        suggested_method = "delta_reconstruct" if duration_hours > 2 else "interpolate"

        dp_deviation = round(float(segment["dp_dev"].mean()), 2)

        results.append({
            "dt_start": a_start.isoformat(),
            "dt_end": a_end.isoformat(),
            "affected_sensor": affected,
            "confidence": confidence,
            "suggested_method": suggested_method,
            "duration_hours": round(duration_hours, 1),
            "dp_deviation": dp_deviation,
        })

    log.info(
        "[detect_anomalies] well=%d days=%d found=%d anomalies",
        well_id, days, len(results),
    )
    return results
