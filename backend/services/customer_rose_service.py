"""Роза критериев — диагностика отклонения текущего периода скважины от
её собственной нормы (медианы по истории на текущем штуцере).

Концепция (отличается от классической «розы парка»):

  • Парк не используется. Норма берётся ИЗ ИСТОРИИ САМОЙ СКВАЖИНЫ:
    распределение скользящих окон такой же длины, что и анализируемый
    период, на том же штуцере (`well_daily.choke_mm`), за вычетом дней
    простоев (для метрик Q/P/CV).
  • Ось розы (0..100) — перцентильный ранг текущего значения метрики
    относительно распределения по окнам истории: 0 = лучше всех окон,
    100 = хуже всех окон. Семантика «> 50 — хуже нормы» сохраняется.
  • 6 критериев — те же, что в ТЗ.
  • Итоговый «балл кандидата» = Σ rank_k * w_k (после нормировки весов).

Что отдаёт `compute_rose()`:
  • `current` — сырые метрики выбранного периода,
  • `history`  — характеристики окна-нормы (медиана + N окон),
  • `ranks`    — 0..100 на каждую ось,
  • `weights`  — нормированные веса режима,
  • `contributions` — `actual=rank*w`, `max=100*w` (для бар-чарта),
  • `score`    — Σ actual.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from backend.services import customer_daily_service as csvc

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Константы критериев и режимов
# ═══════════════════════════════════════════════════════════════════

SCORING_KEYS: tuple[str, ...] = (
    "decline", "p_wh_down", "p_wh_cv", "p_fl_up", "shutdown", "freq",
)

SCORING_LABELS: dict[str, str] = {
    "decline":   "Снижение Q",
    "p_wh_down": "Снижение P устья",
    "p_wh_cv":   "Волатильность P устья",
    "p_fl_up":   "Рост P шлейфа",
    "shutdown":  "Доля простоя",
    "freq":      "Частота простоев",
}

SCORING_LABELS_SHORT: dict[str, str] = {
    "decline":   "Q ↓",
    "p_wh_down": "P уст ↓",
    "p_wh_cv":   "P уст волат.",
    "p_fl_up":   "P шл ↑",
    "shutdown":  "Простой %",
    "freq":      "Эпизоды/30д",
}

SCORING_RAW_UNITS: dict[str, str] = {
    "decline":   "тыс.м³/сут/сут",
    "p_wh_down": "кгс/см²/сут",
    "p_wh_cv":   "—",
    "p_fl_up":   "кгс/см²/сут",
    "shutdown":  "доля",
    "freq":      "эпизод/30д",
}

# Профили весов (нормируются при расчёте — сумма не обязана быть = 1).
SCORING_MODES: dict[str, dict[str, float]] = {
    "liquid":       {"decline": 0.40, "p_wh_down": 0.30, "p_fl_up": 0.00,
                     "p_wh_cv": 0.10, "shutdown": 0.10, "freq": 0.10},
    "gsp":          {"decline": 0.35, "p_wh_down": 0.05, "p_fl_up": 0.40,
                     "p_wh_cv": 0.05, "shutdown": 0.10, "freq": 0.05},
    "purge_cycles": {"decline": 0.35, "p_wh_down": 0.15, "p_fl_up": 0.00,
                     "p_wh_cv": 0.25, "shutdown": 0.10, "freq": 0.15},
    "balanced":     {"decline": 0.35, "p_wh_down": 0.20, "p_fl_up": 0.15,
                     "p_wh_cv": 0.10, "shutdown": 0.10, "freq": 0.10},
}

# Порог «дня простоя» в минутах (используется для метрики `freq` и для
# фильтра дней простоев в Q/P/CV-метриках истории).
SHUTDOWN_THRESHOLD_MIN: float = 30.0

# Минимальная длина окна (дней), чтобы расчёт slope/CV имел смысл.
MIN_WINDOW_DAYS: int = 7

# Минимум окон в истории, иначе роза не строится.
MIN_HISTORY_WINDOWS: int = 10


# ═══════════════════════════════════════════════════════════════════
#  Расчёт 6 метрик для одного окна / периода
# ═══════════════════════════════════════════════════════════════════

@dataclass
class RawMetrics:
    decline:   float | None
    p_wh_down: float | None
    p_wh_cv:   float | None
    p_fl_up:   float | None
    shutdown:  float | None
    freq:      float | None

    def as_dict(self) -> dict[str, float | None]:
        return {k: getattr(self, k) for k in SCORING_KEYS}


def _safe_slope(y: np.ndarray) -> float | None:
    """Slope линейной регрессии y по индексам [0..N-1] (день за единицу).

    Возвращает None если точек мало или результат не финитный.
    """
    mask = np.isfinite(y)
    if mask.sum() < 2:
        return None
    x = np.arange(len(y), dtype=float)[mask]
    yv = y[mask]
    try:
        slope, _ = np.polyfit(x, yv, 1)
        f = float(slope)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except Exception:
        return None


def _count_shutdown_episodes(shutdown_min: np.ndarray, threshold: float) -> int:
    """Эпизод = подряд идущая серия дней с shutdown_min > threshold.

    Соседние «короткие» дни одной серии = один эпизод.
    """
    if shutdown_min.size == 0:
        return 0
    is_down = np.where(np.isfinite(shutdown_min), shutdown_min, 0.0) > threshold
    if not is_down.any():
        return 0
    # Считаем переходы False→True
    prev = np.concatenate([[False], is_down[:-1]])
    starts = is_down & (~prev)
    return int(starts.sum())


def compute_raw_metrics(
    df: pd.DataFrame,
    *,
    drop_shutdown_for_qp: bool = True,
    shutdown_threshold: float = SHUTDOWN_THRESHOLD_MIN,
) -> RawMetrics:
    """6 сырых метрик для одного периода (DataFrame должен быть упорядочен по date).

    drop_shutdown_for_qp: для метрик Q/P/CV исключаем дни с простоями
    (shutdown_min > threshold). Метрики `shutdown`/`freq` всегда считаются
    по ВСЕМ строкам периода (иначе они тождественно нулевые).
    """
    if df.empty:
        return RawMetrics(None, None, None, None, None, None)

    n_days = int(len(df))

    sd_raw = pd.to_numeric(df.get("shutdown_min"), errors="coerce").to_numpy(dtype=float)
    sd_safe = np.where(np.isnan(sd_raw), 0.0, sd_raw)

    # Метрики простоев — по всему периоду
    shutdown_total = float(sd_safe.sum())
    shutdown_frac = shutdown_total / (n_days * 1440.0) if n_days > 0 else None
    if shutdown_frac is not None and (math.isnan(shutdown_frac) or math.isinf(shutdown_frac)):
        shutdown_frac = None

    n_episodes = _count_shutdown_episodes(sd_safe, shutdown_threshold)
    freq_per_30d = (n_episodes * 30.0 / n_days) if n_days > 0 else None
    if freq_per_30d is not None and (math.isnan(freq_per_30d) or math.isinf(freq_per_30d)):
        freq_per_30d = None

    # Подвыборка для Q/P/CV: исключаем дни простоев
    if drop_shutdown_for_qp:
        mask_work = sd_safe <= shutdown_threshold
        df_qp = df.loc[mask_work].reset_index(drop=True)
    else:
        df_qp = df.reset_index(drop=True)

    # Slope Q_total: чем сильнее снижается, тем больше метрика (clip 0).
    q = pd.to_numeric(df_qp.get("q_gas_total"), errors="coerce").to_numpy(dtype=float)
    slope_q = _safe_slope(q)
    decline = max(0.0, -slope_q) if slope_q is not None else None

    # Slope P_wellhead: снижение
    pw = pd.to_numeric(df_qp.get("p_wellhead"), errors="coerce").to_numpy(dtype=float)
    slope_pw = _safe_slope(pw)
    p_wh_down = max(0.0, -slope_pw) if slope_pw is not None else None

    # CV P_wellhead
    pw_finite = pw[np.isfinite(pw)]
    if pw_finite.size >= 2:
        m = float(pw_finite.mean())
        s = float(pw_finite.std(ddof=0))
        p_wh_cv = (s / abs(m)) if (m != 0 and math.isfinite(m) and math.isfinite(s)) else None
        if p_wh_cv is not None and (math.isnan(p_wh_cv) or math.isinf(p_wh_cv)):
            p_wh_cv = None
    else:
        p_wh_cv = None

    # Slope P_flowline: рост
    pf = pd.to_numeric(df_qp.get("p_flowline"), errors="coerce").to_numpy(dtype=float)
    slope_pf = _safe_slope(pf)
    p_fl_up = max(0.0, slope_pf) if slope_pf is not None else None

    return RawMetrics(
        decline=decline,
        p_wh_down=p_wh_down,
        p_wh_cv=p_wh_cv,
        p_fl_up=p_fl_up,
        shutdown=shutdown_frac,
        freq=freq_per_30d,
    )


# ═══════════════════════════════════════════════════════════════════
#  История: распределение по скользящим окнам
# ═══════════════════════════════════════════════════════════════════

def _normalize_weights(w: dict[str, float]) -> dict[str, float]:
    raw = {k: max(0.0, float(w.get(k, 0.0))) for k in SCORING_KEYS}
    s = sum(raw.values())
    if s <= 0:
        return {k: 0.0 for k in SCORING_KEYS}
    return {k: v / s for k, v in raw.items()}


def _percentile_rank(value: float | None, sample: list[float]) -> float | None:
    """Перцентильный ранг 0..100: доля окон истории, у которых значение
    метрики < текущего. То есть rank=73 означает «в 73% случаев в истории
    эта метрика была лучше (меньше) — текущее значение хуже большинства».

    Если sample пуст — None. Если value None — None.
    """
    if value is None:
        return None
    arr = np.asarray([s for s in sample if s is not None and math.isfinite(s)],
                     dtype=float)
    if arr.size == 0:
        return None
    # Доля окон строго меньше + половина равных (среднеранговый перцентиль)
    less = float((arr < value).sum())
    equal = float((arr == value).sum())
    rank = (less + 0.5 * equal) / arr.size * 100.0
    if math.isnan(rank) or math.isinf(rank):
        return None
    return max(0.0, min(100.0, rank))


def _select_current_choke(df_all: pd.DataFrame, period_to: date) -> float | None:
    """Текущий штуцер скважины — берём из последнего дня well_daily ≤ period_to.

    Если в этой строке choke_mm пуст — ищем последний непустой ≤ period_to.
    """
    if df_all.empty or "choke_mm" not in df_all.columns:
        return None
    d = df_all.copy()
    d["date_only"] = pd.to_datetime(d["date"]).dt.date
    sub = d[d["date_only"] <= period_to].sort_values("date_only")
    if sub.empty:
        return None
    choke = pd.to_numeric(sub["choke_mm"], errors="coerce").dropna()
    if choke.empty:
        return None
    val = float(choke.iloc[-1])
    if val <= 0 or math.isnan(val) or math.isinf(val):
        return None
    return val


def _filter_by_choke(df_all: pd.DataFrame, choke: float | None, tol: float = 0.01) -> pd.DataFrame:
    """Оставить только дни с указанным штуцером (с допуском)."""
    if df_all.empty or choke is None or "choke_mm" not in df_all.columns:
        return df_all
    ch = pd.to_numeric(df_all["choke_mm"], errors="coerce")
    mask = (ch.notna()) & (np.abs(ch - choke) <= tol)
    return df_all.loc[mask].reset_index(drop=True)


def _iter_history_windows(
    df_history: pd.DataFrame,
    window_days: int,
    step_days: int = 1,
) -> list[RawMetrics]:
    """Сгенерировать метрики по скользящим окнам.

    Окна формируются по календарным датам внутри df_history (строки уже
    отфильтрованы по штуцеру). Шаг = step_days. Окно валидно, если в нём
    есть ≥ MIN_WINDOW_DAYS строк.
    """
    if df_history.empty or window_days < MIN_WINDOW_DAYS:
        return []
    d = df_history.sort_values("date").reset_index(drop=True)
    d["date_only"] = pd.to_datetime(d["date"]).dt.date
    dmin = d["date_only"].iloc[0]
    dmax = d["date_only"].iloc[-1]

    out: list[RawMetrics] = []
    cur = dmin
    one_day = pd.Timedelta(days=1)
    win_delta = pd.Timedelta(days=window_days - 1)
    step_delta = pd.Timedelta(days=step_days)
    while cur + win_delta <= dmax:
        win_to = cur + win_delta
        sub = d[(d["date_only"] >= cur) & (d["date_only"] <= win_to)]
        if len(sub) >= MIN_WINDOW_DAYS:
            out.append(compute_raw_metrics(sub))
        cur = cur + step_delta
    return out


# ═══════════════════════════════════════════════════════════════════
#  Главный entry-point
# ═══════════════════════════════════════════════════════════════════

def compute_rose(
    db: Session,
    well_number: str,
    *,
    period_from: date,
    period_to: date,
    mode: str = "balanced",
    weights: dict[str, float] | None = None,
    history_step_days: int = 1,
) -> dict[str, Any]:
    """Главная функция: считает розу для (скважина, период, режим).

    Контракт ответа — см. модуль docstring. Если расчёт невозможен —
    `{"ok": False, "error": "..."}` с пояснением.
    """
    # 1. Период
    if period_from > period_to:
        return {"ok": False, "error": "period_from > period_to"}
    period_days = (period_to - period_from).days + 1
    if period_days < MIN_WINDOW_DAYS:
        return {
            "ok": False,
            "error": f"Период слишком короткий: {period_days} дн., минимум {MIN_WINDOW_DAYS}",
        }

    # 2. Веса
    if mode == "custom":
        if not weights:
            return {"ok": False, "error": "Для режима 'custom' нужно передать weights"}
        weights_raw = dict(weights)
    elif mode in SCORING_MODES:
        weights_raw = SCORING_MODES[mode]
    else:
        return {"ok": False, "error": f"Неизвестный режим: {mode}"}
    weights_norm = _normalize_weights(weights_raw)
    if sum(weights_norm.values()) <= 0:
        return {"ok": False, "error": "Сумма весов = 0"}

    # 3. Загружаем всю историю скважины
    df_all = csvc.load_for_well(db, well_number)
    if df_all.empty:
        return {"ok": False, "error": f"В well_daily нет данных по скв. №{well_number}"}

    # 4. Текущий штуцер
    choke = _select_current_choke(df_all, period_to)

    # 5. Период текущий
    df_period = df_all[
        (pd.to_datetime(df_all["date"]).dt.date >= period_from) &
        (pd.to_datetime(df_all["date"]).dt.date <= period_to)
    ].reset_index(drop=True)
    if df_period.empty:
        return {
            "ok": False,
            "error": f"За выбранный период нет данных в well_daily",
        }
    current_raw = compute_raw_metrics(df_period)

    # 6. История: фильтруем по штуцеру и считаем окна
    df_hist = _filter_by_choke(df_all, choke) if choke is not None else df_all
    history_windows = _iter_history_windows(
        df_hist, window_days=period_days, step_days=history_step_days,
    )

    history_meta = {
        "choke_mm": choke,
        "rows_total": int(len(df_hist)),
        "windows_count": len(history_windows),
        "window_days": period_days,
        "step_days": history_step_days,
        "history_from": (
            pd.to_datetime(df_hist["date"]).dt.date.min().isoformat()
            if not df_hist.empty else None
        ),
        "history_to": (
            pd.to_datetime(df_hist["date"]).dt.date.max().isoformat()
            if not df_hist.empty else None
        ),
    }

    if len(history_windows) < MIN_HISTORY_WINDOWS:
        hist_rows = int(len(df_hist))
        choke_lbl = f"{choke} мм" if choke is not None else "(штуцер не задан)"
        hint = ""
        if hist_rows < period_days * 2:
            hint = (
                f". В истории на этом штуцере всего {hist_rows} дн. — это меньше "
                f"двух длин периода ({period_days} дн.). "
                f"Сократите период (например, до {max(7, hist_rows // 4)} дн.) "
                f"или дождитесь накопления данных."
            )
        elif period_days >= 30:
            hint = (
                f". Период {period_days} дн. слишком длинный — окон скользящих "
                f"мало. Попробуйте {max(7, period_days // 3)}–"
                f"{max(14, period_days // 2)} дн."
            )
        return {
            "ok": False,
            "error": (
                f"Истории на штуцере {choke_lbl} недостаточно для расчёта рангов: "
                f"{len(history_windows)} окон длины {period_days} дн., нужно "
                f"≥{MIN_HISTORY_WINDOWS}{hint}"
            ),
            "history": history_meta,
            "current": current_raw.as_dict(),
        }

    # 7. Распределения по 6 метрикам + ранги текущего значения
    distributions: dict[str, list[float]] = {k: [] for k in SCORING_KEYS}
    for w in history_windows:
        d = w.as_dict()
        for k in SCORING_KEYS:
            v = d.get(k)
            if v is not None and math.isfinite(v):
                distributions[k].append(float(v))

    history_median = {k: (float(np.median(distributions[k])) if distributions[k] else None)
                      for k in SCORING_KEYS}

    ranks: dict[str, float | None] = {}
    curr_dict = current_raw.as_dict()
    for k in SCORING_KEYS:
        ranks[k] = _percentile_rank(curr_dict.get(k), distributions[k])

    # Для шкалы 0..100: None → 0 (нет данных = не рисуем лепесток)
    ranks_for_score = {k: (ranks[k] if ranks[k] is not None else 0.0) for k in SCORING_KEYS}

    # 8. Вклады и итоговый балл
    contributions: dict[str, dict[str, float]] = {}
    score = 0.0
    for k in SCORING_KEYS:
        w = weights_norm[k]
        r = ranks_for_score[k]
        actual = r * w
        contributions[k] = {
            "rank": round(r, 1),
            "weight": round(w, 4),
            "actual": round(actual, 2),
            "max":    round(100.0 * w, 2),
        }
        score += actual

    # 9. Внимание про малое покрытие
    weak_data = period_days < 15
    warnings: list[str] = []
    if weak_data:
        warnings.append(
            f"Внимание: в выбранном периоде {period_days} дн. — оценка ненадёжна (< 15 дн.)."
        )
    if choke is None:
        warnings.append(
            "Штуцер не определён (нет choke_mm в well_daily). История считалась по всем дням."
        )

    return {
        "ok": True,
        "well_number": str(well_number),
        "period_from": period_from.isoformat(),
        "period_to": period_to.isoformat(),
        "period_days": period_days,
        "mode": mode,
        "weights_raw": weights_raw,
        "weights": weights_norm,
        "current_raw": {k: _to_jsonable(curr_dict.get(k)) for k in SCORING_KEYS},
        "history_median_raw": {k: _to_jsonable(history_median.get(k)) for k in SCORING_KEYS},
        "history": history_meta,
        "ranks": {k: (round(v, 1) if v is not None else None) for k, v in ranks.items()},
        "contributions": contributions,
        "score": round(score, 1),
        "weak_data": weak_data,
        "warnings": warnings,
        "labels": SCORING_LABELS,
        "labels_short": SCORING_LABELS_SHORT,
        "units": SCORING_RAW_UNITS,
    }


def _to_jsonable(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, 6)
