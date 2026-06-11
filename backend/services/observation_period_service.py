"""
Observation Period Service — Phase C1.

Реализует preview-расчёт для блока kind='observation_period' согласно
RFC observation_v1.0 (schema_version="1.0").

PUBLIC API: единственная функция — compute_period_preview().
Все helper-функции приватные (prefix `_`).

НЕ пишет в БД. НЕ имеет side-effects. Только читает.
Использует B1 (observation_data_service.py):
  - load_observation_data()   — main entry для own data + customer overlay + quality
  - align_our_and_customer()  — для построения daily_table
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

SNAPSHOT_V = "obs_period_v1"
SCHEMA_VERSION = "1.0"

# Пороги «существенно» для diagnostics vs_b1 (RFC §5 + A3)
THRESHOLD_PCT: dict[str, float] = {
    "p_tube":       5.0,
    "p_line":       5.0,
    "dp_pct":       8.0,
    "dp_abs":       0.3,
    "q":           10.0,
    "shutdown_pct": 5.0,
    "cv":           3.0,
}

# Минимум дней с данными для расчёта (insufficient_data порог)
MIN_DAYS: dict[str, int] = {
    "p_tube":   3,
    "p_line":   3,
    "dp":       3,
    "q":        5,
    "shutdown": 5,
    "cv":       7,
}

# MAPE пороги для vs_customer: (match_max_pct, partial_max_pct)
MAPE_THRESHOLDS: dict[str, tuple[float, float]] = {
    "p_tube": (5.0, 15.0),
    "p_line": (5.0, 15.0),
    "q":      (10.0, 25.0),
}

# Slope thresholds: |slope| < threshold → direction="stable"
SLOPE_STABLE_THRESHOLD: dict[str, float] = {
    "p_tube": 0.05,   # атм/день
    "p_line": 0.05,
    "dp":     0.05,
    "q":      50.0,   # тыс.м³/сут/день
}

# Период baseline считается устаревшим, если разнесён от текущего > N дней
BASELINE_MISMATCH_DAYS = 90

# Минимум дней заказчика для vs_customer (иначе insufficient_data)
MIN_CUSTOMER_DAYS = 5

# Порог short_intersection: < N дней пересечения
SHORT_INTERSECTION_DAYS = 5

# Порог diff_pct для data_status='invalid' в daily_table
DAILY_INVALID_THRESHOLD_PCT = 25.0


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------


def compute_period_preview(
    db: Session,
    well_id: int,
    d_from: "date | str",
    d_to: "date | str",
    *,
    baseline_block_id: int | None = None,
    customer_period: dict | None = None,
    include_raw_chart: bool = True,
) -> dict:
    """
    Preview-сервис для kind='observation_period'.

    Возвращает полный snapshot observation_v1.0 / obs_period_v1.

    Не пишет в БД. Не имеет side-effects.

    Args:
        db: SQLAlchemy session (для чтения baseline-блока из customer_report_block)
        well_id: id скважины (wells.id)
        d_from, d_to: даты периода (ISO string или date)
        baseline_block_id: id блока kind='observation_baseline' для сравнения с B1.
                          None → comparison.with_b1.status='no_baseline'
        customer_period: {"from": ..., "to": ..., "use_same_as_analysis": bool} | None.
                        None или use_same_as_analysis=True → берётся analysis period
        include_raw_chart: писать ли raw.chart_payload (массивы дат и значений)

    Returns:
        dict — snapshot со всеми 6 layers + meta. См. RFC §1.3.

    Raises:
        ValueError только при отсутствии well_id (B1 поведение).
        Все остальные edge cases отражаются в quality.status / block_status / *.status полях.
    """
    # Нормализация дат
    if isinstance(d_from, str):
        d_from = date.fromisoformat(d_from)
    if isinstance(d_to, str):
        d_to = date.fromisoformat(d_to)

    computed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    period_dict = {"from": d_from.isoformat(), "to": d_to.isoformat()}

    # ── B1: загрузка данных ──────────────────────────────────────────────
    from backend.services.observation_data_service import (
        load_observation_data,
        align_our_and_customer,
    )

    obs = load_observation_data(
        db=db,
        well_id=well_id,
        d_from=d_from,
        d_to=d_to,
        aggregation="daily",
        smooth_minute=True,
        include_customer_overlay=True,
    )

    our_daily_df: pd.DataFrame = obs.our_df       # агрегат суточный
    customer_df: pd.DataFrame = obs.customer_df   # overlay УзКорГаз
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
            comparisons_layer=_empty_comparisons(
                baseline_block_id=baseline_block_id,
                customer_period_dict=_resolve_customer_period(customer_period, d_from, d_to),
            ),
            diagnostics_layer=[
                {
                    "target": "overall",
                    "context": "combined",
                    "verdict": "insufficient_data",
                    "magnitude": None,
                    "requires_log_check": True,
                }
            ],
            flags_layer=_empty_flags(),
        )

    # ── Layer 1: raw chart_payload ────────────────────────────────────────
    raw_layer = None
    if include_raw_chart:
        raw_layer = _build_raw_layer(our_daily_df, raw_minute_df)

    # ── Layer 2: metrics ─────────────────────────────────────────────────
    metrics_layer = _compute_own_metrics(our_daily_df, obs.our_meta)

    # ── Baseline блок ────────────────────────────────────────────────────
    baseline_block = _load_baseline_block(db, baseline_block_id)

    # ── Layer 4: comparisons ─────────────────────────────────────────────
    cust_period_dict = _resolve_customer_period(customer_period, d_from, d_to)

    with_b1 = _compute_comparison_with_b1(
        current_metrics=metrics_layer,
        baseline_block=baseline_block,
        baseline_block_id=baseline_block_id,
        current_period=period_dict,
    )

    # B1: align для daily_table
    aligned_df: pd.DataFrame = pd.DataFrame()
    if not customer_df.empty:
        aligned_df = align_our_and_customer(our_daily_df, customer_df, prefer_q_working=True)

    with_customer = _compute_comparison_with_customer(
        our_daily_df=our_daily_df,
        customer_df=customer_df,
        aligned_df=aligned_df,
        customer_period_dict=cust_period_dict,
        analysis_period=period_dict,
    )

    comparisons_layer = {
        "with_b1": with_b1,
        "with_customer": with_customer,
    }

    # ── Layer 5: diagnostics ─────────────────────────────────────────────
    diagnostics_layer = _build_diagnostics(
        metrics_layer=metrics_layer,
        with_b1=with_b1,
        with_customer=with_customer,
        our_daily_df=our_daily_df,
    )

    # ── Layer 6: flags ───────────────────────────────────────────────────
    flags_layer = _compute_flags(
        quality_layer=quality_layer,
        with_b1=with_b1,
        with_customer=with_customer,
        baseline_block=baseline_block,
        current_period=period_dict,
    )

    # ── block_status ─────────────────────────────────────────────────────
    days_with_data = data_quality_raw.get("days_with_data", 0)
    days_requested = data_quality_raw.get("days_requested", 1)
    if days_with_data == 0:
        block_status = "no_data"
    elif days_with_data < min(MIN_DAYS.values()):
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
        comparisons_layer=comparisons_layer,
        diagnostics_layer=diagnostics_layer,
        flags_layer=flags_layer,
    )


# ---------------------------------------------------------------------------
# PRIVATE HELPERS
# ---------------------------------------------------------------------------


def _build_quality_layer(data_quality_raw: dict) -> dict:
    """
    Переупаковывает вывод B1 compute_data_quality в Layer 3 формат RFC §1.3.
    Ключ 'quality_flags' из B1 → 'flags' в snapshot.
    """
    flags = data_quality_raw.get("quality_flags", data_quality_raw.get("flags", []))
    return {
        "status": data_quality_raw.get("status", "no_data"),
        "flags": flags,
        "metrics": {
            "coverage_pct":           data_quality_raw.get("coverage_pct", 0.0),
            "gap_count":              data_quality_raw.get("gap_count", 0),
            "max_gap_hours":          data_quality_raw.get("max_gap_hours", 0.0),
            "suspicious_spikes_count": data_quality_raw.get("suspicious_spikes_count", 0),
            "false_zero_pct":         data_quality_raw.get("false_zero_pct", 0.0),
            "days_with_data":         data_quality_raw.get("days_with_data", 0),
            "days_requested":         data_quality_raw.get("days_requested", 0),
        },
    }


def _build_raw_layer(
    our_daily_df: pd.DataFrame,
    raw_minute_df: pd.DataFrame,
) -> dict:
    """
    Строит Layer 1 raw.chart_payload из суточного агрегата.
    Использует суточный df для дат/значений в chart_payload.
    """
    if our_daily_df.empty:
        return {"chart_payload": {
            "dates": [], "p_tube": [], "p_line": [], "dp": [], "q": [], "shutdown_hours": []
        }}

    df = our_daily_df.copy()

    # Нормализуем индекс в строки дат
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

    # shutdown_hours = shutdown_min / 60
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

    Для каждого из p_tube, p_line, dp, q — считает:
    mean, median, min, max, std, cv, slope, direction.

    downtime: total_hours, events_count, max_event_hours, downtime_pct_of_period.
    purge_events_count: из pipeline_meta.
    """
    if our_daily_df.empty:
        return _empty_metrics()

    df = our_daily_df.copy()

    def _metric_stats(col: str, key: str) -> dict:
        if col not in df.columns:
            return _empty_metric_stats()
        series = df[col].dropna()
        if len(series) < MIN_DAYS.get(key, 3):
            return _empty_metric_stats()

        mean_v  = float(series.mean())
        median_v = float(series.median())
        min_v   = float(series.min())
        max_v   = float(series.max())
        std_v   = float(series.std()) if len(series) > 1 else 0.0
        cv_v    = round(std_v / mean_v * 100.0, 4) if mean_v != 0.0 else None

        slope_v, direction = _compute_slope_direction(series, key)

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

    p_tube_stats = _metric_stats("p_tube", "p_tube")
    p_line_stats = _metric_stats("p_line", "p_line")
    dp_stats     = _metric_stats("dp",     "dp")
    q_stats      = _metric_stats("q",      "q")

    # downtime из shutdown_min
    downtime = _compute_downtime_metrics(df, pipeline_meta)

    # purge из pipeline_meta
    purge_cycles = pipeline_meta.get("purge_cycles", [])
    purge_count: int | None = len(purge_cycles) if isinstance(purge_cycles, list) else None

    return {
        "p_tube":             p_tube_stats,
        "p_line":             p_line_stats,
        "dp":                 dp_stats,
        "q":                  q_stats,
        "downtime":           downtime,
        "purge_events_count": purge_count,
    }


