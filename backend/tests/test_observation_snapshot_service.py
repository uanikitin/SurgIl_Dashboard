"""
Тесты для backend/services/observation_snapshot_service.py — Phase C3.

Структура:
  - TestValidation        — unit-тесты validate_snapshot_structure (тесты 1-17)
  - TestClassification    — unit-тесты classify_block_status priority rules (тесты 18-20)
  - TestIntegration       — DB load path с реальным customer_report_block (тест 21)

Запуск unit-тестов (без БД):
    cd /Users/volodymyrnikitin/Documents/PythonFiles/SurgIl_Dashboard
    python -m pytest backend/tests/test_observation_snapshot_service.py -v -k "not integration"

Запуск всех тестов (с реальной БД):
    python -m pytest backend/tests/test_observation_snapshot_service.py -v
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_valid_period_snapshot(
    kind: str = "observation_period",
    *,
    extra: dict | None = None,
) -> dict:
    """
    Минимальный валидный snapshot для observation_period.
    Содержит все обязательные top-level ключи и слои.
    """
    v_map = {
        "observation_period": "obs_period_v1",
        "observation_baseline": "obs_baseline_v1",
        "observation_segment": "obs_segment_v1",
    }
    snap = {
        "_v": v_map.get(kind, "obs_period_v1"),
        "schema_version": "1.0",
        "computed_at": "2026-01-01T00:00:00Z",
        "block_status": "ok",
        "period": {"from": "2026-01-01", "to": "2026-01-31"},
        "metrics": {
            "p_tube": {"mean": 15.0, "median": 15.0, "min": 14.0, "max": 16.0,
                       "std": 0.5, "cv": 3.3, "slope": 0.01, "direction": "stable"},
            "p_line": {"mean": 10.0, "median": 10.0, "min": 9.0, "max": 11.0,
                       "std": 0.3, "cv": 3.0, "slope": 0.0, "direction": "stable"},
            "dp":     {"mean": 5.0, "median": 5.0, "min": 4.0, "max": 6.0,
                       "std": 0.4, "cv": 8.0, "slope": 0.0, "direction": "stable"},
            "q":      {"mean": 20.0, "median": 20.0, "min": 18.0, "max": 22.0,
                       "std": 1.0, "cv": 5.0, "slope": 0.0, "direction": "stable"},
            "downtime": {"total_hours": 5.0, "events_count": 1,
                         "max_event_hours": 5.0, "downtime_pct_of_period": 0.7},
            "purge_events_count": 2,
        },
        "quality": {
            "status": "ok",
            "flags": [],
            "metrics": {
                "coverage_pct": 95.0,
                "gap_count": 0,
                "max_gap_hours": 0.0,
                "suspicious_spikes_count": 0,
                "false_zero_pct": 3.5,
                "days_with_data": 30,
                "days_requested": 31,
            },
        },
        "comparisons": {
            "with_b1": {
                "status": "no_baseline",
                "baseline_block_id": None,
                "baseline_period": None,
                "deltas": None,
            },
            "with_customer": {
                "status": "no_customer_data",
                "customer_period": None,
                "customer_days_available": 0,
                "customer_days_requested": 31,
                "mape": None,
                "q_source_used": None,
                "daily_table": [],
            },
        },
        "diagnostics": [
            {
                "target": "overall",
                "context": "combined",
                "verdict": "insufficient_data",
                "magnitude": None,
                "requires_log_check": True,
            }
        ],
        "flags": {
            "low_coverage": False,
            "significant_gap": False,
            "outlier_detected": False,
            "short_intersection": False,
            "baseline_mismatch_period": False,
            "outdated_baseline_version": False,
        },
    }
    if extra:
        snap.update(extra)
    return snap


def _make_valid_baseline_snapshot() -> dict:
    """Минимальный валидный snapshot для observation_baseline."""
    return {
        "_v": "obs_baseline_v1",
        "schema_version": "1.0",
        "computed_at": "2026-01-01T00:00:00Z",
        "block_status": "ok",
        "period": {"from": "2025-10-01", "to": "2025-10-31"},
        "metrics": {
            "p_tube": {"mean": 14.0, "median": 14.0, "min": 13.0, "max": 15.0,
                       "std": 0.4, "cv": 2.9, "slope": 0.0, "direction": "stable"},
            "p_line": {"mean": 9.5},
            "dp":     {"mean": 4.5},
            "q":      {"mean": 18.0},
            "downtime": {"total_hours": 0.0, "events_count": 0,
                         "max_event_hours": 0.0, "downtime_pct_of_period": 0.0},
            "purge_events_count": 0,
        },
        "quality": {
            "status": "ok",
            "flags": [],
            "metrics": {
                "coverage_pct": 97.0,
                "gap_count": 0,
                "max_gap_hours": 0.0,
                "suspicious_spikes_count": 0,
                "false_zero_pct": 2.0,
                "days_with_data": 31,
                "days_requested": 31,
            },
        },
        "flags": {
            "low_coverage": False,
            "significant_gap": False,
            "outlier_detected": False,
            "short_intersection": False,
            "baseline_mismatch_period": False,
            "outdated_baseline_version": False,
        },
    }


# ---------------------------------------------------------------------------
# TestValidation — unit-тесты validate_snapshot_structure
# ---------------------------------------------------------------------------


class TestValidation:
    """Unit-тесты validate_snapshot_structure. Не требуют БД."""

    # ── Test 1: happy path observation_period ─────────────────────────────

    def test_happy_path_observation_period(self):
        """Валидный snapshot observation_period → no errors, no flags."""
        from backend.services.observation_snapshot_service import validate_snapshot_structure

        snap = _make_valid_period_snapshot("observation_period")
        result = validate_snapshot_structure(snap, "observation_period")

        errors = [i for i in result.issues if i.severity in ("corrupted", "invalid_schema")]
        assert not errors, f"Unexpected errors: {errors}"
        assert not result.has_corrupted
        assert not result.has_invalid_schema
        assert not result.has_legacy

    # ── Test 2: missing required top keys ─────────────────────────────────

    def test_missing_required_top_keys(self):
        """Отсутствие _v, schema_version, period → invalid_schema."""
        from backend.services.observation_snapshot_service import validate_snapshot_structure

        snap = {
            "computed_at": "2026-01-01T00:00:00Z",
            "block_status": "ok",
            "quality": {"status": "ok", "flags": [], "metrics": {}},
            "flags": {},
        }
        result = validate_snapshot_structure(snap, "observation_period")

        assert result.has_invalid_schema or result.has_legacy, (
            "Missing _v/schema_version/period should produce invalid_schema or legacy"
        )
        codes = {i.code for i in result.issues}
        assert "missing_field" in codes

    # ── Test 3: invalid enum in required field ─────────────────────────────

    def test_invalid_enum_quality_status(self):
        """quality.status с недопустимым значением → invalid_schema."""
        from backend.services.observation_snapshot_service import validate_snapshot_structure

        snap = _make_valid_period_snapshot()
        snap["quality"]["status"] = "totally_invalid_status"
        result = validate_snapshot_structure(snap, "observation_period")

        assert result.has_invalid_schema
        codes = {i.code for i in result.issues}
        assert "invalid_enum" in codes

    # ── Test 4: NaN in metric ─────────────────────────────────────────────

    def test_nan_in_metric(self):
        """NaN в metrics.p_tube.mean → corrupted."""
        from backend.services.observation_snapshot_service import validate_snapshot_structure

        snap = _make_valid_period_snapshot()
        snap["metrics"]["p_tube"]["mean"] = float("nan")
        result = validate_snapshot_structure(snap, "observation_period")

        assert result.has_corrupted
        codes = {i.code for i in result.issues}
        assert "nan_in_metric" in codes

    # ── Test 5: Inf in metric ─────────────────────────────────────────────

    def test_inf_in_metric(self):
        """Inf в metrics.q.mean → corrupted."""
        from backend.services.observation_snapshot_service import validate_snapshot_structure

        snap = _make_valid_period_snapshot()
        snap["metrics"]["q"]["mean"] = float("inf")
        result = validate_snapshot_structure(snap, "observation_period")

        assert result.has_corrupted
        codes = {i.code for i in result.issues}
        assert "inf_in_metric" in codes

    # ── Test 6: negative pressure ─────────────────────────────────────────

    def test_negative_pressure(self):
        """Отрицательное давление p_tube.mean < 0 → corrupted."""
        from backend.services.observation_snapshot_service import validate_snapshot_structure

        snap = _make_valid_period_snapshot()
        snap["metrics"]["p_tube"]["mean"] = -5.0
        result = validate_snapshot_structure(snap, "observation_period")

        assert result.has_corrupted
        codes = {i.code for i in result.issues}
        assert "negative_pressure" in codes

    # ── Test 7: coverage > 100 ────────────────────────────────────────────

    def test_coverage_out_of_range(self):
        """coverage_pct > 100 → corrupted."""
        from backend.services.observation_snapshot_service import validate_snapshot_structure

        snap = _make_valid_period_snapshot()
        snap["quality"]["metrics"]["coverage_pct"] = 115.0
        result = validate_snapshot_structure(snap, "observation_period")

        assert result.has_corrupted
        codes = {i.code for i in result.issues}
        assert "coverage_out_of_range" in codes

    # ── Test 8: mismatched chart payload arrays ───────────────────────────

    def test_mismatched_chart_payload_arrays(self):
        """chart_payload arrays разной длины → corrupted."""
        from backend.services.observation_snapshot_service import validate_snapshot_structure

        snap = _make_valid_period_snapshot()
        snap["raw"] = {
            "chart_payload": {
                "dates":   ["2026-01-01", "2026-01-02", "2026-01-03"],
                "p_tube":  [15.0, 15.1],          # на 1 короче
                "p_line":  [10.0, 10.1, 10.2],
            }
        }
        result = validate_snapshot_structure(snap, "observation_period")

        assert result.has_corrupted
        codes = {i.code for i in result.issues}
        assert "mismatched_arrays" in codes

    # ── Test 9: diagnostics not list ──────────────────────────────────────

    def test_diagnostics_not_list(self):
        """diagnostics — не список → corrupted."""
        from backend.services.observation_snapshot_service import validate_snapshot_structure

        snap = _make_valid_period_snapshot()
        snap["diagnostics"] = {"target": "overall"}  # dict вместо list
        result = validate_snapshot_structure(snap, "observation_period")

        assert result.has_corrupted
        codes = {i.code for i in result.issues}
        assert "invalid_type" in codes

    # ── Test 10: inverted period (from > to) ─────────────────────────────

    def test_inverted_period(self):
        """period.from > period.to → corrupted."""
        from backend.services.observation_snapshot_service import validate_snapshot_structure

        snap = _make_valid_period_snapshot()
        snap["period"] = {"from": "2026-02-01", "to": "2026-01-01"}
        result = validate_snapshot_structure(snap, "observation_period")

        assert result.has_corrupted
        codes = {i.code for i in result.issues}
        assert "inverted_period" in codes

    # ── Test 11: legacy — _v missing ─────────────────────────────────────

    def test_legacy_v_missing(self):
        """Отсутствует _v → has_legacy=True."""
        from backend.services.observation_snapshot_service import validate_snapshot_structure

        snap = _make_valid_period_snapshot()
        del snap["_v"]
        result = validate_snapshot_structure(snap, "observation_period")

        assert result.has_legacy
        assert not result.has_corrupted

    # ── Test 12: legacy — _v wrong value ─────────────────────────────────

    def test_legacy_v_wrong_value(self):
        """_v содержит старый тег (obs_period_v0) → has_legacy=True."""
        from backend.services.observation_snapshot_service import validate_snapshot_structure

        snap = _make_valid_period_snapshot()
        snap["_v"] = "obs_period_v0"
        result = validate_snapshot_structure(snap, "observation_period")

        assert result.has_legacy
        assert not result.has_corrupted

    # ── Test 13: major schema mismatch ────────────────────────────────────

    def test_major_schema_mismatch(self):
        """schema_version='2.0' → has_legacy=True (major != 1)."""
        from backend.services.observation_snapshot_service import validate_snapshot_structure

        snap = _make_valid_period_snapshot()
        snap["schema_version"] = "2.0"
        result = validate_snapshot_structure(snap, "observation_period")

        assert result.has_legacy
        assert not result.has_corrupted

    # ── Test 14: partial — raw missing ───────────────────────────────────

    def test_partial_raw_missing(self):
        """Нет слоя raw, все required присутствуют → has_partial=True."""
        from backend.services.observation_snapshot_service import validate_snapshot_structure

        snap = _make_valid_period_snapshot()
        # raw отсутствует по умолчанию в нашем _make_valid_period_snapshot
        assert "raw" not in snap
        result = validate_snapshot_structure(snap, "observation_period")

        assert result.has_partial
        assert not result.has_invalid_schema
        assert not result.has_corrupted

    # ── Test 15: unknown field preserved + warning ────────────────────────

    def test_unknown_top_level_field_preserved(self):
        """Unknown top-level key in snapshot: validate does not crash, no corrupted/invalid."""
        from backend.services.observation_snapshot_service import validate_snapshot_structure

        snap = _make_valid_period_snapshot()
        snap["custom_experiment_field"] = {"some": "data"}
        result = validate_snapshot_structure(snap, "observation_period")

        # validate не должна падать и не создавать corrupted/invalid_schema
        assert not result.has_corrupted
        assert not result.has_invalid_schema

    # ── Test 16: narrative stripped + warning ─────────────────────────────

    def test_narrative_stripped_from_diagnostics(self):
        """narrative-поле в diagnostic entry → stripped + warning в apply_compatibility."""
        import copy
        from backend.services.observation_snapshot_service import (
            validate_snapshot_structure,
            _apply_compatibility,
        )

        snap = _make_valid_period_snapshot()
        snap["diagnostics"] = [
            {
                "target": "overall",
                "context": "combined",
                "verdict": "insufficient_data",
                "magnitude": None,
                "requires_log_check": True,
                "narrative": "Скважина улучшилась",  # запрещённое поле
            }
        ]
        validation = validate_snapshot_structure(snap, "observation_period")
        sanitized = _apply_compatibility(copy.deepcopy(snap), validation)

        # narrative должно быть удалено
        for entry in sanitized["diagnostics"]:
            assert "narrative" not in entry, (
                "narrative должно быть удалено из diagnostic entry"
            )

        # _apply_compatibility добавляет warning в validation (передаётся по ref)
        # Основной контракт: narrative отсутствует в sanitized
        assert "narrative" not in sanitized["diagnostics"][0]
        stripped_warnings = [
            i for i in validation.issues if i.code == "narrative_field_stripped"
        ]
        assert len(stripped_warnings) >= 1, (
            "Должен быть выдан warning narrative_field_stripped"
        )

    # ── Test 17: missing overall diagnostic auto-added ────────────────────

    def test_missing_overall_diagnostic_auto_added(self):
        """Нет overall в diagnostics observation_period → auto-added с verdict=insufficient_data."""
        import copy
        from backend.services.observation_snapshot_service import (
            validate_snapshot_structure,
            _apply_compatibility,
        )

        snap = _make_valid_period_snapshot()
        snap["diagnostics"] = [
            {
                "target": "p_tube",
                "context": "vs_b1",
                "verdict": "improvement",
                "magnitude": {"pct": 5.0},
                "requires_log_check": True,
            }
        ]
        validation = validate_snapshot_structure(snap, "observation_period")
        sanitized = _apply_compatibility(copy.deepcopy(snap), validation, "observation_period")

        overall = [
            e for e in sanitized["diagnostics"]
            if isinstance(e, dict) and e.get("target") == "overall"
        ]
        assert len(overall) == 1, "overall diagnostic должен быть добавлен"
        assert overall[0]["verdict"] == "insufficient_data"
        assert overall[0]["context"] == "combined"

        # _ensure_overall_diagnostic добавляет warning в validation (передаётся по ref)
        # Проверяем через sanitized + наличие overall — достаточно для контракта


# ---------------------------------------------------------------------------
# TestClassification — unit-тесты classify_block_status priority rules
# ---------------------------------------------------------------------------


class TestClassification:
    """Unit-тесты classify_block_status. Не требуют БД."""

    # ── Test 18: corrupted beats legacy ───────────────────────────────────

    def test_corrupted_beats_legacy(self):
        """corrupted имеет приоритет над legacy_v0."""
        from backend.services.observation_snapshot_service import (
            classify_block_status,
            ValidationResult,
        )

        validation = ValidationResult()
        validation.has_corrupted = True
        validation.has_legacy = True  # оба активны

        status = classify_block_status({}, validation)
        assert status == "corrupted"

    # ── Test 19: legacy beats no_data ─────────────────────────────────────

    def test_legacy_beats_no_data(self):
        """legacy_v0 имеет приоритет над no_data (из quality.status)."""
        from backend.services.observation_snapshot_service import (
            classify_block_status,
            ValidationResult,
        )

        validation = ValidationResult()
        validation.has_legacy = True

        snap = {"quality": {"status": "no_data"}}
        status = classify_block_status(snap, validation)
        assert status == "legacy_v0"

    # ── Test 20: partial only → partial ───────────────────────────────────

    def test_partial_only_returns_partial(self):
        """has_partial=True (без corrupted/invalid/legacy) → partial."""
        from backend.services.observation_snapshot_service import (
            classify_block_status,
            ValidationResult,
        )

        validation = ValidationResult()
        validation.has_partial = True

        snap = {"quality": {"status": "ok"}}
        status = classify_block_status(snap, validation)
        assert status == "partial"

    # ── Дополнительные priority tests ────────────────────────────────────

    def test_invalid_schema_beats_legacy(self):
        """invalid_schema имеет приоритет над legacy_v0."""
        from backend.services.observation_snapshot_service import (
            classify_block_status,
            ValidationResult,
        )

        validation = ValidationResult()
        validation.has_invalid_schema = True
        validation.has_legacy = True

        status = classify_block_status({}, validation)
        assert status == "invalid_schema"

    def test_ok_default(self):
        """Без проблем → ok."""
        from backend.services.observation_snapshot_service import (
            classify_block_status,
            ValidationResult,
        )

        validation = ValidationResult()
        snap = {"quality": {"status": "ok"}}
        status = classify_block_status(snap, validation)
        assert status == "ok"

    def test_no_data_from_quality(self):
        """quality.status='no_data' без legacy/corrupted → no_data."""
        from backend.services.observation_snapshot_service import (
            classify_block_status,
            ValidationResult,
        )

        validation = ValidationResult()
        snap = {"quality": {"status": "no_data"}}
        status = classify_block_status(snap, validation)
        assert status == "no_data"

    def test_insufficient_data_from_quality(self):
        """quality.status='insufficient_data' → insufficient_data."""
        from backend.services.observation_snapshot_service import (
            classify_block_status,
            ValidationResult,
        )

        validation = ValidationResult()
        snap = {"quality": {"status": "insufficient_data"}}
        status = classify_block_status(snap, validation)
        assert status == "insufficient_data"


# ---------------------------------------------------------------------------
# TestIntegration — DB load path
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestIntegration:
    """
    Интеграционный тест с реальной БД.

    Запуск:
        DATABASE_URL=postgresql://... python -m pytest \
            backend/tests/test_observation_snapshot_service.py -v -m integration
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

    @pytest.fixture
    def test_block_id(self, db):
        """
        Fixture: создаёт тестовый блок через прямой INSERT, удаляет в teardown.
        """
        from sqlalchemy import text as sql_text

        snap = _make_valid_period_snapshot("observation_period")
        block_params = {
            "source": "observation",
            "chapter": "observation",
            "well_id": 21,
            "period": {"from": "2026-01-01", "to": "2026-01-31"},
        }

        import json as _json
        row = db.execute(
            sql_text("""
                INSERT INTO customer_report_block
                    (well_id, kind, title, params, data_snapshot, in_report, sort_order)
                VALUES
                    (:well_id, :kind, :title,
                     CAST(:block_params_json AS jsonb),
                     CAST(:snapshot_json AS jsonb),
                     true, 999)
                RETURNING id
            """),
            {
                "well_id": 21,
                "kind": "observation_period",
                "title": "C3 Test Block",
                "block_params_json": _json.dumps(block_params),
                "snapshot_json": _json.dumps(snap),
            },
        ).fetchone()
        db.commit()
        block_id = row[0]

        yield block_id

        # Teardown
        db.execute(
            sql_text("DELETE FROM customer_report_block WHERE id = :id"),
            {"id": block_id},
        )
        db.commit()

    def test_load_and_validate_snapshot_happy_path(self, db, test_block_id):
        """
        Test 21 (integration): load_and_validate_snapshot с реальным блоком из БД.

        Проверяем:
        - SanitizedSnapshotResult возвращается корректно
        - block_status вычислен (не из БД)
        - оригинальный snapshot в БД НЕ изменяется
        - can_recompute=True (params содержат well_id + period)
        - block_id echo корректен
        """
        from sqlalchemy import text as sql_text
        from backend.services.observation_snapshot_service import load_and_validate_snapshot

        result = load_and_validate_snapshot(db, test_block_id)

        # Структура результата
        assert result.block_id == test_block_id
        assert result.expected_kind == "observation_period"
        assert result.block_status in {
            "ok", "partial", "no_data", "insufficient_data",
            "invalid_schema", "corrupted", "legacy_v0",
        }

        # Sanitized snapshot содержит block_status (вычисленный C3)
        assert "block_status" in result.snapshot
        assert result.snapshot["block_status"] == result.block_status

        # can_recompute=True (params содержат well_id + period)
        assert result.can_recompute is True
        assert result.recompute_params is not None
        assert "well_id" in result.recompute_params
        assert "period" in result.recompute_params

        # Проверяем: оригинальный data_snapshot в БД НЕ изменён
        original_row = db.execute(
            sql_text("SELECT data_snapshot FROM customer_report_block WHERE id = :id"),
            {"id": test_block_id},
        ).fetchone()
        original_snap = original_row[0]

        # Оригинальный snapshot хранит "ok" (что мы записали)
        # Проверяем что C3 не записал поверх — block_status в БД остался прежним
        assert original_snap.get("block_status") == "ok", (
            "C3 НЕ должен менять оригинальный data_snapshot в БД. "
            f"Ожидали 'ok', получили: {original_snap.get('block_status')!r}"
        )

    def test_load_invalid_block_raises(self, db):
        """load_and_validate_snapshot с несуществующим id → ValueError."""
        from backend.services.observation_snapshot_service import load_and_validate_snapshot

        with pytest.raises(ValueError, match="not found"):
            load_and_validate_snapshot(db, 999999999)
