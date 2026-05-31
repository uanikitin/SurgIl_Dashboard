"""
Характеризационный (golden-master) регрессионный тест дебитного тракта.

ЦЕЛЬ: зафиксировать ТЕКУЩИЙ выход compute_full_flow на наборе реальных скважин,
чтобы перед правкой первичной обработки (clean_pressure / cumulative) видеть
ТОЧНО, что изменилось.

Это НЕ тест «правильности» — это снимок «как есть сейчас». После осознанной
правки golden пересоздаётся командой:
    PYTHONPATH=. .venv/bin/python backend/tests/test_flow_regression.py --regen

Зависимость: читает живую БД (pressure_raw за прошлые месяцы — стабильно).
Если активные маски скважины меняются — golden надо пересоздать.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from backend.services.flow_rate.full_pipeline import compute_full_flow

KUNGRAD_OFFSET = timedelta(hours=5)
GOLDEN_PATH = Path(__file__).parent / "golden" / "flow_baseline_2026_03.json"

# (well_id, number) — период один: март 2026 (Кунград)
CASES = [
    {"well_id": 17, "number": 128},  # 6 активных масок
    {"well_id": 21, "number": 98},   # 3 активные маски
    {"well_id": 4,  "number": 61},   # без масок
    {"well_id": 24, "number": 95},   # без масок
    {"well_id": 23, "number": 130},  # без масок
]
K_START = datetime(2026, 3, 1, 0, 0, 0)
K_END = datetime(2026, 3, 31, 23, 59, 59)

# Сравнение: жёсткое, ловим любое изменение сверх плавающего шума.
REL_TOL = 1e-6
ABS_TOL = 1e-6


def _s(series) -> dict:
    """Стабильные скалярные метрики серии (NaN-aware)."""
    v = series.to_numpy(dtype=float)
    n_nan = int(np.isnan(v).sum())
    clean = v[~np.isnan(v)]
    if clean.size == 0:
        return {"n": int(v.size), "n_nan": n_nan, "mean": None,
                "median": None, "min": None, "max": None, "sum": None}
    return {
        "n": int(v.size),
        "n_nan": n_nan,
        "mean": round(float(clean.mean()), 6),
        "median": round(float(np.median(clean)), 6),
        "min": round(float(clean.min()), 6),
        "max": round(float(clean.max()), 6),
        "sum": round(float(clean.sum()), 4),
    }


def fingerprint(well_id: int, smooth: bool) -> dict:
    """Слепок выхода compute_full_flow для одной скважины.

    Сид фиксируется, т.к. реконструкция масок использует np.random.normal
    (см. pressure_mask_service) — без сида golden невоспроизводим. Сид держит
    реализацию шума постоянной, чтобы тест ловил АЛГОРИТМИЧЕСКИЕ изменения.
    """
    np.random.seed(12345)
    u_start = (K_START - KUNGRAD_OFFSET).isoformat()
    u_end = (K_END - KUNGRAD_OFFSET).isoformat()
    res = compute_full_flow(well_id, u_start, u_end, smooth=smooth)
    df = res["df"]
    summ = res["summary"] or {}
    dp = df["p_tube"] - df["p_line"]
    fp = {
        "data_points": int(len(df)),
        "choke_mm": res.get("choke_mm"),
        "p_tube": _s(df["p_tube"]),
        "p_line": _s(df["p_line"]),
        "dp": _s(dp),
        "flow_rate": _s(df["flow_rate"]),
        "flow_zero_count": int((df["flow_rate"] == 0).sum()),
        "cumulative_final": round(float(df["cumulative_flow"].iloc[-1]), 4) if len(df) else None,
        "summary_cumulative_flow": _round(summ.get("cumulative_flow")),
        "summary_median_flow": _round(summ.get("median_flow_rate")),
        "summary_downtime_hours": _round(summ.get("downtime_hours")),
        "purge_cycles": len(res.get("purge_cycles", []) or []),
    }
    return fp


def _round(x):
    return round(float(x), 6) if isinstance(x, (int, float)) and x is not None else x


def build_baseline() -> dict:
    out = {"period": {"from": K_START.isoformat(), "to": K_END.isoformat()},
           "cases": {}}
    for c in CASES:
        wid = c["well_id"]
        out["cases"][str(wid)] = {
            "number": c["number"],
            "smooth_true": fingerprint(wid, smooth=True),
            "smooth_false": fingerprint(wid, smooth=False),
        }
    return out


# ── сравнение ───────────────────────────────────────────────────────────────
def _close(a, b):
    if a is None or b is None:
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)
    return a == b


def _diff(golden, current, path=""):
    diffs = []
    if isinstance(golden, dict):
        for k in golden:
            diffs += _diff(golden[k], current.get(k), f"{path}.{k}")
    else:
        if not _close(golden, current):
            diffs.append(f"{path}: golden={golden} current={current}")
    return diffs


@pytest.mark.skipif(not GOLDEN_PATH.exists(),
                    reason="нет golden — сгенерируйте: python test_flow_regression.py --regen")
@pytest.mark.parametrize("well_id", [c["well_id"] for c in CASES])
def test_flow_regression(well_id):
    golden = json.loads(GOLDEN_PATH.read_text())["cases"][str(well_id)]
    cur = {
        "number": golden["number"],
        "smooth_true": fingerprint(well_id, smooth=True),
        "smooth_false": fingerprint(well_id, smooth=False),
    }
    diffs = _diff(golden, cur, f"well_{well_id}")
    assert not diffs, "Дебит изменился относительно golden:\n" + "\n".join(diffs)


if __name__ == "__main__":
    import sys
    if "--regen" in sys.argv:
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        baseline = build_baseline()
        GOLDEN_PATH.write_text(json.dumps(baseline, ensure_ascii=False, indent=2))
        print(f"golden записан: {GOLDEN_PATH}")
        for wid, c in baseline["cases"].items():
            ft = c["smooth_true"]
            print(f"  скв.{c['number']:>4} (id={wid}): points={ft['data_points']} "
                  f"Q_mean={ft['flow_rate']['mean']} cum={ft['cumulative_final']} "
                  f"p_tube_nan={ft['p_tube']['n_nan']} dp_mean={ft['dp']['mean']}")
    else:
        print("use --regen to (re)generate golden baseline")
