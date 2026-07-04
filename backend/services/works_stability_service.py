"""
Анализ «Стабильность работы скважины в период проведения работ».

SPEC: plans/SPEC_works_stability_2026-06-26.md §1–§8.
Публичная функция: compute_works_analysis(...)

Логика:
  • Три окна — before / during / after (§1)
  • Медианные метрики: q_median, dp_median, inj_freq, downtime_fraction,
    purge_count, purge_total_min  (§2)
  • Балл 0..100 по 5 метрикам с профилями весов (§6–§7)
  • ΔP-коррекция на внешний фактор (рост линейного давления) (§4)
  • Перцентильные ранги after-окна против истории до work_from (§5)
  • Разбивка продувок: purge_self_min / purge_recovery_min из LoRa (§2)
  • Контракт снимка §8 — descriptions ЗАМОРОЖЕНЫ при создании блока.

Переиспользование:
  customer_rose_service  → _count_shutdown_episodes, _percentile_rank,
                           _select_current_choke, _filter_by_choke
  customer_daily_service → load_for_well, find_well
  reagent_effectiveness_service → _get_reagent_injections
  flow_rate/data_access  → get_pressure_data (для LoRa-разбивки)
  flow_rate/cleaning     → clean_pressure
  flow_rate/calculator   → calculate_purge_loss
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, time as _time, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from backend.services import customer_daily_service as csvc
from backend.services import customer_rose_service as crs

log = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────────
#  Константы (§1, §2, §7)
# ───────────────────────────────────────────────────────────────────

SHUTDOWN_THRESHOLD_MIN: float = 30.0   # мин — рабочий день
N_MIN_HARD: int = 8                    # мин. рабочих точек для score_reliable
N_MIN_BEFORE_ROWS: int = 7             # мин. строк в before, иначе ok=False
MIN_PERCENTILE_WINDOWS: int = 10       # мин. окон истории для ранга

SCORE_METRIC_KEYS: tuple[str, ...] = (
    "q_median", "dp_median", "inj_freq", "downtime_fraction", "purge_count",
)

# Профили весов по типу работ (§7). Сумма = 1.00.
WEIGHT_PROFILES: dict[str, dict[str, float]] = {
    "tpav":  {"q_median": 0.30, "dp_median": 0.25, "inj_freq": 0.15,
               "downtime_fraction": 0.15, "purge_count": 0.15},
    "grp":   {"q_median": 0.40, "dp_median": 0.30, "inj_freq": 0.05,
               "downtime_fraction": 0.10, "purge_count": 0.15},
    "choke": {"q_median": 0.25, "dp_median": 0.40, "inj_freq": 0.05,
               "downtime_fraction": 0.10, "purge_count": 0.20},
    "clean": {"q_median": 0.25, "dp_median": 0.20, "inj_freq": 0.10,
               "downtime_fraction": 0.25, "purge_count": 0.20},
}

WORK_TYPE_LABELS: dict[str, str] = {
    "tpav":  "ТПАВ/ПАВ",
    "grp":   "ГРП",
    "choke": "Смена штуцера",
    "clean": "Чистка/промывка",
    "other": "Работы на скважине",
}

# ───────────────────────────────────────────────────────────────────
#  Внутренние помощники
# ───────────────────────────────────────────────────────────────────

def _safe(v: Any) -> float | None:
    """Любое числовое → float, NaN/Inf/None → None."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _slice_window(df_all: pd.DataFrame, d_from: date, d_to: date) -> pd.DataFrame:
    """Вырезать строки в диапазоне дат [d_from, d_to] включительно."""
    d = pd.to_datetime(df_all["date"]).dt.date
    return df_all.loc[(d >= d_from) & (d <= d_to)].reset_index(drop=True)


