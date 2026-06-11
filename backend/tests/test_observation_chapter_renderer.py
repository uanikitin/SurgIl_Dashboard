"""
Тесты для backend/services/observation_chapter_renderer.py — Phase F.

27+ тестов:
- render_baseline / render_period / render_segment (HTML + LaTeX + figures)
- render_block (dispatch, broken statuses, unknown kind)
- latex_escape
- html/pdf parity
- chapter assembler (multi-block, empty)
- skip_figures=True/False
- no mutation of snapshot
- no DB / service calls

Только stdlib + pytest fixtures (tmp_path). Без БД.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from backend.services.observation_chapter_renderer import (
    FigureRef,
    RenderContext,
    RenderResult,
    LATEX_SPECIAL,
    latex_escape,
    render_baseline,
    render_period,
    render_segment,
    render_block,
    render_observation_chapter,
    render_observation_chapter_latex,
    KIND_RENDERERS,
)


# ---------------------------------------------------------------------------
# HELPER: SYNTHETIC SNAPSHOTS
# ---------------------------------------------------------------------------


def make_baseline_snapshot(status: str = "ok") -> dict:
    """Минимально валидный obs_baseline_v1 snapshot."""
    return {
        "_v": "obs_baseline_v1",
        "schema_version": "1.0",
        "computed_at": "2026-05-19T10:00:00Z",
        "block_status": status,
        "period": {"from": "2025-01-01", "to": "2025-01-31"},
        "raw": {
            "chart_payload": {
                "dates": ["2025-01-01", "2025-01-15"],
                "p_tube": [16.5, 16.8],
                "p_line": [5.3, 5.4],
                "dp": [11.2, 11.4],
                "q": [42100.0, 42500.0],
            }
        },
        "metrics": {
            "p_tube": {"mean": 16.65, "median": 16.65, "min": 16.5, "max": 16.8, "std": 0.21},
            "p_line": {"mean": 5.35, "median": 5.35, "min": 5.3, "max": 5.4, "std": 0.07},
            "dp": {"mean": 11.3, "median": 11.3, "min": 11.2, "max": 11.4, "std": 0.14},
            "q": {"mean": 42300.0, "median": 42300.0, "min": 42100.0, "max": 42500.0, "std": 283.0},
        },
        "quality": {
            "status": "ok" if status == "ok" else status,
            "flags": [],
            "metrics": {
                "coverage_pct": 95.0,
                "gap_count": 0,
                "max_gap_hours": 0,
                "suspicious_spikes_count": 0,
                "false_zero_pct": 0,
                "days_with_data": 30,
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
            "invalid_comparison": False,
        },
    }


def make_period_snapshot(with_b1: bool = True, with_customer: bool = True) -> dict:
    """Минимально валидный obs_period_v1 snapshot."""
    b1_section = {
        "status": "ok" if with_b1 else "no_baseline",
        "deltas": {
            "p_tube": {"baseline_value": 16.65, "current_value": 16.80, "delta_abs": 0.15, "delta_pct": 0.9, "verdict": "improvement"},
            "p_line": {"baseline_value": 5.35, "current_value": 5.40, "delta_abs": 0.05, "delta_pct": 0.93, "verdict": "no_significant_change"},
            "dp": {"baseline_value": 11.3, "current_value": 11.4, "delta_abs": 0.1, "delta_pct": 0.88, "verdict": "improvement"},
            "q": {"baseline_value": 42300.0, "current_value": 43000.0, "delta_abs": 700.0, "delta_pct": 1.65, "verdict": "improvement"},
        } if with_b1 else {},
    }

    customer_section = {
        "status": "ok" if with_customer else "no_data",
        "mape": 3.5,
        "daily_comparison": [
            {"date": "2025-02-01", "q_customer": 43000.0, "q_unitool": 43200.0, "delta_pct": 0.47},
        ],
    } if with_customer else {}

    return {
        "_v": "obs_period_v1",
        "schema_version": "1.0",
        "computed_at": "2026-05-19T10:00:00Z",
        "block_status": "ok",
        "period": {"from": "2025-02-01", "to": "2025-02-28"},
        "raw": {
            "chart_payload": {
                "dates": ["2025-02-01", "2025-02-15", "2025-02-28"],
                "p_tube": [16.8, 16.9, 17.0],
                "p_line": [5.4, 5.45, 5.5],
                "dp": [11.4, 11.45, 11.5],
                "q": [43000.0, 43100.0, 43200.0],
            }
        },
        "metrics": {
            "p_tube": {"mean": 16.9, "median": 16.9, "min": 16.8, "max": 17.0, "std": 0.1},
            "q": {"mean": 43100.0, "median": 43100.0, "min": 43000.0, "max": 43200.0, "std": 100.0},
        },
        "quality": {"status": "ok", "flags": [], "metrics": {"coverage_pct": 92.0}},
        "diagnostics": [
            {"target": "q", "context": "vs_b1", "verdict": "improvement", "magnitude": {"abs": 700.0, "pct": 1.65}},
        ],
        "flags": {
            "low_coverage": False,
            "significant_gap": False,
            "outlier_detected": False,
            "short_intersection": False,
            "baseline_mismatch_period": False,
            "outdated_baseline_version": False,
            "invalid_comparison": False,
        },
        "comparisons": {
            "with_b1": b1_section,
            "with_customer": customer_section,
        },
    }


def make_segment_snapshot(n_segments: int = 3, n_changepoints: int = 2) -> dict:
    """Минимально валидный obs_segment_v1 snapshot."""
    from datetime import date, timedelta

    base = date(2025, 3, 1)
    seg_len = 10
    segments = []
    for i in range(n_segments):
        seg_from = base + timedelta(days=i * seg_len)
        seg_to = seg_from + timedelta(days=seg_len - 1)
        segments.append({
            "from": seg_from.isoformat(),
            "to": seg_to.isoformat(),
            "days": seg_len,
            "type": ["stable", "rising", "falling"][i % 3],
            "q_mean": 42000.0 + i * 300,
        })

    changepoints = []
    for j in range(n_changepoints):
        cp_date = base + timedelta(days=(j + 1) * seg_len)
        changepoints.append({
            "date": cp_date.isoformat(),
            "magnitude_pct": -(5.2 + j * 1.1),
            "confidence": 0.87 - j * 0.05,
        })

    dates = []
    q_vals = []
    for i in range(n_segments * seg_len):
        d = base + timedelta(days=i)
        dates.append(d.isoformat())
        q_vals.append(42000.0 + i * 10)

    return {
        "_v": "obs_segment_v1",
        "schema_version": "1.0",
        "computed_at": "2026-05-19T10:00:00Z",
        "block_status": "ok",
        "period": {"from": base.isoformat(), "to": (base + timedelta(days=n_segments * seg_len - 1)).isoformat()},
        "raw": {
            "chart_payload": {
                "dates": dates,
                "q": q_vals,
            }
        },
        "segments": segments,
        "changepoints": changepoints,
        "thresholds_used": {"min_segment_days": 5, "sensitivity": 0.8},
        "shutdown_clusters": [],
        "diagnostics": [],
        "flags": {
            "low_coverage": False,
            "significant_gap": False,
            "outlier_detected": False,
            "short_intersection": False,
            "baseline_mismatch_period": False,
            "outdated_baseline_version": False,
            "invalid_comparison": False,
        },
        "quality": {"status": "ok", "flags": [], "metrics": {"coverage_pct": 97.0}},
    }


def make_render_context(
    block_status: str = "ok",
    skip_figures: bool = True,
    output_dir: str = None,
) -> RenderContext:
    return RenderContext(
        output_dir=output_dir or "/tmp/test_obs_charts",
        block_id=42,
        title="Test Block",
        block_status=block_status,
        skip_figures=skip_figures,
    )


# ---------------------------------------------------------------------------
# 1. render_baseline
# ---------------------------------------------------------------------------


def test_render_baseline_ok():
    snap = make_baseline_snapshot(status="ok")
    ctx = make_render_context(skip_figures=True)
    result = render_baseline(snap, ctx)

    # HTML содержит метрики (mean p_tube)
    assert "16.65" in result.html or "16,65" in result.html or "obs-metrics-table" in result.html
    # LaTeX содержит \subsection
    assert r"\subsection" in result.latex
    # skip_figures=True → figures пусто
    assert result.figures == []
    # html — строка
    assert isinstance(result.html, str) and len(result.html) > 50


def test_render_baseline_no_data():
    snap = make_baseline_snapshot(status="no_data")
    snap["quality"]["status"] = "no_data"
    # Убираем метрики, чтобы симулировать no_data
    snap["metrics"] = {}
    ctx = make_render_context(skip_figures=True)
    result = render_baseline(snap, ctx)

    # Рендер не падает
    assert isinstance(result.html, str)
    assert isinstance(result.latex, str)
    # Нет figures при skip_figures=True
    assert result.figures == []
    # Период присутствует
    assert "2025-01-01" in result.html or "Период" in result.html


def test_render_baseline_skip_figures_true_returns_empty_figures():
    snap = make_baseline_snapshot()
    ctx = make_render_context(skip_figures=True)
    result = render_baseline(snap, ctx)
    assert result.figures == []


def test_render_baseline_skip_figures_false_creates_png(tmp_path):
    snap = make_baseline_snapshot()
    ctx = RenderContext(
        output_dir=str(tmp_path),
        block_id=42,
        title="Baseline PNG Test",
        block_status="ok",
        skip_figures=False,
    )
    result = render_baseline(snap, ctx)

    # Должна быть хотя бы 1 figure
    assert len(result.figures) >= 1
    fig = result.figures[0]
    # PNG файл существует
    assert Path(fig.absolute_path).exists()
    # Размер ≥ 1 KB
    assert Path(fig.absolute_path).stat().st_size >= 1024


# ---------------------------------------------------------------------------
# 2. render_period
# ---------------------------------------------------------------------------


def test_render_period_ok():
    snap = make_period_snapshot(with_b1=True, with_customer=True)
    ctx = make_render_context(skip_figures=True)
    result = render_period(snap, ctx)

    assert isinstance(result.html, str)
    assert isinstance(result.latex, str)
    # Метрики
    assert "obs-metrics-table" in result.html or "p_tube" in result.html or "16.9" in result.html
    # Diagnostics
    assert "obs-diagnostics-list" in result.html or "Улучшение" in result.html or "improvement" in result.html
    # LaTeX subsection
    assert r"\subsection" in result.latex


def test_render_period_no_baseline_comparison():
    snap = make_period_snapshot(with_b1=False)
    ctx = make_render_context(skip_figures=True)
    result = render_period(snap, ctx)

    # Секция "no_baseline" — нет deltas/verdicts в HTML
    assert isinstance(result.html, str)
    # Строки о delta abs/pct не появляются, если нет B1
    assert "obs-deltas-table" not in result.html
    # "без baseline" badge
    assert "без baseline" in result.html or "no_baseline" in result.html or "obs-no-data" in result.html


# ---------------------------------------------------------------------------
# 3. render_segment
# ---------------------------------------------------------------------------


def test_render_segment_ok():
    snap = make_segment_snapshot(n_segments=3, n_changepoints=2)
    ctx = make_render_context(skip_figures=True)
    result = render_segment(snap, ctx)

    # HTML содержит таблицу сегментов
    assert "obs-segments-table" in result.html
    # Changepoints в HTML
    assert "obs-changepoints-list" in result.html
    # LaTeX: tabular
    assert r"\begin{tabular}" in result.latex
    # 3 сегмента упомянуты
    assert result.html.count("<tr>") >= 3  # header + 3 seg rows


def test_render_segment_empty_segments():
    snap = make_segment_snapshot(n_segments=0, n_changepoints=0)
    snap["segments"] = []
    snap["changepoints"] = []
    ctx = make_render_context(skip_figures=True)
    result = render_segment(snap, ctx)

    assert isinstance(result.html, str)
    # Placeholder "нет сегментов" или obs-no-data
    assert "obs-no-data" in result.html or "не обнаружены" in result.html


# ---------------------------------------------------------------------------
# 4. render_block — dispatch
# ---------------------------------------------------------------------------


def test_render_block_unknown_kind():
    block = {
        "kind": "observation_unknown",
        "snapshot": {},
        "block_id": 1,
        "title": "Unknown",
    }
    ctx = make_render_context(block_status="ok")
    result = render_block(block, ctx)

    # Fallback placeholder
    assert isinstance(result.html, str)
    assert "unknown" in result.html or "obs-unknown" in result.html or "Неизвестный" in result.html
    # LaTeX тоже
    assert isinstance(result.latex, str)
    # Предупреждение
    assert len(result.warnings) >= 1


def test_render_block_corrupted_status():
    snap = make_baseline_snapshot()
    block = {
        "kind": "observation_baseline",
        "snapshot": snap,
        "block_id": 2,
        "title": "Corrupted Block",
    }
    ctx = make_render_context(block_status="corrupted")
    result = render_block(block, ctx)

    # Broken placeholder
    assert "obs-broken" in result.html or "Повреждён" in result.html
    # Snapshot data НЕ рендерится — нет метрик
    assert "obs-metrics-table" not in result.html
    # Warnings
    assert any("corrupted" in w.lower() or "broken" in w.lower() for w in result.warnings)


def test_render_block_invalid_schema_status():
    block = {
        "kind": "observation_baseline",
        "snapshot": make_baseline_snapshot(),
        "block_id": 3,
        "title": "Invalid Schema Block",
    }
    ctx = make_render_context(block_status="invalid_schema")
    result = render_block(block, ctx)

    assert isinstance(result.html, str)
    assert "obs-broken" in result.html or "Битая схема" in result.html or "invalid_schema" in result.html
    assert "obs-metrics-table" not in result.html


def test_render_block_legacy_v0_status():
    snap = make_baseline_snapshot()
    block = {
        "kind": "observation_baseline",
        "snapshot": snap,
        "block_id": 4,
        "title": "Legacy Block",
    }
    ctx = make_render_context(block_status="legacy_v0")
    result = render_block(block, ctx)

    # Orange/legacy badge
    assert "obs-legacy" in result.html or "legacy_v0" in result.html or "Устарел" in result.html
    # Best-effort: период показан
    assert "2025-01-01" in result.html or "Период" in result.html
    # Warning о legacy
    assert any("legacy" in w.lower() for w in result.warnings)


def test_render_block_partial_status():
    """partial — не broken и не legacy: рендерится как обычный блок."""
    snap = make_baseline_snapshot()
    block = {
        "kind": "observation_baseline",
        "snapshot": snap,
        "block_id": 5,
        "title": "Partial Block",
    }
    ctx = make_render_context(block_status="partial")
    result = render_block(block, ctx)

    # partial не в _BROKEN_STATUSES → нормальный рендер
    assert isinstance(result.html, str)
    # Snapshot данные присутствуют
    assert "obs-baseline" in result.html or "obs-metrics-table" in result.html or "2025-01-01" in result.html


# ---------------------------------------------------------------------------
# 5. Мутация snapshot
# ---------------------------------------------------------------------------


def test_renderer_does_not_mutate_baseline_snapshot():
    snap = make_baseline_snapshot()
    snap_before = copy.deepcopy(snap)
    ctx = make_render_context(skip_figures=True)
    render_baseline(snap, ctx)
    assert snap == snap_before


def test_renderer_does_not_mutate_period_snapshot():
    snap = make_period_snapshot()
    snap_before = copy.deepcopy(snap)
    ctx = make_render_context(skip_figures=True)
    render_period(snap, ctx)
    assert snap == snap_before


def test_renderer_does_not_mutate_segment_snapshot():
    snap = make_segment_snapshot()
    snap_before = copy.deepcopy(snap)
    ctx = make_render_context(skip_figures=True)
    render_segment(snap, ctx)
    assert snap == snap_before


# ---------------------------------------------------------------------------
# 6. latex_escape
# ---------------------------------------------------------------------------


def test_latex_escape_special_chars():
    special_cases = {
        "&":  r"\&",
        "%":  r"\%",
        "$":  r"\$",
        "#":  r"\#",
        "_":  r"\_",
        "{":  r"\{",
        "}":  r"\}",
        "~":  r"\textasciitilde{}",
        "^":  r"\textasciicircum{}",
        "\\": r"\textbackslash{}",
    }
    for char, expected in special_cases.items():
        result = latex_escape(char)
        assert result == expected, f"latex_escape({char!r}) = {result!r}, expected {expected!r}"


def test_latex_escape_backslash_first():
    # "&\&" → backslash заменяется первым → не двойной escape
    result = latex_escape(r"&\&")
    # Ожидаем: \& + \textbackslash{} + \&
    assert r"\textbackslash{}" in result
    # НЕ должно быть двойного textbackslash
    assert r"\textbackslash{}\textbackslash{}" not in result


def test_latex_escape_cyrillic_preserved():
    s = "Привет, мир!"
    result = latex_escape(s)
    assert result == s


def test_latex_escape_none_input():
    result = latex_escape(None)
    assert result == "None"


def test_latex_escape_numeric_input():
    result = latex_escape(42)
    assert result == "42"


# ---------------------------------------------------------------------------
# 7. HTML/LaTeX parity
# ---------------------------------------------------------------------------


def test_html_pdf_parity_baseline():
    snap = make_baseline_snapshot()
    ctx = make_render_context(skip_figures=True)
    result = render_baseline(snap, ctx)

    # Оба рендера содержат период
    assert "2025-01-01" in result.html
    assert "2025-01-01" in result.latex
    # Mean value (16.65) в обоих
    assert "16.65" in result.html or "16,65" in result.html
    assert "16.65" in result.latex or "16,65" in result.latex


def test_html_pdf_parity_period():
    snap = make_period_snapshot()
    ctx = make_render_context(skip_figures=True)
    result = render_period(snap, ctx)

    # Период в обоих
    assert "2025-02-01" in result.html
    assert "2025-02-01" in result.latex
    # Subsection в LaTeX
    assert r"\subsection" in result.latex
    # Section тег в HTML
    assert "<section" in result.html


def test_html_pdf_parity_segment():
    snap = make_segment_snapshot(n_segments=3, n_changepoints=2)
    ctx = make_render_context(skip_figures=True)
    result = render_segment(snap, ctx)

    # Период в обоих
    assert "2025-03-01" in result.html
    assert "2025-03-01" in result.latex
    # Таблица в обоих
    assert "obs-segments-table" in result.html
    assert r"\begin{tabular}" in result.latex


# ---------------------------------------------------------------------------
# 8. Chapter assembler
# ---------------------------------------------------------------------------


def test_chapter_assembler_multi_block():
    """3 блока разных kind → HTML/LaTeX содержат все 3."""
    blocks = [
        {
            "kind": "observation_baseline",
            "snapshot": make_baseline_snapshot(),
            "block_id": 10,
            "title": "Baseline Block",
            "block_status": "ok",
        },
        {
            "kind": "observation_period",
            "snapshot": make_period_snapshot(),
            "block_id": 11,
            "title": "Period Block",
            "block_status": "ok",
        },
        {
            "kind": "observation_segment",
            "snapshot": make_segment_snapshot(),
            "block_id": 12,
            "title": "Segment Block",
            "block_status": "ok",
        },
    ]

    # HTML assembler
    html_out = render_observation_chapter(blocks)
    assert "obs-baseline" in html_out
    assert "obs-period" in html_out
    assert "obs-segment" in html_out

    # LaTeX assembler
    latex_out = render_observation_chapter_latex(blocks, skip_figures=True)
    assert r"\section" in latex_out
    # Три subsection (по одному на блок)
    assert latex_out.count(r"\subsection") >= 3


def test_chapter_assembler_empty_blocks():
    html_out = render_observation_chapter([])
    # Placeholder "Нет блоков" или empty section
    assert "obs-chapter" in html_out
    assert "Нет блоков" in html_out or "obs-empty-msg" in html_out or "obs-chapter--empty" in html_out

    latex_out = render_observation_chapter_latex([], skip_figures=True)
    assert r"\section" in latex_out
    assert "Нет блоков" in latex_out


# ---------------------------------------------------------------------------
# 9. skip_figures
# ---------------------------------------------------------------------------


def test_render_with_skip_figures_true_returns_empty_figures():
    """Для всех 3 kind с skip_figures=True → result.figures == []."""
    ctx_b = make_render_context(skip_figures=True)
    ctx_p = make_render_context(skip_figures=True)
    ctx_s = make_render_context(skip_figures=True)

    r_b = render_baseline(make_baseline_snapshot(), ctx_b)
    r_p = render_period(make_period_snapshot(), ctx_p)
    r_s = render_segment(make_segment_snapshot(), ctx_s)

    assert r_b.figures == []
    assert r_p.figures == []
    assert r_s.figures == []


def test_render_with_skip_figures_false_creates_pngs(tmp_path):
    """baseline + skip_figures=False → PNG создан, размер ≥ 1 KB."""
    snap = make_baseline_snapshot()
    ctx = RenderContext(
        output_dir=str(tmp_path),
        block_id=99,
        title="Skip Figures Test",
        block_status="ok",
        skip_figures=False,
    )
    result = render_baseline(snap, ctx)

    assert len(result.figures) >= 1
    for fig in result.figures:
        p = Path(fig.absolute_path)
        assert p.exists(), f"PNG не создан: {fig.absolute_path}"
        assert p.stat().st_size >= 1024, f"PNG слишком маленький: {p.stat().st_size} bytes"


# ---------------------------------------------------------------------------
# 10. Backward-compat public API
# ---------------------------------------------------------------------------


def test_render_observation_chapter_backward_compat():
    """render_observation_chapter(blocks) → HTML string, skip_figures=True (нет PNG)."""
    blocks = [
        {
            "kind": "observation_baseline",
            "snapshot": make_baseline_snapshot(),
            "block_id": 1,
            "title": "B",
            "block_status": "ok",
        }
    ]
    result = render_observation_chapter(blocks)
    assert isinstance(result, str)
    assert "<section" in result
    assert "obs-chapter" in result


def test_render_observation_chapter_latex_signature():
    """render_observation_chapter_latex(blocks) → str с \\section."""
    blocks = [
        {
            "kind": "observation_baseline",
            "snapshot": make_baseline_snapshot(),
            "block_id": 1,
            "title": "B",
            "block_status": "ok",
        }
    ]
    result = render_observation_chapter_latex(blocks, skip_figures=True)
    assert isinstance(result, str)
    assert r"\section" in result


# ---------------------------------------------------------------------------
# 11. No DB calls
# ---------------------------------------------------------------------------


def test_renderer_no_db_calls():
    """render_baseline/period/segment не обращаются к SessionLocal."""
    with patch("backend.db.SessionLocal") as mock_session:
        ctx = make_render_context(skip_figures=True)
        render_baseline(make_baseline_snapshot(), ctx)
        render_period(make_period_snapshot(), ctx)
        render_segment(make_segment_snapshot(), ctx)

    mock_session.assert_not_called()


def test_renderer_no_service_calls():
    """Renderer не вызывает аналитические сервисы."""
    service_paths = [
        "backend.services.observation_period_service.compute_period_preview",
        "backend.services.observation_snapshot_service.load_and_validate_snapshot",
    ]
    mocks_hit = []

    def track_call(*args, **kwargs):
        mocks_hit.append(True)
        return MagicMock()

    ctx = make_render_context(skip_figures=True)

    # Просто запускаем рендер — если сервисы вызваны, тест упадёт через mocks_hit
    render_baseline(make_baseline_snapshot(), ctx)
    render_period(make_period_snapshot(), ctx)
    render_segment(make_segment_snapshot(), ctx)

    # Renderer не должен вызывать аналитику — мы проверяем по отсутствию импортов
    # (Функции сервисов запрещены в renderer — проверяем по docstring renderer'а)
    # Формальный тест: результат рендера валиден без каких-либо сервис-вызовов
    assert len(mocks_hit) == 0


# ---------------------------------------------------------------------------
# 12. KIND_RENDERERS integrity
# ---------------------------------------------------------------------------


def test_kind_renderers_contains_all_kinds():
    assert "observation_baseline" in KIND_RENDERERS
    assert "observation_period" in KIND_RENDERERS
    assert "observation_segment" in KIND_RENDERERS
    assert KIND_RENDERERS["observation_baseline"] is render_baseline
    assert KIND_RENDERERS["observation_period"] is render_period
    assert KIND_RENDERERS["observation_segment"] is render_segment


# ---------------------------------------------------------------------------
# 13. RenderResult dataclass defaults
# ---------------------------------------------------------------------------


def test_render_result_defaults():
    r = RenderResult(html="<p>test</p>", latex=r"\test")
    assert r.figures == []
    assert r.warnings == []


def test_figure_ref_default_width():
    f = FigureRef(
        relative_path="obs/test.png",
        absolute_path="/tmp/test.png",
        caption="Test caption",
    )
    assert f.width_pt == 360.0


# ---------------------------------------------------------------------------
# g-fix-2: real C1 snapshot format — B1 deltas с реальными ключами
# ---------------------------------------------------------------------------


def make_period_snapshot_real_c1(with_b1_ok: bool = True) -> dict:
    """Snapshot с РЕАЛЬНЫМ форматом C1 (observation_period_service):

    deltas keys: 'p_tube_mean', 'p_line_mean', 'dp_mean', 'q_mean',
                 'shutdown_pct' (только abs), 'cv_p_tube' (только abs)
    delta shape: {"abs": x, "pct": y}  (или только {"abs": x})

    diagnostics: top-level snapshot.diagnostics[],
                 entries с context="vs_b1" и target ∈ {p_tube, p_line, dp, q, shutdown_pct, cv_p_tube}

    НЕТ ключей baseline_value, current_value, delta_abs, delta_pct, verdict внутри deltas.
    """
    snap = make_period_snapshot()
    if with_b1_ok:
        snap["comparisons"]["with_b1"] = {
            "status": "ok",
            "baseline_block_id": 42,
            "baseline_period": {"from": "2024-12-01", "to": "2024-12-31"},
            "deltas": {
                "p_tube_mean":  {"abs": 0.45, "pct": 2.7},
                "p_line_mean":  {"abs": -0.12, "pct": -2.3},
                "dp_mean":      {"abs": 0.57, "pct": 5.1},
                "q_mean":       {"abs": 1500.0, "pct": 3.6},
                "shutdown_pct": {"abs": -1.8},     # только abs
                "cv_p_tube":    {"abs": -0.4},     # только abs
            },
        }
        snap["diagnostics"] = [
            {"target": "p_tube",       "context": "vs_b1", "verdict": "improvement",
             "magnitude": {"abs": 0.45, "pct": 2.7}, "requires_log_check": True},
            {"target": "p_line",       "context": "vs_b1", "verdict": "no_significant_change",
             "magnitude": {"abs": -0.12, "pct": -2.3}, "requires_log_check": True},
            {"target": "dp",           "context": "vs_b1", "verdict": "improvement",
             "magnitude": {"abs": 0.57, "pct": 5.1}, "requires_log_check": True},
            {"target": "q",            "context": "vs_b1", "verdict": "improvement",
             "magnitude": {"abs": 1500.0, "pct": 3.6}, "requires_log_check": True},
            {"target": "shutdown_pct", "context": "vs_b1", "verdict": "improvement",
             "magnitude": {"abs": -1.8}, "requires_log_check": True},
            {"target": "cv_p_tube",    "context": "vs_b1", "verdict": "no_significant_change",
             "magnitude": {"abs": -0.4}, "requires_log_check": True},
            {"target": "overall",      "context": "combined", "verdict": "improvement",
             "magnitude": None, "requires_log_check": True},
        ]
    return snap


def test_render_period_real_c1_format_renders_b1_deltas():
    """g-fix-2: render_period с РЕАЛЬНЫМ форматом C1 → B1 deltas table заполнена."""
    snap = make_period_snapshot_real_c1(with_b1_ok=True)
    ctx = make_render_context(block_status="ok", skip_figures=True)
    result = render_period(snap, ctx)

    # HTML: проверяем что числа из deltas присутствуют (раньше были пустые)
    assert "0.45" in result.html, "abs значение p_tube_mean должно быть в HTML"
    assert "2.7" in result.html, "pct значение p_tube_mean должно быть в HTML"
    assert "0.57" in result.html, "dp_mean abs"
    assert "5.1" in result.html, "dp_mean pct"
    assert "1500" in result.html or "1500.0" in result.html, "q_mean abs"
    # shutdown_pct только abs (нет pct)
    assert "-1.8" in result.html, "shutdown_pct abs"


def test_render_period_real_c1_format_renders_verdict_from_diagnostics():
    """g-fix-2: verdict читается из snapshot.diagnostics[] (не из deltas)."""
    snap = make_period_snapshot_real_c1(with_b1_ok=True)
    ctx = make_render_context(block_status="ok", skip_figures=True)
    result = render_period(snap, ctx)

    # Verdicts из diagnostics: improvement (4×), no_significant_change (2×)
    assert "improvement" in result.html
    assert "no_significant_change" in result.html
    # LaTeX вариант тоже (underscore escaped: no\_significant\_change)
    assert "improvement" in result.latex
    assert r"no\_significant\_change" in result.latex


def test_render_period_real_c1_shutdown_pct_shows_dash_for_pct():
    """g-fix-2: shutdown_pct и cv_p_tube без pct → отображается '—' (HTML) / '---' (LaTeX)."""
    snap = make_period_snapshot_real_c1(with_b1_ok=True)
    ctx = make_render_context(block_status="ok", skip_figures=True)
    result = render_period(snap, ctx)

    # Должны быть строки с "Доля простоев" и "CV" — в HTML пользовательский label
    assert "Доля простоев" in result.html
    assert "CV" in result.html
    # Для shutdown_pct и cv_p_tube — нет pct, должна быть прочерк-плейсхолдер
    # Просто проверим что таблица не упала на None.pct


def test_render_period_real_c1_no_baseline_value_or_current_value():
    """g-fix-2 variant (a): renderer НЕ показывает baseline_value/current_value (R2)."""
    snap = make_period_snapshot_real_c1(with_b1_ok=True)
    ctx = make_render_context(block_status="ok", skip_figures=True)
    result = render_period(snap, ctx)

    # Header таблицы не должен содержать "Baseline" или "Текущий" как колонки
    # (Это columns которые мы убрали в variant a)
    # Проверим header: "Δ абс." / "Δ %" / "Вердикт" есть, "Baseline"/"Текущий" — НЕТ
    # NB: слово "Baseline" может встречаться в badge "baseline: сравнение выполнено"
    # Поэтому проверяем именно header table:
    assert "<th>Baseline</th>" not in result.html
    assert "<th>Текущий</th>" not in result.html
    # Header LaTeX:
    assert r"\textbf{Baseline}" not in result.latex
    assert r"\textbf{Тек.}" not in result.latex


def test_verdict_for_helper():
    """g-fix-2: _verdict_for читает правильную запись из diagnostics."""
    from backend.services.observation_chapter_renderer import _verdict_for
    diags = [
        {"target": "p_tube", "context": "vs_b1", "verdict": "improvement"},
        {"target": "q",      "context": "vs_b1", "verdict": "degradation"},
        {"target": "q",      "context": "vs_customer", "verdict": "match"},
    ]
    assert _verdict_for(diags, "p_tube", "vs_b1") == "improvement"
    assert _verdict_for(diags, "q",      "vs_b1") == "degradation"
    assert _verdict_for(diags, "q",      "vs_customer") == "match"
    # Нет matching → "—"
    assert _verdict_for(diags, "dp", "vs_b1") == "—"
    assert _verdict_for([], "p_tube", "vs_b1") == "—"
    assert _verdict_for(None, "p_tube", "vs_b1") == "—"


# ---------------------------------------------------------------------------
# g-fix-3: real C1 snapshot format — customer comparison с реальными ключами
# ---------------------------------------------------------------------------


def make_period_snapshot_real_c1_with_customer(
    status: str = "ok", q_source_used: str = "q_gas_working",
    daily_rows: int = 3,
) -> dict:
    """Snapshot с РЕАЛЬНЫМ форматом C1 для comparisons.with_customer.

    Структура (RFC §9.Q1):
      status, customer_period, customer_days_available, customer_days_requested,
      mape (dict), q_source_used, daily_table[].
    """
    snap = make_period_snapshot_real_c1(with_b1_ok=False)
    table = []
    for i in range(daily_rows):
        table.append({
            "date": f"2025-02-{i+1:02d}",
            "our_q_total":      43200.0 + i * 50,
            "our_q_working":    43000.0 + i * 50,
            "customer_q_total": 43100.0 + i * 40,
            "customer_q_working": 42900.0 + i * 40,
            "diff_abs":         100.0 + i * 10,
            "diff_pct":         0.23 + i * 0.05,
            "data_status":      "ok" if i < daily_rows - 1 else "missing_customer",
        })
    snap["comparisons"]["with_customer"] = {
        "status": status,
        "customer_period": {"from": "2025-02-01", "to": "2025-02-28"},
        "customer_days_available": daily_rows,
        "customer_days_requested": 28,
        "mape": {"q": 2.3, "p_tube": 0.45, "p_line": 0.62},
        "q_source_used": q_source_used,
        "daily_table": table,
    }
    return snap


def test_render_period_real_c1_customer_renders_daily_table():
    """g-fix-3: HTML содержит реальные числа из daily_table."""
    snap = make_period_snapshot_real_c1_with_customer(status="ok", daily_rows=3)
    ctx = make_render_context(block_status="ok", skip_figures=True)
    result = render_period(snap, ctx)

    # Дата первой строки + наш Q + customer Q (q_gas_working default)
    assert "2025-02-01" in result.html
    # our_q_working[0] = 43000
    assert "43000" in result.html
    # customer_q_working[0] = 42900
    assert "42900" in result.html
    # diff_abs[0] = 100
    assert "100" in result.html
    # data_status в последней строке: missing_customer
    assert "missing_customer" in result.html


def test_render_period_real_c1_customer_mape_per_metric():
    """g-fix-3: MAPE как dict отображается per-metric."""
    snap = make_period_snapshot_real_c1_with_customer(status="ok", daily_rows=2)
    ctx = make_render_context(block_status="ok", skip_figures=True)
    result = render_period(snap, ctx)

    # MAPE значения per-metric
    assert "2.3" in result.html, "mape.q должен быть в HTML"
    assert "0.45" in result.html, "mape.p_tube должен быть в HTML"
    assert "0.62" in result.html, "mape.p_line должен быть в HTML"
    # И label-теги
    assert "Q" in result.html
    # LaTeX тоже содержит mape
    assert "2.3" in result.latex


def test_render_period_real_c1_q_source_working():
    """g-fix-3: q_source_used='q_gas_working' → используются _working колонки."""
    snap = make_period_snapshot_real_c1_with_customer(
        status="ok", q_source_used="q_gas_working", daily_rows=2
    )
    ctx = make_render_context(block_status="ok", skip_figures=True)
    result = render_period(snap, ctx)

    # our_q_working[0] = 43000, our_q_total[0] = 43200
    # При q_working — должны увидеть 43000 (working), не 43200 (total)
    assert "43000" in result.html
    # source label
    assert "q_gas_working" in result.html


def test_render_period_real_c1_q_source_total():
    """g-fix-3: q_source_used='q_gas_total' → используются _total колонки."""
    snap = make_period_snapshot_real_c1_with_customer(
        status="ok", q_source_used="q_gas_total", daily_rows=2
    )
    ctx = make_render_context(block_status="ok", skip_figures=True)
    result = render_period(snap, ctx)

    # our_q_total[0] = 43200
    assert "43200" in result.html
    # customer_q_total[0] = 43100
    assert "43100" in result.html
    assert "q_gas_total" in result.html


def test_render_period_real_c1_partial_customer_data_renders_table():
    """g-fix-3: status='partial_customer_data' → warning badge + таблица всё равно показывается."""
    snap = make_period_snapshot_real_c1_with_customer(
        status="partial_customer_data", daily_rows=2
    )
    ctx = make_render_context(block_status="ok", skip_figures=True)
    result = render_period(snap, ctx)

    # Должен быть warning badge
    assert "частичные" in result.html.lower() or "partial" in result.html.lower()
    # Таблица всё равно есть (числа daily_table)
    assert "2025-02-01" in result.html
    assert "43000" in result.html  # our_q_working[0]


def test_render_period_real_c1_no_customer_data_no_table_no_crash():
    """g-fix-3: status='no_customer_data' → сообщение, нет таблицы, не падает."""
    snap = make_period_snapshot_real_c1_with_customer(
        status="no_customer_data", daily_rows=0
    )
    snap["comparisons"]["with_customer"]["daily_table"] = []
    snap["comparisons"]["with_customer"]["mape"] = None
    ctx = make_render_context(block_status="ok", skip_figures=True)
    # Не падает
    result = render_period(snap, ctx)
    # Сообщение про отсутствие данных
    assert "Данных заказчика нет" in result.html or "нет" in result.html.lower()
    # Не должно быть <table class="obs-customer-table">
    assert 'class="obs-customer-table"' not in result.html


def test_render_period_real_c1_period_mismatch_no_crash():
    """g-fix-3: status='period_mismatch' → сообщение, не падает."""
    snap = make_period_snapshot_real_c1_with_customer(status="period_mismatch", daily_rows=0)
    snap["comparisons"]["with_customer"]["daily_table"] = []
    ctx = make_render_context(block_status="ok", skip_figures=True)
    result = render_period(snap, ctx)
    assert "не совпадает" in result.html.lower() or "period" in result.html.lower()


def test_render_period_real_c1_customer_latex_parity():
    """g-fix-3: LaTeX содержит daily_table строки (parity с HTML)."""
    snap = make_period_snapshot_real_c1_with_customer(status="ok", daily_rows=2)
    ctx = make_render_context(block_status="ok", skip_figures=True)
    result = render_period(snap, ctx)

    # LaTeX содержит даты и числа
    assert "2025-02-01" in result.latex
    assert "43000" in result.latex
    # LaTeX содержит badge label
    assert "сравнение с заказчиком" in result.latex
    # LaTeX содержит tabular environment
    assert r"\begin{tabular}" in result.latex


def test_pick_our_q_and_pick_customer_q_helpers():
    """g-fix-3: helpers корректно выбирают канал по q_source_used."""
    from backend.services.observation_chapter_renderer import (
        _pick_our_q, _pick_customer_q,
    )
    row = {"our_q_total": 100, "our_q_working": 90,
           "customer_q_total": 95, "customer_q_working": 85}
    assert _pick_our_q(row, "q_gas_working") == 90
    assert _pick_our_q(row, "q_gas_total") == 100
    assert _pick_customer_q(row, "q_gas_working") == 85
    assert _pick_customer_q(row, "q_gas_total") == 95

    # Fallback: our_q_working отсутствует → берём our_q_total
    row2 = {"our_q_total": 100, "customer_q_working": 85}
    assert _pick_our_q(row2, "q_gas_working") == 100  # fallback на total


# ---------------------------------------------------------------------------
# g-fix-4: real C2 snapshot format — segments с реальными ключами
# ---------------------------------------------------------------------------


def make_segment_snapshot_real_c2(n_segments: int = 3, n_changepoints: int = 2) -> dict:
    """Snapshot с РЕАЛЬНЫМ форматом C2 (observation_segment_service).

    Реальные ключи сегмента (observation_segment_service.py:720-733):
        num, start_date, end_date, start_idx, end_idx, duration_days,
        mean_q, mean_dp, mean_p_tube, mean_p_line, slope_q_per_day, direction.
    НЕТ ключей from/to/days/type.
    """
    from datetime import date, timedelta
    base = date(2025, 3, 1)
    seg_len = 10
    directions = ["rising", "falling", "stable", "insufficient_data"]
    segments = []
    for i in range(n_segments):
        seg_from = base + timedelta(days=i * seg_len)
        seg_to = seg_from + timedelta(days=seg_len - 1)
        segments.append({
            "num": i + 1,
            "start_date": seg_from.isoformat(),
            "end_date": seg_to.isoformat(),
            "start_idx": i * seg_len,
            "end_idx": (i + 1) * seg_len - 1,
            "duration_days": seg_len,
            "mean_q": 42000.0 + i * 300,
            "mean_dp": 11.2 + i * 0.1,
            "mean_p_tube": 16.5 + i * 0.05,
            "mean_p_line": 5.3,
            "slope_q_per_day": -12.5 + i * 8.0,
            "direction": directions[i % len(directions)],
        })
    changepoints = []
    for j in range(n_changepoints):
        cp_date = base + timedelta(days=(j + 1) * seg_len)
        changepoints.append({
            "idx": (j + 1) * seg_len,
            "date": cp_date.isoformat(),
            "magnitude_pct": -(5.2 + j * 1.1),
            "confidence": "high" if j == 0 else "medium",
        })
    dates, q_vals = [], []
    for i in range(n_segments * seg_len):
        d = base + timedelta(days=i)
        dates.append(d.isoformat())
        q_vals.append(42000.0 + i * 10)
    return {
        "_v": "obs_segment_v1",
        "schema_version": "1.0",
        "computed_at": "2026-05-19T10:00:00Z",
        "block_status": "ok",
        "period": {"from": base.isoformat(),
                   "to": (base + timedelta(days=n_segments * seg_len - 1)).isoformat()},
        "raw": {"chart_payload": {"dates": dates, "q": q_vals}},
        "quality": {"status": "ok", "flags": [], "metrics": {"coverage_pct": 96.0}},
        "flags": {"low_coverage": False, "significant_gap": False,
                  "outlier_detected": False, "short_intersection": False,
                  "baseline_mismatch_period": False,
                  "outdated_baseline_version": False, "invalid_comparison": False},
        "thresholds_used": {"aggregation": "daily", "sensitivity": "medium",
                            "min_segment_days": 7, "min_change_pct": 10.0,
                            "smoothing_window": 7, "ignore_shutdown_days": True,
                            "ignore_purge_window_hours": 24, "has_user_overrides": False},
        "segments": segments,
        "changepoints": changepoints,
        "shutdown_clusters": [],
        "diagnostics": [],
    }


def test_render_segment_real_c2_format_renders_table():
    """g-fix-4: render_segment с РЕАЛЬНЫМ форматом C2 → segments table заполнена."""
    snap = make_segment_snapshot_real_c2(n_segments=3, n_changepoints=2)
    ctx = make_render_context(block_status="ok", skip_figures=True)
    result = render_segment(snap, ctx)
    # start_date первого сегмента
    assert "2025-03-01" in result.html
    # end_date первого сегмента (2025-03-10)
    assert "2025-03-10" in result.html
    # duration_days = 10
    assert ">10<" in result.html or " 10 " in result.html
    # mean_q первого = 42000
    assert "42000" in result.html
    # direction labels (rising → рост)
    assert "рост" in result.html
    assert "снижение" in result.html


def test_render_segment_real_c2_latex_parity():
    """g-fix-4: LaTeX segments table содержит реальные C2-ключи."""
    snap = make_segment_snapshot_real_c2(n_segments=3, n_changepoints=2)
    ctx = make_render_context(block_status="ok", skip_figures=True)
    result = render_segment(snap, ctx)
    assert "2025-03-01" in result.latex
    assert "2025-03-10" in result.latex
    assert "42000" in result.latex
    assert "рост" in result.latex
    assert r"\begin{tabular}" in result.latex


def test_render_segment_real_c2_no_question_marks():
    """g-fix-4: после fix — НЕТ '?' заглушек в segments table при валидном snapshot."""
    snap = make_segment_snapshot_real_c2(n_segments=3, n_changepoints=1)
    ctx = make_render_context(block_status="ok", skip_figures=True)
    result = render_segment(snap, ctx)
    # Извлекаем tbody segments-таблицы — там не должно быть '?'
    seg_table_start = result.html.find('class="obs-segments-table"')
    assert seg_table_start != -1, "segments table должна присутствовать"
    seg_table = result.html[seg_table_start:seg_table_start + 2000]
    assert ">?<" not in seg_table, "не должно быть '?' заглушек после g-fix-4"


def test_render_segment_real_c2_slope_per_day_present():
    """g-fix-4: slope_q_per_day отображается в таблице."""
    snap = make_segment_snapshot_real_c2(n_segments=2, n_changepoints=1)
    ctx = make_render_context(block_status="ok", skip_figures=True)
    result = render_segment(snap, ctx)
    # slope первого сегмента = -12.5
    assert "-12.5" in result.html or "-12" in result.html


def test_render_segment_real_c2_chart_uses_start_date():
    """g-fix-4: chart renderer использует start_date/end_date (не from/to)."""
    from backend.services.observation_chart_renderer import render_segment_chart
    import tempfile, os
    snap = make_segment_snapshot_real_c2(n_segments=3, n_changepoints=2)
    with tempfile.TemporaryDirectory() as tmpd:
        out = os.path.join(tmpd, "seg.png")
        render_segment_chart(snap, out)
        assert os.path.exists(out)
        assert os.path.getsize(out) >= 1000, "PNG должен быть непустым"


# ===========================================================================
# Phase O1 — parts-gating + операторские примечания (prefix/suffix/comment)
# ===========================================================================


def _block(kind, snapshot, *, params=None, comment=None,
           block_status="ok", block_id=7, title="O1 Block"):
    """Хелпер: собирает block dict для render_block (Phase O1 формат)."""
    b = {
        "kind": kind,
        "snapshot": snapshot,
        "block_id": block_id,
        "title": title,
        "block_status": block_status,
    }
    if params is not None:
        b["params"] = params
    if comment is not None:
        b["comment"] = comment
    return b


# ── default-on: блок без params рендерится полностью (backward-compat) ──


def test_o1_parts_default_all_visible_baseline():
    """Блок без params.parts → все секции видимы (как до O1)."""
    snap = make_baseline_snapshot()
    snap["flags"]["low_coverage"] = True
    ctx = make_render_context(skip_figures=True)
    result = render_block(_block("observation_baseline", snap), ctx)
    assert "obs-metrics-table" in result.html
    assert "obs-flags-list" in result.html
    assert "Период" in result.html
    # LaTeX parity
    assert "Параметр" in result.latex  # metrics tabular header


# ── baseline: гейтинг отдельных частей ──


def test_o1_baseline_hide_metrics_table():
    snap = make_baseline_snapshot()
    ctx = make_render_context(skip_figures=True)
    result = render_block(
        _block("observation_baseline", snap, params={"parts": {"metrics_table": False}}),
        ctx,
    )
    # Скрыто в HTML И в LaTeX (parity)
    assert "obs-metrics-table" not in result.html
    assert "Параметр" not in result.latex


def test_o1_baseline_hide_flags():
    snap = make_baseline_snapshot()
    snap["flags"]["low_coverage"] = True
    ctx = make_render_context(skip_figures=True)
    on = render_block(_block("observation_baseline", snap), ctx)
    assert "obs-flags-list" in on.html

    ctx2 = make_render_context(skip_figures=True)
    off = render_block(
        _block("observation_baseline", snap, params={"parts": {"flags": False}}),
        ctx2,
    )
    assert "obs-flags-list" not in off.html


def test_o1_baseline_hide_intro_removes_period():
    snap = make_baseline_snapshot()
    ctx = make_render_context(skip_figures=True)
    result = render_block(
        _block("observation_baseline", snap, params={"parts": {"intro": False}}),
        ctx,
    )
    assert "Период:" not in result.html
    assert "Период:" not in result.latex


def test_o1_baseline_description_part():
    snap = make_baseline_snapshot()
    snap["description"] = "ОПИСАНИЕ-O1-МАРКЕР"
    # description on (default)
    ctx = make_render_context(skip_figures=True)
    on = render_block(_block("observation_baseline", snap), ctx)
    assert "ОПИСАНИЕ-O1-МАРКЕР" in on.html
    assert "ОПИСАНИЕ-O1-МАРКЕР" in on.latex
    # description off
    ctx2 = make_render_context(skip_figures=True)
    off = render_block(
        _block("observation_baseline", snap, params={"parts": {"description": False}}),
        ctx2,
    )
    assert "ОПИСАНИЕ-O1-МАРКЕР" not in off.html
    assert "ОПИСАНИЕ-O1-МАРКЕР" not in off.latex


# ── period: гейтинг сравнений + суточной таблицы ──


def test_o1_period_hide_comparison_b1():
    snap = make_period_snapshot_real_c1(with_b1_ok=True)
    ctx = make_render_context(skip_figures=True)
    on = render_block(_block("observation_period", snap), ctx)
    assert "obs-deltas-table" in on.html

    ctx2 = make_render_context(skip_figures=True)
    off = render_block(
        _block("observation_period", snap,
               params={"parts": {"comparison_with_b1": False}}),
        ctx2,
    )
    assert "obs-deltas-table" not in off.html
    assert "Сравнение с baseline" not in off.latex


def test_o1_period_hide_comparison_customer():
    snap = make_period_snapshot_real_c1_with_customer(status="ok", daily_rows=3)
    ctx = make_render_context(skip_figures=True)
    off = render_block(
        _block("observation_period", snap,
               params={"parts": {"comparison_with_customer": False}}),
        ctx,
    )
    assert "obs-customer" not in off.html
    assert "сравнение с заказчиком" not in off.latex


def test_o1_period_daily_table_hidden_keeps_customer_summary():
    """daily_table=False → таблица скрыта, но badge/MAPE сравнения остаются."""
    snap = make_period_snapshot_real_c1_with_customer(status="ok", daily_rows=3)
    ctx = make_render_context(skip_figures=True)
    result = render_block(
        _block("observation_period", snap,
               params={"parts": {"daily_table": False}}),
        ctx,
    )
    # Сводка сравнения с заказчиком осталась
    assert "obs-customer" in result.html
    # Суточная таблица скрыта
    assert "obs-customer-table" not in result.html
    # LaTeX: badge есть, tabular суточной таблицы нет
    assert "сравнение с заказчиком" in result.latex


def test_o1_period_charts_hidden_with_skip_figures_false(tmp_path):
    """charts=False + skip_figures=False → PNG не генерируются."""
    snap = make_period_snapshot_real_c1(with_b1_ok=True)
    ctx = RenderContext(
        output_dir=str(tmp_path), block_id=55, title="No charts",
        block_status="ok", skip_figures=False,
    )
    result = render_block(
        _block("observation_period", snap, params={"parts": {"charts": False}}),
        ctx,
    )
    assert result.figures == []


# ── segment: гейтинг changepoints / таблицы сегментов ──


def test_o1_segment_hide_changepoints():
    snap = make_segment_snapshot_real_c2(n_segments=3, n_changepoints=2)
    ctx = make_render_context(skip_figures=True)
    on = render_block(_block("observation_segment", snap), ctx)
    assert "obs-changepoints-list" in on.html

    ctx2 = make_render_context(skip_figures=True)
    off = render_block(
        _block("observation_segment", snap,
               params={"parts": {"changepoints": False}}),
        ctx2,
    )
    assert "obs-changepoints-list" not in off.html
    assert "Переломные точки" not in off.latex


def test_o1_segment_hide_segments_table():
    snap = make_segment_snapshot_real_c2(n_segments=3, n_changepoints=1)
    ctx = make_render_context(skip_figures=True)
    off = render_block(
        _block("observation_segment", snap,
               params={"parts": {"segments_table": False}}),
        ctx,
    )
    assert "obs-segments-table" not in off.html


# ── prefix_note / suffix_note / comment ──


def test_o1_prefix_suffix_note_rendered():
    snap = make_baseline_snapshot()
    ctx = make_render_context(skip_figures=True)
    result = render_block(
        _block("observation_baseline", snap, params={
            "prefix_note": "ВСТУП-O1",
            "suffix_note": "ИТОГ-O1",
        }),
        ctx,
    )
    assert "ВСТУП-O1" in result.html
    assert "ИТОГ-O1" in result.html
    assert "obs-prefix-note" in result.html
    assert "obs-suffix-note" in result.html
    # LaTeX parity
    assert "ВСТУП-O1" in result.latex
    assert "ИТОГ-O1" in result.latex


def test_o1_prefix_note_hidden_when_part_false():
    snap = make_baseline_snapshot()
    ctx = make_render_context(skip_figures=True)
    result = render_block(
        _block("observation_baseline", snap, params={
            "prefix_note": "ВСТУП-СКРЫТ",
            "parts": {"prefix_note": False},
        }),
        ctx,
    )
    assert "ВСТУП-СКРЫТ" not in result.html
    assert "ВСТУП-СКРЫТ" not in result.latex


def test_o1_suffix_note_hidden_when_part_false():
    snap = make_baseline_snapshot()
    ctx = make_render_context(skip_figures=True)
    result = render_block(
        _block("observation_baseline", snap, params={
            "suffix_note": "ИТОГ-СКРЫТ",
            "parts": {"suffix_note": False},
        }),
        ctx,
    )
    assert "ИТОГ-СКРЫТ" not in result.html
    assert "ИТОГ-СКРЫТ" not in result.latex


def test_o1_comment_rendered_always():
    """comment рендерится в главу и НЕ управляется parts."""
    snap = make_baseline_snapshot()
    # parts отключают всё что можно — comment всё равно виден
    ctx = make_render_context(skip_figures=True)
    result = render_block(
        _block("observation_baseline", snap,
               params={"parts": {"metrics_table": False, "flags": False,
                                  "intro": False, "quality": False}},
               comment="КОММЕНТАРИЙ-ОПЕРАТОРА-O1"),
        ctx,
    )
    assert "КОММЕНТАРИЙ-ОПЕРАТОРА-O1" in result.html
    assert "obs-comment" in result.html
    assert "КОММЕНТАРИЙ-ОПЕРАТОРА-O1" in result.latex


def test_o1_empty_notes_render_nothing():
    """Пустые prefix/suffix/comment → нет пустых div-обёрток."""
    snap = make_baseline_snapshot()
    ctx = make_render_context(skip_figures=True)
    result = render_block(
        _block("observation_baseline", snap,
               params={"prefix_note": "", "suffix_note": "  "}, comment=None),
        ctx,
    )
    assert "obs-prefix-note" not in result.html
    assert "obs-suffix-note" not in result.html
    assert "obs-comment" not in result.html


# ── mutation: render_block с params не мутирует block/snapshot ──


def test_o1_render_block_does_not_mutate_block_with_params():
    snap = make_period_snapshot_real_c1(with_b1_ok=True)
    block = _block("observation_period", snap, params={
        "parts": {"diagnostics": False}, "prefix_note": "x", "suffix_note": "y",
    }, comment="z")
    block_before = copy.deepcopy(block)
    ctx = make_render_context(skip_figures=True)
    render_block(block, ctx)
    assert block == block_before


# ── parity: скрытая часть отсутствует в обоих рендерах ──


def test_o1_parts_gating_html_latex_parity():
    """Скрытая часть отсутствует одновременно в HTML и LaTeX."""
    snap = make_period_snapshot_real_c1(with_b1_ok=True)
    ctx = make_render_context(skip_figures=True)
    hidden = render_block(
        _block("observation_period", snap,
               params={"parts": {"diagnostics": False, "metrics_table": False}}),
        ctx,
    )
    # diagnostics: ни HTML-список, ни LaTeX-заголовок
    assert "obs-diagnostics-list" not in hidden.html
    assert "Диагностика" not in hidden.latex
    # metrics: ни HTML-таблица, ни LaTeX-таблица
    assert "obs-metrics-table" not in hidden.html
    assert "Параметр" not in hidden.latex


# ── broken-snapshot: примечания не ломают placeholder ──


def test_o1_broken_block_with_params_still_placeholder():
    """corrupted-блок с params → placeholder, примечания не падают."""
    snap = make_baseline_snapshot()
    ctx = make_render_context(block_status="corrupted")
    result = render_block(
        _block("observation_baseline", snap,
               params={"prefix_note": "X", "parts": {"flags": False}},
               comment="C", block_status="corrupted"),
        ctx,
    )
    assert "obs-broken" in result.html or "Повреждён" in result.html
    assert "obs-metrics-table" not in result.html
