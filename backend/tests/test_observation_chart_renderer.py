"""
Тесты для backend/services/observation_chart_renderer.py — Phase F.

5 тестов:
- render_baseline_chart: PNG создан, размер ≥ 1 KB
- render_period_timeseries_chart: PNG создан
- render_period_compare_b1_chart: PNG создан
- render_segment_chart: PNG создан
- no mutation: copy.deepcopy сравнение (×4 charts)

Только stdlib + pytest fixtures (tmp_path). Без БД.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from backend.services.observation_chart_renderer import (
    render_baseline_chart,
    render_period_timeseries_chart,
    render_period_compare_b1_chart,
    render_segment_chart,
)


# ---------------------------------------------------------------------------
# HELPER: synthetic snapshots (локальные, независимые от chapter renderer)
# ---------------------------------------------------------------------------


def _baseline_snap() -> dict:
    return {
        "metrics": {
            "p_tube": {"mean": 16.65, "std": 0.21},
            "p_line": {"mean": 5.35, "std": 0.07},
            "dp": {"mean": 11.3, "std": 0.14},
            "q": {"mean": 42300.0, "std": 283.0},
        }
    }


def _period_snap_with_chart() -> dict:
    return {
        "raw": {
            "chart_payload": {
                "dates": [
                    "2025-02-01", "2025-02-05", "2025-02-10",
                    "2025-02-15", "2025-02-20", "2025-02-28",
                ],
                "q": [43000.0, 43100.0, 43200.0, 43050.0, 42900.0, 43300.0],
                "dp": [11.4, 11.45, 11.5, 11.3, 11.2, 11.6],
            }
        },
        "comparisons": {
            "with_b1": {
                "status": "ok",
                "deltas": {
                    "p_tube": {"baseline_value": 16.65, "current_value": 16.80},
                    "p_line": {"baseline_value": 5.35, "current_value": 5.40},
                    "dp": {"baseline_value": 11.3, "current_value": 11.4},
                    "q": {"baseline_value": 42300.0, "current_value": 43100.0},
                },
            }
        },
    }


def _segment_snap() -> dict:
    from datetime import date, timedelta

    base = date(2025, 3, 1)
    dates = [(base + timedelta(days=i)).isoformat() for i in range(30)]
    q_vals = [42000.0 + i * 50 for i in range(30)]
    cp1 = (base + timedelta(days=10)).isoformat()
    cp2 = (base + timedelta(days=20)).isoformat()

    return {
        "raw": {
            "chart_payload": {
                "dates": dates,
                "q": q_vals,
            }
        },
        "segments": [
            {"from": base.isoformat(), "to": (base + timedelta(days=9)).isoformat(), "days": 10, "type": "stable", "q_mean": 42250.0},
            {"from": (base + timedelta(days=10)).isoformat(), "to": (base + timedelta(days=19)).isoformat(), "days": 10, "type": "rising", "q_mean": 42750.0},
            {"from": (base + timedelta(days=20)).isoformat(), "to": (base + timedelta(days=29)).isoformat(), "days": 10, "type": "falling", "q_mean": 43250.0},
        ],
        "changepoints": [
            {"date": cp1, "magnitude_pct": -5.2, "confidence": 0.87},
            {"date": cp2, "magnitude_pct": 3.1, "confidence": 0.82},
        ],
    }


def _empty_snap() -> dict:
    return {}


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


def test_baseline_chart_renders(tmp_path):
    """render_baseline_chart → PNG existing, размер ≥ 1 KB."""
    snap = _baseline_snap()
    out = tmp_path / "baseline.png"
    render_baseline_chart(snap, out)

    assert out.exists(), "PNG файл не создан"
    assert out.stat().st_size >= 1024, f"PNG слишком маленький: {out.stat().st_size} bytes"


def test_period_timeseries_chart_renders(tmp_path):
    """render_period_timeseries_chart → PNG existing, размер ≥ 1 KB."""
    snap = _period_snap_with_chart()
    out = tmp_path / "period_ts.png"
    render_period_timeseries_chart(snap, out)

    assert out.exists(), "PNG файл не создан"
    assert out.stat().st_size >= 1024, f"PNG слишком маленький: {out.stat().st_size} bytes"


def test_period_compare_b1_chart_renders(tmp_path):
    """render_period_compare_b1_chart → PNG existing, размер ≥ 1 KB."""
    snap = _period_snap_with_chart()
    out = tmp_path / "period_cmp.png"
    render_period_compare_b1_chart(snap, out)

    assert out.exists(), "PNG файл не создан"
    assert out.stat().st_size >= 1024, f"PNG слишком маленький: {out.stat().st_size} bytes"


def test_segment_chart_renders(tmp_path):
    """render_segment_chart → PNG existing, размер ≥ 1 KB."""
    snap = _segment_snap()
    out = tmp_path / "segment.png"
    render_segment_chart(snap, out)

    assert out.exists(), "PNG файл не создан"
    assert out.stat().st_size >= 1024, f"PNG слишком маленький: {out.stat().st_size} bytes"


def test_baseline_chart_renders_empty_metrics(tmp_path):
    """render_baseline_chart с пустыми метриками → PNG (no-data figure) создан."""
    out = tmp_path / "baseline_empty.png"
    render_baseline_chart(_empty_snap(), out)

    assert out.exists(), "PNG не создан для пустого snapshot"
    assert out.stat().st_size >= 100, "PNG слишком маленький"


def test_period_timeseries_chart_renders_no_dates(tmp_path):
    """render_period_timeseries_chart без dates → no-data PNG создан (не падает)."""
    out = tmp_path / "period_nodata.png"
    snap = {"raw": {"chart_payload": {"dates": [], "q": [], "dp": []}}}
    render_period_timeseries_chart(snap, out)

    assert out.exists(), "PNG не создан для пустого chart_payload"


def test_chart_does_not_mutate_baseline_snapshot(tmp_path):
    """render_baseline_chart не мутирует snapshot."""
    snap = _baseline_snap()
    snap_before = copy.deepcopy(snap)
    out = tmp_path / "baseline_mut.png"
    render_baseline_chart(snap, out)
    assert snap == snap_before


def test_chart_does_not_mutate_period_timeseries_snapshot(tmp_path):
    """render_period_timeseries_chart не мутирует snapshot."""
    snap = _period_snap_with_chart()
    snap_before = copy.deepcopy(snap)
    out = tmp_path / "period_ts_mut.png"
    render_period_timeseries_chart(snap, out)
    assert snap == snap_before


def test_chart_does_not_mutate_period_compare_b1_snapshot(tmp_path):
    """render_period_compare_b1_chart не мутирует snapshot."""
    snap = _period_snap_with_chart()
    snap_before = copy.deepcopy(snap)
    out = tmp_path / "period_cmp_mut.png"
    render_period_compare_b1_chart(snap, out)
    assert snap == snap_before


def test_chart_does_not_mutate_segment_snapshot(tmp_path):
    """render_segment_chart не мутирует snapshot."""
    snap = _segment_snap()
    snap_before = copy.deepcopy(snap)
    out = tmp_path / "segment_mut.png"
    render_segment_chart(snap, out)
    assert snap == snap_before
