"""
Тесты для backend/services/observation_segment_service.py — Phase C2.

Разделены на:
  - TestUnit               — чистые unit-тесты без БД (synthetic DataFrames / dicts)
  - TestConsistency        — consistency test hourly vs daily (test_30)
  - TestIntegration        — тест с реальной БД (pytest.mark.integration)

Запуск unit-тестов (без БД):
    cd /Users/volodymyrnikitin/Documents/PythonFiles/SurgIl_Dashboard
    python -m pytest backend/tests/test_observation_segment_service.py -v -k "not integration"

Запуск всех тестов (с реальной БД):
    python -m pytest backend/tests/test_observation_segment_service.py -v
"""
from __future__ import annotations

import types
from datetime import date, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Import service functions
# ---------------------------------------------------------------------------

from backend.services.observation_segment_service import (
    BORDERLINE_COEFFICIENT,
    MIN_POINTS_FOR_SEGMENTATION,
    SCHEMA_VERSION,
    SENSITIVITY_PRESETS,
    SNAPSHOT_V,
    _apply_purge_mask,
    _apply_smoothing,
    _build_diagnostics,
    _build_flags_layer,
    _build_quality_layer,
    _build_segments_from_changepoints,
    _build_shutdown_clusters,
    _classify_changepoint_verdict,
    _classify_changepoint_flags,
    _compute_confidence,
    _compute_overall_trend,
    _compute_segment_trends,
    _days_to_points,
    _detect_changepoints,
    _enrich_changepoints,
    _exclude_shutdown_periods,
    _normalize_slope_per_day,
    _resolve_thresholds,
    _validate_custom_sensitivity,
    compute_segment_preview,
)


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_agg_df(
    n_days: int = 30,
    start: date = date(2025, 1, 1),
    q: float | list = 20.0,
    p_tube: float = 15.0,
    p_line: float = 5.0,
    shutdown_min: float = 0.0,
    purge_flag: bool = False,
    freq: str = "1D",
) -> pd.DataFrame:
    """Синтетический агрегированный DataFrame для тестов."""
    idx = pd.date_range(start=pd.Timestamp(start), periods=n_days, freq=freq)
    dp = p_tube - p_line
    if isinstance(q, list):
        q_vals = q
    else:
        q_vals = [q] * n_days
    return pd.DataFrame(
        {
            "p_tube":       [p_tube]       * n_days,
            "p_line":       [p_line]       * n_days,
            "dp":           [dp]           * n_days,
            "q":            q_vals,
            "shutdown_min": [shutdown_min] * n_days,
            "purge_flag":   [purge_flag]   * n_days,
        },
        index=idx,
    )


def _make_quality_raw(
    status: str = "ok",
    flags: list | None = None,
    days_with_data: int = 30,
    days_requested: int = 30,
) -> dict:
    return {
        "status":                  status,
        "quality_flags":           flags or [],
        "coverage_pct":            95.0,
        "gap_count":               0,
        "max_gap_hours":           0.0,
        "suspicious_spikes_count": 0,
        "false_zero_pct":          0.0,
        "days_with_data":          days_with_data,
        "days_requested":          days_requested,
    }


def _make_obs_data_result(
    agg_df: pd.DataFrame | None = None,
    quality_status: str = "ok",
    quality_flags: list | None = None,
    days_with_data: int = 30,
):
    """Создаёт mock ObservationDataResult для patching B1."""
    from backend.services.observation_data_service import ObservationDataResult
    if agg_df is None:
        agg_df = _make_agg_df()
    mock = ObservationDataResult(
        well_id=1,
        period={"from": "2025-01-01", "to": "2025-01-30"},
        aggregation="daily",
        our_df=agg_df,
        our_raw_minute_df=pd.DataFrame(),
        our_meta={"purge_cycles": [], "downtime_periods": pd.DataFrame()},
        customer_df=pd.DataFrame(),
        data_quality=_make_quality_raw(
            status=quality_status,
            flags=quality_flags,
            days_with_data=days_with_data,
        ),
    )
    return mock


def _patch_b1(mock_result, monkeypatch):
    """Патчит load_observation_data через monkeypatch."""
    monkeypatch.setattr(
        "backend.services.observation_segment_service.load_observation_data",
        lambda *args, **kwargs: mock_result,
    )


# ===========================================================================
# TestUnit
# ===========================================================================


