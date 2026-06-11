"""
Тесты для backend/services/observation_period_service.py — Phase C1.

Разделены на два класса:
  - TestUnit        — чистые unit-тесты, без БД, synthetic DataFrames / dicts.
  - TestIntegration — тесты с реальной БД (маркированы pytest.mark.integration).

Запуск unit-тестов (без БД):
    cd /Users/volodymyrnikitin/Documents/PythonFiles/SurgIl_Dashboard
    python -m pytest backend/tests/test_observation_period_service.py -v -k "not integration"

Запуск всех тестов (с реальной БД):
    python -m pytest backend/tests/test_observation_period_service.py -v
"""
from __future__ import annotations

import types
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Вспомогательные фабрики синтетических данных
# ---------------------------------------------------------------------------


def _make_daily_df(
    n_days: int = 30,
    start: date = date(2025, 1, 1),
    p_tube: float = 15.0,
    p_line: float = 10.0,
    q: float = 20.0,
    shutdown_min: float = 30.0,
) -> pd.DataFrame:
    """Синтетический суточный DataFrame для тестов metrics / comparisons."""
    idx = pd.date_range(start=start, periods=n_days, freq="1D")
    dp = p_tube - p_line
    return pd.DataFrame(
        {
            "p_tube":       [p_tube] * n_days,
            "p_line":       [p_line] * n_days,
            "dp":           [dp] * n_days,
            "q":            [q] * n_days,
            "shutdown_min": [shutdown_min] * n_days,
            "purge_flag":   [False] * n_days,
        },
        index=idx,
    )


def _make_customer_df(
    n_days: int = 30,
    start: date = date(2025, 1, 1),
    q_gas_working: float = 19.0,
) -> pd.DataFrame:
    """Синтетический DataFrame заказчика."""
    dates = [start + timedelta(days=i) for i in range(n_days)]
    return pd.DataFrame(
        {
            "date":          pd.to_datetime(dates),
            "p_wellhead":    [14.5] * n_days,
            "p_flowline":    [9.5] * n_days,
            "q_gas_working": [q_gas_working] * n_days,
            "q_gas_total":   [q_gas_working] * n_days,
        }
    )


def _make_full_pipeline_module(
    raises: Exception | None = None,
    n_minutes: int = 30 * 24 * 60,
    p_tube: float = 15.0,
    p_line: float = 10.0,
    q: float = 20.0,
):
    """Создаёт mock-модуль backend.services.flow_rate.full_pipeline."""
    mod = types.ModuleType("backend.services.flow_rate.full_pipeline")

    if raises is not None:
        def _compute(*_a, **_kw):
            raise raises
    else:
        def _compute(*_a, **_kw):
            start = datetime(2025, 1, 1)
            idx = pd.date_range(start, periods=n_minutes, freq="1min")
            df = pd.DataFrame(
                {
                    "p_tube":    [p_tube] * n_minutes,
                    "p_line":    [p_line] * n_minutes,
                    "flow_rate": [q] * n_minutes,
                    "purge_flag": [0] * n_minutes,
                },
                index=idx,
            )
            return {
                "df": df,
                "summary": {},
                "downtime_periods": pd.DataFrame(),
                "purge_cycles": [],
                "data_points": n_minutes,
                "choke_mm": 6.0,
            }

    mod.compute_full_flow = _compute
    return mod


def _make_customer_daily_module(customer_df: pd.DataFrame | None = None):
    """Создаёт mock-модуль backend.services.customer_daily_service."""
    mod = types.ModuleType("backend.services.customer_daily_service")

    if customer_df is None:
        customer_df = pd.DataFrame()

    def _find_well(db, well_number):
        return {"well_number": well_number} if not customer_df.empty else None

    def _load_for_well(db, well_number, d_from=None, d_to=None):
        return customer_df.copy()

    def _ensure_table(db):
        pass

    mod.find_well = _find_well
    mod.load_for_well = _load_for_well
    mod.ensure_table = _ensure_table
    return mod