def _empty_metric_stats() -> dict:
    """Возвращает пустую структуру метрик для одного показателя."""
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
    """Полностью пустой Layer 2 metrics."""
    return {
        "p_tube": _empty_metric_stats(),
        "p_line": _empty_metric_stats(),
        "dp":     _empty_metric_stats(),
        "q":      _empty_metric_stats(),
        "downtime": {
            "total_hours":          None,
            "events_count":         None,
            "max_event_hours":      None,
            "downtime_pct_of_period": None,
        },
        "purge_events_count": None,
    }


def _compute_downtime_metrics(df: pd.DataFrame, pipeline_meta: dict) -> dict:
    """
    Считает метрики простоя из суточного df (shutdown_min).
    Дополнительно использует pipeline_meta["downtime_periods"] для events_count / max_event_hours.
    """
    if "shutdown_min" not in df.columns or df["shutdown_min"].isna().all():
        return {
            "total_hours":          None,
            "events_count":         None,
            "max_event_hours":      None,
            "downtime_pct_of_period": None,
        }

    total_shutdown_min = float(df["shutdown_min"].fillna(0.0).sum())
    total_hours = round(total_shutdown_min / 60.0, 2)

    # Период в часах
    period_hours = len(df) * 24.0  # суточные строки → 24ч каждая
    downtime_pct = round(total_hours / period_hours * 100.0, 2) if period_hours > 0 else None

    # Данные о событиях из pipeline_meta
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