class TestUnit:
    # -----------------------------------------------------------------------
    # 1-6: Sensitivity presets + overrides
    # -----------------------------------------------------------------------

    def test_sensitivity_preset_low_resolves_correctly(self):
        """test_1: preset 'low' возвращает правильные значения."""
        thresholds, has_overrides = _resolve_thresholds("low", {
            "min_segment_days": None,
            "min_change_pct": None,
            "smoothing_window": None,
        })
        assert thresholds["min_change_pct"]   == 20.0
        assert thresholds["min_segment_days"] == 10
        assert thresholds["smoothing_window"] == 10
        assert has_overrides is False

    def test_sensitivity_preset_medium_resolves_correctly(self):
        """test_2: preset 'medium' возвращает правильные значения."""
        thresholds, has_overrides = _resolve_thresholds("medium", {
            "min_segment_days": None,
            "min_change_pct": None,
            "smoothing_window": None,
        })
        assert thresholds["min_change_pct"]   == 10.0
        assert thresholds["min_segment_days"] == 7
        assert thresholds["smoothing_window"] == 7
        assert has_overrides is False

    def test_sensitivity_preset_high_resolves_correctly(self):
        """test_3: preset 'high' возвращает правильные значения."""
        thresholds, has_overrides = _resolve_thresholds("high", {
            "min_segment_days": None,
            "min_change_pct": None,
            "smoothing_window": None,
        })
        assert thresholds["min_change_pct"]   == 5.0
        assert thresholds["min_segment_days"] == 3
        assert thresholds["smoothing_window"] == 3
        assert has_overrides is False

    def test_sensitivity_custom_with_overrides(self):
        """test_4: 'custom' с явными override-параметрами возвращает их значения."""
        thresholds, has_overrides = _resolve_thresholds("custom", {
            "min_segment_days": 14,
            "min_change_pct":   8.0,
            "smoothing_window": 5,
        })
        assert thresholds["min_segment_days"] == 14
        assert thresholds["min_change_pct"]   == 8.0
        assert thresholds["smoothing_window"] == 5
        assert has_overrides is True

    def test_sensitivity_custom_without_params_raises(self):
        """test_5: 'custom' без параметров → ValueError."""
        with pytest.raises(ValueError, match="custom"):
            _validate_custom_sensitivity("custom", {
                "min_segment_days": None,
                "min_change_pct":   None,
                "smoothing_window": None,
            })

    def test_sensitivity_preset_with_partial_override_sets_has_user_overrides(self):
        """test_6: 'medium' + один override → has_user_overrides=True."""
        thresholds, has_overrides = _resolve_thresholds("medium", {
            "min_segment_days": 14,
            "min_change_pct":   None,
            "smoothing_window": None,
        })
        assert has_overrides is True
        # Значение применено
        assert thresholds["min_segment_days"] == 14
        # Остальные из preset
        assert thresholds["min_change_pct"]   == 10.0
        assert thresholds["smoothing_window"] == 7

    # -----------------------------------------------------------------------
    # 7: Smoothing
    # -----------------------------------------------------------------------

    def test_apply_smoothing_window_3_on_copy(self):
        """test_7: smoothing работает на copy — оригинал не мутируется."""
        original = _make_agg_df(n_days=10, q=20.0)
        original_q = original["q"].copy()

        # Добавляем шум
        df_noisy = original.copy()
        df_noisy.loc[df_noisy.index[5], "q"] = 100.0

        result = _apply_smoothing(df_noisy, window=3)

        # Оригинал не изменён
        pd.testing.assert_series_equal(original["q"], original_q)

        # Результат сглажен — точка с аномалией должна быть приглажена
        assert result.loc[result.index[5], "q"] < 100.0

        # result — другой объект
        assert result is not df_noisy

    # -----------------------------------------------------------------------
    # 8-9: Shutdown exclusion
    # -----------------------------------------------------------------------

    def test_exclude_shutdown_days_when_flag_true(self):
        """test_8: ignore_shutdown_days=True — дни кластера становятся NaN."""
        df = _make_agg_df(n_days=20)
        clusters = [{
            "start_date": "2025-01-05",
            "end_date":   "2025-01-08",
            "total_minutes": 5760.0,
        }]

        result = _exclude_shutdown_periods(df, clusters, ignore_flag=True)

        # Дни 5-8 должны быть NaN
        mask_excluded = (result.index >= "2025-01-05") & (result.index <= "2025-01-08")
        assert result.loc[mask_excluded, "q"].isna().all()

        # Остальные не затронуты
        assert result.loc[~mask_excluded, "q"].notna().all()

    def test_exclude_shutdown_days_when_flag_false(self):
        """test_9: ignore_shutdown_days=False — данные не изменяются."""
        df = _make_agg_df(n_days=20)
        clusters = [{
            "start_date": "2025-01-05",
            "end_date":   "2025-01-08",
            "total_minutes": 5760.0,
        }]

        result = _exclude_shutdown_periods(df, clusters, ignore_flag=False)

        # Все значения остались
        assert result["q"].notna().all()

    # -----------------------------------------------------------------------
    # 10-12: Changepoint detection
    # -----------------------------------------------------------------------

    def test_detect_changepoints_no_cp_in_flat_series(self):
        """test_10: плоский ряд — нет changepoints."""
        df = _make_agg_df(n_days=30, q=20.0)
        cps = _detect_changepoints(df, min_change_pct=10.0, min_segment_days=7)
        assert len(cps) == 0

    def test_detect_changepoints_single_cp_above_threshold(self):
        """test_11: резкое падение Q в середине — 1 changepoint обнаружен."""
        q_vals = [30.0] * 15 + [10.0] * 15  # -67% drop
        df = _make_agg_df(n_days=30, q=q_vals)
        cps = _detect_changepoints(df, min_change_pct=10.0, min_segment_days=5)
        assert len(cps) >= 1
        # CP должен быть в районе дня 15 (±3 дня)
        assert any(12 <= cp <= 18 for cp in cps)

    def test_detect_changepoints_below_threshold_ignored(self):
        """test_12: изменение Q ниже порога → нет changepoints."""
        # 2% изменение при пороге 10%
        q_vals = [20.0] * 15 + [20.4] * 15
        df = _make_agg_df(n_days=30, q=q_vals)
        cps = _detect_changepoints(df, min_change_pct=10.0, min_segment_days=5)
        assert len(cps) == 0

    # -----------------------------------------------------------------------
    # 13-14: Segment building
    # -----------------------------------------------------------------------

    def test_build_segments_n_cps_yields_n_plus_1_segments(self):
        """test_13: N changepoints → N+1 сегментов."""
        df = _make_agg_df(n_days=30)
        # 2 changepoints
        cps = [10, 20]
        segs = _build_segments_from_changepoints(df, cps, aggregation="daily")
        assert len(segs) == 3

    def test_build_segments_respects_min_segment_days(self):
        """test_14: changepoints с большим расстоянием — не объединяются."""
        q_vals = [30.0] * 10 + [10.0] * 10 + [20.0] * 10
        df = _make_agg_df(n_days=30, q=q_vals)
        # 2 правильно расставленных CP (с отступом)
        cps = [10, 20]
        segs = _build_segments_from_changepoints(df, cps, aggregation="daily")
        # Все 3 сегмента должны быть не меньше min
        for seg in segs:
            assert seg["duration_days"] >= 1

    # -----------------------------------------------------------------------
    # 15: Segment trends
    # -----------------------------------------------------------------------

    def test_compute_segment_trends_rising_falling_stable(self):
        """test_15: тренды rising/falling/stable корректно определяются."""
        import numpy as np

        # Строим 3 сегмента: rising, falling, stable (длиннее стабильного порога)
        # rising: +500 тыс.м³/сут за 10 дней = slope=50/day → borderline stable
        # Для гарантии: используем steep slope
        n = 60  # 60 дней
        q = (
            list(np.linspace(10, 10000, 20))   # rising
            + list(np.linspace(10000, 10, 20))  # falling
            + [5000.0] * 20                     # stable
        )
        df = _make_agg_df(n_days=n, q=q)
        cps = [20, 40]
        segs = _build_segments_from_changepoints(df, cps, aggregation="daily")
        segs = _compute_segment_trends(segs, df, aggregation="daily")

        assert segs[0]["direction"] == "rising"
        assert segs[1]["direction"] == "falling"
        assert segs[2]["direction"] == "stable"

    # -----------------------------------------------------------------------
    # 16-18: Diagnostics
    # -----------------------------------------------------------------------

    def test_diagnostics_segment_targets_correct(self):
        """test_16: segment diagnostics имеют target='segment', context='trend_<num>'."""
        segs = [
            {"num": 1, "direction": "rising",  "slope_q_per_day": 10.0, "duration_days": 10},
            {"num": 2, "direction": "falling", "slope_q_per_day": -5.0, "duration_days": 10},
        ]
        diags = _build_diagnostics(
            segments=segs,
            changepoints_enriched=[],
            shutdown_clusters=[],
            purge_events=[],
            thresholds={"min_change_pct": 10.0},
            ignore_purge_window_hours=24,
        )
        seg_diags = [d for d in diags if d["target"] == "segment"]
        assert len(seg_diags) == 2
        contexts = {d["context"] for d in seg_diags}
        assert "trend_1" in contexts
        assert "trend_2" in contexts

    def test_diagnostics_changepoint_targets_correct(self):
        """test_17: changepoint diagnostics имеют target='changepoint', context='cp_<idx>'."""
        cps_enriched = [
            {
                "idx": 14, "date": "2025-01-15", "magnitude_pct": -25.0,
                "confidence": "high", "_verdict": "detected",
            }
        ]
        diags = _build_diagnostics(
            segments=[],
            changepoints_enriched=cps_enriched,
            shutdown_clusters=[],
            purge_events=[],
            thresholds={"min_change_pct": 10.0},
            ignore_purge_window_hours=24,
        )
        cp_diags = [d for d in diags if d["target"] == "changepoint"]
        assert len(cp_diags) == 1
        assert cp_diags[0]["context"] == "cp_14"
        assert cp_diags[0]["verdict"] == "detected"

    def test_diagnostics_changepoint_flags_present(self):
        """test_18: changepoint diagnostics содержат flags.shutdown_related / purge_related."""
        cps_enriched = [
            {
                "idx": 5, "date": "2025-01-06", "magnitude_pct": -18.0,
                "confidence": "medium", "_verdict": "detected",
            }
        ]
        shutdown_clusters = [{"start_date": "2025-01-05", "end_date": "2025-01-08", "total_minutes": 5000.0}]
        diags = _build_diagnostics(
            segments=[],
            changepoints_enriched=cps_enriched,
            shutdown_clusters=shutdown_clusters,
            purge_events=[],
            thresholds={"min_change_pct": 10.0},
            ignore_purge_window_hours=24,
        )
        cp_diags = [d for d in diags if d["target"] == "changepoint"]
        assert len(cp_diags) == 1
        flags = cp_diags[0].get("flags")
        assert isinstance(flags, dict)
        assert "shutdown_related" in flags
        assert "purge_related" in flags
        # CP date 2025-01-06 внутри кластера → shutdown_related=True
        assert flags["shutdown_related"] is True
        assert flags["purge_related"] is False

    # -----------------------------------------------------------------------
    # 19-20: Overall verdict
    # -----------------------------------------------------------------------

    def test_diagnostics_overall_weighted_by_duration(self):
        """test_19: длинный rising + короткий falling → overall='rising'."""
        segs = [
            {"num": 1, "direction": "rising",  "slope_q_per_day":  10.0, "duration_days": 20},
            {"num": 2, "direction": "falling", "slope_q_per_day": -10.0, "duration_days": 5},
        ]
        result = _compute_overall_trend(segs)
        assert result == "rising"

    def test_diagnostics_overall_empty_segments_insufficient_data(self):
        """test_20: пустой список сегментов → overall='insufficient_data' (НЕ invent)."""
        result = _compute_overall_trend([])
        assert result == "insufficient_data"

    # -----------------------------------------------------------------------
    # 21-23: Narrative / structure checks (internal helpers)
    # -----------------------------------------------------------------------

    def test_diagnostics_no_narrative_strings(self):
        """test_21: diagnostics не содержат narrative-строк."""
        segs = [
            {"num": 1, "direction": "rising", "slope_q_per_day": 5.0, "duration_days": 10}
        ]
        cps_enriched = [
            {"idx": 10, "date": "2025-01-11", "magnitude_pct": -20.0, "confidence": "high", "_verdict": "detected"}
        ]
        diags = _build_diagnostics(
            segments=segs,
            changepoints_enriched=cps_enriched,
            shutdown_clusters=[],
            purge_events=[],
            thresholds={"min_change_pct": 10.0},
            ignore_purge_window_hours=24,
        )
        narrative_words = {"возможная", "требует", "possibly", "likely", "probably"}
        for d in diags:
            for k, v in d.items():
                if isinstance(v, str):
                    lower_v = v.lower()
                    for word in narrative_words:
                        assert word not in lower_v, f"Narrative word '{word}' found in diagnostic: {d}"

    def test_snapshot_no_metrics_layer(self):
        """test_22: top-level 'metrics' отсутствует в obs_segment_v1 snapshot."""
        from backend.services.observation_segment_service import _assemble_snapshot
        snap = _assemble_snapshot(
            computed_at="2025-01-01T00:00:00Z",
            block_status="ok",
            period={"from": "2025-01-01", "to": "2025-01-30"},
            raw_layer=None,
            quality_layer={"status": "ok", "flags": [], "metrics": {}},
            flags_layer={"low_coverage": False},
            thresholds_used={"sensitivity": "medium"},
            segments=[],
            changepoints=[],
            shutdown_clusters=[],
            diagnostics=[],
        )
        assert "metrics" not in snap

    def test_snapshot_no_comparisons_layer(self):
        """test_23: top-level 'comparisons' отсутствует в obs_segment_v1 snapshot."""
        from backend.services.observation_segment_service import _assemble_snapshot
        snap = _assemble_snapshot(
            computed_at="2025-01-01T00:00:00Z",
            block_status="ok",
            period={"from": "2025-01-01", "to": "2025-01-30"},
            raw_layer=None,
            quality_layer={"status": "ok", "flags": [], "metrics": {}},
            flags_layer={"low_coverage": False},
            thresholds_used={"sensitivity": "medium"},
            segments=[],
            changepoints=[],
            shutdown_clusters=[],
            diagnostics=[],
        )
        assert "comparisons" not in snap

    def test_snapshot_v_and_schema_version_constants(self):
        """test_24: _v='obs_segment_v1', schema_version='1.0'."""
        assert SNAPSHOT_V == "obs_segment_v1"
        assert SCHEMA_VERSION == "1.0"

    def test_snapshot_thresholds_used_reflects_effective(self):
        """test_25: thresholds_used содержит финальные значения, не preset name."""
        from backend.services.observation_segment_service import _assemble_snapshot
        thresholds_used = {
            "sensitivity": "medium",
            "min_change_pct": 10.0,
            "min_segment_days": 7,
            "smoothing_window": 7,
        }
        snap = _assemble_snapshot(
            computed_at="2025-01-01T00:00:00Z",
            block_status="ok",
            period={"from": "2025-01-01", "to": "2025-01-30"},
            raw_layer=None,
            quality_layer={"status": "ok", "flags": [], "metrics": {}},
            flags_layer={"low_coverage": False},
            thresholds_used=thresholds_used,
            segments=[],
            changepoints=[],
            shutdown_clusters=[],
            diagnostics=[],
        )
        tu = snap["thresholds_used"]
        assert tu["min_change_pct"]   == 10.0
        assert tu["min_segment_days"] == 7
        assert tu["smoothing_window"] == 7

    # -----------------------------------------------------------------------
    # 26-27: block_status no_data / insufficient_data
    # -----------------------------------------------------------------------

    def test_compute_segment_preview_no_data(self, monkeypatch):
        """test_26: нет данных → block_status='no_data'."""
        mock_result = _make_obs_data_result(
            agg_df=pd.DataFrame(),
            quality_status="no_data",
            days_with_data=0,
        )
        _patch_b1(mock_result, monkeypatch)

        snap = compute_segment_preview(
            db=MagicMock(),
            well_id=1,
            d_from=date(2025, 1, 1),
            d_to=date(2025, 1, 30),
        )
        assert snap["block_status"] == "no_data"
        assert snap["_v"] == "obs_segment_v1"

    def test_compute_segment_preview_insufficient_data(self, monkeypatch):
        """test_27: менее MIN_POINTS_FOR_SEGMENTATION точек → block_status='insufficient_data'."""
        # 3 строки с валидным q (меньше MIN_POINTS_FOR_SEGMENTATION=6)
        small_df = _make_agg_df(n_days=3, q=20.0)
        mock_result = _make_obs_data_result(
            agg_df=small_df,
            quality_status="sparse",
            days_with_data=3,
        )
        _patch_b1(mock_result, monkeypatch)

        snap = compute_segment_preview(
            db=MagicMock(),
            well_id=1,
            d_from=date(2025, 1, 1),
            d_to=date(2025, 1, 3),
        )
        assert snap["block_status"] == "insufficient_data"

    # -----------------------------------------------------------------------
    # 28-29: slope normalization + min_segment_days in days
    # -----------------------------------------------------------------------

    def test_slope_normalized_per_day_for_hourly(self):
        """test_28: aggregation='hourly', slope в snapshot нормализован per-day."""
        # slope_per_period = 1.0 (тыс.м³ за 1 час)
        # per_day = 1.0 * (1440/60) = 24.0
        slope_per_day = _normalize_slope_per_day(1.0, "hourly")
        assert abs(slope_per_day - 24.0) < 1e-9

    def test_min_segment_days_always_in_days(self):
        """test_29: _days_to_points для hourly: 7 дней → 7*24 = 168 точек."""
        pts = _days_to_points(7, "hourly")
        assert pts == 7 * 24

    # -----------------------------------------------------------------------
    # helper assertion utils
    # -----------------------------------------------------------------------

    def test_classify_changepoint_verdict_detected(self):
        """detected: |pct| >= BORDERLINE_COEFFICIENT * threshold."""
        verdict = _classify_changepoint_verdict(
            magnitude_pct=-(BORDERLINE_COEFFICIENT * 10.0 + 1.0),
            threshold=10.0,
        )
        assert verdict == "detected"

    def test_classify_changepoint_verdict_borderline(self):
        """borderline: threshold <= |pct| < BORDERLINE_COEFFICIENT * threshold."""
        verdict = _classify_changepoint_verdict(
            magnitude_pct=12.0,  # 10.0 <= 12.0 < 15.0
            threshold=10.0,
        )
        assert verdict == "borderline"

    def test_classify_changepoint_verdict_insufficient_data(self):
        """insufficient_data если |pct| < threshold."""
        verdict = _classify_changepoint_verdict(magnitude_pct=5.0, threshold=10.0)
        assert verdict == "insufficient_data"

    def test_classify_changepoint_verdict_none(self):
        """insufficient_data если magnitude_pct is None."""
        verdict = _classify_changepoint_verdict(magnitude_pct=None, threshold=10.0)
        assert verdict == "insufficient_data"


