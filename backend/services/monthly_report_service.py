"""
Сервис ежемесячных отчётов.

Максимально переиспользует существующие функции:
- collect_stage_stats() — общий анализ периода
- compute_monthly_stats() — список месяцев
- analyze_reagent_effectiveness() — ИРВ
- list_baselines(), compare_to_baselines() — сравнение с baseline
- compute_segment_preview() — сегментный анализ
"""
from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models.wells import Well

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  CRUD для таблицы monthly_report
# ═══════════════════════════════════════════════════════════════════

_TABLE_INITIALIZED = False


def _ensure_table(db: Session) -> None:
    """Создать таблицу если не существует (для dev без миграций)."""
    global _TABLE_INITIALIZED
    if _TABLE_INITIALIZED:
        return
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS monthly_report (
            id          SERIAL PRIMARY KEY,
            well_id     INTEGER NOT NULL REFERENCES wells(id) ON DELETE CASCADE,
            period_from DATE NOT NULL,
            period_to   DATE NOT NULL,
            period_label VARCHAR(50),
            title       VARCHAR(255),
            status      VARCHAR(20) NOT NULL DEFAULT 'draft',
            created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by  VARCHAR(100),
            data_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb
        )
    """))
    db.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_monthly_report_well_period
        ON monthly_report (well_id, period_from, period_to)
    """))
    db.commit()
    _TABLE_INITIALIZED = True


def list_reports(db: Session, well_id: int) -> list[dict]:
    """Список отчётов скважины (без data_snapshot для скорости)."""
    _ensure_table(db)
    rows = db.execute(text("""
        SELECT id, period_from, period_to, period_label, title, status,
               created_at, updated_at, created_by
        FROM monthly_report
        WHERE well_id = :wid
        ORDER BY period_from DESC
    """), {"wid": well_id}).fetchall()
    return [
        {
            "id": r[0],
            "period_from": r[1].isoformat() if r[1] else None,
            "period_to": r[2].isoformat() if r[2] else None,
            "period_label": r[3],
            "title": r[4],
            "status": r[5],
            "created_at": r[6].isoformat() if r[6] else None,
            "updated_at": r[7].isoformat() if r[7] else None,
            "created_by": r[8],
        }
        for r in rows
    ]


def get_report(db: Session, report_id: int) -> dict | None:
    """Получить отчёт по ID (с data_snapshot)."""
    _ensure_table(db)
    row = db.execute(text("""
        SELECT id, well_id, period_from, period_to, period_label, title,
               status, created_at, updated_at, created_by, data_snapshot
        FROM monthly_report WHERE id = :rid
    """), {"rid": report_id}).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "well_id": row[1],
        "period_from": row[2].isoformat() if row[2] else None,
        "period_to": row[3].isoformat() if row[3] else None,
        "period_label": row[4],
        "title": row[5],
        "status": row[6],
        "created_at": row[7].isoformat() if row[7] else None,
        "updated_at": row[8].isoformat() if row[8] else None,
        "created_by": row[9],
        "data_snapshot": row[10] or {},
    }


def create_report(
    db: Session,
    well_id: int,
    period_from: date,
    period_to: date,
    period_label: str | None = None,
    title: str | None = None,
    created_by: str | None = None,
    data_snapshot: dict | None = None,
) -> dict:
    """Создать новый отчёт."""
    import json
    _ensure_table(db)

    if not period_label:
        period_label = _format_period_label(period_from, period_to)
    if not title:
        title = f"Отчёт об эффективности проведённых работ за {period_label}"

    row = db.execute(text("""
        INSERT INTO monthly_report
            (well_id, period_from, period_to, period_label, title, created_by, data_snapshot)
        VALUES (:wid, :pf, :pt, :pl, :t, :cb, CAST(:ds AS JSONB))
        RETURNING id, created_at
    """), {
        "wid": well_id,
        "pf": period_from,
        "pt": period_to,
        "pl": period_label,
        "t": title,
        "cb": created_by,
        "ds": json.dumps(data_snapshot or {}, default=str),
    }).fetchone()
    db.commit()
    return {
        "id": row[0],
        "well_id": well_id,
        "period_from": period_from.isoformat(),
        "period_to": period_to.isoformat(),
        "period_label": period_label,
        "title": title,
        "status": "draft",
        "created_at": row[1].isoformat() if row[1] else None,
    }


def update_report(
    db: Session,
    report_id: int,
    data_snapshot: dict | None = None,
    title: str | None = None,
    status: str | None = None,
) -> dict | None:
    """Обновить отчёт."""
    import json
    _ensure_table(db)

    updates = ["updated_at = CURRENT_TIMESTAMP"]
    params: dict[str, Any] = {"rid": report_id}

    if data_snapshot is not None:
        updates.append("data_snapshot = CAST(:ds AS JSONB)")
        params["ds"] = json.dumps(data_snapshot, default=str)
    if title is not None:
        updates.append("title = :t")
        params["t"] = title
    if status is not None:
        updates.append("status = :s")
        params["s"] = status

    db.execute(text(f"""
        UPDATE monthly_report SET {', '.join(updates)} WHERE id = :rid
    """), params)
    db.commit()
    return get_report(db, report_id)


def delete_report(db: Session, report_id: int) -> bool:
    """Удалить отчёт."""
    _ensure_table(db)
    result = db.execute(
        text("DELETE FROM monthly_report WHERE id = :rid"),
        {"rid": report_id},
    )
    db.commit()
    return result.rowcount > 0