def _compute_window_metrics(df: pd.DataFrame) -> dict[str, Any]:
    """Медианные метрики одного окна из well_daily (§2).

    Возвращает:
      q_median, dp_median, downtime_fraction,
      purge_count, purge_total_min,
      n_actual, n_work,
      pf_median (медиана p_flowline, нужна для внешнего фактора ΔP).
    """
    if df.empty:
        return {k: None for k in (
            "q_median", "dp_median", "downtime_fraction",
            "purge_count", "purge_total_min", "pf_median",
        )} | {"n_actual": 0, "n_work": 0}

    sd_raw = pd.to_numeric(df.get("shutdown_min"), errors="coerce").to_numpy(dtype=float)
    sd_safe = np.where(np.isnan(sd_raw), 0.0, sd_raw)
    n_actual = int(len(df))

    # Рабочие дни
    work_mask = sd_safe <= SHUTDOWN_THRESHOLD_MIN
    df_work = df.loc[work_mask].reset_index(drop=True)
    n_work = int(work_mask.sum())

    # q_median по рабочим дням
    q_vals = pd.to_numeric(df_work.get("q_gas_total"), errors="coerce").dropna().values
    q_median = _safe(np.median(q_vals)) if len(q_vals) > 0 else None

    # dp_median — ΔP построчно, только рабочие дни, ΔP > 0 (false-zeros excluded)
    pw = pd.to_numeric(df_work.get("p_wellhead"), errors="coerce").to_numpy(dtype=float)
    pf = pd.to_numeric(df_work.get("p_flowline"), errors="coerce").to_numpy(dtype=float)
    dp_arr = pw - pf
    dp_valid = dp_arr[(np.isfinite(dp_arr)) & (dp_arr > 0.0)]
    dp_median = _safe(np.median(dp_valid)) if len(dp_valid) > 0 else None

    # pf_median (все рабочие дни, для внешнего фактора)
    pf_valid = pf[np.isfinite(pf) & (pf > 0.0)]
    pf_median = _safe(np.median(pf_valid)) if len(pf_valid) > 0 else None

    # downtime_fraction
    total_sd = float(sd_safe.sum())
    downtime_fraction = _safe(total_sd / (n_actual * 1440.0)) if n_actual > 0 else None

    # purge_count — эпизоды (серии дней с shutdown_min > 30)
    purge_count = int(crs._count_shutdown_episodes(sd_safe, SHUTDOWN_THRESHOLD_MIN))

    # purge_total_min — суммарное время простоев из суточной сводки
    purge_total_min = _safe(total_sd)

    return {
        "q_median":          q_median,
        "dp_median":         dp_median,
        "pf_median":         pf_median,
        "downtime_fraction": downtime_fraction,
        "purge_count":       purge_count,
        "purge_total_min":   purge_total_min,
        "n_actual":          n_actual,
        "n_work":            n_work,
    }


def _compute_inj_freq(
    well_id: int | None, d_from: date, d_to: date, calendar_days: int
) -> float | None:
    """Частота вбросов ВСЕХ реагентов из events (шт/сут). §2."""
    if well_id is None or calendar_days <= 0:
        return None
    try:
        from backend.services import reagent_effectiveness_service as _res
        start = datetime.combine(d_from, _time(0, 0, 0))
        end = datetime.combine(d_to, _time(23, 59, 59))
        inj = _res._get_reagent_injections(int(well_id), start, end)
        return _safe(len(inj) / calendar_days)
    except Exception:
        log.exception("_compute_inj_freq failed well_id=%s", well_id)
        return None