def _compute_slope_direction(
    series: pd.Series,
    metric_key: str,
) -> tuple[float | None, str]:
    """
    Линейная регрессия по индексу (дням) для вычисления slope и direction.

    Возвращает (slope_per_day, direction_str).
    direction: 'rising' | 'falling' | 'stable' | 'insufficient_data'.
    """
    if len(series) < 3:
        return None, "insufficient_data"

    x = np.arange(len(series), dtype=float)
    y = series.values.astype(float)

    # Убираем NaN
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

    threshold = SLOPE_STABLE_THRESHOLD.get(metric_key, 0.05)
    if abs(slope) < threshold:
        direction = "stable"
    elif slope > 0:
        direction = "rising"
    else:
        direction = "falling"

    return slope, direction


def _load_baseline_block(
    db: Session,
    baseline_block_id: int | None,
) -> dict | None:
    """
    Загружает baseline-блок из customer_report_block по id.

    Фильтр: kind='observation_baseline' AND params->>'source'='observation'.
    Если не найден → None (caller выставит status='baseline_corrupted').
    Возвращает: {"block_id": ..., "period": ..., "metrics": ..., "_v": ...}
    """
    if baseline_block_id is None:
        return None

    try:
        from sqlalchemy import text

        row = db.execute(
            text(
                """
                SELECT id, kind, params, data_snapshot
                FROM customer_report_block
                WHERE id = :bid
                  AND kind = 'observation_baseline'
                  AND (params->>'source' = 'observation' OR params->>'chapter' = 'observation')
                """
            ),
            {"bid": baseline_block_id},
        ).fetchone()
    except Exception as exc:
        log.warning("[obs_period] _load_baseline_block DB error: %s", exc)
        return None

    if row is None:
        log.info(
            "[obs_period] baseline_block_id=%d not found or wrong kind",
            baseline_block_id,
        )
        return None

    snapshot = row[3]  # data_snapshot (JSONB / dict)
    if not snapshot or not isinstance(snapshot, dict):
        log.info(
            "[obs_period] baseline_block_id=%d has empty data_snapshot",
            baseline_block_id,
        )
        return None

    params = row[2] or {}
    period = params.get("period") or snapshot.get("period")
    metrics = snapshot.get("metrics")
    _v = snapshot.get("_v", "")

    return {
        "block_id":  row[0],
        "period":    period,
        "metrics":   metrics,
        "_v":        _v,
        "snapshot":  snapshot,
    }