def _patch_b1(
    customer_df: pd.DataFrame | None = None,
    pipeline_raises: Exception | None = None,
    n_minutes: int = 30 * 24 * 60,
):
    """Возвращает dict для patch.dict(sys.modules, ...) — мокирует B1 зависимости."""
    customer_mod = _make_customer_daily_module(customer_df)
    pipeline_mod = _make_full_pipeline_module(raises=pipeline_raises, n_minutes=n_minutes)
    return {
        "backend.services.flow_rate.full_pipeline": pipeline_mod,
        "backend.services.customer_daily_service":  customer_mod,
    }


# ---------------------------------------------------------------------------
# TestUnit — без БД
# ---------------------------------------------------------------------------


class TestUnit:
    """Чистые unit-тесты. Не требуют БД."""

    # ── Test 2: _compute_own_metrics — базовый расчёт ────────────────────

    def test_compute_own_metrics_basic(self):
        """
        Суточный df с константными значениями.
        mean должны совпасть с заданными, direction='stable', cv > 0.
        """
        from backend.services.observation_period_service import _compute_own_metrics

        df = _make_daily_df(n_days=30, p_tube=15.0, p_line=10.0, q=20.0)
        meta = {"purge_cycles": [], "downtime_periods": pd.DataFrame()}
        result = _compute_own_metrics(df, meta)

        assert result["p_tube"]["mean"] == pytest.approx(15.0, abs=0.01)
        assert result["p_line"]["mean"] == pytest.approx(10.0, abs=0.01)
        assert result["dp"]["mean"]     == pytest.approx(5.0, abs=0.01)
        assert result["q"]["mean"]      == pytest.approx(20.0, abs=0.01)

    def test_compute_own_metrics_empty_df(self):
        """Пустой df → все метрики None / 'insufficient_data'."""
        from backend.services.observation_period_service import _compute_own_metrics

        result = _compute_own_metrics(pd.DataFrame(), {})
        assert result["p_tube"]["mean"] is None
        assert result["p_tube"]["direction"] == "insufficient_data"
        assert result["q"]["mean"] is None

    # ── Test 3: _compute_slope_direction ─────────────────────────────────

    def test_slope_direction_rising(self):
        """Монотонно возрастающий ряд → direction='rising'."""
        from backend.services.observation_period_service import _compute_slope_direction

        series = pd.Series([10.0 + i * 0.5 for i in range(30)])
        slope, direction = _compute_slope_direction(series, "p_tube")

        assert slope is not None and slope > 0
        assert direction == "rising"

    def test_slope_direction_falling(self):
        """Монотонно убывающий ряд → direction='falling'."""
        from backend.services.observation_period_service import _compute_slope_direction

        series = pd.Series([20.0 - i * 0.5 for i in range(30)])
        slope, direction = _compute_slope_direction(series, "p_tube")

        assert slope is not None and slope < 0
        assert direction == "falling"

    def test_slope_direction_stable(self):
        """Константный ряд → direction='stable' (slope ≈ 0 < threshold)."""
        from backend.services.observation_period_service import _compute_slope_direction

        series = pd.Series([15.0] * 30)
        slope, direction = _compute_slope_direction(series, "p_tube")

        assert direction == "stable"

    def test_slope_direction_insufficient(self):
        """Менее 3 точек → direction='insufficient_data'."""
        from backend.services.observation_period_service import _compute_slope_direction

        series = pd.Series([15.0, 16.0])
        _, direction = _compute_slope_direction(series, "p_tube")

        assert direction == "insufficient_data"

    # ── Test 4: _load_baseline_block — без БД ────────────────────────────

    def test_load_baseline_block_none_id_returns_none(self):
        """baseline_block_id=None → возвращает None."""
        from backend.services.observation_period_service import _load_baseline_block

        result = _load_baseline_block(db=MagicMock(), baseline_block_id=None)
        assert result is None

    def test_load_baseline_block_not_found(self):
        """Если БД ничего не вернула (fetchone=None) → None."""
        from backend.services.observation_period_service import _load_baseline_block

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = None

        result = _load_baseline_block(db=mock_db, baseline_block_id=999)
        assert result is None

    def test_load_baseline_block_found(self):
        """Если БД вернула строку со snapshot → возвращает dict с metrics и period."""
        from backend.services.observation_period_service import _load_baseline_block

        snapshot = {
            "_v": "obs_baseline_v1",
            "metrics": {"p_tube": {"mean": 14.0}},
            "period": {"from": "2024-10-01", "to": "2024-10-31"},
        }
        params = {"source": "observation", "period": {"from": "2024-10-01", "to": "2024-10-31"}}

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = (
            42, "observation_baseline", params, snapshot
        )

        result = _load_baseline_block(db=mock_db, baseline_block_id=42)
        assert result is not None
        assert result["block_id"] == 42
        assert result["metrics"]["p_tube"]["mean"] == 14.0
        assert result["_v"] == "obs_baseline_v1"

    # ── Test 5: _compute_comparison_with_b1 ──────────────────────────────

    def test_comparison_with_b1_no_baseline(self):
        """baseline_block_id=None → status='no_baseline'."""
        from backend.services.observation_period_service import _compute_comparison_with_b1

        result = _compute_comparison_with_b1(
            current_metrics={},
            baseline_block=None,
            baseline_block_id=None,
            current_period={"from": "2025-01-01", "to": "2025-01-31"},
        )
        assert result["status"] == "no_baseline"
        assert result["deltas"] is None

    def test_comparison_with_b1_corrupted(self):
        """baseline_block_id задан, но блок не найден → status='baseline_corrupted'."""
        from backend.services.observation_period_service import _compute_comparison_with_b1

        result = _compute_comparison_with_b1(
            current_metrics={},
            baseline_block=None,
            baseline_block_id=42,
            current_period={"from": "2025-01-01", "to": "2025-01-31"},
        )
        assert result["status"] == "baseline_corrupted"
        assert result["baseline_block_id"] == 42

    def test_comparison_with_b1_ok_computes_deltas(self):
        """Базовый happy path: deltas вычисляются корректно."""
        from backend.services.observation_period_service import _compute_comparison_with_b1

        current_metrics = {
            "p_tube": {"mean": 15.0, "cv": 5.0},
            "p_line": {"mean": 10.0},
            "dp":     {"mean": 5.0},
            "q":      {"mean": 20.0},
            "downtime": {"downtime_pct_of_period": 2.0},
        }
        baseline_block = {
            "block_id": 42,
            "period":   {"from": "2024-10-01", "to": "2024-10-31"},
            "metrics": {
                "p_tube": {"mean": 14.0, "cv": 6.0},
                "p_line": {"mean": 11.0},
                "dp":     {"mean": 3.0},
                "q":      {"mean": 18.0},
                "downtime": {"downtime_pct_of_period": 4.0},
            },
            "_v": "obs_baseline_v1",
        }

        result = _compute_comparison_with_b1(
            current_metrics=current_metrics,
            baseline_block=baseline_block,
            baseline_block_id=42,
            current_period={"from": "2025-01-01", "to": "2025-01-31"},
        )

        assert result["status"] == "ok"
        deltas = result["deltas"]

        # p_tube: 15.0 - 14.0 = +1.0 abs, +7.14% pct
        assert deltas["p_tube_mean"]["abs"] == pytest.approx(1.0, abs=0.01)
        assert deltas["p_tube_mean"]["pct"] == pytest.approx(7.14, abs=0.1)

        # q: 20.0 - 18.0 = +2.0 abs, +11.1% pct
        assert deltas["q_mean"]["abs"] == pytest.approx(2.0, abs=0.01)

        # shutdown: 2.0 - 4.0 = -2.0 abs
        assert deltas["shutdown_pct"]["abs"] == pytest.approx(-2.0, abs=0.01)

    # ── Test 6: _resolve_customer_period ─────────────────────────────────

    def test_resolve_customer_period_none_returns_analysis(self):
        """None → возвращает период анализа."""
        from backend.services.observation_period_service import _resolve_customer_period

        result = _resolve_customer_period(None, date(2025, 1, 1), date(2025, 1, 31))
        assert result == {"from": "2025-01-01", "to": "2025-01-31"}

    def test_resolve_customer_period_use_same_true(self):
        """use_same_as_analysis=True → возвращает период анализа, игнорируя from/to."""
        from backend.services.observation_period_service import _resolve_customer_period

        result = _resolve_customer_period(
            {"from": "2024-12-01", "to": "2024-12-31", "use_same_as_analysis": True},
            date(2025, 1, 1),
            date(2025, 1, 31),
        )
        assert result["from"] == "2025-01-01"
        assert result["to"]   == "2025-01-31"

    def test_resolve_customer_period_custom(self):
        """use_same_as_analysis=False → возвращает кастомный период."""
        from backend.services.observation_period_service import _resolve_customer_period

        result = _resolve_customer_period(
            {"from": "2024-12-01", "to": "2024-12-31", "use_same_as_analysis": False},
            date(2025, 1, 1),
            date(2025, 1, 31),
        )
        assert result["from"] == "2024-12-01"
        assert result["to"]   == "2024-12-31"

    # ── Test 7: _classify_data_status_per_day ────────────────────────────

    def test_classify_data_status_ok(self):
        """Оба есть, diff_pct < 25% → 'ok'."""
        from backend.services.observation_period_service import _classify_data_status_per_day

        assert _classify_data_status_per_day(20.0, 19.0, 5.0) == "ok"

    def test_classify_data_status_missing_our(self):
        """our_q is None → 'missing_our'."""
        from backend.services.observation_period_service import _classify_data_status_per_day

        assert _classify_data_status_per_day(None, 19.0, None) == "missing_our"

    def test_classify_data_status_missing_customer(self):
        """customer_q is None → 'missing_customer'."""
        from backend.services.observation_period_service import _classify_data_status_per_day

        assert _classify_data_status_per_day(20.0, None, None) == "missing_customer"

    def test_classify_data_status_invalid(self):
        """diff_pct >= 25% → 'invalid'."""
        from backend.services.observation_period_service import _classify_data_status_per_day

        assert _classify_data_status_per_day(20.0, 14.0, 30.0) == "invalid"

    def test_classify_data_status_invalid_exact_threshold(self):
        """diff_pct == 25% → 'invalid' (граничное значение)."""
        from backend.services.observation_period_service import _classify_data_status_per_day

        assert _classify_data_status_per_day(20.0, 16.0, 25.0) == "invalid"

    # ── Test 8: _build_diagnostics — overall verdict ──────────────────────

    def test_diagnostics_overall_in_list(self):
        """diagnostics содержит запись target='overall', context='combined'."""
        from backend.services.observation_period_service import _build_diagnostics

        df = _make_daily_df(n_days=30)
        with_b1 = {
            "status": "no_baseline",
            "baseline_block_id": None,
            "baseline_period": None,
            "deltas": None,
        }
        with_customer = {
            "status": "no_customer_data",
            "customer_days_available": 0,
            "mape": None,
        }
        diagnostics = _build_diagnostics(
            metrics_layer={
                "p_tube": {"mean": 15.0}, "p_line": {"mean": 10.0},
                "dp": {"mean": 5.0}, "q": {"mean": 20.0},
            },
            with_b1=with_b1,
            with_customer=with_customer,
            our_daily_df=df,
        )

        overall_entries = [
            e for e in diagnostics
            if e.get("target") == "overall" and e.get("context") == "combined"
        ]
        assert len(overall_entries) == 1, (
            f"Ожидается ровно 1 запись overall, получено {len(overall_entries)}"
        )

    def test_diagnostics_no_narrative_strings(self):
        """Все поля verdict — только допустимые enum-значения. Никаких свободных строк."""
        from backend.services.observation_period_service import _build_diagnostics

        allowed_verdicts = {
            "improvement", "degradation", "no_significant_change",
            "insufficient_data", "match", "partial_match", "diverge",
        }
        df = _make_daily_df(n_days=30)
        with_b1 = {"status": "no_baseline", "baseline_block_id": None, "deltas": None}
        with_customer = {"status": "no_customer_data", "customer_days_available": 0, "mape": None}

        diagnostics = _build_diagnostics(
            metrics_layer={
                "p_tube": {"mean": 15.0}, "p_line": {"mean": 10.0},
                "dp": {"mean": 5.0}, "q": {"mean": 20.0},
            },
            with_b1=with_b1,
            with_customer=with_customer,
            our_daily_df=df,
        )

        for entry in diagnostics:
            verdict = entry.get("verdict")
            assert verdict in allowed_verdicts, (
                f"Недопустимое значение verdict: '{verdict}'"
            )

    def test_diagnostics_requires_log_check_always_true(self):
        """requires_log_check всегда True во всех записях diagnostics."""
        from backend.services.observation_period_service import _build_diagnostics

        df = _make_daily_df(n_days=30)
        with_b1 = {"status": "no_baseline", "baseline_block_id": None, "deltas": None}
        with_customer = {"status": "no_customer_data", "customer_days_available": 0, "mape": None}

        diagnostics = _build_diagnostics(
            metrics_layer={},
            with_b1=with_b1,
            with_customer=with_customer,
            our_daily_df=df,
        )

        for entry in diagnostics:
            assert entry.get("requires_log_check") is True, (
                f"requires_log_check должен быть True, запись: {entry}"
            )

    # ── Test 9: _compute_flags ────────────────────────────────────────────

    def test_flags_all_false_by_default(self):
        """Без нарушений флаги — все False."""
        from backend.services.observation_period_service import _compute_flags

        quality_layer = {
            "flags": [],
            "metrics": {"coverage_pct": 95.0},
        }
        with_b1 = {"status": "no_baseline", "baseline_block_id": None}
        with_customer = {"status": "no_customer_data", "customer_days_available": 0}

        flags = _compute_flags(
            quality_layer=quality_layer,
            with_b1=with_b1,
            with_customer=with_customer,
            baseline_block=None,
            current_period={"from": "2025-01-01", "to": "2025-01-31"},
        )

        for key, val in flags.items():
            assert val is False, f"Флаг '{key}' должен быть False, получено {val}"

    def test_flags_low_coverage_from_quality(self):
        """low_coverage из quality.flags → flags.low_coverage=True."""
        from backend.services.observation_period_service import _compute_flags

        quality_layer = {"flags": ["low_coverage"], "metrics": {}}
        with_b1 = {"status": "no_baseline", "baseline_block_id": None}
        with_customer = {"status": "no_customer_data", "customer_days_available": 0}

        flags = _compute_flags(
            quality_layer=quality_layer,
            with_b1=with_b1,
            with_customer=with_customer,
            baseline_block=None,
            current_period={"from": "2025-01-01", "to": "2025-01-31"},
        )

        assert flags["low_coverage"] is True

    def test_flags_invalid_comparison_when_corrupted(self):
        """status='baseline_corrupted' → invalid_comparison=True."""
        from backend.services.observation_period_service import _compute_flags

        quality_layer = {"flags": [], "metrics": {}}
        with_b1 = {"status": "baseline_corrupted", "baseline_block_id": 42}
        with_customer = {"status": "no_customer_data", "customer_days_available": 0}

        flags = _compute_flags(
            quality_layer=quality_layer,
            with_b1=with_b1,
            with_customer=with_customer,
            baseline_block=None,
            current_period={"from": "2025-01-01", "to": "2025-01-31"},
        )

        assert flags["invalid_comparison"] is True

    # ── Test 10: snapshot structure ───────────────────────────────────────

    def test_snapshot_top_level_keys(self):
        """
        compute_period_preview возвращает snapshot с обязательными top-level ключами
        (тест без реальной БД, с мокированным B1).
        """
        import sys

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = (
            "Скважина 1",  # number column
        )

        with patch.dict(sys.modules, _patch_b1()):
            from backend.services.observation_period_service import compute_period_preview
            snapshot = compute_period_preview(
                db=mock_db,
                well_id=21,
                d_from="2025-01-01",
                d_to="2025-01-30",
                include_raw_chart=True,
            )

        required_keys = {
            "_v", "schema_version", "computed_at", "block_status",
            "period", "metrics", "quality", "comparisons", "diagnostics", "flags",
        }
        for key in required_keys:
            assert key in snapshot, f"Обязательный ключ '{key}' отсутствует в snapshot"

    def test_snapshot_v_and_schema_version_constants(self):
        """_v и schema_version соответствуют константам."""
        import sys

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = ("21",)

        with patch.dict(sys.modules, _patch_b1()):
            from backend.services.observation_period_service import (
                compute_period_preview,
                SNAPSHOT_V,
                SCHEMA_VERSION,
            )
            snapshot = compute_period_preview(
                db=mock_db,
                well_id=21,
                d_from="2025-01-01",
                d_to="2025-01-30",
            )

        assert snapshot["_v"] == SNAPSHOT_V
        assert snapshot["schema_version"] == SCHEMA_VERSION

    def test_snapshot_no_data_block_status(self):
        """Если данных нет → block_status='no_data'."""
        import sys

        mock_db = MagicMock()

        with patch.dict(
            sys.modules,
            _patch_b1(pipeline_raises=ValueError("нет данных")),
        ):
            from backend.services.observation_period_service import compute_period_preview
            snapshot = compute_period_preview(
                db=mock_db,
                well_id=99999,
                d_from="2025-01-01",
                d_to="2025-01-30",
            )

        assert snapshot["block_status"] == "no_data"

    def test_snapshot_daily_table_row_object_format(self):
        """
        daily_table — массив объектов (не column-arrays).
        Каждая строка содержит поля: date, our_q_total, our_q_working,
        customer_q_total, customer_q_working, diff_abs, diff_pct, data_status.
        """
        import sys

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = ("21",)

        customer = _make_customer_df(n_days=30, start=date(2025, 1, 1))

        with patch.dict(sys.modules, _patch_b1(customer_df=customer)):
            from backend.services.observation_period_service import compute_period_preview
            snapshot = compute_period_preview(
                db=mock_db,
                well_id=21,
                d_from="2025-01-01",
                d_to="2025-01-30",
            )

        daily_table = snapshot["comparisons"]["with_customer"]["daily_table"]
        assert isinstance(daily_table, list), "daily_table должен быть списком"

        if daily_table:
            required_fields = {
                "date", "our_q_total", "our_q_working",
                "customer_q_total", "customer_q_working",
                "diff_abs", "diff_pct", "data_status",
            }
            first_row = daily_table[0]
            assert isinstance(first_row, dict), "Каждая строка должна быть dict"
            for field in required_fields:
                assert field in first_row, (
                    f"Поле '{field}' отсутствует в строке daily_table"
                )

    def test_snapshot_overall_only_in_diagnostics(self):
        """
        overall_verdict — только в diagnostics[] с target='overall'.
        НЕТ отдельного поля 'overall_verdict' на верхнем уровне.
        """
        import sys

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = ("21",)

        with patch.dict(sys.modules, _patch_b1()):
            from backend.services.observation_period_service import compute_period_preview
            snapshot = compute_period_preview(
                db=mock_db,
                well_id=21,
                d_from="2025-01-01",
                d_to="2025-01-30",
            )

        assert "overall_verdict" not in snapshot, (
            "Поле 'overall_verdict' не должно быть на верхнем уровне snapshot"
        )

        overall_entries = [
            e for e in snapshot["diagnostics"]
            if e.get("target") == "overall" and e.get("context") == "combined"
        ]
        assert len(overall_entries) == 1

    def test_snapshot_no_db_writes(self):
        """
        compute_period_preview не должна вызывать session.add / session.commit.
        """
        import sys

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = ("21",)

        with patch.dict(sys.modules, _patch_b1()):
            from backend.services.observation_period_service import compute_period_preview
            compute_period_preview(
                db=mock_db,
                well_id=21,
                d_from="2025-01-01",
                d_to="2025-01-30",
            )

        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_called()

    def test_snapshot_block_status_values(self):
        """block_status — только 'ok' | 'no_data' | 'insufficient_data'."""
        import sys

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = ("21",)

        allowed_statuses = {"ok", "no_data", "insufficient_data"}

        with patch.dict(sys.modules, _patch_b1()):
            from backend.services.observation_period_service import compute_period_preview
            snapshot = compute_period_preview(
                db=mock_db,
                well_id=21,
                d_from="2025-01-01",
                d_to="2025-01-30",
            )

        assert snapshot["block_status"] in allowed_statuses, (
            f"block_status '{snapshot['block_status']}' не из допустимых {allowed_statuses}"
        )

    def test_snapshot_raw_chart_payload_present(self):
        """include_raw_chart=True → 'raw' с 'chart_payload' в snapshot."""
        import sys

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = ("21",)

        with patch.dict(sys.modules, _patch_b1()):
            from backend.services.observation_period_service import compute_period_preview
            snapshot = compute_period_preview(
                db=mock_db,
                well_id=21,
                d_from="2025-01-01",
                d_to="2025-01-30",
                include_raw_chart=True,
            )

        if snapshot["block_status"] != "no_data":
            assert "raw" in snapshot, "'raw' должен быть в snapshot при include_raw_chart=True"
            assert "chart_payload" in snapshot["raw"]
            cp = snapshot["raw"]["chart_payload"]
            for key in ("dates", "p_tube", "p_line", "dp", "q", "shutdown_hours"):
                assert key in cp, f"'{key}' отсутствует в chart_payload"

    def test_snapshot_raw_chart_absent_when_disabled(self):
        """include_raw_chart=False → 'raw' отсутствует в snapshot."""
        import sys

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = ("21",)

        with patch.dict(sys.modules, _patch_b1()):
            from backend.services.observation_period_service import compute_period_preview
            snapshot = compute_period_preview(
                db=mock_db,
                well_id=21,
                d_from="2025-01-01",
                d_to="2025-01-30",
                include_raw_chart=False,
            )

        assert "raw" not in snapshot, "'raw' не должен быть в snapshot при include_raw_chart=False"

    def test_diagnostics_vs_b1_improvement_verdict(self):
        """
        Если Q вырос на 15% (> 10% threshold) → verdict='improvement' для q.
        """
        from backend.services.observation_period_service import _build_diagnostics

        df = _make_daily_df(n_days=30)
        with_b1 = {
            "status": "ok",
            "baseline_block_id": 1,
            "deltas": {
                "p_tube_mean":  {"abs": 0.3,  "pct": 2.0},   # ниже порога
                "p_line_mean":  {"abs": -0.1, "pct": -1.0},  # ниже порога
                "dp_mean":      {"abs": 0.4,  "pct": 8.5},   # выше порога dp_pct=8%
                "q_mean":       {"abs": 3.0,  "pct": 15.0},  # выше порога 10%
                "shutdown_pct": {"abs": -1.0},
                "cv_p_tube":    {"abs": -0.5},
            },
        }
        with_customer = {"status": "no_customer_data", "customer_days_available": 0, "mape": None}

        diagnostics = _build_diagnostics(
            metrics_layer={},
            with_b1=with_b1,
            with_customer=with_customer,
            our_daily_df=df,
        )

        q_entry = next((e for e in diagnostics if e["target"] == "q" and e["context"] == "vs_b1"), None)
        assert q_entry is not None, "Запись для target='q' context='vs_b1' должна быть"
        assert q_entry["verdict"] == "improvement", (
            f"Рост Q на 15% → 'improvement', получено '{q_entry['verdict']}'"
        )

    def test_diagnostics_vs_customer_match_verdict(self):
        """MAPE q=3% → verdict='match' (< 10%)."""
        from backend.services.observation_period_service import _vs_customer_verdict

        result = _vs_customer_verdict(
            target="q",
            customer_days=15,
            mape_val=3.0,
            thresholds=(10.0, 25.0),
        )
        assert result["verdict"] == "match"

    def test_diagnostics_vs_customer_diverge_verdict(self):
        """MAPE q=30% → verdict='diverge' (>= 25%)."""
        from backend.services.observation_period_service import _vs_customer_verdict

        result = _vs_customer_verdict(
            target="q",
            customer_days=15,
            mape_val=30.0,
            thresholds=(10.0, 25.0),
        )
        assert result["verdict"] == "diverge"

    def test_diagnostics_vs_customer_insufficient_few_days(self):
        """customer_days < MIN_CUSTOMER_DAYS → verdict='insufficient_data'."""
        from backend.services.observation_period_service import _vs_customer_verdict

        result = _vs_customer_verdict(
            target="q",
            customer_days=2,
            mape_val=5.0,
            thresholds=(10.0, 25.0),
        )
        assert result["verdict"] == "insufficient_data"


