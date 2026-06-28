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

def _agreement_with_verdict(sensor_vals: list, customer_vals: list, label: str) -> dict:
    """Анализ согласия двух рядов: отклонение, тренды, смещение/разброс, задержка.

    Корреляция (Pearson) отвечает «совпадает ли форма», смещение (bias) — «есть ли
    систематическая погрешность величин», MAPE — случайный разброс, кросс-корреляция
    со сдвигом — задержка одного источника. Возвращает метрики + текстовый вывод.
    """
    import numpy as np

    s, c = [], []
    for sv, cv in zip(sensor_vals, customer_vals):
        if sv is None or cv is None:
            continue
        try:
            sf, cf = float(sv), float(cv)
        except (TypeError, ValueError):
            continue
        if sf != sf or cf != cf:  # NaN
            continue
        s.append(sf); c.append(cf)
    n = len(s)
    if n < 3:
        return {"label": label, "n": n, "insufficient": True,
                "verdict": f"{label}: мало совпадающих дней для анализа согласия."}

    sa, ca = np.array(s), np.array(c)
    diff = sa - ca
    bias_abs = float(np.mean(diff))
    c_mean = float(np.mean(ca))
    bias_pct = (bias_abs / abs(c_mean) * 100) if c_mean else None
    scatter_std = float(np.std(diff, ddof=1)) if n > 1 else 0.0
    nz = np.abs(ca) > 1e-9
    mape = float(np.mean(np.abs(diff[nz] / ca[nz])) * 100) if nz.any() else None
    EPS = 1e-9
    s_flat = float(np.std(sa)) <= EPS
    c_flat = float(np.std(ca)) <= EPS
    if not s_flat and not c_flat:
        try:
            r = float(np.corrcoef(sa, ca)[0, 1])
            if r != r:  # NaN
                r = None
        except Exception:
            r = None
    else:
        r = None
    x = np.arange(n)
    slope_s = float(np.polyfit(x, sa, 1)[0])
    slope_c = float(np.polyfit(x, ca, 1)[0])
    if s_flat or c_flat:
        trend_match = None
    else:
        trend_match = (slope_s > 0) == (slope_c > 0)

    # Задержка: сдвиг customer на d дней, ищем максимум корреляции
    best_lag, best_corr = 0, -2.0
    L = min(4, n // 3)
    for d in range(-L, L + 1):
        if d >= 0:
            a_, b_ = sa[:n - d], ca[d:]
        else:
            a_, b_ = sa[-d:], ca[:n + d]
        if len(a_) >= 3 and float(np.std(a_)) > 0 and float(np.std(b_)) > 0:
            cc = float(np.corrcoef(a_, b_)[0, 1])
            if cc > best_corr:
                best_corr, best_lag = cc, d

    # Текстовый вывод
    if trend_match is None:
        parts = ["один из рядов постоянный (тренды не сравнимы)"]
    elif trend_match:
        parts = ["тренды совпадают по направлению"]
    else:
        parts = ["тренды расходятся по направлению"]
    if bias_pct is not None:
        if abs(bias_pct) < 10:
            parts.append(f"смещение мало ({bias_pct:+.0f}%)")
        else:
            parts.append(f"систематическое смещение {bias_pct:+.0f}% (LoRa {'выше' if bias_pct > 0 else 'ниже'})")
    if mape is not None:
        parts.append(f"разброс {mape:.0f}% (MAPE)")
    if r is not None:
        parts.append(f"совпадение формы r={r:.2f}")
    if best_lag:
        parts.append(f"возможная задержка ~{abs(best_lag)} дн.")
    else:
        parts.append("без задержки")
    verdict = f"{label}: " + "; ".join(parts) + "."

    return {
        "label": label, "n": n,
        "bias_abs": round(bias_abs, 3),
        "bias_pct": round(bias_pct, 1) if bias_pct is not None else None,
        "scatter_std": round(scatter_std, 3),
        "mape": round(mape, 1) if mape is not None else None,
        "pearson_r": round(r, 3) if r is not None else None,
        "slope_sensor": round(slope_s, 4),
        "slope_customer": round(slope_c, 4),
        "trend_match": bool(trend_match),
        "best_lag_days": int(best_lag),
        "lag_corr": round(best_corr, 3) if best_corr > -2 else None,
        "verdict": verdict,
    }


def _segment_trend(dates: list, vals: list, *, min_phase: int = 2) -> list[dict]:
    """Разбивает ряд на фазы рост/падение/стабильно по сглаженной кривой.

    Лёгкое сглаживание (окно 3) + порог «стабильно» 6% размаха; короткие фазы
    (< min_phase шагов) поглощаются соседними, соседние одинаковые — сливаются.
    Возвращает [{dir, dir_ru, from, to}].
    """
    import numpy as np

    pairs = [(d, v) for d, v in zip(dates, vals) if v is not None and v == v]
    if len(pairs) < 3:
        return []
    ds = [p[0] for p in pairs]
    ys = np.array([float(p[1]) for p in pairs])
    n = len(ys)
    sm = np.array([ys[max(0, i - 1):min(n, i + 2)].mean() for i in range(n)])
    rng = float(np.max(ys) - np.min(ys)) or 1.0
    flat = 0.06 * rng
    dirs = []
    for i in range(1, n):
        dv = sm[i] - sm[i - 1]
        dirs.append(0 if abs(dv) < flat else (1 if dv > 0 else -1))
    # подряд одинаковые направления → фазы
    phases = []
    i = 0
    while i < len(dirs):
        j = i
        while j + 1 < len(dirs) and dirs[j + 1] == dirs[i]:
            j += 1
        phases.append({"dir": dirs[i], "i0": i, "i1": j + 1})
        i = j + 1
    # поглотить короткие фазы соседями
    changed = True
    while changed and len(phases) > 1:
        changed = False
        for k in range(len(phases)):
            if phases[k]["i1"] - phases[k]["i0"] < min_phase and len(phases) > 1:
                if k == 0:
                    phases[1]["i0"] = phases[0]["i0"]
                    phases.pop(0)
                else:
                    phases[k - 1]["i1"] = phases[k]["i1"]
                    phases.pop(k)
                changed = True
                break
    # слить соседние одинаковые направления
    merged = [phases[0]]
    for ph in phases[1:]:
        if ph["dir"] == merged[-1]["dir"]:
            merged[-1]["i1"] = ph["i1"]
        else:
            merged.append(ph)
    DIRRU = {1: "рост", -1: "падение", 0: "стабильно"}
    return [{"dir": ph["dir"], "dir_ru": DIRRU[ph["dir"]],
             "from": ds[ph["i0"]], "to": ds[ph["i1"]]} for ph in merged]


def _trend_character(dates: list, s_vals: list, c_vals: list,
                     label: str = "Дебит Q (рабочий)") -> dict:
    """Сравнение ХАРАКТЕРА динамики LoRa и УзКорГаз — ОБЪЕКТИВНО, по нескольким
    устойчивым мерам (одна посуточная доля совпадения шумна и занижает оценку при
    задержке/разной дискретности):

      1) ОБЩИЙ ТРЕНД — знак наклона сглаженного ряда (рост/падение/без тренда);
      2) ФОРМА С УЧЁТОМ ЗАДЕРЖКИ — макс. кросс-корреляция при сдвиге ±L дней
         (r_lag) + сама задержка (учитывает запаздывание сводок заказчика);
      3) посуточное совпадение направлений (день-в-день) — приводится как есть,
         но НЕ доминирует в вердикте (чувствительно к суточной дискретности).

    Итоговый класс (match_level) и вердикт взвешивают (1)+(2) — устойчивые меры.
    """
    import numpy as np

    s_phases = _segment_trend(dates, s_vals)
    c_phases = _segment_trend(dates, c_vals)
    s_seq = " → ".join(p["dir_ru"] for p in s_phases) if s_phases else "—"
    c_seq = " → ".join(p["dir_ru"] for p in c_phases) if c_phases else "—"
    base = {"label": label, "sensor_phases": s_phases, "customer_phases": c_phases,
            "sensor_seq": s_seq, "customer_seq": c_seq}

    pairs = [(sv, cv) for sv, cv in zip(s_vals, c_vals)
             if sv is not None and cv is not None and sv == sv and cv == cv]
    n = len(pairs)
    if n < 3:
        base.update(agreement_pct=None, agreement_lag_pct=None, best_lag=0,
                    shape_corr=None, overall_sensor="—", overall_customer="—",
                    overall_match=None, match_level="insufficient",
                    verdict="Недостаточно совпадающих дней для оценки характера динамики.")
        return base

    sa = np.array([float(p[0]) for p in pairs])
    ca = np.array([float(p[1]) for p in pairs])

    def _smooth(a):
        return np.array([a[max(0, i - 1):min(len(a), i + 2)].mean() for i in range(len(a))])
    sm_s, sm_c = _smooth(sa), _smooth(ca)
    rng_s = float(sm_s.max() - sm_s.min()) or 1.0
    rng_c = float(sm_c.max() - sm_c.min()) or 1.0
    DIRRU = {1: "рост", -1: "падение", 0: "без выраженного тренда"}

    # 1) Общий тренд: суммарное изменение тренда против размаха
    x = np.arange(n)
    slope_s = float(np.polyfit(x, sm_s, 1)[0])
    slope_c = float(np.polyfit(x, sm_c, 1)[0])

    def _odir(slope, rng):
        change = slope * (n - 1)
        return 0 if abs(change) < 0.10 * rng else (1 if change > 0 else -1)
    od_s, od_c = _odir(slope_s, rng_s), _odir(slope_c, rng_c)
    overall_match = bool(od_s == od_c and od_s != 0)

    # 2) Форма с учётом задержки: кросс-корреляция при сдвиге, r при лучшем лаге
    best_lag, best_corr = 0, -2.0
    L = min(4, n // 3)
    for d in range(-L, L + 1):
        a_, b_ = (sm_s[:n - d], sm_c[d:]) if d >= 0 else (sm_s[-d:], sm_c[:n + d])
        if len(a_) >= 3 and a_.std() > 0 and b_.std() > 0:
            cc = float(np.corrcoef(a_, b_)[0, 1])
            if cc > best_corr:
                best_corr, best_lag = cc, d
    shape_corr = round(best_corr, 2) if best_corr > -2 else None

    # 3) Совпадение направлений (на сглаженных): день-в-день и с учётом задержки
    def _dir_agree(a, b):
        fa, fb = 0.06 * (float(a.max() - a.min()) or 1.0), 0.06 * (float(b.max() - b.min()) or 1.0)
        ag = tot = 0
        for i in range(1, len(a)):
            tot += 1
            da, db = a[i] - a[i - 1], b[i] - b[i - 1]
            sda = 0 if abs(da) < fa else (1 if da > 0 else -1)
            sdb = 0 if abs(db) < fb else (1 if db > 0 else -1)
            if sda == sdb:
                ag += 1
        return round(ag / tot * 100) if tot else None
    agreement_pct = _dir_agree(sm_s, sm_c)
    al, bl = (sm_s[:n - best_lag], sm_c[best_lag:]) if best_lag >= 0 else (sm_s[-best_lag:], sm_c[:n + best_lag])
    agreement_lag_pct = _dir_agree(al, bl) if len(al) >= 3 else agreement_pct

    # ── Класс совпадения по УСТОЙЧИВЫМ мерам (тренд + форма с лагом) ──
    strong_shape = shape_corr is not None and shape_corr >= 0.5
    mod_shape = shape_corr is not None and shape_corr >= 0.3
    if overall_match and (strong_shape or (agreement_lag_pct or 0) >= 65):
        match_level, head = "full", "Характер динамики в целом совпадает"
    elif overall_match or mod_shape or (agreement_lag_pct or 0) >= 55:
        match_level, head = "partial", "Характер динамики совпадает частично"
    else:
        match_level, head = "none", "Характер динамики расходится"

    bits = []
    if overall_match:
        bits.append(f"по общему тренду совпадают (оба — {DIRRU[od_s]})")
    elif od_s == 0 or od_c == 0:
        bits.append(f"общий тренд: LoRa — {DIRRU[od_s]}, УзКорГаз — {DIRRU[od_c]}")
    else:
        bits.append(f"общий тренд противоположен (LoRa — {DIRRU[od_s]}, УзКорГаз — {DIRRU[od_c]})")
    if shape_corr is not None:
        form = "согласуется" if strong_shape else ("согласуется умеренно" if mod_shape else "слабо согласуется")
        if best_lag:
            bits.append(f"с учётом задержки ~{abs(best_lag)} дн форма {form} (r={shape_corr:.2f})")
        else:
            bits.append(f"форма {form} (r={shape_corr:.2f})")
    if agreement_pct is not None:
        bits.append(f"посуточно направления совпадают на {agreement_pct:.0f}% "
                    f"(суточная дискретность сводок заказчика снижает посуточное совпадение)")
    verdict = f"{head}: LoRa — {s_seq}; УзКорГаз — {c_seq}; " + "; ".join(bits) + "."

    base.update(agreement_pct=agreement_pct, agreement_lag_pct=agreement_lag_pct,
                best_lag=int(best_lag), shape_corr=shape_corr,
                overall_sensor=DIRRU[od_s], overall_customer=DIRRU[od_c],
                overall_match=overall_match, match_level=match_level, verdict=verdict)
    return base


def _methodology_text(well_number: str, period_from: str, period_to: str) -> str:
    """Текстовое описание методики сопоставления (единый источник HTML/PDF)."""
    return (
        f"Сопоставлены данные манометров LoRa скважины №{well_number} "
        f"(автоматическая запись по расписанию: день 07:00–22:00 — каждые 5 минут, "
        f"ночь — каждые 30 минут; минутный ряд агрегируется до суточных значений) "
        f"с суточными сводками УзКорГаз за период {period_from} — {period_to}. "
        f"ВАЖНО: данные УзКорГаз имеют суточную дискретность (одно значение в сутки), "
        f"тогда как LoRa — частый ряд. Различие дискретности и моментов осреднения "
        f"может приводить к расхождениям, не связанным с реальным режимом работы скважины."
    )


def _comparison_conclusion(summary: dict, trend_character: dict | None,
                           analysis: dict | None) -> str:
    """Текстовое заключение с УЧЁТОМ ДВУХ сравнений: по величине (отклонение) и
    по соответствию (характер динамики + форма). Единый источник для build и
    enrich (старые блоки) — принцип «всё из анализа → в отчёт»."""
    summary = summary or {}
    d_q_pct = summary.get("delta_q_pct")
    d_dp_pct = summary.get("delta_dp_pct")
    days_matched = summary.get("days_matched") or 0
    days_total = summary.get("days_total") or 0
    if days_matched == 0:
        return ("За выбранный период отсутствуют совпадающие данные "
                "мониторинга LoRa и суточных сводок УзКорГаз.")
    parts = []
    q_ok = d_q_pct is not None and abs(d_q_pct) < 10
    dp_ok = d_dp_pct is not None and abs(d_dp_pct) < 10
    if q_ok and dp_ok:
        parts.append("Данные мониторинга LoRa согласуются с суточными сводками УзКорГаз.")
    elif q_ok or dp_ok:
        parts.append(
            "Данные частично согласуются: "
            f"{'дебит в пределах нормы' if q_ok else 'расхождение по дебиту'}; "
            f"{'ΔP в пределах нормы' if dp_ok else 'расхождение по ΔP'}.")
    else:
        parts.append("Выявлено существенное расхождение между данными мониторинга "
                     "LoRa и суточными сводками УзКорГаз.")
    # Сравнение 1: по величине
    if d_q_pct is not None:
        parts.append(f"Среднее отклонение дебита: {'+' if d_q_pct >= 0 else ''}{d_q_pct:.1f}%.")
    if d_dp_pct is not None:
        parts.append(f"Среднее отклонение ΔP: {'+' if d_dp_pct >= 0 else ''}{d_dp_pct:.1f}%.")
    # Сравнение 2: соответствие (характер + форма)
    tc = trend_character or {}
    if tc.get("verdict"):
        parts.append("Анализ соответствия: " + tc["verdict"])
    aw = (analysis or {}).get("q_working") or {}
    if not aw.get("insufficient") and aw.get("pearson_r") is not None:
        r = aw["pearson_r"]
        form = ("форма кривых хорошо совпадает" if r >= 0.7
                else "форма кривых совпадает умеренно" if r >= 0.4
                else "форма кривых слабо совпадает")
        lag = aw.get("best_lag_days") or 0
        lag_txt = f", возможная задержка ~{abs(lag)} дн." if lag else ""
        parts.append(f"Соответствие формы r={r:.2f} — {form}{lag_txt}.")
    parts.append(f"Сопоставлено {days_matched} из {days_total} дней.")
    return " ".join(parts)


def _dev_tolerances(display: dict | None) -> tuple[float, float]:
    """Пороги зон допуска (%): t1 — «в допуске», t2 — «повышенное». Регулируются
    из расчёта (snapshot.display.dev_tol_green / dev_tol_amber). Дефолт 15 / 30."""
    d = display or {}
    def _pos(v, default):
        try:
            v = float(v)
            return v if v > 0 else default
        except (TypeError, ValueError):
            return default
    t1 = _pos(d.get("dev_tol_green"), 15.0)
    t2 = _pos(d.get("dev_tol_amber"), 30.0)
    if t2 <= t1:
        t2 = t1 + 1.0
    return t1, t2


def _deviation_summary_from_daily(daily_diff: list, t1: float = 15.0, t2: float = 30.0) -> str:
    """Текстовый итог отклонения по зонам (к данным LoRa) из daily_diff."""
    dev = [(r.get("date"), r["delta_q"] / r["sensor_q"] * 100)
           for r in daily_diff
           if r.get("delta_q") is not None and r.get("sensor_q")]
    if not dev:
        return ""
    n = len(dev)
    green = sum(1 for _, p in dev if abs(p) <= t1)
    amber = sum(1 for _, p in dev if t1 < abs(p) <= t2)
    red = sum(1 for _, p in dev if abs(p) > t2)
    wdate, worst = max(dev, key=lambda kv: abs(kv[1]))
    return (f"Относительное отклонение LoRa от УзКорГаз (к данным LoRa) по дням: "
            f"в допуске ±{t1:.0f}% — {green} из {n} дн.; повышенное {t1:.0f}–{t2:.0f}% — {amber} дн.; "
            f"значительное >{t2:.0f}% — {red} дн. Максимум {abs(worst):.0f}% "
            f"({wdate}, LoRa {'выше' if worst > 0 else 'ниже'} заказчика).")


def _trend_characters_from_daily(daily_diff: list) -> list[dict]:
    """Объективный характер динамики для ТРЁХ показателей: Q рабочий, Q общий, ΔP."""
    dates = [r.get("date") for r in daily_diff]
    series = [
        ("Дебит Q (рабочий)", "sensor_q", "customer_q"),
        ("Дебит Q (общий)", "sensor_q_total", "customer_q_total"),
        ("Перепад ΔP", "sensor_dp", "customer_dp"),
    ]
    out = []
    for label, sk, ck in series:
        s = [r.get(sk) for r in daily_diff]
        c = [r.get(ck) for r in daily_diff]
        if any(v is not None for v in s) and any(v is not None for v in c):
            out.append(_trend_character(dates, s, c, label))
    return out


def enrich_scc_snapshot(snap: dict) -> dict:
    """Дополняет snapshot блока sensor_customer_comparison ПРОИЗВОДНЫМИ полями
    анализа (analysis / trend_character / methodology / deviation_summary +
    обновлённое conclusion), если их нет — для блоков, сохранённых до появления
    этих секций. Считает из daily_diff (без БД). Принцип: всё, что есть в анализе,
    попадает в отчёт и для ранее сохранённых блоков (HTML и PDF)."""
    if not isinstance(snap, dict):
        return snap
    dd = snap.get("daily_diff") or []
    if not dd:
        return snap
    dates = [r.get("date") for r in dd]
    s_q = [r.get("sensor_q") for r in dd]
    c_q = [r.get("customer_q") for r in dd]
    s_qt = [r.get("sensor_q_total") for r in dd]
    c_qt = [r.get("customer_q_total") for r in dd]
    s_dp = [r.get("sensor_dp") for r in dd]
    c_dp = [r.get("customer_dp") for r in dd]
    if not snap.get("analysis"):
        snap["analysis"] = {
            "q_working": _agreement_with_verdict(s_q, c_q, "Дебит Q (рабочий)"),
            "q_total":   _agreement_with_verdict(s_qt, c_qt, "Дебит Q (общий)"),
            "dp":        _agreement_with_verdict(s_dp, c_dp, "Перепад ΔP"),
        }
    # Характер динамики по ТРЁМ показателям (Q раб / Q общ / ΔP)
    if not snap.get("trend_characters"):
        snap["trend_characters"] = _trend_characters_from_daily(dd)
    if not snap.get("trend_character"):
        snap["trend_character"] = (snap["trend_characters"][0]
                                   if snap.get("trend_characters")
                                   else _trend_character(dates, s_q, c_q, "Дебит Q (рабочий)"))
    if not snap.get("methodology"):
        per = snap.get("period") or {}
        pf = snap.get("period_from") or per.get("from") or ""
        pt = snap.get("period_to") or per.get("to") or ""
        snap["methodology"] = _methodology_text(snap.get("well_number") or "", pf, pt)
    # Итог отклонения — ВСЕГДА пересчитываем под текущие пороги display
    t1, t2 = _dev_tolerances(snap.get("display"))
    snap["deviation_summary"] = _deviation_summary_from_daily(dd, t1, t2)
    # Обновляем conclusion старого формата (без анализа соответствия)
    if "Анализ соответствия" not in (snap.get("conclusion") or "") and snap.get("summary"):
        snap["conclusion"] = _comparison_conclusion(
            snap.get("summary"), snap.get("trend_character"), snap.get("analysis"))
    return snap


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
    # Два дебита: q_total (общий) и q_working (рабочий) + dp.
    metrics = ["q_total", "q_working", "dp"]
    sensor_data = {}
    customer_data = {}

    for metric in metrics:
        # Наши данные (our_flow для Q, our_pressure для ΔP)
        source = "our_flow" if metric in ("q_total", "q_working") else "our_pressure"
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

    # ΔP (перепад) по соглашению не может быть отрицательным: все значения < 0
    # приравниваем к 0 (шкала графика — от нуля). Кламп до построения кривых,
    # daily_diff и сводки, чтобы во всех слоях были одинаковые значения.
    for _d in (sensor_data, customer_data):
        _dp = _d.get("dp")
        if _dp and _dp.get("values"):
            _dp["values"] = [None if v is None else max(0.0, v) for v in _dp["values"]]

    result["curves"] = {
        # Q рабочий (основной — для обратной совместимости)
        "sensor_q": sensor_data.get("q_working", {}),
        "customer_q": customer_data.get("q_working", {}),
        # Q общий (второй дебит — наложение второй кривой)
        "sensor_q_total": sensor_data.get("q_total", {}),
        "customer_q_total": customer_data.get("q_total", {}),
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
    sensor_qt_map = to_dict(sensor_data.get("q_total", {}))
    customer_qt_map = to_dict(customer_data.get("q_total", {}))
    sensor_dp_map = to_dict(sensor_data.get("dp", {}))
    customer_dp_map = to_dict(customer_data.get("dp", {}))

    daily_diff = []
    for d in all_dates:
        s_q = sensor_q_map.get(d)
        c_q = customer_q_map.get(d)
        s_qt = sensor_qt_map.get(d)
        c_qt = customer_qt_map.get(d)
        s_dp = sensor_dp_map.get(d)
        c_dp = customer_dp_map.get(d)

        delta_q = (s_q - c_q) if (s_q is not None and c_q is not None) else None
        delta_q_total = (s_qt - c_qt) if (s_qt is not None and c_qt is not None) else None
        delta_dp = (s_dp - c_dp) if (s_dp is not None and c_dp is not None) else None

        daily_diff.append({
            "date": d,
            "sensor_q": s_q,
            "customer_q": c_q,
            "delta_q": round(delta_q, 3) if delta_q is not None else None,
            "sensor_q_total": s_qt,
            "customer_q_total": c_qt,
            "delta_q_total": round(delta_q_total, 3) if delta_q_total is not None else None,
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
    s_qt_vals = [r["sensor_q_total"] for r in daily_diff]
    c_qt_vals = [r["customer_q_total"] for r in daily_diff]
    s_dp_vals = [r["sensor_dp"] for r in daily_diff]
    c_dp_vals = [r["customer_dp"] for r in daily_diff]
    d_q_vals = [r["delta_q"] for r in daily_diff]
    d_qt_vals = [r["delta_q_total"] for r in daily_diff]
    d_dp_vals = [r["delta_dp"] for r in daily_diff]

    s_q_avg = safe_mean(s_q_vals)
    c_q_avg = safe_mean(c_q_vals)
    s_qt_avg = safe_mean(s_qt_vals)
    c_qt_avg = safe_mean(c_qt_vals)
    s_dp_avg = safe_mean(s_dp_vals)
    c_dp_avg = safe_mean(c_dp_vals)
    d_q_avg = safe_mean(d_q_vals)
    d_qt_avg = safe_mean(d_qt_vals)
    d_dp_avg = safe_mean(d_dp_vals)

    # Процент отклонения
    d_q_pct = (d_q_avg / abs(c_q_avg) * 100) if (d_q_avg is not None and c_q_avg) else None
    d_qt_pct = (d_qt_avg / abs(c_qt_avg) * 100) if (d_qt_avg is not None and c_qt_avg) else None
    d_dp_pct = (d_dp_avg / abs(c_dp_avg) * 100) if (d_dp_avg is not None and c_dp_avg) else None

    days_matched = sum(1 for r in daily_diff if r["sensor_q"] is not None and r["customer_q"] is not None)

    result["summary"] = {
        # Q рабочий
        "sensor_q_avg": round(s_q_avg, 2) if s_q_avg is not None else None,
        "customer_q_avg": round(c_q_avg, 2) if c_q_avg is not None else None,
        "delta_q_avg": round(d_q_avg, 3) if d_q_avg is not None else None,
        "delta_q_pct": round(d_q_pct, 1) if d_q_pct is not None else None,
        # Q общий (второй дебит)
        "sensor_q_total_avg": round(s_qt_avg, 2) if s_qt_avg is not None else None,
        "customer_q_total_avg": round(c_qt_avg, 2) if c_qt_avg is not None else None,
        "delta_q_total_avg": round(d_qt_avg, 3) if d_qt_avg is not None else None,
        "delta_q_total_pct": round(d_qt_pct, 1) if d_qt_pct is not None else None,
        "sensor_dp_avg": round(s_dp_avg, 2) if s_dp_avg is not None else None,
        "customer_dp_avg": round(c_dp_avg, 2) if c_dp_avg is not None else None,
        "delta_dp_avg": round(d_dp_avg, 3) if d_dp_avg is not None else None,
        "delta_dp_pct": round(d_dp_pct, 1) if d_dp_pct is not None else None,
        "days_total": len(all_dates),
        "days_matched": days_matched,
    }

    # ── Анализ согласия (отклонение, тренды, смещение/разброс, задержка) ──
    result["analysis"] = {
        "q_working": _agreement_with_verdict(s_q_vals, c_q_vals, "Дебит Q (рабочий)"),
        "q_total":   _agreement_with_verdict(s_qt_vals, c_qt_vals, "Дебит Q (общий)"),
        "dp":        _agreement_with_verdict(s_dp_vals, c_dp_vals, "Перепад ΔP"),
    }

    # ── Характер динамики по ТРЁМ показателям (Q рабочий / Q общий / ΔP) ──
    result["trend_characters"] = _trend_characters_from_daily(daily_diff)
    result["trend_character"] = (result["trend_characters"][0]
                                 if result["trend_characters"] else None)

    # ── Методика сопоставления (манометры LoRa ↔ суточные сводки УзКорГаз) ──
    result["methodology"] = _methodology_text(
        well_number, result["period_from"], result["period_to"])

    # ── Текстовый итог отклонения по зонам (нормировка на данные LoRa) ──
    result["deviation_summary"] = _deviation_summary_from_daily(daily_diff)

    # ── Текстовое заключение (обе оценки: ВЕЛИЧИНА + СООТВЕТСТВИЕ) ──
    result["conclusion"] = _comparison_conclusion(
        result["summary"], result.get("trend_character"), result.get("analysis"))

    return result