def _compute_comparison_with_b1(
    current_metrics: dict,
    baseline_block: dict | None,
    baseline_block_id: int | None,
    current_period: dict,
) -> dict:
    """
    Строит comparisons.with_b1.

    Если baseline_block_id is None → status='no_baseline'.
    Если baseline_block is None (не найден) → status='baseline_corrupted'.
    Иначе → status='ok' или 'insufficient_overlap'.
    """
    if baseline_block_id is None:
        return {
            "status":           "no_baseline",
            "baseline_block_id": None,
            "baseline_period":  None,
            "deltas":           None,
        }

    if baseline_block is None:
        return {
            "status":           "baseline_corrupted",
            "baseline_block_id": baseline_block_id,
            "baseline_period":  None,
            "deltas":           None,
        }

    baseline_metrics = baseline_block.get("metrics")
    baseline_period  = baseline_block.get("period")

    if not baseline_metrics:
        return {
            "status":           "baseline_corrupted",
            "baseline_block_id": baseline_block_id,
            "baseline_period":  baseline_period,
            "deltas":           None,
        }

    # Вычисляем deltas
    deltas = _compute_deltas(current_metrics, baseline_metrics)

    return {
        "status":           "ok",
        "baseline_block_id": baseline_block_id,
        "baseline_period":  baseline_period,
        "deltas":           deltas,
    }


def _compute_deltas(current_metrics: dict, baseline_metrics: dict) -> dict:
    """
    Вычисляет dict deltas между текущими метриками и baseline.

    Поля: p_tube_mean, p_line_mean, dp_mean, q_mean, shutdown_pct, cv_p_tube.
    Каждое поле: {"abs": ..., "pct": ...} или {"abs": ...} (для shutdown_pct, cv_p_tube).
    """

    def _safe_delta(
        curr_v: float | None, base_v: float | None
    ) -> tuple[float | None, float | None]:
        if curr_v is None or base_v is None:
            return None, None
        abs_d = round(curr_v - base_v, 4)
        pct_d = round((curr_v - base_v) / base_v * 100.0, 2) if base_v != 0.0 else None
        return abs_d, pct_d

    def _get_mean(m: dict, key: str) -> float | None:
        sub = m.get(key)
        if not sub or not isinstance(sub, dict):
            return None
        v = sub.get("mean")
        return float(v) if v is not None else None

    def _get_cv(m: dict, key: str) -> float | None:
        sub = m.get(key)
        if not sub or not isinstance(sub, dict):
            return None
        v = sub.get("cv")
        return float(v) if v is not None else None

    def _get_downtime_pct(m: dict) -> float | None:
        dt = m.get("downtime")
        if not dt or not isinstance(dt, dict):
            return None
        v = dt.get("downtime_pct_of_period")
        return float(v) if v is not None else None

    p_tube_abs, p_tube_pct = _safe_delta(
        _get_mean(current_metrics, "p_tube"),
        _get_mean(baseline_metrics, "p_tube"),
    )
    p_line_abs, p_line_pct = _safe_delta(
        _get_mean(current_metrics, "p_line"),
        _get_mean(baseline_metrics, "p_line"),
    )
    dp_abs, dp_pct = _safe_delta(
        _get_mean(current_metrics, "dp"),
        _get_mean(baseline_metrics, "dp"),
    )
    q_abs, q_pct = _safe_delta(
        _get_mean(current_metrics, "q"),
        _get_mean(baseline_metrics, "q"),
    )
    shutdown_abs, _ = _safe_delta(
        _get_downtime_pct(current_metrics),
        _get_downtime_pct(baseline_metrics),
    )
    cv_abs, _ = _safe_delta(
        _get_cv(current_metrics, "p_tube"),
        _get_cv(baseline_metrics, "p_tube"),
    )

    return {
        "p_tube_mean":  {"abs": p_tube_abs, "pct": p_tube_pct},
        "p_line_mean":  {"abs": p_line_abs, "pct": p_line_pct},
        "dp_mean":      {"abs": dp_abs,     "pct": dp_pct},
        "q_mean":       {"abs": q_abs,      "pct": q_pct},
        "shutdown_pct": {"abs": shutdown_abs},
        "cv_p_tube":    {"abs": cv_abs},
    }


def _resolve_customer_period(
    customer_period: dict | None,
    analysis_from: date,
    analysis_to: date,
) -> dict:
    """
    Определяет период данных заказчика для сравнения.

    Если customer_period is None или use_same_as_analysis=True →
    возвращает тот же период, что и анализ.
    Иначе парсит customer_period['from'] / customer_period['to'].
    """
    if customer_period is None:
        return {"from": analysis_from.isoformat(), "to": analysis_to.isoformat()}

    use_same = customer_period.get("use_same_as_analysis", True)
    if use_same:
        return {"from": analysis_from.isoformat(), "to": analysis_to.isoformat()}

    raw_from = customer_period.get("from")
    raw_to   = customer_period.get("to")

    if not raw_from or not raw_to:
        return {"from": analysis_from.isoformat(), "to": analysis_to.isoformat()}

    try:
        c_from = date.fromisoformat(str(raw_from)) if isinstance(raw_from, str) else raw_from
        c_to   = date.fromisoformat(str(raw_to))   if isinstance(raw_to, str)   else raw_to
        return {"from": c_from.isoformat(), "to": c_to.isoformat()}
    except (ValueError, AttributeError):
        return {"from": analysis_from.isoformat(), "to": analysis_to.isoformat()}