def _compute_purge_split(
    well_id: int | None, d_from: date, d_to: date
) -> dict | None:
    """Разбивка простоев по минутным LoRa-данным (§2).

    Технически реализуемо: calculate_purge_loss добавляет колонку purge_flag
    (True когда p_tube < p_line — активная продувка). Остаток времени простоя
    (dp < 0.1 атм, но purge_flag=0) — фаза набора давления.

    Возвращает {purge_self_min, purge_recovery_min, purge_total_min} или None.
    None → деградация до purge_count + purge_total_min из well_daily,
            purge_split_available=False в снимке.
    """
    if well_id is None:
        return None
    try:
        from backend.services.flow_rate.data_access import get_pressure_data
        from backend.services.flow_rate.cleaning import clean_pressure
        from backend.services.flow_rate.calculator import calculate_purge_loss
    except Exception:
        return None
    try:
        dfm = get_pressure_data(int(well_id), d_from.isoformat(), d_to.isoformat())
        if dfm is None or dfm.empty:
            return None
        dfm = clean_pressure(dfm)
        dfm = calculate_purge_loss(dfm)

        # Интервалы между замерами в минутах
        time_idx = pd.to_datetime(dfm.index)
        dt_sec = np.diff(
            time_idx.asi8 // 10 ** 9,
            prepend=time_idx.asi8[0] // 10 ** 9,
        )
        dt_min = dt_sec / 60.0
        dt_min[0] = 0.0

        purge_flag = dfm["purge_flag"].values.astype(bool)
        dp = (dfm["p_tube"] - dfm["p_line"]).values.astype(float)
        downtime_mask = (dp < 0.1) | purge_flag

        purge_self_min = float(np.sum(dt_min[purge_flag]))
        total_downtime_min = float(np.sum(dt_min[downtime_mask]))
        purge_recovery_min = max(0.0, total_downtime_min - purge_self_min)

        return {
            "purge_self_min":     round(purge_self_min, 1),
            "purge_recovery_min": round(purge_recovery_min, 1),
            "purge_total_min":    round(total_downtime_min, 1),
        }
    except Exception:
        log.exception("_compute_purge_split failed well_id=%s", well_id)
        return None


def _dp_external_factor(
    before: dict, after: dict, df_before: pd.DataFrame, df_after: pd.DataFrame
) -> dict[str, Any]:
    """Коррекция ΔP на внешний фактор (рост давления в шлейфе). §4."""
    dp_before = before.get("dp_median")
    dp_after = after.get("dp_median")
    pf_before = before.get("pf_median")
    pf_after = after.get("pf_median")

    result: dict[str, Any] = {
        "dp_decline_external": False,
        "dp_split_reliable":   True,
        "external_fraction":   0.0,
        "delta_flowline":      None,
        "delta_total_dp":      None,
    }

    if dp_before is None or dp_after is None:
        return result

    delta_total_dp = dp_after - dp_before
    result["delta_total_dp"] = _safe(delta_total_dp)

    if pf_before is not None and pf_after is not None:
        delta_fl = pf_after - pf_before
        result["delta_flowline"] = _safe(delta_fl)
    else:
        delta_fl = None

    # Гейт стабильности линии: CV(p_flowline_after) > 0.1 → ненадёжно
    pf_arr = pd.to_numeric(df_after.get("p_flowline"), errors="coerce").dropna().values
    if len(pf_arr) >= 2:
        m = float(np.mean(pf_arr))
        if m != 0 and math.isfinite(m):
            cv_pf = float(np.std(pf_arr, ddof=0) / abs(m))
            if cv_pf > 0.1:
                result["dp_split_reliable"] = False
                result["external_fraction"] = 0.5   # нейтральное значение
                return result

    # Внешняя доля: только при падении ΔP
    if delta_total_dp < 0 and delta_fl is not None:
        ef = delta_fl / abs(delta_total_dp) if delta_total_dp != 0 else 0.0
        ef = max(0.0, min(1.0, _safe(ef) or 0.0))
        result["external_fraction"] = round(ef, 4)
        if ef >= 0.5:
            result["dp_decline_external"] = True
    # При ef >= 0.9 полное исключение задаётся через (1 − external_fraction) в score

    return result


def _normalize_weights(
    weights: dict[str, float], excluded: set[str]
) -> dict[str, float]:
    """Нормировать веса по включённым метрикам."""
    active = {k: max(0.0, v) for k, v in weights.items() if k not in excluded}
    s = sum(active.values())
    if s <= 0:
        return {k: 0.0 for k in weights}
    normed = {k: v / s for k, v in active.items()}
    # Добавить нули для исключённых (чтобы JSON был полным)
    for k in weights:
        normed.setdefault(k, 0.0)
    return normed


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, float(x)))


