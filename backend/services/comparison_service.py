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


# ═══════════════════════════════════════════════════════════════════
#  Сопоставление данных LoRa и УзКорГаз (sensor_customer_comparison)
# ═══════════════════════════════════════════════════════════════════

def build_sensor_customer_comparison(
    db: Session,
    *,
    well_number: str,
    period_from: date,
    period_to: date,
) -> dict[str, Any]:
    """Сравнение данных мониторинга LoRa с суточными сводками УзКорГаз.

    Возвращает структуру для UI и snapshot:
        {
          ok: bool, error?: str,
          period_from, period_to, well_number,
          curves: {sensor_q, customer_q, sensor_dp, customer_dp},
          daily_diff: [{date, sensor_q, customer_q, delta_q, ...}, ...],
          summary: {sensor_q_avg, customer_q_avg, delta_q_avg, delta_q_pct, ...},
          conclusion: str,
        }
    """
    result = {
        "ok": True,
        "period_from": period_from.isoformat(),
        "period_to": period_to.isoformat(),
        "well_number": well_number,
        "curves": {},
        "daily_diff": [],
        "summary": {},
        "conclusion": "",
    }

    # ── Загрузка данных через time_series ──
    metrics = ["q_working", "dp"]
    sensor_data = {}
    customer_data = {}

    for metric in metrics:
        # Наши данные (our_flow для Q, our_pressure для ΔP)
        source = "our_flow" if metric == "q_working" else "our_pressure"
        sensor = _cds.time_series(
            db, source=source, well=well_number, metric=metric,
            d_from=period_from, d_to=period_to,
        )
        if sensor.get("ok"):
            sensor_data[metric] = {
                "dates": sensor.get("dates", []),
                "values": sensor.get("values", []),
            }
        else:
            sensor_data[metric] = {"dates": [], "values": []}

        # Данные заказчика
        customer = _cds.time_series(
            db, source="customer", well=well_number, metric=metric,
            d_from=period_from, d_to=period_to,
        )
        if customer.get("ok"):
            customer_data[metric] = {
                "dates": customer.get("dates", []),
                "values": customer.get("values", []),
            }
        else:
            customer_data[metric] = {"dates": [], "values": []}

    result["curves"] = {
        "sensor_q": sensor_data.get("q_working", {}),
        "customer_q": customer_data.get("q_working", {}),
        "sensor_dp": sensor_data.get("dp", {}),
        "customer_dp": customer_data.get("dp", {}),
    }

    # ── Построение daily_diff ──
    # Собираем все уникальные даты
    all_dates = set()
    for metric in metrics:
        all_dates.update(sensor_data.get(metric, {}).get("dates", []))
        all_dates.update(customer_data.get(metric, {}).get("dates", []))
    all_dates = sorted(all_dates)

    # Индексируем по дате
    def to_dict(data: dict) -> dict:
        return dict(zip(data.get("dates", []), data.get("values", [])))

    sensor_q_map = to_dict(sensor_data.get("q_working", {}))
    customer_q_map = to_dict(customer_data.get("q_working", {}))
    sensor_dp_map = to_dict(sensor_data.get("dp", {}))
    customer_dp_map = to_dict(customer_data.get("dp", {}))

    daily_diff = []
    for d in all_dates:
        s_q = sensor_q_map.get(d)
        c_q = customer_q_map.get(d)
        s_dp = sensor_dp_map.get(d)
        c_dp = customer_dp_map.get(d)

        delta_q = (s_q - c_q) if (s_q is not None and c_q is not None) else None
        delta_dp = (s_dp - c_dp) if (s_dp is not None and c_dp is not None) else None

        daily_diff.append({
            "date": d,
            "sensor_q": s_q,
            "customer_q": c_q,
            "delta_q": round(delta_q, 3) if delta_q is not None else None,
            "sensor_dp": s_dp,
            "customer_dp": c_dp,
            "delta_dp": round(delta_dp, 3) if delta_dp is not None else None,
        })

    result["daily_diff"] = daily_diff

    # ── Сводная статистика ──
    def safe_mean(vals: list) -> float | None:
        clean = [v for v in vals if v is not None]
        return sum(clean) / len(clean) if clean else None

    s_q_vals = [r["sensor_q"] for r in daily_diff]
    c_q_vals = [r["customer_q"] for r in daily_diff]
    s_dp_vals = [r["sensor_dp"] for r in daily_diff]
    c_dp_vals = [r["customer_dp"] for r in daily_diff]
    d_q_vals = [r["delta_q"] for r in daily_diff]
    d_dp_vals = [r["delta_dp"] for r in daily_diff]

    s_q_avg = safe_mean(s_q_vals)
    c_q_avg = safe_mean(c_q_vals)
    s_dp_avg = safe_mean(s_dp_vals)
    c_dp_avg = safe_mean(c_dp_vals)
    d_q_avg = safe_mean(d_q_vals)
    d_dp_avg = safe_mean(d_dp_vals)

    # Процент отклонения
    d_q_pct = (d_q_avg / abs(c_q_avg) * 100) if (d_q_avg is not None and c_q_avg) else None
    d_dp_pct = (d_dp_avg / abs(c_dp_avg) * 100) if (d_dp_avg is not None and c_dp_avg) else None

    days_matched = sum(1 for r in daily_diff if r["sensor_q"] is not None and r["customer_q"] is not None)

    result["summary"] = {
        "sensor_q_avg": round(s_q_avg, 2) if s_q_avg is not None else None,
        "customer_q_avg": round(c_q_avg, 2) if c_q_avg is not None else None,
        "delta_q_avg": round(d_q_avg, 3) if d_q_avg is not None else None,
        "delta_q_pct": round(d_q_pct, 1) if d_q_pct is not None else None,
        "sensor_dp_avg": round(s_dp_avg, 2) if s_dp_avg is not None else None,
        "customer_dp_avg": round(c_dp_avg, 2) if c_dp_avg is not None else None,
        "delta_dp_avg": round(d_dp_avg, 3) if d_dp_avg is not None else None,
        "delta_dp_pct": round(d_dp_pct, 1) if d_dp_pct is not None else None,
        "days_total": len(all_dates),
        "days_matched": days_matched,
    }

    # ── Текстовое заключение ──
    conclusion_parts = []
    if days_matched == 0:
        conclusion_parts.append(
            "За выбранный период отсутствуют совпадающие данные "
            "мониторинга LoRa и суточных сводок УзКорГаз."
        )
    else:
        # Оценка согласованности
        q_ok = d_q_pct is not None and abs(d_q_pct) < 10
        dp_ok = d_dp_pct is not None and abs(d_dp_pct) < 10

        if q_ok and dp_ok:
            conclusion_parts.append(
                "Данные мониторинга LoRa согласуются с суточными сводками УзКорГаз."
            )
        elif q_ok or dp_ok:
            conclusion_parts.append(
                "Данные частично согласуются: "
                f"{'дебит в пределах нормы' if q_ok else 'расхождение по дебиту'}; "
                f"{'ΔP в пределах нормы' if dp_ok else 'расхождение по ΔP'}."
            )
        else:
            conclusion_parts.append(
                "Выявлено существенное расхождение между данными мониторинга LoRa "
                "и суточными сводками УзКорГаз."
            )

        # Детали
        if d_q_pct is not None:
            sign = "+" if d_q_pct >= 0 else ""
            conclusion_parts.append(
                f"Среднее отклонение дебита: {sign}{d_q_pct:.1f}%."
            )
        if d_dp_pct is not None:
            sign = "+" if d_dp_pct >= 0 else ""
            conclusion_parts.append(
                f"Среднее отклонение ΔP: {sign}{d_dp_pct:.1f}%."
            )

        conclusion_parts.append(f"Сопоставлено {days_matched} из {len(all_dates)} дней.")

    result["conclusion"] = " ".join(conclusion_parts)

    return result