def _compute_comparison_with_customer(
    our_daily_df: pd.DataFrame,
    customer_df: pd.DataFrame,
    aligned_df: pd.DataFrame,
    customer_period_dict: dict,
    analysis_period: dict,
) -> dict:
    """
    Строит comparisons.with_customer.

    Если customer_df пуст → status='no_customer_data'.
    Если есть overlap, но мало дней → status='partial_customer_data'.
    Иначе → status='ok'.
    """
    if customer_df.empty or aligned_df.empty:
        return {
            "status":                   "no_customer_data",
            "customer_period":          customer_period_dict,
            "customer_days_available":  0,
            "customer_days_requested":  _period_days(customer_period_dict),
            "mape":                     None,
            "q_source_used":            None,
            "daily_table":              [],
        }

    # Определяем q_source
    q_source = "q_gas_working"
    if "customer_q_source" in aligned_df.columns:
        sources = aligned_df["customer_q_source"].dropna().unique()
        if len(sources) > 0:
            q_source = str(sources[0])

    # Кол-во дней заказчика с данными
    customer_days_available = int(customer_df.shape[0]) if not customer_df.empty else 0
    customer_days_requested = _period_days(customer_period_dict)

    # daily_table
    daily_table = _build_daily_table(aligned_df, q_source)

    # MAPE
    mape = _compute_mape(aligned_df)

    # Статус
    if customer_days_available == 0:
        status = "no_customer_data"
    elif customer_days_available < SHORT_INTERSECTION_DAYS:
        status = "partial_customer_data"
    else:
        status = "ok"

    return {
        "status":                  status,
        "customer_period":         customer_period_dict,
        "customer_days_available": customer_days_available,
        "customer_days_requested": customer_days_requested,
        "mape":                    mape,
        "q_source_used":           q_source,
        "daily_table":             daily_table,
    }


def _build_daily_table(aligned_df: pd.DataFrame, q_source: str) -> list[dict]:
    """
    Строит daily_table — массив объектов (одна строка = один день).
    Поля RFC §9.Q1: date, our_q_total, our_q_working, customer_q_total,
    customer_q_working, diff_abs, diff_pct, data_status.

    Используем aligned_df из B1 align_our_and_customer().
    """
    if aligned_df.empty:
        return []

    rows = []
    for _, row in aligned_df.iterrows():
        date_val = row.get("date")
        if date_val is None:
            continue

        date_str = (
            date_val.strftime("%Y-%m-%d")
            if hasattr(date_val, "strftime")
            else str(date_val)[:10]
        )

        our_q = _safe_float(row.get("our_q"))

        # our_q_total = our_q_working = наш Q (у нас нет разделения total/working в суточном)
        our_q_total   = our_q
        our_q_working = our_q

        # customer Q: aligned_df хранит в customer_q (смаппировано align_our_and_customer)
        cust_q_raw = _safe_float(row.get("customer_q"))

        # Если q_source = q_gas_working — оба working = cust_q_raw, total = None
        # Если q_source = q_gas_total — total = cust_q_raw, working = None
        if q_source == "q_gas_working":
            customer_q_working = cust_q_raw
            customer_q_total   = None
        else:
            customer_q_total   = cust_q_raw
            customer_q_working = None

        diff_abs = _safe_float(row.get("diff_q_abs"))
        diff_pct = _safe_float(row.get("diff_q_pct"))

        data_status = _classify_data_status_per_day(our_q, cust_q_raw, diff_pct)

        rows.append({
            "date":               date_str,
            "our_q_total":        our_q_total,
            "our_q_working":      our_q_working,
            "customer_q_total":   customer_q_total,
            "customer_q_working": customer_q_working,
            "diff_abs":           diff_abs,
            "diff_pct":           diff_pct,
            "data_status":        data_status,
        })

    return rows


def _classify_data_status_per_day(
    our_q: float | None,
    customer_q: float | None,
    diff_pct: float | None,
    threshold_pct: float = DAILY_INVALID_THRESHOLD_PCT,
) -> str:
    """
    Классифицирует статус одного дня в daily_table.

    Правила:
      - нет наших данных → 'missing_our'
      - нет данных заказчика → 'missing_customer'
      - |diff_pct| >= threshold → 'invalid'
      - иначе → 'ok'
    """
    if our_q is None:
        return "missing_our"
    if customer_q is None:
        return "missing_customer"
    if diff_pct is not None and abs(diff_pct) >= threshold_pct:
        return "invalid"
    return "ok"


