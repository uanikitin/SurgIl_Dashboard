"""
ScenarioCalculationService — расчёт дебита с коррекциями и хранением результатов.

Pipeline (повторяет _run_calculation из flow_rate router, но добавляет коррекции):
1. get_pressure_data()        — raw данные из pressure_raw
2. clean_pressure()           — нули/NaN → ffill/bfill
3. apply_corrections()        — *** НОВЫЙ ШАГ: exclude/interpolate/manual/clamp ***
4. smooth_pressure()          — Савицкий-Голай (опционально)
5. calculate_flow_rate()      — мгновенный дебит
6. calculate_purge_loss()     — потери при стравливании
7. PurgeDetector.detect()     — детекция продувок
8. recalculate_purge_loss()   — потери ТОЛЬКО в фазах venting
9. calculate_cumulative()     — накопленный дебит
10. detect_downtime_periods() — простои
11. build_summary()           — сводные KPI
12. aggregate_to_daily()      — группировка → flow_result
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from backend.models.flow_analysis import FlowScenario, FlowCorrection, FlowResult
from .data_access import get_pressure_data, get_choke_mm, get_purge_events
from .cleaning import clean_pressure, smooth_pressure
from .calculator import calculate_flow_rate, calculate_cumulative, calculate_purge_loss
from .config import FlowRateConfig
from .purge_detector import PurgeDetector, recalculate_purge_loss_with_cycles
from .downtime import detect_downtime_periods
from .summary import build_summary

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
#  Коррекции
# ═══════════════════════════════════════════════════════════

def apply_corrections(
    df: pd.DataFrame,
    corrections: list[FlowCorrection],
) -> tuple[pd.DataFrame, int]:
    """
    Применяет коррекции к DataFrame давления (между clean и smooth).

    Коррекции применяются в порядке sort_order.
    Каждая работает с p_tube и p_line.

    Returns: (df, corrected_points_count)
    """
    if not corrections:
        return df, 0

    df = df.copy()
    total_corrected = 0

    sorted_corrections = sorted(corrections, key=lambda c: c.sort_order)

    for corr in sorted_corrections:
        mask = (df.index >= corr.dt_start) & (df.index <= corr.dt_end)
        n_affected = int(mask.sum())
        if n_affected == 0:
            continue

        total_corrected += n_affected

        if corr.correction_type == "exclude":
            # Исключить данные → NaN, затем ffill/bfill восстановит
            df.loc[mask, "p_tube"] = np.nan
            df.loc[mask, "p_line"] = np.nan

        elif corr.correction_type == "interpolate":
            # NaN + pandas interpolate
            method = corr.interp_method or "linear"
            df.loc[mask, "p_tube"] = np.nan
            df.loc[mask, "p_line"] = np.nan
            for col in ("p_tube", "p_line"):
                df[col] = df[col].interpolate(method=method)

        elif corr.correction_type == "manual_value":
            # Фиксированные значения
            if corr.manual_p_tube is not None:
                df.loc[mask, "p_tube"] = corr.manual_p_tube
            if corr.manual_p_line is not None:
                df.loc[mask, "p_line"] = corr.manual_p_line

        elif corr.correction_type == "clamp":
            # Ограничение диапазоном [clamp_min, clamp_max]
            for col in ("p_tube", "p_line"):
                vals = df.loc[mask, col]
                if corr.clamp_min is not None:
                    vals = vals.clip(lower=corr.clamp_min)
                if corr.clamp_max is not None:
                    vals = vals.clip(upper=corr.clamp_max)
                df.loc[mask, col] = vals

        log.debug(
            "correction %s: type=%s, %d points affected",
            corr.id, corr.correction_type, n_affected,
        )

    # Заполняем NaN после exclude/interpolate
    df = df.ffill().bfill()

    return df, total_corrected


# ═══════════════════════════════════════════════════════════
#  Суточная агрегация
# ═══════════════════════════════════════════════════════════

def aggregate_to_daily(
    df: pd.DataFrame,
    corrected_points: int = 0,
) -> list[dict]:
    """
    Группировка поминутных данных в суточные результаты.

    Returns: list of dicts, каждый — одна строка FlowResult.
    """
    if df.empty:
        return []

    daily = df.groupby(df.index.date)
    results = []

    total_points = len(df)

    for day, group in daily:
        q = group["flow_rate"]
        n = len(group)

        # Накопленный дебит за день (трапеция, 1 мин = 1/1440 сут)
        dt = 1.0 / 1440.0
        q_vals = q.values
        q_prev = np.roll(q_vals, 1)
        q_prev[0] = q_vals[0]
        cum_day = float(np.sum((q_prev + q_vals) * dt / 2.0))

        # Потери при стравливании за день
        purge_loss_day = 0.0
        if "purge_loss_per_min" in group.columns:
            purge_loss_day = float(group["purge_loss_per_min"].sum())

        # Простои (минуты с purge_flag=1 или p_tube < p_line)
        downtime_min = 0.0
        if "purge_flag" in group.columns:
            downtime_min = float(group["purge_flag"].sum())

        # Доля скорректированных точек (пропорционально дню)
        corr_day = int(round(corrected_points * n / total_points)) if total_points > 0 else 0

        results.append({
            "result_date": day,
            "avg_flow_rate": round(float(q.mean()), 4),
            "min_flow_rate": round(float(q.min()), 4),
            "max_flow_rate": round(float(q.max()), 4),
            "median_flow_rate": round(float(q.median()), 4),
            "cumulative_flow": round(cum_day, 4),
            "avg_p_tube": round(float(group["p_tube"].mean()), 3),
            "avg_p_line": round(float(group["p_line"].mean()), 3),
            "avg_dp": round(float((group["p_tube"] - group["p_line"]).mean()), 3),
            "purge_loss": round(purge_loss_day, 5),
            "downtime_minutes": round(downtime_min, 1),
            "data_points": n,
            "corrected_points": corr_day,
        })

    return results


# ═══════════════════════════════════════════════════════════
#  Основной расчёт
# ═══════════════════════════════════════════════════════════

def run_scenario_calculation(
    scenario: FlowScenario,
    db: Session,
    save_results: bool = True,
) -> dict:
    """
    Полный расчёт дебита по сценарию.

    Parameters
    ----------
    scenario : FlowScenario с загруженными corrections
    db : SQLAlchemy session (для сохранения результатов)
    save_results : True = сохранить в flow_result + обновить meta

    Returns
    -------
    dict с ключами: summary, chart, downtime_periods, purge_cycles,
                    daily_results, data_points, corrected_points
    """
    dt_start = scenario.period_start.isoformat()
    dt_end = scenario.period_end.isoformat()

    # 1. Данные из pressure_raw
    df = get_pressure_data(scenario.well_id, dt_start, dt_end)
    if df.empty:
        raise ValueError(
            f"Нет данных давления для well_id={scenario.well_id} "
            f"за период {dt_start}..{dt_end}"
        )

    # 2. Штуцер
    choke = scenario.choke_mm or get_choke_mm(scenario.well_id)
    if choke is None:
        raise ValueError(
            f"Штуцер (choke_diam_mm) не найден для well_id={scenario.well_id}. "
            f"Укажите choke_mm в сценарии или заполните well_construction."
        )

    # 3. Очистка
    df = clean_pressure(df)

    # 4. *** КОРРЕКЦИИ *** (новый шаг)
    corrections = list(scenario.corrections) if scenario.corrections else []
    df, corrected_points = apply_corrections(df, corrections)

    # 5. Сглаживание
    if scenario.smooth_enabled:
        df = smooth_pressure(
            df,
            window=scenario.smooth_window,
            polyorder=scenario.smooth_polyorder,
        )

    # 6. Расчёт дебита
    cfg = FlowRateConfig(
        multiplier=scenario.multiplier,
        C1=scenario.c1,
        C2=scenario.c2,
        C3=scenario.c3,
        critical_ratio=scenario.critical_ratio,
    )
    df = calculate_flow_rate(df, choke, cfg)

    # 7. Потери при продувках (предварительные)
    df = calculate_purge_loss(df)

    # 8. Детекция продувок
    exclude_ids = set()
    if scenario.exclude_purge_ids:
        exclude_ids = {s.strip() for s in scenario.exclude_purge_ids.split(",") if s.strip()}

    events_df = get_purge_events(scenario.well_id, dt_start, dt_end)
    detector = PurgeDetector()
    purge_cycles = detector.detect(df, events_df, exclude_ids)

    # 9. Пересчёт потерь: ТОЛЬКО в фазах venting
    df = recalculate_purge_loss_with_cycles(df, purge_cycles)

    # 10. Накопленный дебит
    df = calculate_cumulative(df)

    # 11. Простои
    periods = detect_downtime_periods(df)

    # 12. Сводка
    summary = build_summary(df, periods, scenario.well_id, choke, purge_cycles)

    # 13. Суточная агрегация
    daily_results = aggregate_to_daily(df, corrected_points)

    # 14. Сохранение
    if save_results:
        _save_results(scenario, daily_results, summary, db)

    # 15. Данные для графика (прореженные, макс ~2000 точек)
    step = max(1, len(df) // 2000)
    chart_df = df.iloc[::step]

    chart = {
        "timestamps": chart_df.index.strftime("%Y-%m-%dT%H:%M:%S").tolist(),
        "flow_rate": chart_df["flow_rate"].round(3).tolist(),
        "cumulative_flow": chart_df["cumulative_flow"].round(3).tolist(),
        "p_tube": chart_df["p_tube"].round(2).tolist(),
        "p_line": chart_df["p_line"].round(2).tolist(),
    }

    # Зоны коррекций для отображения на графике
    correction_zones = []
    for corr in corrections:
        correction_zones.append({
            "id": corr.id,
            "type": corr.correction_type,
            "start": corr.dt_start.isoformat(),
            "end": corr.dt_end.isoformat(),
            "reason": corr.reason,
        })

    # Периоды простоев
    dt_list = []
    if not periods.empty:
        for _, row in periods.iterrows():
            dt_list.append({
                "start": row["start"].isoformat(),
                "end": row["end"].isoformat(),
                "duration_min": row["duration_min"],
            })

    return {
        "summary": summary,
        "chart": chart,
        "correction_zones": correction_zones,
        "downtime_periods": dt_list,
        "purge_cycles": [c.to_dict() for c in purge_cycles],
        "daily_results": daily_results,
        "data_points": len(df),
        "corrected_points": corrected_points,
    }


def _save_results(
    scenario: FlowScenario,
    daily_results: list[dict],
    summary: dict,
    db: Session,
) -> None:
    """
    Сохраняет суточные результаты в flow_result и summary в scenario.meta.
    Удаляет старые результаты перед вставкой.
    """
    # Удалить старые результаты
    db.query(FlowResult).filter(FlowResult.scenario_id == scenario.id).delete()

    # Вставить новые
    for row in daily_results:
        result = FlowResult(
            scenario_id=scenario.id,
            **row,
        )
        db.add(result)

    # Обновить scenario
    meta = dict(scenario.meta) if scenario.meta else {}
    meta["summary"] = summary
    meta["calculated_at"] = datetime.utcnow().isoformat()
    scenario.meta = meta
    scenario.status = "calculated"
    scenario.updated_at = datetime.utcnow()

    db.flush()
    log.info(
        "Saved %d daily results for scenario %d (well_id=%d)",
        len(daily_results), scenario.id, scenario.well_id,
    )


# ═══════════════════════════════════════════════════════════
#  Сравнение сценариев
# ═══════════════════════════════════════════════════════════

def compare_scenarios(
    scenario_id: int,
    baseline_id: int,
    granularity: str,
    db: Session,
) -> dict:
    """
    Сравнение двух сценариев по суточным результатам.

    granularity: 'daily' | 'weekly' | 'monthly'

    Returns: dict с comparison table и delta metrics.
    """
    current_results = (
        db.query(FlowResult)
        .filter(FlowResult.scenario_id == scenario_id)
        .order_by(FlowResult.result_date)
        .all()
    )
    baseline_results = (
        db.query(FlowResult)
        .filter(FlowResult.scenario_id == baseline_id)
        .order_by(FlowResult.result_date)
        .all()
    )

    if not current_results or not baseline_results:
        return {
            "error": "Нет результатов для одного из сценариев. "
                     "Сначала выполните расчёт.",
            "rows": [],
        }

    # Конвертируем в DataFrame
    def results_to_df(results: list[FlowResult]) -> pd.DataFrame:
        rows = []
        for r in results:
            rows.append({
                "date": r.result_date,
                "avg_flow_rate": r.avg_flow_rate or 0,
                "cumulative_flow": r.cumulative_flow or 0,
                "avg_p_tube": r.avg_p_tube or 0,
                "avg_p_line": r.avg_p_line or 0,
                "avg_dp": r.avg_dp or 0,
                "purge_loss": r.purge_loss or 0,
                "downtime_minutes": r.downtime_minutes or 0,
            })
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        return df

    df_curr = results_to_df(current_results)
    df_base = results_to_df(baseline_results)

    # Агрегация по granularity
    resample_map = {
        "daily": "D",
        "weekly": "W-MON",
        "monthly": "MS",
    }
    freq = resample_map.get(granularity, "D")

    agg_spec = {
        "avg_flow_rate": "mean",
        "cumulative_flow": "sum",
        "avg_p_tube": "mean",
        "avg_p_line": "mean",
        "avg_dp": "mean",
        "purge_loss": "sum",
        "downtime_minutes": "sum",
    }

    df_curr_agg = df_curr.resample(freq).agg(agg_spec)
    df_base_agg = df_base.resample(freq).agg(agg_spec)

    # Join по дате
    merged = df_curr_agg.join(df_base_agg, lsuffix="_current", rsuffix="_baseline", how="outer")

    # Формируем таблицу сравнения
    rows = []
    for idx, row in merged.iterrows():
        period_label = idx.strftime("%Y-%m-%d")
        if granularity == "weekly":
            period_label = f"W{idx.isocalendar()[1]:02d} ({idx.strftime('%Y-%m-%d')})"
        elif granularity == "monthly":
            period_label = idx.strftime("%Y-%m")

        q_curr = row.get("avg_flow_rate_current")
        q_base = row.get("avg_flow_rate_baseline")
        cum_curr = row.get("cumulative_flow_current")
        cum_base = row.get("cumulative_flow_baseline")

        delta_q = None
        delta_pct = None
        if pd.notna(q_curr) and pd.notna(q_base) and q_base != 0:
            delta_q = round(q_curr - q_base, 4)
            delta_pct = round((q_curr - q_base) / q_base * 100, 2)

        delta_cum = None
        if pd.notna(cum_curr) and pd.notna(cum_base):
            delta_cum = round(cum_curr - cum_base, 4)

        rows.append({
            "period": period_label,
            "current_avg_flow": round(q_curr, 4) if pd.notna(q_curr) else None,
            "baseline_avg_flow": round(q_base, 4) if pd.notna(q_base) else None,
            "delta_flow": delta_q,
            "delta_flow_pct": delta_pct,
            "current_cumulative": round(cum_curr, 4) if pd.notna(cum_curr) else None,
            "baseline_cumulative": round(cum_base, 4) if pd.notna(cum_base) else None,
            "delta_cumulative": delta_cum,
            "current_downtime_min": (
                round(row.get("downtime_minutes_current"), 1)
                if pd.notna(row.get("downtime_minutes_current")) else None
            ),
            "baseline_downtime_min": (
                round(row.get("downtime_minutes_baseline"), 1)
                if pd.notna(row.get("downtime_minutes_baseline")) else None
            ),
        })

    # Общая статистика
    totals = {}
    if not df_curr_agg.empty and not df_base_agg.empty:
        totals = {
            "current_total_flow": round(float(df_curr_agg["cumulative_flow"].sum()), 3),
            "baseline_total_flow": round(float(df_base_agg["cumulative_flow"].sum()), 3),
            "current_avg_rate": round(float(df_curr_agg["avg_flow_rate"].mean()), 4),
            "baseline_avg_rate": round(float(df_base_agg["avg_flow_rate"].mean()), 4),
        }
        if totals["baseline_total_flow"] != 0:
            totals["delta_total_pct"] = round(
                (totals["current_total_flow"] - totals["baseline_total_flow"])
                / totals["baseline_total_flow"] * 100, 2
            )

    return {
        "granularity": granularity,
        "scenario_id": scenario_id,
        "baseline_id": baseline_id,
        "totals": totals,
        "rows": rows,
    }