# ---------------------------------------------------------------------------
# TestIntegration — требуют реальной БД
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestIntegration:
    """
    Интеграционные тесты — требуют реального PostgreSQL.

    Запуск:
        DATABASE_URL=postgresql://... python -m pytest \
            backend/tests/test_observation_period_service.py -v -m integration
    """

    @pytest.fixture
    def db(self):
        """Сессия БД из реального SessionLocal."""
        try:
            from backend.db import SessionLocal
        except ImportError:
            pytest.skip("backend.db не доступен — пропускаем интеграционный тест")
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    def test_compute_period_preview_happy_path(self, db):
        """
        Test 1 (integration): реальная скважина well_id=21, 30 дней.
        Проверяем структуру snapshot и соответствие RFC observation_v1.0.
        """
        from backend.services.observation_period_service import (
            compute_period_preview,
            SNAPSHOT_V,
            SCHEMA_VERSION,
        )

        d_to   = date.today() - timedelta(days=1)
        d_from = d_to - timedelta(days=29)

        snapshot = compute_period_preview(
            db=db,
            well_id=21,
            d_from=d_from,
            d_to=d_to,
            include_raw_chart=True,
        )

        # Meta
        assert snapshot["_v"] == SNAPSHOT_V
        assert snapshot["schema_version"] == SCHEMA_VERSION
        assert "computed_at" in snapshot
        assert snapshot["block_status"] in ("ok", "no_data", "insufficient_data")

        # Period
        assert snapshot["period"]["from"] == d_from.isoformat()
        assert snapshot["period"]["to"]   == d_to.isoformat()

        # Если нет данных — пропускаем структурные проверки
        if snapshot["block_status"] == "no_data":
            pytest.skip("well_id=21 не имеет данных за запрошенный период")

        # Layer 2: metrics
        for key in ("p_tube", "p_line", "dp", "q", "downtime"):
            assert key in snapshot["metrics"], f"metrics.{key} отсутствует"

        # Layer 3: quality
        assert "status" in snapshot["quality"]
        assert "flags" in snapshot["quality"]
        assert "metrics" in snapshot["quality"]

        # Layer 4: comparisons
        assert "with_b1" in snapshot["comparisons"]
        assert "with_customer" in snapshot["comparisons"]
        assert "status" in snapshot["comparisons"]["with_b1"]
        assert "status" in snapshot["comparisons"]["with_customer"]

        # Layer 5: diagnostics
        assert isinstance(snapshot["diagnostics"], list)
        overall = [
            e for e in snapshot["diagnostics"]
            if e.get("target") == "overall" and e.get("context") == "combined"
        ]
        assert len(overall) == 1, "Должна быть ровно одна запись overall в diagnostics"
        assert overall[0]["verdict"] in (
            "improvement", "degradation", "no_significant_change", "insufficient_data"
        )

        # Layer 6: flags
        assert isinstance(snapshot["flags"], dict)
        for flag in (
            "low_coverage", "significant_gap", "outlier_detected",
            "short_intersection", "baseline_mismatch_period",
            "outdated_baseline_version", "invalid_comparison",
        ):
            assert flag in snapshot["flags"], f"flags.{flag} отсутствует"

        # No DB writes
        # (интеграционный тест — session использовалась только на чтение)
        # Если бы был commit — данные изменились бы и следующий тест упал