def _compute_mape(aligned_df: pd.DataFrame) -> dict | None:
    """
    Вычисляет MAPE (mean absolute percentage error) по aligned_df.
    Возвращает {"p_tube": ..., "p_line": ..., "q": ...}.
    None для метрики если недостаточно данных (< 5 точек).
    """
    if aligned_df.empty:
        return None

    def _mape(our_col: str, cust_col: str) -> float | None:
        if our_col not in aligned_df.columns or cust_col not in aligned_df.columns:
            return None
        mask = aligned_df[our_col].notna() & aligned_df[cust_col].notna()
        our  = aligned_df.loc[mask, our_col]
        cust = aligned_df.loc[mask, cust_col]
        if len(our) < MIN_CUSTOMER_DAYS:
            return None
        denom = cust.replace(0.0, np.nan)
        ape = ((our - denom).abs() / denom.abs() * 100.0).dropna()
        if len(ape) == 0:
            return None
        return round(float(ape.mean()), 2)

    return {
        "p_tube": _mape("our_p_tube", "customer_p_wellhead"),
        "p_line": _mape("our_p_line", "customer_p_flowline"),
        "q":      _mape("our_q",      "customer_q"),
    }


def _pick_q_source(customer_df: pd.DataFrame) -> str:
    """
    Определяет, какую колонку Q заказчика использовать.
    Предпочтение: q_gas_working (если есть непустые значения).
    Fallback: q_gas_total.
    """
    if customer_df.empty:
        return "q_gas_total"
    if "q_gas_working" in customer_df.columns and customer_df["q_gas_working"].notna().any():
        return "q_gas_working"
    return "q_gas_total"


def _build_diagnostics(
    metrics_layer: dict,
    with_b1: dict,
    with_customer: dict,
    our_daily_df: pd.DataFrame,
) -> list[dict]:
    """
    Строит Layer 5 diagnostics — список formal enum verdicts.

    Только enum values. Никаких narrative-строк, никаких causal claims.
    requires_log_check всегда True для observation (RFC §5.3).
    """
    entries: list[dict] = []

    b1_status = with_b1.get("status", "no_baseline")
    deltas     = with_b1.get("deltas")
    days_count = int(our_daily_df.shape[0]) if not our_daily_df.empty else 0

    # ── vs_b1 verdicts ───────────────────────────────────────────────────
    if b1_status == "ok" and deltas is not None:
        # p_tube
        entries.append(_vs_b1_verdict(
            target="p_tube",
            days=days_count,
            min_days=MIN_DAYS["p_tube"],
            delta_dict=deltas.get("p_tube_mean"),
            direction_better_positive=True,   # рост p_tube = улучшение
            threshold_pct=THRESHOLD_PCT["p_tube"],
            threshold_abs=None,
        ))
        # p_line
        entries.append(_vs_b1_verdict(
            target="p_line",
            days=days_count,
            min_days=MIN_DAYS["p_line"],
            delta_dict=deltas.get("p_line_mean"),
            direction_better_positive=False,  # снижение p_line = улучшение
            threshold_pct=THRESHOLD_PCT["p_line"],
            threshold_abs=None,
        ))
        # dp
        entries.append(_vs_b1_verdict(
            target="dp",
            days=days_count,
            min_days=MIN_DAYS["dp"],
            delta_dict=deltas.get("dp_mean"),
            direction_better_positive=True,   # рост ΔP = улучшение
            threshold_pct=THRESHOLD_PCT["dp_pct"],
            threshold_abs=THRESHOLD_PCT["dp_abs"],
        ))
        # q
        entries.append(_vs_b1_verdict(
            target="q",
            days=days_count,
            min_days=MIN_DAYS["q"],
            delta_dict=deltas.get("q_mean"),
            direction_better_positive=True,   # рост Q = улучшение
            threshold_pct=THRESHOLD_PCT["q"],
            threshold_abs=None,
        ))
        # shutdown_pct
        entries.append(_vs_b1_verdict(
            target="shutdown_pct",
            days=days_count,
            min_days=MIN_DAYS["shutdown"],
            delta_dict=deltas.get("shutdown_pct"),
            direction_better_positive=False,  # снижение простоя = улучшение
            threshold_pct=THRESHOLD_PCT["shutdown_pct"],
            threshold_abs=None,
            is_abs_only=True,  # у shutdown_pct нет поля pct в дельтах
        ))
        # cv_p_tube
        entries.append(_vs_b1_verdict(
            target="cv_p_tube",
            days=days_count,
            min_days=MIN_DAYS["cv"],
            delta_dict=deltas.get("cv_p_tube"),
            direction_better_positive=False,  # снижение CV = улучшение (меньше нестабильности)
            threshold_pct=THRESHOLD_PCT["cv"],
            threshold_abs=None,
            is_abs_only=True,  # cv_p_tube delta: только abs
        ))

    # ── vs_customer verdicts ─────────────────────────────────────────────
    cust_status = with_customer.get("status", "no_customer_data")
    mape        = with_customer.get("mape")
    cust_days   = with_customer.get("customer_days_available", 0)

    if cust_status not in ("no_customer_data",) and mape is not None:
        for metric_key, cust_col in [("p_tube", "p_tube"), ("p_line", "p_line"), ("q", "q")]:
            mape_val = mape.get(cust_col)
            entries.append(_vs_customer_verdict(
                target=metric_key,
                customer_days=cust_days,
                mape_val=mape_val,
                thresholds=MAPE_THRESHOLDS[metric_key],
            ))

    # ── overall (target="overall", context="combined") ───────────────────
    overall = _compute_overall_verdict(entries, b1_status)
    entries.append({
        "target":            "overall",
        "context":           "combined",
        "verdict":           overall,
        "magnitude":         None,
        "requires_log_check": True,
    })

    return entries