# ═══════════════════════════════════════════════════════════════════
#  Вспомогательные функции
# ═══════════════════════════════════════════════════════════════════

def _format_period_label(period_from: date, period_to: date) -> str:
    """Форматировать метку периода."""
    months_ru = [
        "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
        "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
    ]
    # Если полный календарный месяц
    if period_from.day == 1:
        last_day = monthrange(period_from.year, period_from.month)[1]
        if period_to.day == last_day and period_from.month == period_to.month:
            return f"{months_ru[period_from.month]} {period_from.year}"
    # Произвольный период
    return f"{period_from.strftime('%d.%m.%Y')} — {period_to.strftime('%d.%m.%Y')}"


def get_calendar_months(db: Session, well_id: int) -> list[dict]:
    """Список календарных месяцев с данными для скважины."""
    # Переиспользуем compute_monthly_stats
    from backend.services.adaptation_report_service import compute_monthly_stats

    months = compute_monthly_stats(db, well_id, months_back=36)
    return [
        {
            "year_month": m.get("year_month"),
            "label": m.get("label"),
            "q_mean": m.get("q_mean"),
            "has_data": m.get("hours_with_data", 0) > 0,
        }
        for m in months
    ]


def get_month_boundaries(year: int, month: int) -> tuple[date, date]:
    """Получить границы календарного месяца."""
    first_day = date(year, month, 1)
    last_day = date(year, month, monthrange(year, month)[1])
    return first_day, last_day


# ═══════════════════════════════════════════════════════════════════
#  Обёртки над существующими сервисами (без дублирования логики)
# ═══════════════════════════════════════════════════════════════════

def compute_general_analysis(
    db: Session,
    well_id: int,
    period_from: date,
    period_to: date,
) -> dict:
    """Общий анализ периода — переиспользует collect_stage_stats."""
    from backend.services.adaptation_report_service import collect_stage_stats

    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        return {"error": "Скважина не найдена"}

    # Штуцер
    row = db.execute(text("""
        SELECT choke_diam_mm FROM well_construction
        WHERE well_no = :wno
        ORDER BY data_as_of DESC NULLS LAST LIMIT 1
    """), {"wno": str(well.number)}).fetchone()
    choke_mm = float(row[0]) if row and row[0] else None

    stats = collect_stage_stats(
        db, well, choke_mm, period_from, period_to,
        render_charts=False, chart_tag="monthly",
    )
    return stats


def compute_irv_summary(
    db: Session,
    well_id: int,
    period_from: date,
    period_to: date,
) -> dict:
    """Сводка по ИРВ за период — переиспользует analyze_reagent_effectiveness."""
    from backend.services.reagent_effectiveness_service import analyze_reagent_effectiveness

    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        return {"error": "Скважина не найдена"}

    result = analyze_reagent_effectiveness(
        db,
        well_number=str(well.number),
        date_from=period_from,
        date_to=period_to,
    )

    irv_list = result.get("irv_list", [])
    if not irv_list:
        return {"irv_count": 0, "score_avg": None}

    scores = [irv.get("score") for irv in irv_list if irv.get("score") is not None]
    best_irv = max(irv_list, key=lambda x: x.get("score") or 0, default=None)
    worst_irv = min(irv_list, key=lambda x: x.get("score") or 100, default=None)

    return {
        "irv_count": len(irv_list),
        "score_avg": sum(scores) / len(scores) if scores else None,
        "score_min": min(scores) if scores else None,
        "score_max": max(scores) if scores else None,
        "best_irv": best_irv,
        "worst_irv": worst_irv,
        "total_dose_kg": sum(irv.get("dose_kg") or 0 for irv in irv_list),
    }


def compute_comparison(
    db: Session,
    well_id: int,
    period_from: date,
    period_to: date,
) -> dict:
    """Сравнение с прошлым месяцем и baseline."""
    from backend.services.customer_baseline_service import list_baselines, compare_to_baselines

    # Текущий период
    current = compute_general_analysis(db, well_id, period_from, period_to)

    # Прошлый месяц (сдвиг на 1 месяц назад)
    prev_to = period_from - timedelta(days=1)
    prev_from = prev_to.replace(day=1)
    prev = compute_general_analysis(db, well_id, prev_from, prev_to)

    comparison_prev = None
    if prev.get("flow_avg") is not None and current.get("flow_avg") is not None:
        delta_q = current["flow_avg"] - prev["flow_avg"]
        delta_pct = (delta_q / prev["flow_avg"] * 100) if prev["flow_avg"] else None
        comparison_prev = {
            "period_label": _format_period_label(prev_from, prev_to),
            "prev_q_avg": prev.get("flow_avg"),
            "current_q_avg": current.get("flow_avg"),
            "delta_q_avg": delta_q,
            "delta_q_pct": delta_pct,
        }

    # Сравнение с baseline
    baselines = list_baselines(db, well_id)
    comparison_baseline = compare_to_baselines(current, baselines) if baselines else None

    return {
        "comparison_prev_month": comparison_prev,
        "comparison_baseline": comparison_baseline,
        "baselines": baselines,
    }


def compute_segments(
    db: Session,
    well_id: int,
    period_from: date,
    period_to: date,
) -> dict:
    """Сегментный анализ — переиспользует compute_segment_preview."""
    from backend.services.observation_segment_service import compute_segment_preview

    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        return {"error": "Скважина не найдена"}

    return compute_segment_preview(
        db,
        well_id=well_id,
        date_from=period_from,
        date_to=period_to,
    )