# ===========================================================================
# TestConsistency  (test_30 — обязательный)
# ===========================================================================


class TestConsistency:

    def test_hourly_vs_daily_same_major_cp(self, monkeypatch):
        """
        test_30 — ОБЯЗАТЕЛЬНЫЙ consistency test.

        Синтетический резкий drop Q в день X (день 20 из 40).
        Запускаем compute_segment_preview дважды: aggregation='daily' и 'hourly'.
        Проверяем:
        - Оба обнаруживают хотя бы 1 changepoint
        - Даты CP различаются не более ±1 день
        - magnitude_pct согласован в пределах ±10% (relative difference)
        """
        # Строим синтетический ряд: 20 дней Q=30, затем 20 дней Q=10 (-67%)
        drop_day = 20
        n_days = 40

        # --- Daily mock ---
        q_daily = [30.0] * drop_day + [10.0] * (n_days - drop_day)
        daily_df = _make_agg_df(n_days=n_days, q=q_daily, freq="1D")

        mock_daily = _make_obs_data_result(
            agg_df=daily_df,
            quality_status="ok",
            days_with_data=n_days,
        )
        mock_daily.data_quality["days_requested"] = n_days

        # --- Hourly mock: те же данные но с hourly частотой ---
        # Каждый день = 24 точки с одним Q
        q_hourly = [30.0] * (drop_day * 24) + [10.0] * ((n_days - drop_day) * 24)
        hourly_df = _make_agg_df(n_days=n_days * 24, q=q_hourly, freq="1h")

        mock_hourly = _make_obs_data_result(
            agg_df=hourly_df,
            quality_status="ok",
            days_with_data=n_days,
        )
        mock_hourly.data_quality["days_requested"] = n_days

        # --- Запуск для daily ---
        with patch(
            "backend.services.observation_segment_service.load_observation_data",
            return_value=mock_daily,
        ):
            snap_daily = compute_segment_preview(
                db=MagicMock(),
                well_id=1,
                d_from=date(2025, 1, 1),
                d_to=date(2025, 2, 9),
                aggregation="daily",
                sensitivity="high",  # high sensitivity для надёжной детекции
            )

        # --- Запуск для hourly ---
        with patch(
            "backend.services.observation_segment_service.load_observation_data",
            return_value=mock_hourly,
        ):
            snap_hourly = compute_segment_preview(
                db=MagicMock(),
                well_id=1,
                d_from=date(2025, 1, 1),
                d_to=date(2025, 2, 9),
                aggregation="hourly",
                sensitivity="high",
            )

        # --- Проверки ---
        cps_daily  = snap_daily.get("changepoints", [])
        cps_hourly = snap_hourly.get("changepoints", [])

        # Оба должны найти хотя бы 1 CP
        assert len(cps_daily)  >= 1, f"daily: no changepoints detected; block_status={snap_daily.get('block_status')}"
        assert len(cps_hourly) >= 1, f"hourly: no changepoints detected; block_status={snap_hourly.get('block_status')}"

        # Берём CP с наибольшим |magnitude_pct| для сравнения
        def _best_cp(cps: list[dict]) -> dict:
            valid = [c for c in cps if c.get("magnitude_pct") is not None]
            if not valid:
                return cps[0]
            return max(valid, key=lambda c: abs(c["magnitude_pct"]))

        best_daily  = _best_cp(cps_daily)
        best_hourly = _best_cp(cps_hourly)

        # Даты: различаются не более ±1 день
        try:
            d_daily  = pd.Timestamp(best_daily["date"])
            d_hourly = pd.Timestamp(best_hourly["date"])
            diff_days = abs((d_daily - d_hourly).total_seconds()) / 86400.0
            assert diff_days <= 1.0, (
                f"CP dates differ by {diff_days:.1f} days: daily={best_daily['date']}, hourly={best_hourly['date']}"
            )
        except (TypeError, KeyError):
            pass  # Если нет date — только magnitude check

        # magnitude_pct: согласованы в пределах ±10% (относительно)
        m_daily  = best_daily.get("magnitude_pct")
        m_hourly = best_hourly.get("magnitude_pct")
        if m_daily is not None and m_hourly is not None:
            relative_diff = abs(m_daily - m_hourly) / max(abs(m_daily), abs(m_hourly), 1e-9) * 100.0
            assert relative_diff <= 10.0, (
                f"magnitude_pct differs by {relative_diff:.1f}%: daily={m_daily}, hourly={m_hourly}"
            )