def _vs_b1_verdict(
    target: str,
    days: int,
    min_days: int,
    delta_dict: dict | None,
    direction_better_positive: bool,
    threshold_pct: float,
    threshold_abs: float | None = None,
    is_abs_only: bool = False,
) -> dict:
    """
    Строит одну запись diagnostics для контекста vs_b1.
    """
    if days < min_days or delta_dict is None:
        return {
            "target":            target,
            "context":           "vs_b1",
            "verdict":           "insufficient_data",
            "magnitude":         None,
            "requires_log_check": True,
        }

    abs_d = delta_dict.get("abs")
    pct_d = delta_dict.get("pct")

    if abs_d is None:
        return {
            "target":            target,
            "context":           "vs_b1",
            "verdict":           "insufficient_data",
            "magnitude":         None,
            "requires_log_check": True,
        }

    # Проверка порога «существенности»
    if is_abs_only:
        # Для shutdown_pct, cv_p_tube: порог по abs
        below_threshold = abs(abs_d) < threshold_pct
    else:
        # Для обычных метрик: проверяем pct, для dp дополнительно abs
        below_threshold = (pct_d is not None and abs(pct_d) < threshold_pct)
        if threshold_abs is not None and not below_threshold:
            # dp: дополнительно |abs| < threshold_abs → тоже «несущественно»
            below_threshold = below_threshold or (abs(abs_d) < threshold_abs)

    if below_threshold:
        verdict = "no_significant_change"
    else:
        # Направление: лучше или хуже?
        if direction_better_positive:
            verdict = "improvement" if abs_d > 0 else "degradation"
        else:
            verdict = "improvement" if abs_d < 0 else "degradation"

    magnitude: dict | None = {"abs": abs_d}
    if pct_d is not None and not is_abs_only:
        magnitude["pct"] = pct_d

    return {
        "target":            target,
        "context":           "vs_b1",
        "verdict":           verdict,
        "magnitude":         magnitude,
        "requires_log_check": True,
    }


def _vs_customer_verdict(
    target: str,
    customer_days: int,
    mape_val: float | None,
    thresholds: tuple[float, float],
) -> dict:
    """
    Строит одну запись diagnostics для контекста vs_customer.
    """
    if customer_days < MIN_CUSTOMER_DAYS or mape_val is None:
        return {
            "target":            target,
            "context":           "vs_customer",
            "verdict":           "insufficient_data",
            "magnitude":         None,
            "requires_log_check": True,
        }

    match_max, partial_max = thresholds
    if mape_val < match_max:
        verdict = "match"
    elif mape_val < partial_max:
        verdict = "partial_match"
    else:
        verdict = "diverge"

    return {
        "target":            target,
        "context":           "vs_customer",
        "verdict":           verdict,
        "magnitude":         {"mape": mape_val},
        "requires_log_check": True,
    }


def _compute_overall_verdict(
    entries: list[dict],
    b1_status: str,
) -> str:
    """
    Вычисляет overall verdict на основе всех записей diagnostics.

    Логика RFC спецификации:
      - Если нет vs_b1 записей (baseline_block_id is None):
          если all vs_customer == 'insufficient_data' → 'insufficient_data'
          иначе → 'no_significant_change' (без B1 не делаем выводов)
      - Иначе:
          if any vs_b1.verdict == 'degradation' → 'degradation'
          elif all vs_b1.verdict == 'improvement' → 'improvement'
          elif all vs_b1.verdict == 'insufficient_data' → 'insufficient_data'
          else → 'no_significant_change'
    """
    vs_b1 = [e for e in entries if e.get("context") == "vs_b1"]
    vs_cust = [e for e in entries if e.get("context") == "vs_customer"]

    if not vs_b1:
        if vs_cust and all(e.get("verdict") == "insufficient_data" for e in vs_cust):
            return "insufficient_data"
        return "no_significant_change"

    b1_verdicts = [e.get("verdict") for e in vs_b1]

    if "degradation" in b1_verdicts:
        return "degradation"
    if all(v == "improvement" for v in b1_verdicts):
        return "improvement"
    if all(v == "insufficient_data" for v in b1_verdicts):
        return "insufficient_data"
    return "no_significant_change"


