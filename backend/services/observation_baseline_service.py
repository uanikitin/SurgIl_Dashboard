"""
Observation Baseline Service — Phase C4.

Thin wrapper для preview-расчёта блока kind='observation_baseline'.
Согласно RFC observation_v1.0 (schema_version="1.0").

PUBLIC API: единственная функция — compute_baseline_preview().

НЕ пишет в БД. НЕ имеет side-effects. Только читает.
Использует B1 (observation_data_service.py).

Структура snapshot (RFC §2.1 — baseline kind):
  _v = "obs_baseline_v1"
  schema_version = "1.0"
  computed_at, block_status, period
  raw (опционально), metrics, quality, flags
  БЕЗ comparisons, БЕЗ diagnostics.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

SNAPSHOT_V = "obs_baseline_v1"
SCHEMA_VERSION = "1.0"

# Минимум дней с данными для расчёта
MIN_DAYS_DATA = 3


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------


def compute_baseline_preview(
    db: Session,
    well_id: int,
    d_from: "date | str",
    d_to: "date | str",
    *,
    sensor_source: str = "lora",
    comment: str | None = None,
    include_raw_chart: bool = False,
) -> dict:
    """
    Preview-сервис для kind='observation_baseline'.

    Возвращает snapshot kind='observation_baseline' (obs_baseline_v1).

    НЕ пишет в БД. НЕ имеет side-effects.

    Args:
        db: SQLAlchemy session (только чтение)
        well_id: id скважины (wells.id)
        d_from, d_to: даты периода (ISO string или date)
        sensor_source: источник данных (сейчас только "lora")
        comment: опциональный комментарий (не включается в snapshot)
        include_raw_chart: писать ли raw.chart_payload (массивы дат и значений)

    Returns:
        dict — snapshot obs_baseline_v1 с layers: raw?, metrics, quality, flags.
        БЕЗ comparisons, БЕЗ diagnostics.

    Raises:
        ValueError: при отсутствии well_id (поведение B1).
    """
    # Нормализация дат
    if isinstance(d_from, str):
        d_from = date.fromisoformat(d_from)
    if isinstance(d_to, str):
        d_to = date.fromisoformat(d_to)

    computed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    period_dict = {"from": d_from.isoformat(), "to": d_to.isoformat()}

    # ── B1: загрузка данных ──────────────────────────────────────────────
    from backend.services.observation_data_service import load_observation_data

    obs = load_observation_data(
        db=db,
        well_id=well_id,
        d_from=d_from,
        d_to=d_to,
        aggregation="daily",
        smooth_minute=True,
        include_customer_overlay=False,  # baseline не сравнивает с заказчиком
    )

    our_daily_df: pd.DataFrame = obs.our_df
    data_quality_raw: dict = obs.data_quality
    raw_minute_df: pd.DataFrame = obs.our_raw_minute_df

    # ── Layer 3: quality ─────────────────────────────────────────────────
    quality_layer = _build_quality_layer(data_quality_raw)

    # ── Ранний выход если данных нет ─────────────────────────────────────
    no_data = (data_quality_raw.get("status") == "no_data") or our_daily_df.empty
    if no_data:
        return _assemble_snapshot(
            computed_at=computed_at,
            block_status="no_data",
            period=period_dict,
            raw_layer=None,
            metrics_layer=_empty_metrics(),
            quality_layer=quality_layer,
            flags_layer=_empty_flags(),
        )

    # ── Layer 1: raw chart_payload ────────────────────────────────────────
    raw_layer = None
    if include_raw_chart:
        raw_layer = _build_raw_layer(our_daily_df, raw_minute_df)

    # ── Layer 2: metrics ─────────────────────────────────────────────────
    metrics_layer = _compute_own_metrics(our_daily_df, obs.our_meta)

    # ── Layer 6: flags ───────────────────────────────────────────────────
    flags_layer = _compute_flags(quality_layer)

    # ── block_status ─────────────────────────────────────────────────────
    days_with_data = data_quality_raw.get("days_with_data", 0)
    if days_with_data == 0:
        block_status = "no_data"
    elif days_with_data < MIN_DAYS_DATA:
        block_status = "insufficient_data"
    else:
        block_status = "ok"

    return _assemble_snapshot(
        computed_at=computed_at,
        block_status=block_status,
        period=period_dict,
        raw_layer=raw_layer,
        metrics_layer=metrics_layer,
        quality_layer=quality_layer,
        flags_layer=flags_layer,
    )


# ---------------------------------------------------------------------------
# PRIVATE HELPERS
# ---------------------------------------------------------------------------


def _build_quality_layer(data_quality_raw: dict) -> dict:
    """
    Переупаковывает вывод B1 compute_data_quality в Layer 3 формат RFC §1.3.
    Реиспользует паттерн из observation_period_service._build_quality_layer.
    """
    flags = data_quality_raw.get("quality_flags", data_quality_raw.get("flags", []))
    return {
        "status": data_quality_raw.get("status", "no_data"),
        "flags": flags,
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


def _build_raw_layer(
    our_daily_df: pd.DataFrame,
    raw_minute_df: pd.DataFrame,
) -> dict:
    """Строит Layer 1 raw.chart_payload из суточного агрегата."""
    if our_daily_df.empty:
        return {
            "chart_payload": {
                "dates": [], "p_tube": [], "p_line": [], "dp": [], "q": [], "shutdown_hours": []
            }
        }

    df = our_daily_df.copy()

    if isinstance(df.index, pd.DatetimeIndex):
        dates = [ts.strftime("%Y-%m-%d") for ts in df.index]
    else:
        dates = [str(d) for d in df.index]

    def _to_list(col: str) -> list:
        if col not in df.columns:
            return [None] * len(df)
        return [
            round(float(v), 4) if (v is not None and not (isinstance(v, float) and np.isnan(v)))
            else None
            for v in df[col].tolist()
        ]

    if "shutdown_min" in df.columns:
        shutdown_hours = [
            round(float(v) / 60.0, 2) if (v is not None and not (isinstance(v, float) and np.isnan(v)))
            else None
            for v in df["shutdown_min"].tolist()
        ]
    else:
        shutdown_hours = [None] * len(df)

    return {
        "chart_payload": {
            "dates":          dates,
            "p_tube":         _to_list("p_tube"),
            "p_line":         _to_list("p_line"),
            "dp":             _to_list("dp"),
            "q":              _to_list("q"),
            "shutdown_hours": shutdown_hours,
        }
    }


def _compute_own_metrics(our_daily_df: pd.DataFrame, pipeline_meta: dict) -> dict:
    """
    Вычисляет Layer 2 metrics из суточного агрегата.
    Реиспользует паттерн из observation_period_service._compute_own_metrics.
    """
    if our_daily_df.empty:
        return _empty_metrics()

    df = our_daily_df.copy()

    def _metric_stats(col: str) -> dict:
        if col not in df.columns:
            return _empty_metric_stats()
        series = df[col].dropna()
        if len(series) < MIN_DAYS_DATA:
            return _empty_metric_stats()

        mean_v   = float(series.mean())
        median_v = float(series.median())
        min_v    = float(series.min())
        max_v    = float(series.max())
        std_v    = float(series.std()) if len(series) > 1 else 0.0
        cv_v     = round(std_v / mean_v * 100.0, 4) if mean_v != 0.0 else None

        slope_v, direction = _compute_slope_direction(series)

        return {
            "mean":      round(mean_v, 4),
            "median":    round(median_v, 4),
            "min":       round(min_v, 4),
            "max":       round(max_v, 4),
            "std":       round(std_v, 4),
            "cv":        round(cv_v, 4) if cv_v is not None else None,
            "slope":     round(slope_v, 6) if slope_v is not None else None,
            "direction": direction,
        }

    # downtime из shutdown_min
    downtime = _compute_downtime_metrics(df, pipeline_meta)

    # purge из pipeline_meta
    purge_cycles = pipeline_meta.get("purge_cycles", [])
    purge_count: int | None = len(purge_cycles) if isinstance(purge_cycles, list) else None

    return {
        "p_tube":             _metric_stats("p_tube"),
        "p_line":             _metric_stats("p_line"),
        "dp":                 _metric_stats("dp"),
        "q":                  _metric_stats("q"),
        "downtime":           downtime,
        "purge_events_count": purge_count,
    }


def _empty_metric_stats() -> dict:
    return {
        "mean":      None,
        "median":    None,
        "min":       None,
        "max":       None,
        "std":       None,
        "cv":        None,
        "slope":     None,
        "direction": "insufficient_data",
    }


def _empty_metrics() -> dict:
    return {
        "p_tube": _empty_metric_stats(),
        "p_line": _empty_metric_stats(),
        "dp":     _empty_metric_stats(),
        "q":      _empty_metric_stats(),
        "downtime": {
            "total_hours":            None,
            "events_count":           None,
            "max_event_hours":        None,
            "downtime_pct_of_period": None,
        },
        "purge_events_count": None,
    }


def _compute_downtime_metrics(df: pd.DataFrame, pipeline_meta: dict) -> dict:
    """Считает метрики простоя из суточного df."""
    if "shutdown_min" not in df.columns or df["shutdown_min"].isna().all():
        return {
            "total_hours":            None,
            "events_count":           None,
            "max_event_hours":        None,
            "downtime_pct_of_period": None,
        }

    total_shutdown_min = float(df["shutdown_min"].fillna(0.0).sum())
    total_hours = round(total_shutdown_min / 60.0, 2)

    period_hours = len(df) * 24.0
    downtime_pct = round(total_hours / period_hours * 100.0, 2) if period_hours > 0 else None

    downtime_periods = pipeline_meta.get("downtime_periods", pd.DataFrame())
    events_count: int | None = None
    max_event_hours: float | None = None

    if isinstance(downtime_periods, pd.DataFrame) and not downtime_periods.empty:
        events_count = len(downtime_periods)
        if "duration_hours" in downtime_periods.columns:
            max_event_hours = round(float(downtime_periods["duration_hours"].max()), 2)
        elif "duration_min" in downtime_periods.columns:
            max_event_hours = round(float(downtime_periods["duration_min"].max()) / 60.0, 2)

    return {
        "total_hours":            total_hours,
        "events_count":           events_count,
        "max_event_hours":        max_event_hours,
        "downtime_pct_of_period": downtime_pct,
    }


def _compute_slope_direction(series: pd.Series) -> tuple[float | None, str]:
    """Линейная регрессия для slope и direction."""
    if len(series) < 3:
        return None, "insufficient_data"

    x = np.arange(len(series), dtype=float)
    y = series.values.astype(float)

    valid = ~np.isnan(y)
    if valid.sum() < 3:
        return None, "insufficient_data"

    x_v = x[valid]
    y_v = y[valid]

    try:
        coeffs = np.polyfit(x_v, y_v, 1)
        slope = float(coeffs[0])
    except (np.linalg.LinAlgError, ValueError):
        return None, "insufficient_data"

    # Универсальный порог для baseline
    threshold = 0.05
    if abs(slope) < threshold:
        direction = "stable"
    elif slope > 0:
        direction = "rising"
    else:
        direction = "falling"

    return slope, direction


def _compute_flags(quality_layer: dict) -> dict:
    """Строит Layer 6 flags для baseline (без comparisons-флагов)."""
    quality_flags_list: list = quality_layer.get("flags", [])
    return {
        "low_coverage":     "low_coverage" in quality_flags_list,
        "significant_gap":  "significant_gap" in quality_flags_list,
        "outlier_detected": "outlier_detected" in quality_flags_list,
    }


def _empty_flags() -> dict:
    return {
        "low_coverage":     False,
        "significant_gap":  False,
        "outlier_detected": False,
    }


def _assemble_snapshot(
    computed_at: str,
    block_status: str,
    period: dict,
    raw_layer: dict | None,
    metrics_layer: dict,
    quality_layer: dict,
    flags_layer: dict,
) -> dict:
    """Собирает финальный snapshot dict согласно RFC §2.1 (baseline kind)."""
    snapshot: dict[str, Any] = {
        "_v":             SNAPSHOT_V,
        "schema_version": SCHEMA_VERSION,
        "computed_at":    computed_at,
        "block_status":   block_status,
        "period":         period,
        "metrics":        metrics_layer,
        "quality":        quality_layer,
        "flags":          flags_layer,
    }
    if raw_layer is not None:
        snapshot["raw"] = raw_layer
    return snapshot