def _compute_score(
    before: dict,
    after: dict,
    external_fraction: float,
    weights_norm: dict[str, float],
    excluded: set[str],
) -> tuple[float | None, dict[str, float | None]]:
    """Балл 0..100 и нормированные дельты (§6)."""
    deltas: dict[str, float | None] = {}

    # q_median ↑ хорошо
    qb, qa = before.get("q_median"), after.get("q_median")
    if qb is not None and qa is not None and "q_median" not in excluded:
        deltas["q_median"] = _clip((qa - qb) / max(qb, 0.1))
    else:
        deltas["q_median"] = None

    # dp_median ↑ хорошо, с коррекцией на внешний фактор
    dpb, dpa = before.get("dp_median"), after.get("dp_median")
    if dpb is not None and dpa is not None and "dp_median" not in excluded:
        raw_dp = (dpa - dpb) / max(dpb, 0.1)
        deltas["dp_median"] = _clip(raw_dp * (1.0 - external_fraction))
    else:
        deltas["dp_median"] = None

    # inj_freq ↓ хорошо
    ib, ia = before.get("inj_freq"), after.get("inj_freq")
    if ib is not None and ia is not None and "inj_freq" not in excluded:
        deltas["inj_freq"] = _clip((ib - ia) / max(ib, 1e-3))
    else:
        deltas["inj_freq"] = None

    # downtime_fraction ↓ хорошо
    db_, da = before.get("downtime_fraction"), after.get("downtime_fraction")
    if db_ is not None and da is not None and "downtime_fraction" not in excluded:
        deltas["downtime_fraction"] = _clip((db_ - da) / max(db_, 1e-4))
    else:
        deltas["downtime_fraction"] = None

    # purge_count ↓ хорошо
    pb, pa = before.get("purge_count"), after.get("purge_count")
    if pb is not None and pa is not None and "purge_count" not in excluded:
        deltas["purge_count"] = _clip((float(pb) - float(pa)) / max(float(pb), 1e-3))
    else:
        deltas["purge_count"] = None

    raw = 0.0
    total_w = 0.0
    for k in SCORE_METRIC_KEYS:
        d = deltas.get(k)
        w = weights_norm.get(k, 0.0)
        if d is not None and w > 0:
            raw += w * d
            total_w += w

    if total_w <= 0:
        return None, deltas

    # Делим на total_w (не на 1.0), чтобы корректно перенормировать случай,
    # когда часть «включённых» метрик всё же не имеет данных (d=None).
    score = 50.0 + (raw / total_w) * 50.0
    return round(max(0.0, min(100.0, score)), 1), deltas


def _verdict_text(score: float | None, reliable: bool) -> str:
    if score is None:
        return "Недостаточно данных для оценки"
    if score >= 70:
        t = "Работы эффективны — режим скважины улучшился"
    elif score >= 55:
        t = "Умеренный положительный эффект после работ"
    elif score >= 45:
        t = "Режим без существенных изменений"
    elif score >= 30:
        t = "Возможно слабое ухудшение после работ"
    else:
        t = "Показатели ухудшились после работ"
    if not reliable:
        t += " (ориентировочно, мало данных)"
    return t


