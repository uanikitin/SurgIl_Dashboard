"""Сервис baseline-ов скважины (на данных заказчика).

Расчёт характерных значений за заданный период из таблицы well_daily,
сохранение/чтение/удаление в customer_baseline. Используется в адаптационном
отчёте: фиксируем «базовые» показатели (например, 1 месяц до начала работ),
потом сравниваем с другими периодами.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models.customer_baseline import CustomerBaseline
from backend.models.wells import Well
from backend.services.customer_daily_service import (
    ensure_table as _ensure_well_daily,
    load_for_well,
    find_well,
)

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Расчёт характерных значений за период
# ═══════════════════════════════════════════════════════════════════

def compute_period_stats(
    db: Session, well_number: str,
    period_from: date, period_to: date,
) -> dict[str, Any]:
    """Посчитать сводные характеристики за период из well_daily.

    Возвращает dict с avg/median по Q (total + working), P (wellhead, flowline),
    ΔP, простоями (shutdown_min) и подсчётом дней с/без простоев.
    """
    _ensure_well_daily(db)
    df = load_for_well(db, well_number, d_from=period_from, d_to=period_to)
    if df.empty:
        return {
            "well": str(well_number),
            "period_from": period_from,
            "period_to": period_to,
            "days_count": 0,
            "rows": 0,
        }

    def _stat(col, kind="median"):
        if col not in df.columns:
            return None
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if s.empty:
            return None
        if kind == "median":
            return float(s.median())
        if kind == "avg":
            return float(s.mean())
        if kind == "sum":
            return float(s.sum())
        return None

    # ΔP = P_wellhead − P_flowline (по строкам), потом усреднение
    if {"p_wellhead", "p_flowline"}.issubset(df.columns):
        pt = pd.to_numeric(df["p_wellhead"], errors="coerce")
        pl = pd.to_numeric(df["p_flowline"], errors="coerce")
        dp = (pt - pl).clip(lower=0).dropna()
        dp_avg = float(dp.mean()) if len(dp) > 0 else None
        dp_med = float(dp.median()) if len(dp) > 0 else None
    else:
        dp_avg = dp_med = None

    # Простои
    sd_total = _stat("shutdown_min", "sum")
    sd_avg = _stat("shutdown_min", "avg")
    sd_days = 0
    if "shutdown_min" in df.columns:
        sd_days = int((pd.to_numeric(df["shutdown_min"], errors="coerce").fillna(0) > 0).sum())

    return {
        "well": str(well_number),
        "period_from": period_from,
        "period_to": period_to,
        "days_count": int(len(df)),
        "rows": int(len(df)),

        "q_total_avg": _stat("q_gas_total", "avg"),
        "q_total_median": _stat("q_gas_total", "median"),
        "q_working_avg": _stat("q_gas_working", "avg"),
        "q_working_median": _stat("q_gas_working", "median"),

        "p_wellhead_avg": _stat("p_wellhead", "avg"),
        "p_wellhead_median": _stat("p_wellhead", "median"),
        "p_flowline_avg": _stat("p_flowline", "avg"),
        "p_flowline_median": _stat("p_flowline", "median"),
        "dp_avg": dp_avg,
        "dp_median": dp_med,

        "shutdown_min_total": sd_total,
        "shutdown_min_avg": sd_avg,
        "shutdown_days_count": sd_days,
    }


# ═══════════════════════════════════════════════════════════════════
# Анализ периода — расширенная версия для UI/PDF
# ═══════════════════════════════════════════════════════════════════

def compute_period_analysis(
    db: Session, well_number: str,
    period_from: date, period_to: date,
    description: str | None = None,
) -> dict[str, Any]:
    """Полный анализ периода: статистика + ряды для графиков + простои по дням.

    Используется в UI вкладки и в PDF-главе.
    """
    _ensure_well_daily(db)
    df = load_for_well(db, well_number, d_from=period_from, d_to=period_to)

    base = compute_period_stats(db, well_number, period_from, period_to)
    base["description"] = description

    if df.empty:
        base["chart_data"] = []
        base["downtime_by_day"] = []
        return base

    # Серии для графика
    chart_data = []
    for _, row in df.iterrows():
        d = row.get("date")
        chart_data.append({
            "date": d.isoformat() if hasattr(d, "isoformat") else str(d),
            "p_wellhead": _safe_float(row.get("p_wellhead")),
            "p_flowline": _safe_float(row.get("p_flowline")),
            "dp": _safe_dp(row.get("p_wellhead"), row.get("p_flowline")),
            "q_total": _safe_float(row.get("q_gas_total")),
            "q_working": _safe_float(row.get("q_gas_working")),
            "shutdown_min": _safe_float(row.get("shutdown_min")),
        })

    # Простои по дням (для столбчатого графика)
    downtime_by_day = []
    if "shutdown_min" in df.columns:
        for _, row in df.iterrows():
            sd = _safe_float(row.get("shutdown_min"))
            if sd is None:
                continue
            d = row.get("date")
            downtime_by_day.append({
                "date": d.isoformat() if hasattr(d, "isoformat") else str(d),
                "minutes": sd,
            })

    # Тренд Q_working (если ≥3 точек)
    q_trend = None
    qs = [c["q_working"] for c in chart_data if c["q_working"] is not None]
    if len(qs) >= 3:
        x = np.arange(len(qs), dtype=float)
        try:
            coef = np.polyfit(x, np.array(qs), 1)
            slope_per_day = float(coef[0])
            y_pred = np.polyval(coef, x)
            ss_res = float(np.sum((np.array(qs) - y_pred) ** 2))
            ss_tot = float(np.sum((np.array(qs) - np.mean(qs)) ** 2))
            r2 = 1 - ss_res / ss_tot if ss_tot > 1e-9 else 0
            direction = ("up" if slope_per_day > 0.05
                         else "down" if slope_per_day < -0.05 else "flat")
            q_trend = {
                "slope_per_day": round(slope_per_day, 4),
                "r_squared": round(r2, 3),
                "direction": direction,
            }
        except (np.linalg.LinAlgError, ValueError):
            pass

    base["chart_data"] = chart_data
    base["downtime_by_day"] = downtime_by_day
    base["q_trend"] = q_trend
    return base


def _safe_float(v):
    if v is None:
        return None
    try:
        f = float(v)
        if np.isnan(f) or np.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _safe_dp(pt, pl):
    pt = _safe_float(pt)
    pl = _safe_float(pl)
    if pt is None or pl is None:
        return None
    return max(pt - pl, 0)


# ═══════════════════════════════════════════════════════════════════
# CRUD baseline
# ═══════════════════════════════════════════════════════════════════

def list_baselines(db: Session, well_id: int) -> list[dict[str, Any]]:
    """Список baseline-ов для скважины (закреплённые сверху)."""
    rows = db.query(CustomerBaseline).filter(
        CustomerBaseline.well_id == well_id,
    ).order_by(
        CustomerBaseline.is_pinned.desc(),
        CustomerBaseline.created_at.desc(),
    ).all()
    return [_baseline_to_dict(b) for b in rows]


def save_baseline(
    db: Session, well_id: int,
    name: str,
    period_from: date, period_to: date,
    source: str = "customer",
    notes: str | None = None,
    created_by: str | None = None,
    is_pinned: bool = False,
) -> dict[str, Any]:
    """Создать baseline: посчитать статистику и записать в БД."""
    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        raise ValueError(f"Скважина id={well_id} не найдена")
    well_number = str(well.number)

    stats = compute_period_stats(db, well_number, period_from, period_to)
    if stats.get("days_count", 0) == 0:
        raise ValueError(
            f"За период {period_from}..{period_to} нет данных по скважине "
            f"в well_daily."
        )

    bl = CustomerBaseline(
        well_id=well_id,
        name=name,
        source=source,
        period_from=period_from,
        period_to=period_to,
        days_count=stats.get("days_count"),
        q_total_avg=stats.get("q_total_avg"),
        q_total_median=stats.get("q_total_median"),
        q_working_avg=stats.get("q_working_avg"),
        q_working_median=stats.get("q_working_median"),
        p_wellhead_avg=stats.get("p_wellhead_avg"),
        p_wellhead_median=stats.get("p_wellhead_median"),
        p_flowline_avg=stats.get("p_flowline_avg"),
        p_flowline_median=stats.get("p_flowline_median"),
        dp_avg=stats.get("dp_avg"),
        dp_median=stats.get("dp_median"),
        shutdown_min_total=stats.get("shutdown_min_total"),
        shutdown_min_avg=stats.get("shutdown_min_avg"),
        shutdown_days_count=stats.get("shutdown_days_count"),
        notes=notes,
        created_by=created_by,
        is_pinned=is_pinned,
    )
    db.add(bl)
    db.commit()
    db.refresh(bl)
    return _baseline_to_dict(bl)


def delete_baseline(db: Session, baseline_id: int) -> bool:
    """Удалить baseline. Возвращает True если удалили."""
    bl = db.query(CustomerBaseline).filter(
        CustomerBaseline.id == baseline_id,
    ).first()
    if not bl:
        return False
    db.delete(bl)
    db.commit()
    return True


def update_baseline(
    db: Session, baseline_id: int,
    name: str | None = None,
    notes: str | None = None,
    is_pinned: bool | None = None,
) -> dict[str, Any] | None:
    """Обновить метаданные baseline (имя, заметки, закрепление)."""
    bl = db.query(CustomerBaseline).filter(
        CustomerBaseline.id == baseline_id,
    ).first()
    if not bl:
        return None
    if name is not None:
        bl.name = name
    if notes is not None:
        bl.notes = notes
    if is_pinned is not None:
        bl.is_pinned = is_pinned
    db.commit()
    db.refresh(bl)
    return _baseline_to_dict(bl)


def _baseline_to_dict(bl: CustomerBaseline) -> dict[str, Any]:
    return {
        "id": bl.id,
        "well_id": bl.well_id,
        "name": bl.name,
        "source": bl.source,
        "period_from": bl.period_from.isoformat() if bl.period_from else None,
        "period_to": bl.period_to.isoformat() if bl.period_to else None,
        "days_count": bl.days_count,
        "q_total_avg": bl.q_total_avg,
        "q_total_median": bl.q_total_median,
        "q_working_avg": bl.q_working_avg,
        "q_working_median": bl.q_working_median,
        "p_wellhead_avg": bl.p_wellhead_avg,
        "p_wellhead_median": bl.p_wellhead_median,
        "p_flowline_avg": bl.p_flowline_avg,
        "p_flowline_median": bl.p_flowline_median,
        "dp_avg": bl.dp_avg,
        "dp_median": bl.dp_median,
        "shutdown_min_total": bl.shutdown_min_total,
        "shutdown_min_avg": bl.shutdown_min_avg,
        "shutdown_days_count": bl.shutdown_days_count,
        "notes": bl.notes,
        "created_at": bl.created_at.isoformat() if bl.created_at else None,
        "created_by": bl.created_by,
        "is_pinned": bl.is_pinned,
    }


# ═══════════════════════════════════════════════════════════════════
# Сравнение: текущая статистика vs каждый из baseline-ов
# ═══════════════════════════════════════════════════════════════════

def compare_to_baselines(
    current_stats: dict, baselines: list[dict],
) -> list[dict]:
    """Для каждого baseline посчитать дельты текущих метрик vs baseline.

    Возвращает список dict с ключами baseline + delta_<metric>(_pct).
    """
    metrics = [
        "q_total_avg", "q_total_median",
        "q_working_avg", "q_working_median",
        "p_wellhead_avg", "p_wellhead_median",
        "p_flowline_avg", "p_flowline_median",
        "dp_avg", "dp_median",
        "shutdown_min_total", "shutdown_min_avg",
    ]
    result = []
    for bl in baselines:
        row = dict(bl)  # копируем baseline
        deltas = {}
        for m in metrics:
            cur = current_stats.get(m)
            base = bl.get(m)
            if cur is None or base is None:
                deltas[f"delta_{m}"] = None
                deltas[f"delta_{m}_pct"] = None
                continue
            d = cur - base
            deltas[f"delta_{m}"] = round(d, 3)
            deltas[f"delta_{m}_pct"] = (
                round((d / abs(base)) * 100, 2) if abs(base) > 1e-9 else None
            )
        row["deltas"] = deltas
        result.append(row)
    return result
