"""
param_correlation_service — «Зависимость двух параметров» (scatter + регрессия).

Переиспользуемый модуль: облако точек X↔Y за период + линия регрессии (МНК) и R².
По умолчанию ΔP ↔ Q (LoRa) — больше перепад, выше дебит. Подключается в любую
главу через params.chapter.

Два источника данных:
  • LoRa (наши датчики) — поминутно (compute_full_flow): dp, q, p_tube, p_line.
  • УзКорГаз (суточные сводки) — посуточно (customer_daily.time_series).

Разрешение точек:
  • оба сигнала — чистый LoRa-физический (dp/q/p_tube/p_line) → ПОМИНУТНО (плотно).
  • иначе (участвует УзКорГаз или LoRa-суточный) → ПОСУТОЧНО (одна точка = день),
    с выравниванием по дате. Это позволяет сравнивать LoRa ↔ УзКорГаз (Q, P, ΔP).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import numpy as np

KUNGRAD_OFFSET = timedelta(hours=5)
MAX_POINTS = 1500          # прореживание поминутного облака
MIN_POINTS_MINUTE = 30
MIN_POINTS_DAILY = 3

# Метаданные сигналов (ключ → подпись + единицы)
SIGNAL_META = {
    # LoRa
    "dp":        {"label": "ΔP (LoRa)", "unit": "кгс/см²"},
    "q":         {"label": "Q дебит (LoRa)", "unit": "тыс.м³/сут"},
    "q_working": {"label": "Q рабочий (LoRa)", "unit": "тыс.м³/сут"},
    "p_tube":    {"label": "P устьевое (LoRa)", "unit": "кгс/см²"},
    "p_line":    {"label": "P линейное (LoRa)", "unit": "кгс/см²"},
    # УзКорГаз (суточные)
    "cust_q_total":    {"label": "Q общий (УзКорГаз)", "unit": "тыс.м³/сут"},
    "cust_q_working":  {"label": "Q рабочий (УзКорГаз)", "unit": "тыс.м³/сут"},
    "cust_dp":         {"label": "ΔP (УзКорГаз)", "unit": "кгс/см²"},
    "cust_p_wellhead": {"label": "P устьевое (УзКорГаз)", "unit": "кгс/см²"},
    "cust_p_flowline": {"label": "P линейное (УзКорГаз)", "unit": "кгс/см²"},
}

# Чистый LoRa-физический поминутный сигнал
LORA_MINUTE_KEYS = {"dp", "q", "p_tube", "p_line"}

_LORA_KEYS = {"dp", "q", "q_working", "p_tube", "p_line"}
_CUST_KEYS = {"cust_q_total", "cust_q_working", "cust_dp", "cust_p_wellhead", "cust_p_flowline"}


def _is_circular(x: str, y: str) -> bool:
    """Расчётная (тавтологичная) пара в одном источнике: Y выводится из X.

    LoRa: dp = P_уст−P_лин, Q = f(dp) → внутри LoRa осмысленна только пара
    P_уст↔P_лин. УзКорГаз: cust_dp = разность давлений → cust_dp↔cust_P циклична.
    Кросс-источник (LoRa↔УзКорГаз) — всегда независим.
    """
    if x in _LORA_KEYS and y in _LORA_KEYS:
        return {x, y} != {"p_tube", "p_line"}
    if x in _CUST_KEYS and y in _CUST_KEYS:
        return "cust_dp" in (x, y) and bool({x, y} & {"cust_p_wellhead", "cust_p_flowline"})
    return False

# Маппинг ключ → (source, metric) для суточного time_series
DAILY_SIGNAL_MAP = {
    "q":               ("our_flow", "q_total"),
    "q_working":       ("our_flow", "q_working"),
    "dp":              ("our_pressure", "dp"),
    "p_tube":          ("our_pressure", "p_wellhead"),
    "p_line":          ("our_pressure", "p_flowline"),
    "cust_q_total":    ("customer", "q_total"),
    "cust_q_working":  ("customer", "q_working"),
    "cust_dp":         ("customer", "dp"),
    "cust_p_wellhead": ("customer", "p_wellhead"),
    "cust_p_flowline": ("customer", "p_flowline"),
}


def _iso_utc(v, *, is_end: bool = False) -> str:
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, date):
        t = time(23, 59, 59) if is_end else time(0, 0, 0)
        dt = datetime.combine(v, t)
    else:
        dt = datetime.fromisoformat(str(v))
    return (dt - KUNGRAD_OFFSET).isoformat()


def _as_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _minute_series(df, key: str) -> np.ndarray:
    """Поминутный сигнал LoRa из DataFrame."""
    p_tube = df["p_tube"].to_numpy(dtype=float)
    p_line = df["p_line"].to_numpy(dtype=float)
    if key == "dp":
        return np.clip(p_tube - p_line, 0.0, None)
    if key == "q":
        return df["flow_rate"].to_numpy(dtype=float) if "flow_rate" in df.columns else np.full(len(df), np.nan)
    if key == "p_tube":
        return np.where(p_tube > 0, p_tube, np.nan)
    if key == "p_line":
        return np.where(p_line > 0, p_line, np.nan)
    return np.full(len(df), np.nan)


def _daily_map(db, well_number, key: str, d_from: date, d_to: date) -> dict:
    """Суточный ряд {date_str: value} через customer_daily.time_series."""
    src, metric = DAILY_SIGNAL_MAP[key]
    from backend.services import customer_daily_service as _cds
    res = _cds.time_series(db, source=src, well=well_number, metric=metric, d_from=d_from, d_to=d_to)
    if not res.get("ok"):
        return {}
    return dict(zip(res.get("dates", []), res.get("values", [])))


def _regression(x: np.ndarray, y: np.ndarray) -> dict | None:
    if x.size < 2:
        return None
    try:
        slope, intercept = np.polyfit(x, y, 1)
    except Exception:
        return None
    y_pred = slope * x + intercept
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = (1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0
    return {
        "slope": round(float(slope), 5),
        "intercept": round(float(intercept), 4),
        "r2": round(r2, 4),
        "x_min": round(float(np.min(x)), 3),
        "x_max": round(float(np.max(x)), 3),
    }


def build_param_correlation(
    db,
    *,
    well_id: int,
    well_number: str | None = None,
    period_from: date | str,
    period_to: date | str,
    x_signal: str = "dp",
    y_signal: str = "q",
    label: str | None = None,
) -> dict[str, Any]:
    """Облако точек X↔Y за период + регрессия. Поддерживает LoRa и УзКорГаз."""
    x_signal = x_signal if x_signal in SIGNAL_META else "dp"
    y_signal = y_signal if y_signal in SIGNAL_META else "q"

    pf = period_from if isinstance(period_from, str) else period_from.isoformat()
    pt = period_to if isinstance(period_to, str) else period_to.isoformat()

    snapshot: dict[str, Any] = {
        "_v": "param_correlation_v1",
        "schema_version": "1.0",
        "computed_at": None,
        "block_status": "ok",
        "well_id": well_id,
        "well_number": well_number,
        "label": label or "Зависимость параметров",
        "period": {"from": str(pf)[:10], "to": str(pt)[:10]},
        "x": {"key": x_signal, **SIGNAL_META[x_signal]},
        "y": {"key": y_signal, **SIGNAL_META[y_signal]},
        "warning": ("Расчётная зависимость: Y вычисляется из X в одном источнике "
                    "(напр. Q из ΔP) — корреляция тавтологична, неинформативна."
                    if _is_circular(x_signal, y_signal) else None),
        "resolution": None,        # 'minute' | 'daily'
        "points": {"x": [], "y": []},
        "regression": None,
        "n_points": 0,
    }

    minute_path = (x_signal in LORA_MINUTE_KEYS) and (y_signal in LORA_MINUTE_KEYS)

    if minute_path:
        # ── Поминутно (плотное облако, чистый LoRa) ──
        snapshot["resolution"] = "minute"
        try:
            from backend.services.flow_rate.full_pipeline import compute_full_flow
            ds = _iso_utc(period_from, is_end=False)
            de = _iso_utc(period_to, is_end=True)
            df = compute_full_flow(well_id, ds, de, smooth=True)["df"]
        except Exception as exc:  # noqa: BLE001
            snapshot["block_status"] = "no_data"
            snapshot["error"] = f"compute_full_flow failed: {exc}"
            return snapshot
        if df is None or df.empty:
            snapshot["block_status"] = "no_data"
            return snapshot
        xv = _minute_series(df, x_signal)
        yv = _minute_series(df, y_signal)
        mask = np.isfinite(xv) & np.isfinite(yv)
        xv, yv = xv[mask], yv[mask]
        n = int(xv.size)
        if n == 0:
            snapshot["block_status"] = "no_data"
            return snapshot
        if n < MIN_POINTS_MINUTE:
            snapshot["block_status"] = "insufficient_data"
        snapshot["regression"] = _regression(xv, yv)
        snapshot["n_points"] = n
        step = max(1, n // MAX_POINTS)
        snapshot["points"] = {
            "x": [round(float(v), 3) for v in xv[::step]],
            "y": [round(float(v), 3) for v in yv[::step]],
        }
        return snapshot

    # ── Посуточно (выравнивание по дате; для УзКорГаз и кросс-источника) ──
    snapshot["resolution"] = "daily"
    d_from, d_to = _as_date(period_from), _as_date(period_to)
    xmap = _daily_map(db, well_number, x_signal, d_from, d_to)
    ymap = _daily_map(db, well_number, y_signal, d_from, d_to)
    common = sorted(set(xmap) & set(ymap))
    xs, ys, dates = [], [], []
    for d in common:
        xvd, yvd = xmap.get(d), ymap.get(d)
        if xvd is None or yvd is None:
            continue
        try:
            xf, yf = float(xvd), float(yvd)
        except (TypeError, ValueError):
            continue
        if xf != xf or yf != yf:  # NaN
            continue
        xs.append(round(xf, 3)); ys.append(round(yf, 3)); dates.append(d)

    n = len(xs)
    if n == 0:
        snapshot["block_status"] = "no_data"
        return snapshot
    if n < MIN_POINTS_DAILY:
        snapshot["block_status"] = "insufficient_data"
    snapshot["regression"] = _regression(np.array(xs, dtype=float), np.array(ys, dtype=float))
    snapshot["n_points"] = n
    snapshot["points"] = {"x": xs, "y": ys, "dates": dates}
    return snapshot