def _build_descriptions(
    work_type_label: str,
    before: dict,
    after: dict,
    dp_ext: dict,
) -> dict[str, str]:
    """Осторожные текстовые описания — ЗАМОРОЖЕНЫ в снимке (§8)."""
    desc: dict[str, str] = {}

    qb, qa = before.get("q_median"), after.get("q_median")
    if qb is not None and qa is not None:
        diff = qa - qb
        sign = "вырос" if diff > 0 else "снизился"
        desc["q_median"] = (
            f"Медианный дебит после работ ({work_type_label}) {sign} "
            f"на {abs(diff):.1f} тыс. м³/сут "
            f"(с {qb:.1f} до {qa:.1f} тыс. м³/сут). "
            "Значение медианное — ориентируйтесь на рабочие сутки."
        )
    else:
        desc["q_median"] = "Нет данных по дебиту для сравнения."

    dpb, dpa = before.get("dp_median"), after.get("dp_median")
    if dpb is not None and dpa is not None:
        diff = dpa - dpb
        sign = "вырос" if diff > 0 else "снизился"
        ext_note = ""
        if dp_ext.get("dp_decline_external"):
            ext_note = (
                " Снижение ΔP может быть обусловлено ростом давления в шлейфе "
                "(внешний фактор — сторона заказчика). Вклад в оценку снижен."
            )
        desc["dp_median"] = (
            f"Медианный перепад давления ΔP = P_уст − P_шл после работ {sign} "
            f"на {abs(diff):.2f} кгс/см² (с {dpb:.2f} до {dpa:.2f}).{ext_note}"
        )
    else:
        desc["dp_median"] = "Нет данных по перепаду давления для сравнения."

    db_, da = before.get("downtime_fraction"), after.get("downtime_fraction")
    if db_ is not None and da is not None:
        diff_pct = (da - db_) * 100.0
        sign = "увеличилась" if diff_pct > 0 else "снизилась"
        desc["downtime"] = (
            f"Доля простоя после работ {sign} на {abs(diff_pct):.1f}% "
            f"(с {db_ * 100:.1f}% до {da * 100:.1f}%)."
        )
    else:
        desc["downtime"] = "Нет данных по простоям для сравнения."

    ib, ia = before.get("inj_freq"), after.get("inj_freq")
    if ib == 0.0:
        desc["inj_freq"] = (
            "Вбросов реагентов в периоде «до» не зафиксировано — "
            "метрика исключена из балла (деление на ноль)."
        )
    elif ib is not None and ia is not None:
        diff = ia - ib
        sign = "выросла" if diff > 0 else "снизилась"
        desc["inj_freq"] = (
            f"Частота вбросов реагентов после работ {sign}: "
            f"с {ib:.3f} до {ia:.3f} шт/сут."
        )
    else:
        desc["inj_freq"] = "Нет данных по вбросам реагентов для сравнения."

    return desc


def _iter_q_dp_windows(
    df_history: pd.DataFrame,
    window_days: int,
    step_days: int = 1,
) -> tuple[list[float], list[float]]:
    """Скользящие окна по истории: выборки q_median и dp_median.

    Аналог _iter_history_windows, но для медианных метрик.
    Нужен ≥ N_MIN_HARD рабочих точек в окне.
    """
    if df_history.empty or window_days < 7:
        return [], []
    d = df_history.sort_values("date").copy()
    d["_do"] = pd.to_datetime(d["date"]).dt.date
    dmin = d["_do"].iloc[0]
    dmax = d["_do"].iloc[-1]

    q_sample: list[float] = []
    dp_sample: list[float] = []
    cur = dmin
    step = timedelta(days=step_days)
    win_delta = timedelta(days=window_days - 1)

    while cur + win_delta <= dmax:
        win_to = cur + win_delta
        sub = d[(d["_do"] >= cur) & (d["_do"] <= win_to)]
        if len(sub) >= N_MIN_HARD:
            m = _compute_window_metrics(sub)
            if m["q_median"] is not None:
                q_sample.append(m["q_median"])
            if m["dp_median"] is not None:
                dp_sample.append(m["dp_median"])
        cur += step

    return q_sample, dp_sample


# ───────────────────────────────────────────────────────────────────
#  Публичный API
# ───────────────────────────────────────────────────────────────────

