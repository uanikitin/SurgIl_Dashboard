"""
pressure_spectrum_service — «Спектр распределения давления» (стабильность скважины).

Переиспользуемый модуль: считает гистограмму распределения устьевого давления
P_уст и перепада ΔP за ОДИН период + метрики разброса/нормальности/стабильности.
Snapshot хранит И метрики, И данные графика (bins+counts) — чтобы в другой главе
можно было сравнить два сохранённых спектра БЕЗ повторного расчёта.

Этап 1: один период, без сравнения. Сравнение «до/после» — отдельный блок,
который читает два таких snapshot'а.

Сигналы (по согласованию с владельцем):
  • P_уст (p_tube)  — устьевое давление, бин 0.2 кгс/см² по умолчанию.
  • ΔP = max(0, p_tube − p_line) — перепад, бин 0.1. Косвенно включает линейное
    давление, поэтому отдельный спектр P_лин не строим.

Метод (см. HANDOFF спектра): основная мера разброса — нормированный IQR
(nIQR = IQR/медиана); нормальность — по асимметрии+эксцессу (не Шапиро, т.к. на
тысячах минутных точек он гиперчувствителен); классификация стабильности по nIQR.
Пороги помечены [требует калибровки] — уточняются на реальных данных.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import numpy as np

# ── Константы метода ────────────────────────────────────────────────────────
DEFAULT_BIN_WIDTH_PRESSURE = 0.2   # кгс/см²
DEFAULT_BIN_WIDTH_DP = 0.1         # кгс/см²
MIN_BIN_WIDTH = 0.05
MAX_BIN_WIDTH = 2.0

MIN_POINTS = 100                   # ниже — insufficient_data
SHORT_PERIOD_POINTS = 2880         # < 2 сут минутных данных → флаг short_period

KUNGRAD_OFFSET = timedelta(hours=5)

# Пороги классификации стабильности по nIQR = IQR/median (безразмерный).
# Дефолты МЯГКИЕ (скважина — не идеальна) и РЕГУЛИРУЮТСЯ из UI.
STABILITY_THRESHOLDS = {
    "p_tube": {"stable": 0.06, "moderate": 0.15},
    "dp": {"stable": 0.10, "moderate": 0.25},
}
HIGH_VARIABILITY_CV = 25.0         # % — порог эскалации/флага по CV (был 15)
OUTLIER_IQR_K = 3.0                # мягкий забор для промышленных данных
OUTLIER_PCT_FLAG = 3.0             # % выбросов → эскалация/флаг (был 2)

# Нормальность по форме (пороги не зависят от N → воспроизводимы).
# Скважинные данные почти никогда не идеально-нормальны (запуски, продувки),
# поэтому пороги МЯГКИЕ — меньше штрафов, шире зона «близко к нормальному».
NORMAL_SKEW = 1.0
NORMAL_KURT = 3.0
NEAR_NORMAL_SKEW = 2.0
NEAR_NORMAL_KURT = 8.0

SIGNALS = [
    {"key": "p_tube", "label": "Устьевое давление", "unit": "кгс/см²",
     "default_bin": DEFAULT_BIN_WIDTH_PRESSURE},
    {"key": "dp", "label": "Перепад ΔP", "unit": "кгс/см²",
     "default_bin": DEFAULT_BIN_WIDTH_DP},
]


def _iso_utc(v, *, is_end: bool = False) -> str:
    """date/datetime/str (Кунград) → UTC ISO для compute_full_flow."""
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, date):
        t = time(23, 59, 59) if is_end else time(0, 0, 0)
        dt = datetime.combine(v, t)
    else:
        dt = datetime.fromisoformat(str(v))
    return (dt - KUNGRAD_OFFSET).isoformat()


def _clamp_bin(w: float | None, default: float) -> float:
    try:
        w = float(w)
    except (TypeError, ValueError):
        return default
    return max(MIN_BIN_WIDTH, min(MAX_BIN_WIDTH, w))


def _skew_kurt(x: np.ndarray) -> tuple[float, float]:
    """Асимметрия и эксцесс (Фишеровский, excess) через моменты. NaN-safe."""
    n = x.size
    if n < 3:
        return 0.0, 0.0
    mean = float(np.mean(x))
    std = float(np.std(x))  # population std
    if std == 0:
        return 0.0, 0.0
    z = (x - mean) / std
    skew = float(np.mean(z ** 3))
    kurt = float(np.mean(z ** 4) - 3.0)
    return skew, kurt


def _compute_signal_spectrum(values: np.ndarray, *, bin_width: float,
                             left_edge_zero: bool, thresholds: dict,
                             cv_threshold: float = HIGH_VARIABILITY_CV,
                             outlier_threshold: float = OUTLIER_PCT_FLAG,
                             remove_outliers: bool = False) -> dict:
    """Гистограмма + метрики + класс стабильности для одного сигнала."""
    x = values[np.isfinite(values)]
    n = int(x.size)
    if n == 0:
        return {"n_points": 0, "bin_edges": [], "counts": [], "metrics": None,
                "stability_class": "no_data"}

    # Опциональное удаление выбросов (по галочке). По умолчанию — НЕ убираем.
    removed_pct = 0.0
    if remove_outliers and n >= 4:
        _p25, _p75 = np.percentile(x, [25, 75])
        _iqr = _p75 - _p25
        if _iqr > 0:
            lo = _p25 - OUTLIER_IQR_K * _iqr
            hi = _p75 + OUTLIER_IQR_K * _iqr
            keep = (x >= lo) & (x <= hi)
            removed = int(np.count_nonzero(~keep))
            if removed > 0 and int(keep.sum()) >= 2:
                removed_pct = removed / n * 100.0
                x = x[keep]
                n = int(x.size)

    vmin = 0.0 if left_edge_zero else float(np.floor(x.min() / bin_width) * bin_width)
    vmax = float(np.ceil(x.max() / bin_width) * bin_width)
    if vmax <= vmin:
        vmax = vmin + bin_width
    # +1.5*bin гарантирует, что верхний край включает max
    edges = np.arange(vmin, vmax + bin_width * 1.5, bin_width)
    counts, edges = np.histogram(x, bins=edges)

    median = float(np.median(x))
    mean = float(np.mean(x))
    std = float(np.std(x, ddof=1)) if n > 1 else 0.0
    p10, p25, p75, p90 = (float(np.percentile(x, p)) for p in (10, 25, 75, 90))
    iqr = p75 - p25
    w90 = p90 - p10
    mad = float(np.median(np.abs(x - median)))
    niqr = (iqr / median) if median else 0.0
    cv_median = (std / median * 100.0) if median else 0.0
    skew, kurt = _skew_kurt(x)

    is_normal = abs(skew) < NORMAL_SKEW and abs(kurt) < NORMAL_KURT
    is_near_normal = abs(skew) < NEAR_NORMAL_SKEW and abs(kurt) < NEAR_NORMAL_KURT
    norm_label = "normal" if is_normal else ("near_normal" if is_near_normal else "non_normal")

    # Класс стабильности по nIQR
    if iqr == 0:
        stability = "degenerate"
    elif niqr < thresholds["stable"]:
        stability = "stable"
    elif niqr < thresholds["moderate"]:
        stability = "moderate"
    else:
        stability = "unstable"

    # Выбросы (мягкий забор 3·IQR)
    fence_lo = p25 - OUTLIER_IQR_K * iqr
    fence_hi = p75 + OUTLIER_IQR_K * iqr
    outlier_cnt = int(np.count_nonzero((x < fence_lo) | (x > fence_hi)))
    outlier_pct = outlier_cnt / n * 100.0

    # Эскалация: узкое ядро (малый nIQR) не делает режим стабильным, если есть
    # заметные выбросы или высокая вариативность (CV). Это устраняет ложное
    # «стабильно» при явных хвостах в спектре.
    _levels = ["stable", "moderate", "unstable"]
    if stability in _levels and (outlier_pct > outlier_threshold or cv_median > cv_threshold):
        stability = _levels[min(_levels.index(stability) + 1, 2)]

    return {
        "n_points": n,
        "bin_width": round(bin_width, 4),
        "bin_edges": [round(float(e), 4) for e in edges],
        "counts": [int(c) for c in counts],
        "metrics": {
            "median": round(median, 3), "mean": round(mean, 3), "std": round(std, 3),
            "min": round(float(x.min()), 3), "max": round(float(x.max()), 3),
            "p10": round(p10, 3), "p25": round(p25, 3),
            "p75": round(p75, 3), "p90": round(p90, 3),
            "iqr": round(iqr, 3), "w90": round(w90, 3), "mad": round(mad, 3),
            "niqr": round(niqr, 4), "cv_median": round(cv_median, 2),
            "skewness": round(skew, 3), "kurtosis": round(kurt, 3),
            "normality": norm_label, "is_normal": is_normal,
            "outlier_pct": round(outlier_pct, 2),
            "outliers_removed_pct": round(removed_pct, 2),
        },
        "stability_class": stability,
    }


_STAB_RANGE_RU = {
    "stable": "узкий — режим стабильный",
    "moderate": "умеренный",
    "unstable": "широкий — режим нестабильный",
    "degenerate": "вырожденный (нет разброса)",
    "no_data": "нет данных",
}
_NORM_RU = {
    "normal": "близко к нормальному",
    "near_normal": "почти нормальное (приемлемо для скважины)",
    "non_normal": "не нормальное",
}


def _describe_signal(spec: dict) -> str:
    """Авто-описание распределения сигнала (для оператора, на русском)."""
    m = spec.get("metrics") or {}
    if not m:
        return ""
    unit = spec.get("unit") or ""
    label = spec.get("label") or ""
    cls = spec.get("stability_class")
    parts = [f"{label}: медиана {m.get('median')} {unit};"]
    parts.append(
        f"рабочий диапазон (P10–P90) {m.get('p10')}–{m.get('p90')} {unit}, "
        f"ширина спектра nIQR={m.get('niqr')} — {_STAB_RANGE_RU.get(cls, str(cls))}."
    )
    rem = m.get("outliers_removed_pct") or 0
    if rem:
        parts.append(f"Выбросы удалены ({rem}% точек).")
    else:
        parts.append(f"Выбросов {m.get('outlier_pct')}% (за пределами 3·IQR).")
    parts.append(
        f"Распределение {_NORM_RU.get(m.get('normality'), str(m.get('normality')))} "
        f"(асимметрия {m.get('skewness')}, эксцесс {m.get('kurtosis')})."
    )
    return " ".join(parts)


def build_pressure_spectrum(
    db,
    *,
    well_id: int,
    well_number: str | None = None,
    period_from: date | str,
    period_to: date | str,
    bin_width_pressure: float | None = None,
    bin_width_dp: float | None = None,
    label: str | None = None,
    cv_threshold: float | None = None,
    outlier_threshold: float | None = None,
    niqr_p_stable: float | None = None,
    niqr_p_moderate: float | None = None,
    niqr_dp_stable: float | None = None,
    niqr_dp_moderate: float | None = None,
    remove_outliers: bool = False,
) -> dict[str, Any]:
    """Спектр распределения P_уст и ΔP за ОДИН период (этап 1, без сравнения).

    Возвращает snapshot (для сохранения в customer_report_block.data_snapshot и
    для UI). Содержит гистограммы (bins+counts) и метрики — достаточно для
    повторного рендера и для последующего сравнения двух периодов БЕЗ пересчёта.

    Критерии стабильности регулируются (cv_threshold, outlier_threshold, nIQR-
    пороги). Дефолты — мягкие; используемые значения сохраняются в snapshot.criteria.
    """
    bw_p = _clamp_bin(bin_width_pressure, DEFAULT_BIN_WIDTH_PRESSURE)
    bw_dp = _clamp_bin(bin_width_dp, DEFAULT_BIN_WIDTH_DP)

    # ── Эффективные критерии стабильности (override → дефолт) ──
    def _pos(v, default):
        try:
            v = float(v)
            return v if v > 0 else default
        except (TypeError, ValueError):
            return default

    cv_thr = _pos(cv_threshold, HIGH_VARIABILITY_CV)
    outl_thr = _pos(outlier_threshold, OUTLIER_PCT_FLAG)
    eff_thresholds = {
        "p_tube": {
            "stable": _pos(niqr_p_stable, STABILITY_THRESHOLDS["p_tube"]["stable"]),
            "moderate": _pos(niqr_p_moderate, STABILITY_THRESHOLDS["p_tube"]["moderate"]),
        },
        "dp": {
            "stable": _pos(niqr_dp_stable, STABILITY_THRESHOLDS["dp"]["stable"]),
            "moderate": _pos(niqr_dp_moderate, STABILITY_THRESHOLDS["dp"]["moderate"]),
        },
    }

    pf = period_from if isinstance(period_from, str) else period_from.isoformat()
    pt = period_to if isinstance(period_to, str) else period_to.isoformat()

    snapshot: dict[str, Any] = {
        "_v": "pressure_spectrum_v1",
        "schema_version": "1.0",
        "computed_at": None,  # проставляется вызывающим кодом / роутером
        "block_status": "ok",
        "well_id": well_id,
        "well_number": well_number,
        "label": label or "Спектр давления",
        "period": {"from": str(pf)[:10], "to": str(pt)[:10]},
        "bin_width_pressure": bw_p,
        "bin_width_dp": bw_dp,
        "criteria": {
            "cv_threshold": cv_thr,
            "outlier_threshold": outl_thr,
            "niqr_p_stable": eff_thresholds["p_tube"]["stable"],
            "niqr_p_moderate": eff_thresholds["p_tube"]["moderate"],
            "niqr_dp_stable": eff_thresholds["dp"]["stable"],
            "niqr_dp_moderate": eff_thresholds["dp"]["moderate"],
            "remove_outliers": bool(remove_outliers),
        },
        "signals": {},
        "flags": {},
    }

    # ── Данные через единый pipeline (тот же, что страница скважины) ──
    try:
        from backend.services.flow_rate.full_pipeline import compute_full_flow
        ds = _iso_utc(period_from, is_end=False)
        de = _iso_utc(period_to, is_end=True)
        result = compute_full_flow(well_id, ds, de, smooth=True)
        df = result["df"]
    except Exception as exc:  # noqa: BLE001
        snapshot["block_status"] = "no_data"
        snapshot["error"] = f"compute_full_flow failed: {exc}"
        return snapshot

    if df is None or df.empty:
        snapshot["block_status"] = "no_data"
        return snapshot

    p_tube = df["p_tube"].to_numpy(dtype=float)
    p_line = df["p_line"].to_numpy(dtype=float)
    # ΔP = max(0, p_tube − p_line) — соглашение проекта (перепад ≥ 0)
    dp = np.clip(p_tube - p_line, 0.0, None)
    # false-zeros: устьевое > 0
    p_tube_valid = np.where(p_tube > 0, p_tube, np.nan)

    series_map = {"p_tube": p_tube_valid, "dp": dp}
    bin_map = {"p_tube": bw_p, "dp": bw_dp}
    left_zero = {"p_tube": False, "dp": True}

    max_n = 0
    for sig in SIGNALS:
        key = sig["key"]
        spec = _compute_signal_spectrum(
            series_map[key], bin_width=bin_map[key],
            left_edge_zero=left_zero[key],
            thresholds=eff_thresholds[key],
            cv_threshold=cv_thr, outlier_threshold=outl_thr,
            remove_outliers=bool(remove_outliers),
        )
        spec["label"] = sig["label"]
        spec["unit"] = sig["unit"]
        spec["description"] = _describe_signal(spec)
        snapshot["signals"][key] = spec
        max_n = max(max_n, spec["n_points"])

    # ── Статус по объёму данных ──
    if max_n == 0:
        snapshot["block_status"] = "no_data"
    elif max_n < MIN_POINTS:
        snapshot["block_status"] = "insufficient_data"

    # ── Флаги ──
    pt_m = (snapshot["signals"].get("p_tube") or {}).get("metrics") or {}
    dp_m = (snapshot["signals"].get("dp") or {}).get("metrics") or {}
    snapshot["flags"] = {
        "short_period": 0 < max_n < SHORT_PERIOD_POINTS,
        "high_variability": (pt_m.get("cv_median", 0) > cv_thr
                             or dp_m.get("cv_median", 0) > cv_thr),
        "outliers_present": (pt_m.get("outlier_pct", 0) > outl_thr
                             or dp_m.get("outlier_pct", 0) > outl_thr),
    }

    return snapshot