def _compute_flags(
    quality_layer: dict,
    with_b1: dict,
    with_customer: dict,
    baseline_block: dict | None,
    current_period: dict,
) -> dict:
    """
    Строит Layer 6 flags — boolean dict.

    Флаги из RFC §3.3:
      low_coverage, significant_gap, outlier_detected, short_intersection,
      baseline_mismatch_period, outdated_baseline_version, invalid_comparison.
    """
    quality_flags_list: list = quality_layer.get("flags", [])
    quality_metrics: dict = quality_layer.get("metrics", {})

    low_coverage     = "low_coverage" in quality_flags_list
    significant_gap  = "significant_gap" in quality_flags_list
    outlier_detected = "outlier_detected" in quality_flags_list

    # short_intersection — если customer_days_available < SHORT_INTERSECTION_DAYS
    cust_days_avail = with_customer.get("customer_days_available", 0)
    short_intersection = (
        with_customer.get("status") not in ("no_customer_data",)
        and cust_days_avail < SHORT_INTERSECTION_DAYS
    )

    # baseline_mismatch_period — если период baseline и текущий разнесены > 90 дней
    baseline_mismatch_period = False
    if with_b1.get("status") == "ok" and baseline_block is not None:
        b_period = baseline_block.get("period") or {}
        baseline_mismatch_period = _check_period_mismatch(
            current_period=current_period,
            baseline_period=b_period,
            max_days=BASELINE_MISMATCH_DAYS,
        )

    # outdated_baseline_version — если baseline _v старее текущего SNAPSHOT_V
    outdated_baseline_version = False
    if baseline_block is not None:
        baseline_v = baseline_block.get("_v", "")
        outdated_baseline_version = bool(baseline_v and baseline_v != "obs_baseline_v1")

    # invalid_comparison — baseline_block_id указывает на удалённый/повреждённый блок
    invalid_comparison = (
        with_b1.get("status") == "baseline_corrupted"
    )

    return {
        "low_coverage":              low_coverage,
        "significant_gap":           significant_gap,
        "outlier_detected":          outlier_detected,
        "short_intersection":        short_intersection,
        "baseline_mismatch_period":  baseline_mismatch_period,
        "outdated_baseline_version": outdated_baseline_version,
        "invalid_comparison":        invalid_comparison,
    }


def _check_period_mismatch(
    current_period: dict,
    baseline_period: dict,
    max_days: int,
) -> bool:
    """
    Проверяет, разнесены ли периоды больше чем на max_days.
    Возвращает True если разнесены (mismatch).
    """
    try:
        c_from = date.fromisoformat(str(current_period.get("from", "")))
        b_to   = date.fromisoformat(str(baseline_period.get("to", "")))
        gap    = abs((c_from - b_to).days)
        return gap > max_days
    except (ValueError, TypeError):
        return False


def _empty_comparisons(
    baseline_block_id: int | None,
    customer_period_dict: dict,
) -> dict:
    """Пустые comparisons для no_data/insufficient_data блока."""
    return {
        "with_b1": {
            "status":           "no_baseline" if baseline_block_id is None else "baseline_corrupted",
            "baseline_block_id": baseline_block_id,
            "baseline_period":  None,
            "deltas":           None,
        },
        "with_customer": {
            "status":                  "no_customer_data",
            "customer_period":         customer_period_dict,
            "customer_days_available": 0,
            "customer_days_requested": _period_days(customer_period_dict),
            "mape":                    None,
            "q_source_used":           None,
            "daily_table":             [],
        },
    }


def _empty_flags() -> dict:
    """Все флаги False — для no_data/insufficient_data блока."""
    return {
        "low_coverage":              False,
        "significant_gap":           False,
        "outlier_detected":          False,
        "short_intersection":        False,
        "baseline_mismatch_period":  False,
        "outdated_baseline_version": False,
        "invalid_comparison":        False,
    }


def _assemble_snapshot(
    computed_at: str,
    block_status: str,
    period: dict,
    raw_layer: dict | None,
    metrics_layer: dict,
    quality_layer: dict,
    comparisons_layer: dict,
    diagnostics_layer: list[dict],
    flags_layer: dict,
) -> dict:
    """
    Собирает финальный snapshot dict по RFC §1.3.
    Гарантирует наличие всех обязательных top-level полей.
    """
    snapshot: dict[str, Any] = {
        "_v":            SNAPSHOT_V,
        "schema_version": SCHEMA_VERSION,
        "computed_at":   computed_at,
        "block_status":  block_status,
        "period":        period,
        "metrics":       metrics_layer,
        "quality":       quality_layer,
        "comparisons":   comparisons_layer,
        "diagnostics":   diagnostics_layer,
        "flags":         flags_layer,
    }
    if raw_layer is not None:
        snapshot["raw"] = raw_layer
    return snapshot


def _period_days(period_dict: dict) -> int:
    """Возвращает кол-во дней в периоде (включительно)."""
    try:
        d_from = date.fromisoformat(str(period_dict.get("from", "")))
        d_to   = date.fromisoformat(str(period_dict.get("to", "")))
        return max(1, (d_to - d_from).days + 1)
    except (ValueError, TypeError):
        return 0


def _safe_float(v: Any) -> float | None:
    """Конвертирует значение в float или None (для NaN/None)."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if np.isnan(f) else round(f, 4)
    except (TypeError, ValueError):
        return None
