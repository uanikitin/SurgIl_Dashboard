"""Сервис «Конструктора сравнения» для отчёта адаптации.

Концепция: один набор (`ComparisonSet`) — это один график с N кривыми.
Каждая кривая (`ComparisonCurve`) описывает «откуда брать ряд»:
  * source='customer'     — well_daily (сводка УзКорГаз);
  * source='our_pressure' — наши манометры (live: pressure_raw → агрегация);
  * source='our_flow'     — наш расчётный дебит (live flow_rate);
  * source='baseline'     — зафиксированный CustomerBaseline (горизонтальная
                            линия на y=baseline_value).

Для трёх первых источников переиспользуем существующую функцию
`customer_daily_service.time_series()` — НЕ дублируем выборки.
Для baseline возвращаем `baseline_value` + пустые dates/values; рендер
(UI/PDF) рисует горизонтальную линию на этом значении через всю ось X.

Ось X в сводном результате:
  * x_axis_mode='offset' — каждая кривая преобразуется в [0, 1, ..., N−1]
    (день от начала своего периода), что позволяет накладывать периоды
    разной длины и из разных дат друг на друга.
  * x_axis_mode='date'   — фактические даты.

Скважина одна на набор (требование пользователя). Передаётся в
`build_set` как `well_number` — берётся из `set.well_id` через `wells`.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from backend.models.comparison_set import ComparisonSet
from backend.models.comparison_curve import ComparisonCurve
from backend.models.customer_baseline import CustomerBaseline
from backend.models.wells import Well
from backend.services import customer_daily_service as _cds

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Константы / валидация
# ═══════════════════════════════════════════════════════════════════

VALID_SOURCES = {"customer", "our_pressure", "our_flow", "baseline"}
VALID_METRICS = {"q_total", "q_working", "dp", "p_wellhead", "p_flowline"}
VALID_X_AXIS = {"offset", "date"}

# Источники с ограничениями по метрикам (синхронно с time_series).
_METRICS_BY_SOURCE: dict[str, set[str]] = {
    "customer":     {"q_total", "q_working", "dp", "p_wellhead", "p_flowline"},
    "our_pressure": {"dp", "p_wellhead", "p_flowline"},
    "our_flow":     {"q_total", "q_working", "dp"},
    "baseline":     {"q_total", "q_working", "dp", "p_wellhead", "p_flowline"},
}

# Соответствие metric → поле в CustomerBaseline (берём avg как «характерное»).
_BASELINE_FIELD: dict[str, str] = {
    "q_total":    "q_total_avg",
    "q_working":  "q_working_avg",
    "dp":         "dp_avg",
    "p_wellhead": "p_wellhead_avg",
    "p_flowline": "p_flowline_avg",
}


def validate_curve_payload(
    *,
    source: str,
    metric: str,
    baseline_id: int | None,
    period_from: date | None,
    period_to: date | None,
) -> None:
    """Проверка корректности параметров кривой. Бросает ValueError."""
    if source not in VALID_SOURCES:
        raise ValueError(f"source должен быть one of {sorted(VALID_SOURCES)}")
    if metric not in VALID_METRICS:
        raise ValueError(f"metric должна быть one of {sorted(VALID_METRICS)}")
    if metric not in _METRICS_BY_SOURCE[source]:
        raise ValueError(
            f"Метрика '{metric}' не поддерживается источником '{source}'. "
            f"Допустимы: {sorted(_METRICS_BY_SOURCE[source])}"
        )
    if source == "baseline":
        if not baseline_id:
            raise ValueError("Для source='baseline' нужен baseline_id")
    else:
        if not (period_from and period_to):
            raise ValueError(
                f"Для source='{source}' нужны period_from и period_to"
            )
        if period_from > period_to:
            raise ValueError("period_from > period_to")


# ═══════════════════════════════════════════════════════════════════
#  Чтение well_number по well_id
# ═══════════════════════════════════════════════════════════════════

def _well_number(db: Session, well_id: int) -> str | None:
    w = db.query(Well).filter(Well.id == well_id).first()
    if not w:
        return None
    return str(w.number)


# ═══════════════════════════════════════════════════════════════════
#  Построение одной кривой
# ═══════════════════════════════════════════════════════════════════

def build_curve(
    db: Session,
    curve: ComparisonCurve,
    *,
    well_number: str,
) -> dict[str, Any]:
    """Построить одну кривую: вернуть её точки + метаданные.

    Формат:
        {
          ok: bool, error?: str,
          curve_id: int, label: str, color: str|None,
          source: str, metric: str,
          period_from: iso|None, period_to: iso|None,
          dates: [iso, ...], values: [float|None, ...],
          baseline_value: float|None,   # только для source='baseline'
          description: str|None,
        }
    """
    base = {
        "curve_id": curve.id,
        "label": curve.label,
        "color": curve.color,
        "source": curve.source,
        "metric": curve.metric,
        "description": curve.description,
        "dates": [],
        "values": [],
        "baseline_value": None,
        "period_from": None,
        "period_to": None,
    }

    # ── source='baseline' → одно число на горизонтальную линию ──
    if curve.source == "baseline":
        if not curve.baseline_id:
            return {**base, "ok": False, "error": "baseline_id не задан"}
        bl = db.query(CustomerBaseline).filter(
            CustomerBaseline.id == curve.baseline_id,
        ).first()
        if not bl:
            return {**base, "ok": False, "error": f"Baseline #{curve.baseline_id} не найден"}
        field = _BASELINE_FIELD.get(curve.metric)
        if not field:
            return {**base, "ok": False,
                    "error": f"Нет baseline-поля для metric={curve.metric}"}
        return {
            **base,
            "ok": True,
            "baseline_value": getattr(bl, field, None),
            "period_from": bl.period_from.isoformat() if bl.period_from else None,
            "period_to":   bl.period_to.isoformat()   if bl.period_to   else None,
        }

    # ── customer / our_pressure / our_flow → time_series ──
    if not (curve.period_from and curve.period_to):
        return {**base, "ok": False, "error": "Не задан период"}

    series = _cds.time_series(
        db,
        source=curve.source, well=well_number, metric=curve.metric,
        d_from=curve.period_from, d_to=curve.period_to,
    )
    if not series.get("ok"):
        return {**base, "ok": False, "error": series.get("error", "unknown")}

    return {
        **base,
        "ok": True,
        "dates":  series.get("dates",  []),
        "values": series.get("values", []),
        "period_from": curve.period_from.isoformat(),
        "period_to":   curve.period_to.isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════
#  Построение полного набора (для UI и рендера PDF)
# ═══════════════════════════════════════════════════════════════════

def build_set(db: Session, set_obj: ComparisonSet) -> dict[str, Any]:
    """Построить набор: все кривые + общая ось X (offset|date).

    Возвращает структуру для UI/Plotly:
        {
          set_id, name, description, x_axis_mode, x_label,
          curves: [
            { ...build_curve_result..., x: [...], }
          ]
        }
    """
    well_number = _well_number(db, set_obj.well_id)
    if not well_number:
        return {
            "set_id": set_obj.id, "name": set_obj.name,
            "description": set_obj.description,
            "x_axis_mode": set_obj.x_axis_mode,
            "curves": [],
            "error": f"Скважина id={set_obj.well_id} не найдена",
        }

    raw_curves = [
        build_curve(db, c, well_number=well_number)
        for c in set_obj.curves
    ]

    if set_obj.x_axis_mode == "offset":
        x_label = "День от начала периода"
        for c in raw_curves:
            n = len(c["dates"])
            c["x"] = list(range(n))
    else:
        x_label = "Дата"
        for c in raw_curves:
            c["x"] = c["dates"]

    return {
        "set_id": set_obj.id,
        "name": set_obj.name,
        "description": set_obj.description,
        "x_axis_mode": set_obj.x_axis_mode,
        "x_label": x_label,
        "in_report": set_obj.in_report,
        "curves": raw_curves,
    }


# ═══════════════════════════════════════════════════════════════════
#  CRUD: наборы
# ═══════════════════════════════════════════════════════════════════

def list_sets(db: Session, well_id: int) -> list[dict[str, Any]]:
    sets = (
        db.query(ComparisonSet)
        .filter(ComparisonSet.well_id == well_id)
        .order_by(ComparisonSet.sort_order, ComparisonSet.id)
        .all()
    )
    return [_set_to_dict(s) for s in sets]


def get_set(db: Session, set_id: int) -> ComparisonSet | None:
    return db.query(ComparisonSet).filter(ComparisonSet.id == set_id).first()


def create_set(
    db: Session,
    *,
    well_id: int,
    name: str,
    description: str | None = None,
    x_axis_mode: str = "offset",
    in_report: bool = True,
    created_by: str | None = None,
) -> ComparisonSet:
    if x_axis_mode not in VALID_X_AXIS:
        raise ValueError(f"x_axis_mode должен быть one of {sorted(VALID_X_AXIS)}")
    s = ComparisonSet(
        well_id=well_id,
        name=name.strip() or "Сравнение",
        description=description,
        x_axis_mode=x_axis_mode,
        in_report=in_report,
        created_by=created_by,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def update_set(
    db: Session, set_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    x_axis_mode: str | None = None,
    in_report: bool | None = None,
    sort_order: int | None = None,
) -> ComparisonSet | None:
    s = get_set(db, set_id)
    if not s:
        return None
    if name is not None:
        s.name = name.strip() or s.name
    if description is not None:
        s.description = description or None
    if x_axis_mode is not None:
        if x_axis_mode not in VALID_X_AXIS:
            raise ValueError(f"x_axis_mode должен быть one of {sorted(VALID_X_AXIS)}")
        s.x_axis_mode = x_axis_mode
    if in_report is not None:
        s.in_report = bool(in_report)
    if sort_order is not None:
        s.sort_order = int(sort_order)
    db.commit()
    db.refresh(s)
    return s


def delete_set(db: Session, set_id: int) -> bool:
    s = get_set(db, set_id)
    if not s:
        return False
    db.delete(s)
    db.commit()
    return True


# ═══════════════════════════════════════════════════════════════════
#  CRUD: кривые
# ═══════════════════════════════════════════════════════════════════

def add_curve(
    db: Session, set_id: int,
    *,
    source: str, metric: str,
    label: str,
    baseline_id: int | None = None,
    period_from: date | None = None,
    period_to: date | None = None,
    color: str | None = None,
    description: str | None = None,
) -> ComparisonCurve:
    s = get_set(db, set_id)
    if not s:
        raise ValueError(f"ComparisonSet id={set_id} не найден")
    validate_curve_payload(
        source=source, metric=metric, baseline_id=baseline_id,
        period_from=period_from, period_to=period_to,
    )
    # Auto-order: следующий index
    max_order = (
        db.query(ComparisonCurve.order_index)
        .filter(ComparisonCurve.set_id == set_id)
        .order_by(ComparisonCurve.order_index.desc())
        .first()
    )
    next_idx = (max_order[0] + 1) if max_order else 0
    c = ComparisonCurve(
        set_id=set_id,
        order_index=next_idx,
        source=source,
        baseline_id=baseline_id if source == "baseline" else None,
        period_from=period_from if source != "baseline" else None,
        period_to=period_to     if source != "baseline" else None,
        metric=metric,
        label=label.strip() or f"{source}/{metric}",
        color=color,
        description=description,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def update_curve(
    db: Session, curve_id: int,
    *,
    source: str | None = None,
    metric: str | None = None,
    label: str | None = None,
    baseline_id: int | None = None,
    period_from: date | None = None,
    period_to: date | None = None,
    color: str | None = None,
    description: str | None = None,
    order_index: int | None = None,
) -> ComparisonCurve | None:
    c = db.query(ComparisonCurve).filter(ComparisonCurve.id == curve_id).first()
    if not c:
        return None
    new_source = source if source is not None else c.source
    new_metric = metric if metric is not None else c.metric
    new_baseline_id = baseline_id if baseline_id is not None else c.baseline_id
    new_pf = period_from if period_from is not None else c.period_from
    new_pt = period_to   if period_to   is not None else c.period_to
    validate_curve_payload(
        source=new_source, metric=new_metric, baseline_id=new_baseline_id,
        period_from=new_pf, period_to=new_pt,
    )
    c.source = new_source
    c.metric = new_metric
    if new_source == "baseline":
        c.baseline_id = new_baseline_id
        c.period_from = None
        c.period_to = None
    else:
        c.baseline_id = None
        c.period_from = new_pf
        c.period_to = new_pt
    if label is not None:
        c.label = label.strip() or c.label
    if color is not None:
        c.color = color or None
    if description is not None:
        c.description = description or None
    if order_index is not None:
        c.order_index = int(order_index)
    db.commit()
    db.refresh(c)
    return c


def delete_curve(db: Session, curve_id: int) -> bool:
    c = db.query(ComparisonCurve).filter(ComparisonCurve.id == curve_id).first()
    if not c:
        return False
    db.delete(c)
    db.commit()
    return True


# ═══════════════════════════════════════════════════════════════════
#  Сериализация
# ═══════════════════════════════════════════════════════════════════

def _set_to_dict(s: ComparisonSet) -> dict[str, Any]:
    return {
        "id": s.id,
        "well_id": s.well_id,
        "name": s.name,
        "description": s.description,
        "x_axis_mode": s.x_axis_mode,
        "in_report": s.in_report,
        "sort_order": s.sort_order,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        "created_by": s.created_by,
        "curves_count": len(s.curves),
    }


def curve_to_dict(c: ComparisonCurve) -> dict[str, Any]:
    return {
        "id": c.id,
        "set_id": c.set_id,
        "order_index": c.order_index,
        "source": c.source,
        "baseline_id": c.baseline_id,
        "period_from": c.period_from.isoformat() if c.period_from else None,
        "period_to":   c.period_to.isoformat()   if c.period_to   else None,
        "metric": c.metric,
        "label": c.label,
        "color": c.color,
        "description": c.description,
    }


def set_with_curves(db: Session, set_id: int) -> dict[str, Any] | None:
    s = get_set(db, set_id)
    if not s:
        return None
    out = _set_to_dict(s)
    out["curves"] = [curve_to_dict(c) for c in s.curves]
    return out
