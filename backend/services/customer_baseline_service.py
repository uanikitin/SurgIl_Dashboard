"""Сервис baseline-ов скважины (на данных заказчика).

Расчёт характерных значений за заданный период из таблицы well_daily,
сохранение/чтение/удаление в customer_baseline. Используется в адаптационном
отчёте: фиксируем «базовые» показатели (например, 1 месяц до начала работ),
потом сравниваем с другими периодами.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta
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
# Конструктор «опорного периода» по якорю и пресету
# ═══════════════════════════════════════════════════════════════════

PRESET_LABELS = {
    "week":     "1 нед.",
    "month":    "1 мес.",
    "calendar": "календ. мес.",
    "custom":   "произв.",
    "manual":   "своя дата",
}


def resolve_period(
    anchor: date,
    preset: str,
    *,
    n_days: int | None = None,
    direction: str = "before",
    include_anchor: bool = False,
) -> tuple[date, date]:
    """Построить (period_from, period_to) от опорной даты `anchor`.

    Args:
        anchor: точка отсчёта (обычно начало этапа Наблюдения/Адаптации).
        preset: 'week' | 'month' | 'calendar' | 'custom'.
            week — 7 дней, month — 30 дней, calendar — календарный месяц
            (с 1-го по последнее число месяца, в который попадает «якорь−1»),
            custom — произвольное число дней `n_days`.
        n_days: длина периода в днях для preset='custom' (>=1).
        direction: 'before' — период ДО якоря; 'after' — ПОСЛЕ якоря.
        include_anchor: True — крайний день включает сам `anchor`;
            False — крайний день = anchor ± 1 день (т.е. период
            примыкает к якорю, не пересекаясь с ним).

    Returns:
        (period_from, period_to), где period_from <= period_to.
    """
    if not isinstance(anchor, date):
        raise ValueError("anchor must be a date")
    if direction not in ("before", "after"):
        raise ValueError("direction must be 'before' or 'after'")

    if preset == "calendar":
        # Календарный месяц «до» — месяц, в который попадает якорь−1.
        # «После» — месяц, в который попадает якорь+1.
        ref = anchor - timedelta(days=1) if direction == "before" else anchor + timedelta(days=1)
        first = ref.replace(day=1)
        last_day = calendar.monthrange(ref.year, ref.month)[1]
        last = ref.replace(day=last_day)
        return (first, last)

    if preset == "week":
        length = 7
    elif preset == "month":
        length = 30
    elif preset == "custom":
        if n_days is None or n_days < 1:
            raise ValueError("custom preset requires n_days >= 1")
        length = int(n_days)
    else:
        raise ValueError(f"unknown preset: {preset!r}")

    if direction == "before":
        period_to = anchor if include_anchor else anchor - timedelta(days=1)
        period_from = period_to - timedelta(days=length - 1)
    else:
        period_from = anchor if include_anchor else anchor + timedelta(days=1)
        period_to = period_from + timedelta(days=length - 1)
    return (period_from, period_to)


def make_period_name(
    anchor_label: str,
    preset: str,
    *,
    n_days: int | None = None,
    period_from: date | None = None,
    period_to: date | None = None,
) -> str:
    """Сгенерировать человекочитаемое имя для baseline.

    Пример: 'Базовый: 1 мес. до Наблюдения (12.03–11.04)'.
    """
    if preset == "custom" and n_days:
        preset_label = f"{n_days} дн."
    else:
        preset_label = PRESET_LABELS.get(preset, preset)
    parts = [f"Базовый: {preset_label} до {anchor_label}"]
    if period_from and period_to:
        parts.append(f"({period_from.strftime('%d.%m')}–{period_to.strftime('%d.%m')})")
    return " ".join(parts)


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
    *,
    well_id: int | None = None,
    window_days: int = 3,
) -> dict[str, Any]:
    """Полный анализ периода: статистика + ряды для графиков + простои по дням.

    Дополнительные блоки (если передан well_id):
      - measurements: количество сырых точек pressure_raw по каждому датчику
      - purges: продувки (кол-во, интервалы) из events
      - reagents: вбросы реагента (список, сводка по типам, периодичность)
      - best_window: наиболее эффективное окно внутри периода (detect_optimal_windows)

    Используется в UI вкладки «Исходные данные» и в PDF-главе.
    """
    _ensure_well_daily(db)
    df = load_for_well(db, well_number, d_from=period_from, d_to=period_to)

    base = compute_period_stats(db, well_number, period_from, period_to)
    base["description"] = description
    base["window_days"] = window_days

    # Доп. блоки по сырым данным/событиям (если есть well_id)
    if well_id is not None:
        dt_from = datetime.combine(period_from, datetime.min.time())
        dt_to = datetime.combine(period_to, datetime.max.time().replace(microsecond=0))
        try:
            base["measurements"] = _count_raw_points_by_sensor(
                db, well_id, dt_from, dt_to,
            )
        except Exception as exc:  # pragma: no cover — не блокируем основной анализ
            log.exception("measurements failed: %s", exc)
            base["measurements"] = None
        try:
            base["purges"] = _analyze_purges(db, well_number, dt_from, dt_to)
        except Exception as exc:
            log.exception("purges failed: %s", exc)
            base["purges"] = None
        try:
            base["reagents"] = _analyze_reagents(db, well_number, dt_from, dt_to)
        except Exception as exc:
            log.exception("reagents failed: %s", exc)
            base["reagents"] = None
        try:
            base["best_window"] = _detect_best_window(
                db, well_id, dt_from, dt_to, window_days=window_days,
            )
        except Exception as exc:
            log.exception("best_window failed: %s", exc)
            base["best_window"] = None

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
# Доп. блоки анализа периода: measurements, purges, reagents, best window
# ═══════════════════════════════════════════════════════════════════

def _count_raw_points_by_sensor(
    db: Session, well_id: int, dt_from: datetime, dt_to: datetime,
) -> dict[str, Any]:
    """Количество сырых точек в pressure_raw по каждому датчику за период.

    Возвращает списки по tube/line с {sensor_id, label, serial, total, non_null,
    non_zero}. total — все записи с участием датчика, non_null — где значение
    присутствует, non_zero — где значение реально (>0); «ложные нули» LoRa
    SMOD-PT-60 составляют ~4% и учитываются отдельно.
    """
    # Даты в Кунград-локальном времени; pressure_raw — UTC
    from backend.services.adaptation_report_service import KUNGRAD_OFFSET
    utc_from = dt_from - KUNGRAD_OFFSET
    utc_to = dt_to - KUNGRAD_OFFSET

    def _side(column_val: str, column_sensor: str) -> list[dict[str, Any]]:
        q = text(f"""
            SELECT pr.{column_sensor} AS sid,
                   COUNT(*) AS total,
                   COUNT(pr.{column_val}) AS non_null,
                   COUNT(*) FILTER (WHERE pr.{column_val} IS NOT NULL
                                      AND pr.{column_val} > 0) AS non_zero,
                   ls.serial_number,
                   ls.label
            FROM pressure_raw pr
            LEFT JOIN lora_sensors ls ON ls.id = pr.{column_sensor}
            WHERE pr.well_id = :wid
              AND pr.measured_at BETWEEN :t0 AND :t1
              AND pr.{column_sensor} IS NOT NULL
            GROUP BY pr.{column_sensor}, ls.serial_number, ls.label
            ORDER BY total DESC
        """)
        rows = db.execute(q, {"wid": well_id, "t0": utc_from, "t1": utc_to}).fetchall()
        return [
            {
                "sensor_id": int(r[0]) if r[0] is not None else None,
                "total": int(r[1] or 0),
                "non_null": int(r[2] or 0),
                "non_zero": int(r[3] or 0),
                "serial": r[4],
                "label": r[5],
            }
            for r in rows
        ]

    tube = _side("p_tube", "sensor_id_tube")
    line = _side("p_line", "sensor_id_line")
    # Фоллбэк: без sensor_id — суммарно по стороне
    q_summary = text("""
        SELECT
            COUNT(p_tube) AS tube_non_null,
            COUNT(*) FILTER (WHERE p_tube IS NOT NULL AND p_tube > 0) AS tube_nz,
            COUNT(p_line) AS line_non_null,
            COUNT(*) FILTER (WHERE p_line IS NOT NULL AND p_line > 0) AS line_nz,
            COUNT(*) AS total_rows
        FROM pressure_raw
        WHERE well_id = :wid AND measured_at BETWEEN :t0 AND :t1
    """)
    s = db.execute(q_summary, {"wid": well_id, "t0": utc_from, "t1": utc_to}).fetchone()
    return {
        "period_rows": int(s[4] or 0) if s else 0,
        "tube_total_non_null": int(s[0] or 0) if s else 0,
        "tube_total_non_zero": int(s[1] or 0) if s else 0,
        "line_total_non_null": int(s[2] or 0) if s else 0,
        "line_total_non_zero": int(s[3] or 0) if s else 0,
        "tube_by_sensor": tube,
        "line_by_sensor": line,
    }


def _analyze_purges(
    db: Session, well_number: str, dt_from: datetime, dt_to: datetime,
) -> dict[str, Any]:
    """Продувки за период: кол-во циклов, ср./мед. интервал между ними.

    Один полный цикл продувки = 3 события в `events` (start → press → stop).
    Считаем циклы по числу маркеров 'start'; легаси-данные без phase
    учитываются как одно событие = один цикл.
    """
    rows = db.execute(text("""
        SELECT event_time FROM events
        WHERE well = :wno AND event_type = 'purge'
          AND (purge_phase = 'start' OR purge_phase IS NULL)
          AND event_time BETWEEN :t0 AND :t1
        ORDER BY event_time
    """), {"wno": str(well_number), "t0": dt_from, "t1": dt_to}).fetchall()
    times = [r[0] for r in rows if r[0]]
    count = len(times)

    days_total = max((dt_to - dt_from).total_seconds() / 86400.0, 1.0)
    freq_per_day = count / days_total if count else 0.0

    intervals_hr: list[float] = []
    for i in range(1, len(times)):
        dh = (times[i] - times[i - 1]).total_seconds() / 3600.0
        if dh > 0:
            intervals_hr.append(dh)

    return {
        "count": count,
        "frequency_per_day": round(freq_per_day, 3),
        "avg_interval_hours": (
            round(float(np.mean(intervals_hr)), 2) if intervals_hr else None
        ),
        "median_interval_hours": (
            round(float(np.median(intervals_hr)), 2) if intervals_hr else None
        ),
        "min_interval_hours": (
            round(min(intervals_hr), 2) if intervals_hr else None
        ),
        "max_interval_hours": (
            round(max(intervals_hr), 2) if intervals_hr else None
        ),
        "events": [t.isoformat(timespec="minutes") for t in times],
    }


def _analyze_reagents(
    db: Session, well_number: str, dt_from: datetime, dt_to: datetime,
) -> dict[str, Any]:
    """Вбросы реагента за период: кол-во, периодичность, сводка по типам.

    Возвращает:
      count, frequency_per_day, avg/median/min/max интервал (часы),
      by_type: [{reagent, injections, total_qty, avg_qty, share_pct}],
      injections: [{event_time, reagent, qty, p_tube, p_line}]
    """
    rows = db.execute(text("""
        SELECT event_time, reagent, qty, p_tube, p_line
        FROM events
        WHERE well = :wno AND event_type = 'reagent'
          AND event_time BETWEEN :t0 AND :t1
        ORDER BY event_time
    """), {"wno": str(well_number), "t0": dt_from, "t1": dt_to}).fetchall()

    injections = [
        {
            "event_time": r[0].isoformat(timespec="minutes") if r[0] else None,
            "reagent": (r[1] or "").strip() or None,
            "qty": _safe_float(r[2]),
            "p_tube": _safe_float(r[3]),
            "p_line": _safe_float(r[4]),
        }
        for r in rows
    ]
    times = [r[0] for r in rows if r[0]]
    count = len(times)
    days_total = max((dt_to - dt_from).total_seconds() / 86400.0, 1.0)

    intervals_hr: list[float] = []
    for i in range(1, len(times)):
        dh = (times[i] - times[i - 1]).total_seconds() / 3600.0
        if dh > 0:
            intervals_hr.append(dh)

    # Сводка по типам
    by_type: dict[str, dict[str, Any]] = {}
    total_qty = 0.0
    for inj in injections:
        name = inj["reagent"] or "—"
        g = by_type.setdefault(name, {"reagent": name, "injections": 0, "total_qty": 0.0})
        g["injections"] += 1
        if inj["qty"] is not None:
            g["total_qty"] += inj["qty"]
            total_qty += inj["qty"]
    by_type_list = []
    for g in by_type.values():
        n = g["injections"]
        tq = g["total_qty"]
        by_type_list.append({
            "reagent": g["reagent"],
            "injections": n,
            "total_qty": round(tq, 2) if tq else 0.0,
            "avg_qty": round(tq / n, 2) if n else None,
            "share_pct": round(100.0 * tq / total_qty, 1) if total_qty else None,
        })
    by_type_list.sort(key=lambda x: x["injections"], reverse=True)

    return {
        "count": count,
        "frequency_per_day": round(count / days_total, 3) if days_total else 0.0,
        "avg_interval_hours": (
            round(float(np.mean(intervals_hr)), 2) if intervals_hr else None
        ),
        "median_interval_hours": (
            round(float(np.median(intervals_hr)), 2) if intervals_hr else None
        ),
        "min_interval_hours": (
            round(min(intervals_hr), 2) if intervals_hr else None
        ),
        "max_interval_hours": (
            round(max(intervals_hr), 2) if intervals_hr else None
        ),
        "total_qty": round(total_qty, 2) if total_qty else 0.0,
        "by_type": by_type_list,
        "injections": injections,
    }


def _detect_best_window(
    db: Session, well_id: int, dt_from: datetime, dt_to: datetime,
    window_days: int = 3,
) -> dict[str, Any] | None:
    """Наиболее эффективное окно внутри периода.

    Использует существующий механизм `detect_optimal_windows` из
    adaptation_report_service, но с настраиваемым окном для UI.
    Также пытается связать выбранное окно с активным типом реагента:
      — берётся тип ПАВ с наибольшим числом вбросов внутри best-окна,
      — средний объём/интервал — по этому же подмножеству.
    """
    from backend.services.adaptation_report_service import detect_optimal_windows
    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        return None

    row = db.execute(text("""
        SELECT choke_diam_mm FROM well_construction
        WHERE well_no = :wno
        ORDER BY data_as_of DESC NULLS LAST LIMIT 1
    """), {"wno": str(well.number)}).fetchone()
    choke_mm = float(row[0]) if row and row[0] else None
    if not choke_mm:
        return None

    window_hours = max(24, int(window_days * 24))
    candidates = detect_optimal_windows(
        db, well, choke_mm,
        dt_from=dt_from, dt_to=dt_to,
        window_hours=window_hours, top_n=3, step_hours=6,
    )
    if not candidates:
        return None

    def _cand_with_reagents(c: dict[str, Any]) -> dict[str, Any]:
        w_from = c.get("start")
        w_to = c.get("end")
        out = {
            "start": w_from.isoformat(timespec="minutes") if w_from else None,
            "end": w_to.isoformat(timespec="minutes") if w_to else None,
            "q_mean": round(c.get("q_mean"), 2) if c.get("q_mean") is not None else None,
            "q_median": round(c.get("q_median"), 2) if c.get("q_median") is not None else None,
            "cv_q": round(c.get("cv_q"), 4) if c.get("cv_q") is not None else None,
            "utilization_pct": (
                round(c.get("utilization_pct"), 1)
                if c.get("utilization_pct") is not None else None
            ),
            "purge_freq_per_day": (
                round(c.get("purge_freq_per_day"), 3)
                if c.get("purge_freq_per_day") is not None else None
            ),
            "data_points": c.get("data_points"),
            "score": round(c.get("score", 0), 3),
            "reagent": None,
        }
        if w_from and w_to:
            out["reagent"] = _analyze_reagents(
                db, str(well.number), w_from, w_to,
            )
        return out

    best = _cand_with_reagents(candidates[0])
    others = [_cand_with_reagents(c) for c in candidates[1:]]
    return {
        "window_days": window_days,
        "best": best,
        "others": others,
        "choke_mm": choke_mm,
    }


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


def save_observation_baseline(
    db: Session, well_id: int,
    name: str,
    obs_from: datetime, obs_to: datetime,
    notes: str | None = None,
    created_by: str | None = None,
    is_pinned: bool = True,
) -> dict[str, Any]:
    """Зафиксировать baseline на основании НАШИХ данных за этап наблюдения.

    Использует тот же конвейер что /api/flow-rate/calculate
    (pressure_raw → clean → masks → flow_rate → aggregate_to_daily).
    Записывает результат в `customer_baseline` со source='observation'.

    Используется как опорная линия для сравнения этапа адаптации:
      «Δ adapt vs наблюдение» — насколько улучшилось ОТ нас.
    Параллельно с baseline source='customer' (от заказчика по well_daily).
    """
    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        raise ValueError(f"Скважина id={well_id} не найдена")

    # Используем единый live-pipeline из customer_daily_service
    # (он же что в /well, daily_report, adaptation_report).
    from backend.services import customer_daily_service as cdsvc
    if isinstance(obs_from, datetime):
        d_from = obs_from.date()
    else:
        d_from = obs_from
    if isinstance(obs_to, datetime):
        d_to = obs_to.date()
    else:
        d_to = obs_to

    daily_rows, meta = cdsvc._live_flow_daily(db, well_id, d_from, d_to)
    if not daily_rows:
        raise ValueError(
            "Нет данных pressure_raw за период наблюдения "
            f"({d_from}..{d_to}) — невозможно зафиксировать baseline."
        )

    # Агрегируем daily_rows → средние/медианы по этапу
    import statistics as _st
    def _avg(key):
        vs = [r.get(key) for r in daily_rows if r.get(key) is not None]
        return float(sum(vs) / len(vs)) if vs else None
    def _med(key):
        vs = [r.get(key) for r in daily_rows if r.get(key) is not None]
        return float(_st.median(vs)) if vs else None

    q_total_avg     = _avg("avg_flow_rate")
    q_total_median  = _med("avg_flow_rate")
    p_wellhead_avg  = _avg("avg_p_tube")
    p_wellhead_med  = _med("avg_p_tube")
    p_flowline_avg  = _avg("avg_p_line")
    p_flowline_med  = _med("avg_p_line")
    dp_avg          = _avg("avg_dp")
    dp_med          = _med("avg_dp")
    downtime_total  = sum(float(r.get("downtime_minutes") or 0) for r in daily_rows) or None
    downtime_avg    = (downtime_total / len(daily_rows)) if downtime_total else None
    downtime_days   = sum(1 for r in daily_rows if (r.get("downtime_minutes") or 0) > 0)

    # Q working = avg_flow_rate × (1440 − downtime) / 1440 — на каждом дне,
    # потом среднее. Если downtime отсутствует — q_working ≈ q_total.
    q_working_vals: list[float] = []
    for r in daily_rows:
        q = r.get("avg_flow_rate")
        if q is None: continue
        dm = r.get("downtime_minutes") or 0
        q_working_vals.append(float(q) * (1440.0 - dm) / 1440.0)
    q_working_avg = (sum(q_working_vals) / len(q_working_vals)) if q_working_vals else None
    q_working_med = float(_st.median(q_working_vals)) if q_working_vals else None

    bl = CustomerBaseline(
        well_id=well_id,
        name=name,
        source="observation",  # ← ключевое отличие от customer
        period_from=d_from,
        period_to=d_to,
        days_count=len(daily_rows),
        q_total_avg=q_total_avg,
        q_total_median=q_total_median,
        q_working_avg=q_working_avg,
        q_working_median=q_working_med,
        p_wellhead_avg=p_wellhead_avg,
        p_wellhead_median=p_wellhead_med,
        p_flowline_avg=p_flowline_avg,
        p_flowline_median=p_flowline_med,
        dp_avg=dp_avg,
        dp_median=dp_med,
        shutdown_min_total=downtime_total,
        shutdown_min_avg=downtime_avg,
        shutdown_days_count=downtime_days,
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

def compare_stage_to_baseline(
    stage: dict, baseline: dict | None,
) -> dict[str, Any]:
    """Сравнение этапа (наблюдение/адаптация) с одним baseline.

    Поля этапа из collect_report_data:
      flow_median, flow_avg, dp_median, p_tube_median, p_line_median,
      downtime_hours, utilization_pct.
    Поля baseline (CustomerBaseline):
      q_total_median, q_total_avg, dp_median,
      p_wellhead_median, p_flowline_median, shutdown_min_total.

    Возвращает dict с дельтами по каждой метрике (delta + delta_pct).
    Если baseline=None — все дельты None.
    """
    out: dict[str, Any] = {
        "baseline_id":   baseline.get("id") if baseline else None,
        "baseline_name": baseline.get("name") if baseline else None,
        "source":        baseline.get("source") if baseline else None,
    }
    if not baseline:
        for k in ("q_med","dp_med","p_tube_med","p_line_med","downtime"):
            out[f"delta_{k}"] = None
            out[f"delta_{k}_pct"] = None
        return out

    pairs = [
        ("q_med",      stage.get("flow_median"),     baseline.get("q_total_median")),
        ("dp_med",     stage.get("dp_median"),       baseline.get("dp_median")),
        ("p_tube_med", stage.get("p_tube_median"),   baseline.get("p_wellhead_median")),
        ("p_line_med", stage.get("p_line_median"),   baseline.get("p_flowline_median")),
        # downtime: у этапа в часах, у baseline в минутах. Переводим в часы.
        ("downtime",
         stage.get("downtime_hours"),
         (baseline.get("shutdown_min_total") or 0) / 60.0
            if baseline.get("shutdown_min_total") else None),
    ]
    for key, cur, base in pairs:
        if cur is None or base is None:
            out[f"delta_{key}"] = None
            out[f"delta_{key}_pct"] = None
            continue
        d = float(cur) - float(base)
        out[f"delta_{key}"] = round(d, 3)
        out[f"delta_{key}_pct"] = (
            round((d / abs(float(base))) * 100.0, 2) if abs(float(base)) > 1e-9 else None
        )
    return out


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
