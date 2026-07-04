"""
works_effectiveness_service — Анализ эффективности наших работ на скважине.

SPEC v3: plans/SPEC_works_effectiveness_v3_2026-06-28.md
Рефактор: произвольные периоды + single/compare режимы.

Публичная функция:
    compute_works_effectiveness(
        db, well_number, *,
        period_from=None, period_to=None,
        ref_from=None, ref_to=None,
        compare=None,
        work_type='tpav',
    ) -> {ok: True, data_snapshot} | {ok: False, error}

РЕЖИМЫ:
    single  (compare=False):
        Только period_main — абсолютная роза + метрики + описание,
        без Δ и без Балла.
    compare (compare=None|True, по умолчанию):
        period_main + period_ref → overlay-роза, Δ-таблица, Балл БЭР, вердикт.
        Если ref не задан: авто-поиск «до первого вброса».

ДЕФОЛТ (без period_from/to):
    period_main = [первый вброс, сейчас]   (период работ)
    period_ref  = [первый вброс − 30 дн, первый вброс]  (до работ)
    Сохраняет прежнее поведение.

ИСТОЧНИК ДАННЫХ: только compute_full_flow (поминутный пайплайн),
НЕ well_daily.

TZ-ИНВАРИАНТ:
    events.event_time    → UTC naive  (бот пишет UTC)
    pressure_raw.measured_at → UTC
    compute_full_flow.df.index → Кунград (+5h), naive
    period_from/to / ref_from/to от пользователя → Кунград → −5h → UTC
    При срезе df вокруг event_time: t_local = event_time_utc + _KUNGRAD
    Отображение дат в снимке: utc_dt + _KUNGRAD → строка
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# ── TZ ────────────────────────────────────────────────────────────────────────
_KUNGRAD = timedelta(hours=5)    # UTC → Кунград (Asia/Tashkent UTC+5)

# ── Пороги ────────────────────────────────────────────────────────────────────
N_MIN_POINTS: int = 8
MIN_PERIOD_DAYS: float = 7.0     # мин. дней в периоде → флаг insufficient_data
DEFAULT_BASELINE_DAYS: int = 30  # глубина авто-ref «до первого вброса»
PLINE_EXTERNAL_RATIO: float = 0.50   # >50% → внешний фактор ΔP

# ── Нормировочные шкалы для розы (абсолютные) ────────────────────────────────
# "higher petal = better" на всех осях; «invert» оси нормируются как (1−v/ref)
_R_Q = 20.0       # тыс.м³/сут  → 100 petal при Q ≥ 20
_R_NIQR = 0.25    # безразм.    → 0  petal при niqr ≥ 0.25; 100 при niqr = 0
_R_DP = 5.0       # кгс/см²     → 100 petal при ΔP ≥ 5
_R_INJ = 2.0      # вбросов/сут → 0  petal при freq ≥ 2; 100 при freq = 0
_R_UPL = 30.0     # % Q-uplift  → 100 petal при uplift ≥ 30%
_R_KIV = 100.0    # %           → 100 petal при KIV = 100%
_R_VENT = 120.0   # мин         → 0  petal при vent ≥ 120; 100 при vent = 0
_R_BUILD = 120.0  # мин         → 0  petal при build ≥ 120; 100 при build = 0

# ── Веса БЭР (compare, индикативные) ─────────────────────────────────────────
_W_Q = 0.30
_W_DP = 0.25
_W_KIV = 0.20
_W_INJ = 0.15
_W_TVENT = 0.10

# Шкалы Δ для БЭР (Δ = main − ref; при Δ = scale → вклад = 1.0)
_BER_Q = 5.0       # тыс.м³/сут
_BER_DP = 1.0      # кгс/см²
_BER_KIV = 10.0    # %
_BER_TVENT = 240.0 # минут

# ── Оси розы (константа) ─────────────────────────────────────────────────────
ROSE_AXES: list[str] = [
    "Дебит Q",
    "Стаб. ΔP (↓→100)",
    "Уровень ΔP",
    "Частота вбросов (↓→100)",
    "Реакция Q",
    "КИВ",
    "Врем. страв. (↓→100)",
    "Набор давл. (↓→100)",
]


# ─────────────────────────────────────────────────────────────────────────────
#  Утилиты
# ─────────────────────────────────────────────────────────────────────────────

def _strip_tz(dt: datetime) -> datetime:
    """Naive UTC из tz-aware или naive datetime."""
    if dt is None:
        return dt
    if getattr(dt, "tzinfo", None) is not None:
        from datetime import timezone
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _safe(v, default=None, ndigits: int = 4):
    """None / NaN / Inf → default; float → round(v, ndigits)."""
    if v is None:
        return default
    try:
        f = float(v)
        if not math.isfinite(f):
            return default
        return round(f, ndigits)
    except (TypeError, ValueError):
        return default


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _petal(v, ref: float, invert: bool = False) -> float:
    """
    [0..100] для оси розы.
    invert=True → «меньше = лучше»: petal = clip(1 − v/ref) × 100.
    v=None → 0 (нет данных).
    """
    if v is None or ref <= 0:
        return 0.0
    raw = (1.0 - float(v) / ref) if invert else (float(v) / ref)
    return round(float(max(0.0, min(100.0, raw * 100.0))), 1)


def _to_utc(v) -> datetime | None:
    """
    Пользовательский параметр (строка/date/datetime) в Кунград-времени
    → naive UTC datetime (−5h).
    """
    if v is None:
        return None
    if isinstance(v, str):
        dt_local = datetime.fromisoformat(v)
    elif isinstance(v, datetime):
        dt_local = v
    else:
        from datetime import date as _date, time as _time
        dt_local = datetime.combine(v, _time(0, 0))
    return _strip_tz(dt_local) - _KUNGRAD


def _fmt_kungrad(utc_dt: datetime) -> str:
    return (utc_dt + _KUNGRAD).strftime("%Y-%m-%d")


def _theil_sen_slope(x: np.ndarray, y: np.ndarray) -> float:
    """Медианный наклон (Theil-Sen). O(n²), n ≤ 200 суточных точек."""
    slopes: list[float] = []
    for i in range(len(x)):
        for j in range(i + 1, len(x)):
            dx = float(x[j] - x[i])
            if dx:
                slopes.append(float(y[j] - y[i]) / dx)
    return float(np.median(slopes)) if slopes else 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  Пайплайн одного периода (обёртка)
# ─────────────────────────────────────────────────────────────────────────────

def _run_pipeline(well_id: int, utc_start: datetime, utc_end: datetime) -> dict:
    """compute_full_flow с перехватом исключений."""
    from backend.services.flow_rate.full_pipeline import compute_full_flow
    try:
        r = compute_full_flow(well_id, utc_start.isoformat(), utc_end.isoformat())
        return {
            "summary": r["summary"],
            "df": r["df"],
            "purge_cycles": r["purge_cycles"],
            "data_points": r["data_points"],
            "choke_mm": r.get("choke_mm"),
            "error": None,
        }
    except Exception as exc:
        log.warning("[works_eff] pipeline well_id=%d %s..%s: %s",
                    well_id, utc_start.date(), utc_end.date(), exc)
        return {
            "summary": None, "df": None, "purge_cycles": [],
            "data_points": 0, "choke_mm": None, "error": str(exc),
        }


# ─────────────────────────────────────────────────────────────────────────────
#  Вспомогательные расчёты
# ─────────────────────────────────────────────────────────────────────────────

def _dp_niqr(df: pd.DataFrame | None) -> float | None:
    """nIQR спектра ΔP = clip(p_tube−p_line, 0)."""
    if df is None or df.empty:
        return None
    try:
        from backend.services.pressure_spectrum_service import (
            _compute_signal_spectrum,
            STABILITY_THRESHOLDS,
            DEFAULT_BIN_WIDTH_DP,
        )
    except ImportError:
        return None
    arr = (df["p_tube"] - df["p_line"]).clip(lower=0).dropna().values
    if len(arr) < 10:
        return None
    spec = _compute_signal_spectrum(
        arr.astype(float),
        bin_width=DEFAULT_BIN_WIDTH_DP,
        left_edge_zero=True,
        thresholds=STABILITY_THRESHOLDS["dp"],
    )
    m = spec.get("metrics")
    return _safe(m["niqr"] if m else None, ndigits=4)


def _q_uplifts(
    df: pd.DataFrame,
    injections: list[dict],
    pre_h: float = 2.0,
    post_start_h: float = 0.5,
    post_end_h: float = 6.0,
) -> list[float]:
    """Q-uplift% per injection. df.index — Кунград; event_time — UTC (+5h для среза)."""
    result: list[float] = []
    for inj in injections:
        t_utc = _strip_tz(inj["event_time"])
        if t_utc is None:
            continue
        t = t_utc + _KUNGRAD
        pre = df.loc[t - timedelta(hours=pre_h):t, "flow_rate"].dropna()
        post = df.loc[t + timedelta(hours=post_start_h):t + timedelta(hours=post_end_h), "flow_rate"].dropna()
        if pre.empty or post.empty:
            continue
        q_pre = float(pre.median())
        if q_pre <= 0:
            continue
        result.append((float(post.median()) - q_pre) / q_pre * 100.0)
    return result


def _purge_stats(purge_cycles) -> dict:
    """Медианные показатели активных PurgeCycle."""
    active = [c for c in (purge_cycles or []) if not c.excluded]
    if not active:
        return {"count": 0, "median_venting_min": None,
                "median_buildup_min": None, "median_depth_kgf": None}
    vent = [c.venting_duration_min for c in active if c.venting_duration_min >= 0]
    build = [c.buildup_duration_min for c in active if c.buildup_duration_min >= 0]
    depths = [c.p_start - c.p_bottom
              for c in active if c.p_start and c.p_bottom and c.p_start > c.p_bottom]
    return {
        "count": len(active),
        "median_venting_min": _safe(np.median(vent) if vent else None, ndigits=2),
        "median_buildup_min": _safe(np.median(build) if build else None, ndigits=2),
        "median_depth_kgf": _safe(np.median(depths) if depths else None, ndigits=3),
    }


def _q_trend(df: pd.DataFrame | None) -> float | None:
    """Theil-Sen наклон ежесуточной медианы Q (тыс.м³/сут в сутки)."""
    if df is None or df.empty:
        return None
    daily = df["flow_rate"].resample("D").median().dropna()
    if len(daily) < 3:
        return None
    x = np.arange(len(daily), dtype=float)
    return _safe(_theil_sen_slope(x, daily.values.astype(float)), ndigits=5)


# ─────────────────────────────────────────────────────────────────────────────
#  Расчёт данных одного периода + роза
# ─────────────────────────────────────────────────────────────────────────────

def _compute_period_data(
    well_id: int,
    utc_start: datetime,
    utc_end: datetime,
    all_injections: list[dict],
) -> dict:
    """
    Полный расчёт метрик для произвольного периода.

    Возвращает:
        from, to         — Кунград-дата (строка YYYY-MM-DD)
        n_points, days
        metrics          — dict всех числовых критериев
        rose_values      — list[float] [0..100], 8 осей
        purge_stats      — dict
        choke_mm
        error            — str | None
    """
    res = _run_pipeline(well_id, utc_start, utc_end)
    s: dict = res["summary"] or {}
    df: pd.DataFrame | None = res["df"]
    pc = res["purge_cycles"] or []
    days = max(1.0, (utc_end - utc_start).total_seconds() / 86400.0)

    niqr = _dp_niqr(df)
    pm = _purge_stats(pc)
    c9 = _q_trend(df)

    period_injs = [
        i for i in all_injections
        if utc_start <= _strip_tz(i["event_time"]) <= utc_end
    ]
    inj_freq = len(period_injs) / days

    uplifts = _q_uplifts(df, period_injs) if df is not None and period_injs else []
    c4_uplift = _safe(np.median(uplifts) if uplifts else None, ndigits=2)

    metrics: dict[str, Any] = {
        # Дебит
        "median_flow_rate": _safe(s.get("median_flow_rate")),
        "q1_flow_rate": _safe(s.get("q1_flow_rate")),
        "q3_flow_rate": _safe(s.get("q3_flow_rate")),
        "cumulative_flow": _safe(s.get("cumulative_flow")),
        "actual_avg_flow": _safe(s.get("actual_avg_flow")),
        # Давление
        "median_dp": _safe(s.get("median_dp")),
        "median_p_tube": _safe(s.get("median_p_tube")),
        "median_p_line": _safe(s.get("median_p_line")),
        # КИВ / простои
        "utilization_pct": _safe(s.get("utilization_pct")),
        "downtime_total_hours": _safe(s.get("downtime_total_hours")),
        "total_downtime_periods": s.get("total_downtime_periods", 0),
        # Продувки (из summary)
        "purge_venting_count": s.get("purge_venting_count", 0),
        "purge_venting_hours": _safe(s.get("purge_venting_hours")),
        "purge_buildup_hours": _safe(s.get("purge_buildup_hours")),
        "purge_loss_daily_avg": _safe(s.get("purge_loss_daily_avg")),
        "purge_marker_count": s.get("purge_marker_count", 0),
        # Продувки (из PurgeCycle, медианы)
        "median_venting_min": pm["median_venting_min"],
        "median_buildup_min": pm["median_buildup_min"],
        "median_depth_kgf": pm["median_depth_kgf"],
        # Спектр ΔP
        "niqr": niqr,
        # Реакция на вброс
        "c4_uplift": c4_uplift,
        "c4_n_uplifts": len(uplifts),
        # Тренд Q
        "c9_slope": c9,
        # Вбросы
        "inj_freq": round(inj_freq, 4),
        "n_injections": len(period_injs),
        # Метаданные периода
        "observation_days": _safe(s.get("observation_days")),
        "choke_mm": _safe(res.get("choke_mm")),
    }

    rose_values = _period_rose(metrics)

    return {
        "from": _fmt_kungrad(utc_start),
        "to": _fmt_kungrad(utc_end),
        "n_points": res["data_points"],
        "days": round(days, 1),
        "metrics": metrics,
        "rose_axes": ROSE_AXES,
        "rose_values": rose_values,
        "purge_stats": pm,
        "choke_mm": res.get("choke_mm"),
        "error": res["error"],
    }


def _period_rose(metrics: dict) -> list[float]:
    """
    Абсолютная нормировка [0..100] для осей розы.
    higher = better на всех осях.
    """
    return [
        _petal(metrics.get("median_flow_rate"), _R_Q),
        _petal(metrics.get("niqr"), _R_NIQR, invert=True),          # ↓ niqr = лучше
        _petal(metrics.get("median_dp"), _R_DP),
        _petal(metrics.get("inj_freq", 0.0), _R_INJ, invert=True),  # ↓ freq = лучше
        _petal(metrics.get("c4_uplift"), _R_UPL),
        _petal(metrics.get("utilization_pct"), _R_KIV),
        _petal(metrics.get("median_venting_min"), _R_VENT, invert=True),   # ↓ = лучше
        _petal(metrics.get("median_buildup_min"), _R_BUILD, invert=True),  # ↓ = лучше
    ]


def _period_description(metrics: dict, label: str) -> str:
    """Осторожное описание одного периода (замораживается в снимке)."""
    q = metrics.get("median_flow_rate")
    kiv = metrics.get("utilization_pct")
    niqr = metrics.get("niqr")
    n_purge = metrics.get("purge_venting_count", 0)
    days = metrics.get("observation_days")

    parts = [f"За {label}:"]
    parts.append(f"медиана дебита {q:.2f} тыс.м³/сут," if q is not None else "дебит н/д,")
    parts.append(f"КИВ {kiv:.1f}%," if kiv is not None else "КИВ н/д,")
    parts.append(f"{n_purge} продувок.")
    if niqr is not None:
        parts.append(f"Стабильность ΔP (nIQR={niqr:.3f}).")
    parts.append("Показатели носят индикативный характер.")
    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
#  Δ-таблица и Балл БЭР (только compare)
# ─────────────────────────────────────────────────────────────────────────────

def _build_delta_table(
    main: dict,
    ref: dict,
    external_pline: bool,
) -> list[dict]:
    """Δ-таблица: value_ref, value_main, delta, direction, good."""
    m = main["metrics"]
    r = ref["metrics"]

    def _d(key: str) -> float | None:
        mv = m.get(key)
        rv = r.get(key)
        return _safe(mv - rv) if mv is not None and rv is not None else None

    # Частота вбросов: ref ≈ 0 по определению «до работ», но не принуждаем
    delta_inj = _safe((r.get("inj_freq", 0.0) or 0.0) - (m.get("inj_freq", 0.0) or 0.0))

    # Потери — BEFORE − WORK (уменьшение = хорошо)
    def _dloss(key):
        rv = r.get(key) or 0.0
        mv = m.get(key) or 0.0
        return _safe(rv - mv) if r.get(key) is not None or m.get(key) is not None else None

    def _good(delta, direction):
        if delta is None:
            return False
        return delta > 0 if direction == "up" else delta < 0

    table = [
        {
            "key": "C1_q",
            "label": "Медиана дебита Q",
            "value_ref": r.get("median_flow_rate"),
            "value_main": m.get("median_flow_rate"),
            "delta": _d("median_flow_rate"),
            "delta_rel_pct": (
                _safe((_d("median_flow_rate") or 0) / max(r.get("median_flow_rate") or 0.001, 0.001) * 100, ndigits=2)
                if _d("median_flow_rate") is not None else None
            ),
            "direction": "up",
            "good": (_d("median_flow_rate") or 0) > 0,
            "unit": "тыс. м³/сут",
        },
        {
            "key": "C2_niqr",
            "label": "Стабильность ΔP (nIQR, ↓ лучше)",
            "value_ref": r.get("niqr"),
            "value_main": m.get("niqr"),
            "delta": _safe(r.get("niqr") - m.get("niqr"))
                if r.get("niqr") is not None and m.get("niqr") is not None else None,
            "direction": "down",
            "good": (m.get("niqr") or 999) < (r.get("niqr") or 999),
            "unit": "безразм.",
        },
        {
            "key": "C2p_dp",
            "label": "Уровень ΔP",
            "value_ref": r.get("median_dp"),
            "value_main": m.get("median_dp"),
            "delta": _d("median_dp"),
            "direction": "up",
            "good": (_d("median_dp") or 0) > 0,
            "unit": "кгс/см²",
            "external_flag": external_pline,
            "note": "флаг: рост P_линии объясняет >50% изм. ΔP" if external_pline else None,
        },
        {
            "key": "C3_inj",
            "label": "Частота вбросов",
            "value_ref": r.get("inj_freq"),
            "value_main": m.get("inj_freq"),
            "delta": delta_inj,   # ref − main (↓ main = хорошо → delta > 0)
            "direction": "down",
            "good": (m.get("inj_freq") or 0) <= (r.get("inj_freq") or 0) + 0.001,
            "unit": "вбросов/сут",
            "n_injections_main": m.get("n_injections", 0),
        },
        {
            "key": "C4_uplift",
            "label": "Реакция Q на вброс (медиана)",
            "value_ref": r.get("c4_uplift"),
            "value_main": m.get("c4_uplift"),
            "delta": m.get("c4_uplift"),   # только main (ref ≈ 0 или нет данных)
            "direction": "up",
            "good": (m.get("c4_uplift") or 0) > 0,
            "unit": "%",
            "n_uplifts": m.get("c4_n_uplifts", 0),
        },
        {
            "key": "C5_purge_rate",
            "label": "Продувки/сут",
            "value_ref": round((r.get("purge_venting_count") or 0) / max(ref["days"], 1), 4),
            "value_main": round((m.get("purge_venting_count") or 0) / max(main["days"], 1), 4),
            "delta": None,
            "direction": "down",
            "good": (
                (m.get("purge_venting_count") or 0) / max(main["days"], 1)
                <= (r.get("purge_venting_count") or 0) / max(ref["days"], 1) + 0.001
            ),
            "unit": "шт/сут",
        },
        {
            "key": "C6a_vent",
            "label": "Медиана времени стравливания",
            "value_ref": r.get("median_venting_min"),
            "value_main": m.get("median_venting_min"),
            "delta": _safe((r.get("median_venting_min") or 0) - (m.get("median_venting_min") or 0), ndigits=2)
                if r.get("median_venting_min") is not None or m.get("median_venting_min") is not None else None,
            "direction": "down",
            "good": (m.get("median_venting_min") or 999) < (r.get("median_venting_min") or 999),
            "unit": "мин",
        },
        {
            "key": "C6b_build",
            "label": "Медиана времени набора давления",
            "value_ref": r.get("median_buildup_min"),
            "value_main": m.get("median_buildup_min"),
            "delta": _safe((r.get("median_buildup_min") or 0) - (m.get("median_buildup_min") or 0), ndigits=2)
                if r.get("median_buildup_min") is not None or m.get("median_buildup_min") is not None else None,
            "direction": "down",
            "good": (m.get("median_buildup_min") or 999) < (r.get("median_buildup_min") or 999),
            "unit": "мин",
            "note": "быстрый набор = хорошо",
        },
        {
            "key": "C6c_depth",
            "label": "Глубина просадки при продувке",
            "value_ref": r.get("median_depth_kgf"),
            "value_main": m.get("median_depth_kgf"),
            "delta": _safe((r.get("median_depth_kgf") or 0) - (m.get("median_depth_kgf") or 0), ndigits=3)
                if r.get("median_depth_kgf") is not None or m.get("median_depth_kgf") is not None else None,
            "direction": "down",
            "good": (m.get("median_depth_kgf") or 999) < (r.get("median_depth_kgf") or 999),
            "unit": "кгс/см²",
        },
        {
            "key": "C7_kiv",
            "label": "КИВ",
            "value_ref": r.get("utilization_pct"),
            "value_main": m.get("utilization_pct"),
            "delta": _d("utilization_pct"),
            "direction": "up",
            "good": (_d("utilization_pct") or 0) > 0,
            "unit": "%",
        },
        {
            "key": "C9_trend",
            "label": "Тренд Q в периоде (Theil-Sen)",
            "value_ref": r.get("c9_slope"),
            "value_main": m.get("c9_slope"),
            "delta": None,
            "direction": "up",
            "good": (m.get("c9_slope") or 0) >= 0,
            "unit": "тыс.м³/сут/сут",
        },
        {
            "key": "Cp_loss",
            "label": "Суточные потери при продувках",
            "value_ref": r.get("purge_loss_daily_avg"),
            "value_main": m.get("purge_loss_daily_avg"),
            "delta": _dloss("purge_loss_daily_avg"),
            "direction": "down",
            "good": (m.get("purge_loss_daily_avg") or 999) <= (r.get("purge_loss_daily_avg") or 999),
            "unit": "тыс. м³/сут",
        },
    ]
    # Delta для C5:
    table[5]["delta"] = _safe(
        (r.get("purge_venting_count") or 0) / max(ref["days"], 1)
        - (m.get("purge_venting_count") or 0) / max(main["days"], 1),
        ndigits=4,
    )
    return table


def _build_ber_score(
    main: dict,
    ref: dict,
    external_pline: bool,
) -> float:
    """
    Балл БЭР (0..100, индикативный).
    Взвешенная сумма нормированных Δ (clip 0..1).
    """
    mm = main["metrics"]
    rm = ref["metrics"]

    def _delta(key: str) -> float:
        mv = mm.get(key) or 0.0
        rv = rm.get(key) or 0.0
        return mv - rv

    norm_q = _clip01(_delta("median_flow_rate") / _BER_Q)
    norm_dp = _clip01(_delta("median_dp") / _BER_DP) if not external_pline else 0.0
    norm_kiv = _clip01(_delta("utilization_pct") / _BER_KIV)
    # Частота: 0 вбросов/сут в main → вклад 1.0; 2+/сут → 0
    norm_inj = _clip01(max(0.0, 1.0 - (mm.get("inj_freq") or 0.0)))
    # Время стравливания: уменьшение (ref−main) нормировано на шкалу
    delta_vent = (rm.get("median_venting_min") or 0.0) - (mm.get("median_venting_min") or 0.0)
    norm_tvent = _clip01(delta_vent / _BER_TVENT)

    return round(100.0 * (
        _W_Q * norm_q
        + _W_DP * norm_dp
        + _W_KIV * norm_kiv
        + _W_INJ * norm_inj
        + _W_TVENT * norm_tvent
    ), 1)


def _verdict_text(verdict: str, score: float, insufficient: bool) -> str:
    warn = " [осторожно: мало данных в базовом периоде]" if insufficient else ""
    if verdict == "positive":
        return (
            f"По совокупности критериев основной период показывает улучшение относительно "
            f"базового. Балл БЭР: {score:.0f}/100.{warn} "
            f"Интерпретация требует учёта условий (смена штуцера, внешний P_линии)."
        )
    if verdict == "neutral":
        return (
            f"Результат неоднозначный: часть показателей улучшилась, часть без изменений. "
            f"Балл БЭР: {score:.0f}/100.{warn} Рекомендуется длительное наблюдение."
        )
    return (
        f"Явных признаков улучшения режима не обнаружено. "
        f"Балл БЭР: {score:.0f}/100.{warn} "
        f"Рекомендуется повторный анализ с уточнёнными параметрами."
    )


# ─────────────────────────────────────────────────────────────────────────────
#  ГЛАВНАЯ ФУНКЦИЯ
# ─────────────────────────────────────────────────────────────────────────────

def compute_works_effectiveness(
    db: Session,
    well_number: str,
    *,
    period_from=None,
    period_to=None,
    ref_from=None,
    ref_to=None,
    compare=None,
    work_type: str = "tpav",
) -> dict:
    """
    Анализ эффективности работ для произвольного периода.

    Параметры
    ---------
    db           : SQLAlchemy Session
    well_number  : строковый номер скважины
    period_from  : начало основного периода (Кунград UTC+5 ; None = первый вброс)
    period_to    : конец основного периода  (Кунград UTC+5 ; None = сейчас)
    ref_from     : начало базового периода  (Кунград UTC+5 ; None = авто «до работ»)
    ref_to       : конец базового периода   (Кунград UTC+5 ; None = авто «до работ»)
    compare      : True | False | None
                   False  → single mode (только основной период, без Δ и Балла)
                   True / None → compare mode (+ базовый период, Δ-таблица, Балл)
    work_type    : 'tpav' | 'grp' | 'choke' | 'cleaning'

    Возвращает
    ----------
    {ok: True, data_snapshot: {...}} | {ok: False, error: str}

    data_snapshot содержит:
        mode             : 'single' | 'compare'
        period_main      : {from, to, n_points, days, metrics, rose_axes, rose_values,
                            purge_stats, description}
        period_ref       : {...} | None  (только compare)
        delta_table      : [{key, label, value_ref, value_main, delta, direction, good, unit}]
                           | None  (только compare)
        score_ber        : float | None  (только compare)
        verdict          : str | None    (только compare)
        flags            : {insufficient_data, external_pline, choke_changed,
                            purges_no_markers}
    """
    from backend.services import customer_daily_service as csvc
    from backend.services.reagent_effectiveness_service import _get_reagent_injections

    # ── 1. Найти скважину ─────────────────────────────────────────────────────
    well = csvc.find_well(db, str(well_number))
    if not well:
        return {"ok": False, "error": f"Скважина '{well_number}' не найдена"}
    well_id: int = well["id"]

    do_compare: bool = compare is not False   # False = single; None/True = compare

    # ── 2. Вбросы за 3 года (для авто-границ + C3/C4) ────────────────────────
    now_utc = datetime.utcnow()
    search_start = now_utc - timedelta(days=3 * 365)
    all_injections = _get_reagent_injections(well_id, search_start, now_utc)

    # ── 3. Первый вброс (для дефолтных границ) ───────────────────────────────
    first_inj_utc: datetime | None = None
    if all_injections:
        first_inj_utc = _strip_tz(
            min(all_injections, key=lambda r: r["event_time"])["event_time"]
        )

    if not all_injections and period_from is None:
        return {
            "ok": False,
            "error": "Вбросы реагента не найдены и период не задан — нечего анализировать",
        }

    # ── 4. UTC-границы основного периода ─────────────────────────────────────
    if period_from is not None:
        main_start_utc = _to_utc(period_from)
    else:
        main_start_utc = first_inj_utc  # дефолт: от первого вброса

    if period_to is not None:
        main_end_utc = _to_utc(period_to)
    else:
        main_end_utc = now_utc          # дефолт: до сейчас

    if main_start_utc is None or main_end_utc is None or main_start_utc >= main_end_utc:
        return {"ok": False, "error": "Некорректный основной период (start >= end)"}

    # ── 5. UTC-границы базового периода (только compare) ─────────────────────
    ref_start_utc: datetime | None = None
    ref_end_utc: datetime | None = None
    if do_compare:
        if ref_from is not None:
            ref_start_utc = _to_utc(ref_from)
        if ref_to is not None:
            ref_end_utc = _to_utc(ref_to)

        if ref_start_utc is None and ref_end_utc is None:
            # Авто: DEFAULT_BASELINE_DAYS до первого вброса
            if first_inj_utc is None:
                # Нет вбросов → auto-ref недоступен → деградируем до single
                log.warning("[works_eff] нет вбросов, auto-ref невозможен → single mode")
                do_compare = False
            else:
                ref_end_utc = first_inj_utc
                ref_start_utc = first_inj_utc - timedelta(days=DEFAULT_BASELINE_DAYS)
        elif ref_start_utc is None or ref_end_utc is None:
            return {"ok": False, "error": "Задан только один из ref_from/ref_to — укажите оба"}

        if do_compare and ref_start_utc >= ref_end_utc:
            return {"ok": False, "error": "Некорректный базовый период (start >= end)"}

    log.info(
        "[works_eff] well=%s id=%d mode=%s main=%s..%s ref=%s..%s",
        well_number, well_id,
        "compare" if do_compare else "single",
        main_start_utc.date(), main_end_utc.date(),
        ref_start_utc.date() if ref_start_utc else "-",
        ref_end_utc.date() if ref_end_utc else "-",
    )

    # ── 6. Расчёт основного периода ──────────────────────────────────────────
    main_data = _compute_period_data(well_id, main_start_utc, main_end_utc, all_injections)

    if main_data["error"]:
        return {"ok": False, "error": f"Нет данных для основного периода: {main_data['error']}"}

    # ── 7. Расчёт базового периода (только compare) ───────────────────────────
    ref_data: dict | None = None
    if do_compare:
        ref_data = _compute_period_data(well_id, ref_start_utc, ref_end_utc, all_injections)
        # Если базовый не считается — не критично: compare продолжим без Балла
        if ref_data["error"]:
            log.warning("[works_eff] базовый период недоступен: %s", ref_data["error"])

    # ── 8. Флаги ──────────────────────────────────────────────────────────────
    main_days = main_data["days"]
    ref_days = ref_data["days"] if ref_data else 0.0
    ref_pts = ref_data["n_points"] if ref_data else 0

    insufficient_data = (
        main_data["n_points"] < N_MIN_POINTS
        or main_days < MIN_PERIOD_DAYS
    )
    insufficient_ref = do_compare and (
        ref_data is None
        or ref_data["error"] is not None
        or ref_pts < N_MIN_POINTS
        or ref_days < MIN_PERIOD_DAYS
    )

    # C8 внешний фактор
    external_pline = False
    if ref_data and ref_data["error"] is None:
        dp_main = main_data["metrics"].get("median_dp") or 0.0
        dp_ref = ref_data["metrics"].get("median_dp") or 0.0
        pl_main = main_data["metrics"].get("median_p_line") or 0.0
        pl_ref = ref_data["metrics"].get("median_p_line") or 0.0
        delta_dp = dp_main - dp_ref
        delta_pl = pl_main - pl_ref
        if abs(delta_dp) > 0.01:
            external_pline = (delta_pl / (abs(delta_dp) + 0.01)) > PLINE_EXTERNAL_RATIO

    choke_changed = (
        do_compare
        and ref_data is not None
        and main_data.get("choke_mm") is not None
        and ref_data.get("choke_mm") is not None
        and main_data["choke_mm"] != ref_data["choke_mm"]
    )
    purges_no_markers = (
        (main_data["metrics"].get("purge_venting_count") or 0) > 0
        and (main_data["metrics"].get("purge_marker_count") or 0) == 0
    )

    flags = {
        "insufficient_data": insufficient_data,
        "insufficient_ref": insufficient_ref,
        "external_pline": external_pline,
        "choke_changed": choke_changed,
        "purges_no_markers": purges_no_markers,
    }

    # ── 9. Δ-таблица, Балл, Вердикт ──────────────────────────────────────────
    delta_table: list[dict] | None = None
    score_ber: float | None = None
    verdict: str | None = None
    verdict_text: str | None = None

    if do_compare and ref_data is not None and ref_data["error"] is None:
        delta_table = _build_delta_table(main_data, ref_data, external_pline)
        score_ber = _build_ber_score(main_data, ref_data, external_pline)

        n_good = sum(1 for c in delta_table
                     if c.get("good") and c["key"] not in ("C8_pline",))
        n_decisive = sum(1 for c in delta_table
                         if c["key"] not in ("C8_pline", "C3_inj", "C4_uplift"))
        if score_ber >= 60 and n_good >= max(1, n_decisive // 2):
            verdict = "positive"
        elif score_ber >= 30:
            verdict = "neutral"
        else:
            verdict = "negative"
        verdict_text = _verdict_text(verdict, score_ber, insufficient_ref)

    # ── 10. Описания периодов (замороженные) ─────────────────────────────────
    main_label = (
        f"{main_data['from']} — {main_data['to']}"
        f" ({main_data['n_points']} точек, {main_days:.0f} дн)"
    )
    main_data["description"] = _period_description(main_data["metrics"], main_label)

    if ref_data and ref_data["error"] is None:
        ref_label = (
            f"базовый {ref_data['from']} — {ref_data['to']}"
            f" ({ref_data['n_points']} точек, {ref_days:.0f} дн)"
        )
        ref_data["description"] = _period_description(ref_data["metrics"], ref_label)

    # ── 11. Сборка снимка ─────────────────────────────────────────────────────
    def _clean_period(pd_: dict) -> dict:
        """Убирает поля, не пригодные для JSON (нет df/pd.DataFrame)."""
        return {
            "from": pd_["from"],
            "to": pd_["to"],
            "n_points": pd_["n_points"],
            "days": pd_["days"],
            "metrics": pd_.get("metrics", {}),
            "rose_axes": pd_.get("rose_axes", ROSE_AXES),
            "rose_values": pd_.get("rose_values", []),
            "purge_stats": pd_.get("purge_stats", {}),
            "description": pd_.get("description", ""),
        }

    data_snapshot: dict[str, Any] = {
        "kind": "works_analysis",
        "chapter": "works",
        "_v": "2",
        "well_number": str(well_number),
        "work_type": work_type,
        "computed_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "compare" if do_compare else "single",
        # Периоды
        "period_main": _clean_period(main_data),
        "period_ref": _clean_period(ref_data) if (ref_data and ref_data.get("error") is None) else None,
        # Сравнение (только compare)
        "delta_table": delta_table,
        "score_ber": score_ber,
        "verdict": verdict,
        "verdict_text": verdict_text,
        # Флаги
        "flags": flags,
        # Описание инструментария
        "descriptions": {
            "c4_note": (
                f"Реакция Q: {main_data['metrics'].get('c4_n_uplifts', 0)} из "
                f"{main_data['metrics'].get('n_injections', 0)} вбросов в основном периоде "
                f"(окна [-2ч;0] и [+0.5ч;+6ч])."
            ),
            "c4b_note": "ИРВ-Score (C4b) не реализован в ФАЗА 1.",
            "score_disclaimer": (
                "Балл БЭР индикативный. Веса Q=0.30, ΔP=0.25, КИВ=0.20, "
                "частота=0.15, страв.=0.10 — требуют калибровки."
            ) if do_compare else None,
            "rose_note": (
                "Роза: абсолютная нормировка [0..100] на каждой оси, higher=better. "
                f"Шкалы: Q/{_R_Q} тыс.м³/сут, ΔP-стаб nIQR/{_R_NIQR}, "
                f"ΔP/{_R_DP} кгс/см², вбросы/{_R_INJ}/сут, "
                f"uplift/{_R_UPL}%, КИВ/{_R_KIV}%, "
                f"страв./{_R_VENT}мин, набор/{_R_BUILD}мин."
            ),
        },
    }

    return {"ok": True, "data_snapshot": data_snapshot}