def compute_works_analysis(
    db: Session,
    well_number: str,
    *,
    work_from: date,
    work_to: date,
    baseline_days: int = 14,
    work_type: str = "tpav",
    weight_profile: str = "tpav",
    custom_weights: dict[str, float] | None = None,
    ref_from: date | None = None,
    ref_to: date | None = None,
    source: str = "well_daily",
) -> dict[str, Any]:
    """Анализ эффективности работ на скважине (SPEC §1–§8).

    Возвращает dict {ok: True, data_snapshot: {...}} или {ok: False, error: str}.
    """
    # ── 0. Базовые проверки ──────────────────────────────────────────
    if work_from > work_to:
        return {"ok": False, "error": "work_from > work_to"}

    # ── 1. Окна анализа (§1) ─────────────────────────────────────────
    before_from = work_from - timedelta(days=baseline_days)
    before_to   = work_from - timedelta(days=1)
    during_from = work_from
    during_to   = work_to
    after_from  = work_to + timedelta(days=1)
    after_to    = work_to + timedelta(days=baseline_days)

    windows = {
        "before": {"from": before_from.isoformat(), "to": before_to.isoformat()},
        "during": {"from": during_from.isoformat(), "to": during_to.isoformat()},
        "after":  {"from": after_from.isoformat(),  "to": after_to.isoformat()},
    }

    # ── 2. Загружаем ВСЮ историю скважины ───────────────────────────
    # Берём чуть пошире: от before_from до after_to + 1 год назад для рангов.
    history_from = before_from - timedelta(days=365)
    df_all = csvc.load_for_well(db, str(well_number), history_from, after_to)
    if df_all.empty:
        return {"ok": False, "error": f"В well_daily нет данных по скв. №{well_number}"}

    # ── 3. Нарезаем окна ──────────────────────────────────────────────
    df_before = _slice_window(df_all, before_from, before_to)
    df_during = _slice_window(df_all, during_from, during_to)
    df_after  = _slice_window(df_all, after_from, after_to)

    # Проверка минимума данных в before (§1)
    if len(df_before) < N_MIN_BEFORE_ROWS:
        return {
            "ok": False,
            "error": (
                f"В окне «до» ({before_from} – {before_to}) только "
                f"{len(df_before)} дней данных, нужно ≥ {N_MIN_BEFORE_ROWS}."
            ),
        }

    # ── 4. Метрики окон из well_daily ────────────────────────────────
    m_before = _compute_window_metrics(df_before)
    m_during = _compute_window_metrics(df_during)
    m_after  = _compute_window_metrics(df_after)

    cal_before = (before_to - before_from).days + 1
    cal_during = (during_to - during_from).days + 1
    cal_after  = (after_to  - after_from).days + 1

    # ── 5. well_id для LoRa и вбросов ────────────────────────────────
    well_info = csvc.find_well(db, str(well_number))
    well_id: int | None = int(well_info["id"]) if well_info else None

    # ── 6. inj_freq (все реагенты, §2) ───────────────────────────────
    m_before["inj_freq"] = _compute_inj_freq(well_id, before_from, before_to, cal_before)
    m_during["inj_freq"] = _compute_inj_freq(well_id, during_from, during_to, cal_during)
    m_after["inj_freq"]  = _compute_inj_freq(well_id, after_from,  after_to,  cal_after)

    # ── 7. LoRa-разбивка продувок (§2) ───────────────────────────────
    # Технически реализуемо: purge_flag из calculate_purge_loss делит простой на
    # «активная продувка» (p_tube < p_line) и «набор давления» (ΔP < 0.1 атм).
    # При недоступности LoRa-данных — деградация (purge_split_available=False).
    lora_before = _compute_purge_split(well_id, before_from, before_to)
    lora_during = _compute_purge_split(well_id, during_from, during_to)
    lora_after  = _compute_purge_split(well_id, after_from, after_to)
    purge_split_available = any(x is not None for x in (lora_before, lora_during, lora_after))

    def _add_lora(m: dict, lora: dict | None) -> None:
        m["purge_self_min"]     = lora["purge_self_min"]     if lora else None
        m["purge_recovery_min"] = lora["purge_recovery_min"] if lora else None
        m["purge_total_min_lora"] = lora["purge_total_min"]  if lora else None

    _add_lora(m_before, lora_before)
    _add_lora(m_during, lora_during)
    _add_lora(m_after,  lora_after)

    # ── 8. Confidence (§1) ───────────────────────────────────────────
    after_reliable  = m_after.get("n_work", 0) >= N_MIN_HARD
    before_reliable = m_before.get("n_work", 0) >= N_MIN_HARD

    # Если after пустой (нет данных ещё) — говорим об этом
    if m_after.get("n_actual", 0) == 0:
        confidence_after = "no_data"
    elif not after_reliable:
        confidence_after = "insufficient"
    else:
        confidence_after = "normal"
    score_after_reliable = (confidence_after == "normal") and before_reliable

    # ── 9. Штуцер — флаг смены в период работ (§РЕШЕНИЕ3) ───────────
    def _choke_values(df: pd.DataFrame) -> set[float]:
        vals = pd.to_numeric(df.get("choke_mm"), errors="coerce").dropna()
        return {round(float(v), 2) for v in vals if v > 0}

    choke_before_set = _choke_values(df_before)
    choke_during_set = _choke_values(df_during)
    choke_changed_during = bool(
        choke_during_set and choke_before_set and
        not choke_during_set.issubset(choke_before_set)
    )

    # ── 10. Внешний фактор ΔP (§4) ───────────────────────────────────
    dp_ext = _dp_external_factor(m_before, m_after, df_before, df_after)
    external_fraction = dp_ext.get("external_fraction", 0.0) or 0.0

    # ── 11. Веса и исключённые метрики ───────────────────────────────
    if weight_profile == "custom":
        if not custom_weights:
            return {"ok": False, "error": "Для weight_profile='custom' нужны custom_weights"}
        raw_weights = dict(custom_weights)
    elif weight_profile in WEIGHT_PROFILES:
        raw_weights = dict(WEIGHT_PROFILES[weight_profile])
    else:
        return {"ok": False, "error": f"Неизвестный weight_profile: {weight_profile}"}

    # Исключаем метрики с before=0 (деление на ноль, §6)
    excluded: set[str] = set()
    if (m_before.get("inj_freq") or 0.0) == 0.0:
        excluded.add("inj_freq")

    weights_norm = _normalize_weights(raw_weights, excluded)

    # ── 12. Балл after (основной) ────────────────────────────────────
    score_after, deltas_after = _compute_score(
        m_before, m_after, external_fraction, weights_norm, excluded
    )

    # ── 13. Балл during (справочный) ─────────────────────────────────
    score_during, deltas_during = _compute_score(
        m_before, m_during, external_fraction, weights_norm, excluded
    )
    during_reliable = m_during.get("n_work", 0) >= N_MIN_HARD

    # ── 14. Перцентильные ранги (§5) ─────────────────────────────────
    # История до work_from на текущем штуцере — переиспользуем df_all
    df_hist_all = _slice_window(df_all, history_from, before_to)
    choke_now = crs._select_current_choke(df_hist_all, before_to) if not df_hist_all.empty else None
    df_hist = crs._filter_by_choke(df_hist_all, choke_now) if choke_now else df_hist_all

    q_sample, dp_sample = _iter_q_dp_windows(df_hist, baseline_days)
    percentile_rank_available = (
        len(q_sample) >= MIN_PERCENTILE_WINDOWS
        or len(dp_sample) >= MIN_PERCENTILE_WINDOWS
    )
    percentile_ranks: dict[str, float | None] = {
        "q_median_after":  crs._percentile_rank(m_after.get("q_median"), q_sample)
                           if len(q_sample) >= MIN_PERCENTILE_WINDOWS else None,
        "dp_median_after": crs._percentile_rank(m_after.get("dp_median"), dp_sample)
                           if len(dp_sample) >= MIN_PERCENTILE_WINDOWS else None,
        "n_q_windows":     len(q_sample),
        "n_dp_windows":    len(dp_sample),
    }

    # ── 15. Справочные периоды сравнения (§5) ─────────────────────────
    week_from  = work_from - timedelta(days=7)
    month_from = work_from - timedelta(days=30)
    df_week  = _slice_window(df_all, week_from, before_to)
    df_month = _slice_window(df_all, month_from, before_to)
    m_week  = _compute_window_metrics(df_week)
    m_month = _compute_window_metrics(df_month)

    comparison_periods: dict[str, Any] = {
        "week_ref":  {
            "from": week_from.isoformat(), "to": before_to.isoformat(),
            "metrics": {k: _safe(m_week.get(k)) for k in ("q_median", "dp_median",
                        "downtime_fraction", "purge_count")},
        },
        "month_ref": {
            "from": month_from.isoformat(), "to": before_to.isoformat(),
            "metrics": {k: _safe(m_month.get(k)) for k in ("q_median", "dp_median",
                        "downtime_fraction", "purge_count")},
        },
    }
    if ref_from is not None and ref_to is not None:
        df_custom = _slice_window(df_all, ref_from, ref_to)
        m_custom  = _compute_window_metrics(df_custom)
        comparison_periods["custom_ref"] = {
            "from": ref_from.isoformat(), "to": ref_to.isoformat(),
            "metrics": {k: _safe(m_custom.get(k)) for k in ("q_median", "dp_median",
                        "downtime_fraction", "purge_count")},
        }

    # ── 16. Descriptions (заморозка, §8) ─────────────────────────────
    work_type_label = WORK_TYPE_LABELS.get(work_type, work_type)
    descriptions = _build_descriptions(work_type_label, m_before, m_after, dp_ext)

    # ── 17. Сборка снимка (§8) ──────────────────────────────────────
    def _metrics_out(m: dict) -> dict:
        return {
            "q_median":          _safe(m.get("q_median")),
            "dp_median":         _safe(m.get("dp_median")),
            "inj_freq":          _safe(m.get("inj_freq")),
            "downtime_fraction": _safe(m.get("downtime_fraction")),
            "purge_count":       m.get("purge_count"),
            "purge_total_min":   _safe(m.get("purge_total_min")),
            "purge_self_min":    _safe(m.get("purge_self_min")),
            "purge_recovery_min":_safe(m.get("purge_recovery_min")),
            "n_actual":          m.get("n_actual"),
            "n_work":            m.get("n_work"),
        }

    def _delta_out(before_val: Any, after_val: Any) -> float | None:
        b, a = _safe(before_val), _safe(after_val)
        if b is None or a is None:
            return None
        return _safe(a - b)

    data_snapshot: dict[str, Any] = {
        "chapter":         "works",
        "kind":            "works_analysis",
        "_v":              "1",
        "well_number":     str(well_number),
        "params": {
            "work_type":      work_type,
            "work_from":      work_from.isoformat(),
            "work_to":        work_to.isoformat(),
            "baseline_days":  baseline_days,
            "weight_profile": weight_profile,
            "custom_weights": custom_weights,
            "ref_from":       ref_from.isoformat() if ref_from else None,
            "ref_to":         ref_to.isoformat()   if ref_to   else None,
            "source":         source,
        },
        "windows":             windows,
        "confidence": {
            "before": "normal" if before_reliable else "insufficient",
            "after":  confidence_after,
            "during": "normal" if during_reliable else "insufficient",
        },
        "metrics_by_window": {
            "before": _metrics_out(m_before),
            "during": _metrics_out(m_during),
            "after":  _metrics_out(m_after),
        },
        "delta_after_vs_before": {
            "q_median":          _delta_out(m_before.get("q_median"), m_after.get("q_median")),
            "dp_median":         _delta_out(m_before.get("dp_median"), m_after.get("dp_median")),
            "inj_freq":          _delta_out(m_before.get("inj_freq"), m_after.get("inj_freq")),
            "downtime_fraction": _delta_out(m_before.get("downtime_fraction"),
                                            m_after.get("downtime_fraction")),
            "purge_count":       _delta_out(m_before.get("purge_count"),
                                            m_after.get("purge_count")),
        },
        "delta_during_vs_before": {
            "q_median":          _delta_out(m_before.get("q_median"), m_during.get("q_median")),
            "dp_median":         _delta_out(m_before.get("dp_median"), m_during.get("dp_median")),
            "downtime_fraction": _delta_out(m_before.get("downtime_fraction"),
                                            m_during.get("downtime_fraction")),
        },
        "dp_external_factor":    dp_ext,
        "score_after":           score_after,
        "score_after_reliable":  score_after_reliable,
        "score_during":          score_during,
        "score_during_reliable": during_reliable and before_reliable,
        "verdict_after":         _verdict_text(score_after, score_after_reliable),
        "verdict_during":        _verdict_text(score_during, during_reliable and before_reliable),
        "weight_profile":        weight_profile,
        "weights":               raw_weights,
        "weights_used":          weights_norm,
        "excluded_metrics":      sorted(excluded),
        "inj_freq_excluded":     "inj_freq" in excluded,
        "flags": {
            "choke_changed_during":   choke_changed_during,
            "purge_split_available":  purge_split_available,
            "dp_decline_external":    dp_ext.get("dp_decline_external", False),
            "score_reliable":         score_after_reliable,
        },
        "percentile_ranks":          percentile_ranks,
        "percentile_rank_available": percentile_rank_available,
        "comparison_periods":        comparison_periods,
        "descriptions":              descriptions,
        "choke_mm":                  _safe(choke_now),
    }

    return {"ok": True, "data_snapshot": data_snapshot}