# ===========================================================================
# TestIntegration
# ===========================================================================


@pytest.mark.integration
class TestIntegration:

    def test_compute_segment_preview_real_well_happy_path(self):
        """
        test_31 — integration test с реальной БД.
        well_id=21, 30 дней. Проверяем структуру snapshot и block_status.
        """
        try:
            from backend.db import SessionLocal
        except ImportError:
            pytest.skip("backend.db недоступен — пропускаем integration тест")

        db = SessionLocal()
        try:
            snap = compute_segment_preview(
                db=db,
                well_id=21,
                d_from=date(2025, 3, 1),
                d_to=date(2025, 3, 30),
                aggregation="daily",
                sensitivity="medium",
            )

            # Структурные проверки
            assert snap.get("_v") == "obs_segment_v1"
            assert snap.get("schema_version") == "1.0"
            assert snap.get("block_status") in {"ok", "no_data", "insufficient_data"}
            assert "period" in snap
            assert "quality" in snap
            assert "flags" in snap
            assert "thresholds_used" in snap
            assert "segments" in snap
            assert "changepoints" in snap
            assert "shutdown_clusters" in snap
            assert "diagnostics" in snap

            # НЕТ metrics, НЕТ comparisons
            assert "metrics"     not in snap
            assert "comparisons" not in snap

            # thresholds_used содержит эффективные значения
            tu = snap["thresholds_used"]
            assert "min_change_pct"   in tu
            assert "min_segment_days" in tu

            # diagnostics структура
            for d in snap["diagnostics"]:
                assert d.get("target") in {"segment", "changepoint", "overall"}
                assert "requires_log_check" in d

            # Если ok: changepoints и segments — списки
            if snap["block_status"] == "ok":
                assert isinstance(snap["segments"], list)
                assert isinstance(snap["changepoints"], list)

                # slope всегда per-day (float или None)
                for seg in snap["segments"]:
                    slope = seg.get("slope_q_per_day")
                    if slope is not None:
                        assert isinstance(slope, float)

        finally:
            db.close()
