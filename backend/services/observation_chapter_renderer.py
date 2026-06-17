"""
Observation Chapter Renderer — Phase F (расширение STUB Phase C4).

PUBLIC API (backward-compat):
    render_observation_chapter(blocks) -> str          # HTML-only, editor preview
    render_observation_chapter_latex(blocks, ...) -> str  # LaTeX fragment для PDF

NEW API:
    render_block(block, ctx) -> RenderResult           # dispatch по kind
    render_baseline / render_period / render_segment   # per-kind renderers

ЗАПРЕЩЕНО:
    - db, Session, query, execute (renderer не читает БД)
    - compute_period_preview, load_and_validate_snapshot и прочие аналитик-сервисы
    - мутировать snapshot dict
    - пересчитывать аналитику

РАЗРЕШЕНО:
    - stdlib html.escape, math, os
    - dataclasses
    - matplotlib через observation_chart_renderer (только при skip_figures=False)
    - структурный HTML, LaTeX text-mode
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from html import escape as html_escape
from pathlib import Path
from typing import Any, Callable

from backend.constants.observation_parts import part_visible

__all__ = [
    "render_observation_chapter",
    "render_observation_chapter_latex",
    "render_block",
    "render_analysis",
    "render_baseline",
    "render_period",
    "render_segment",
    "RenderContext",
    "RenderResult",
    "FigureRef",
    "latex_escape",
    "KIND_RENDERERS",
]


# ---------------------------------------------------------------------------
# DATACLASSES
# ---------------------------------------------------------------------------


@dataclass
class FigureRef:
    relative_path: str   # "observation_charts/<block_id>_<kind>_<chart_type>.png"
    absolute_path: str   # полный путь
    caption: str         # подпись LaTeX
    width_pt: float = 360.0
    label: str = ""      # LaTeX label для cross-references


@dataclass
class RenderContext:
    output_dir: str           # backend/generated/observation_charts/
    block_id: int | None      # для имён файлов
    title: str                # из customer_report_block.title
    block_status: str         # из C3 classification
    skip_figures: bool = False
    # Phase O1 — видимость частей блока + операторские примечания.
    # Заполняются render_block() из block['params'] / block['comment'].
    # При прямом вызове render_baseline/period/segment остаются дефолтными
    # (parts={} → все части видимы, notes пустые) — backward-compat.
    parts: dict = field(default_factory=dict)   # {part_key: bool}
    prefix_note: str = ""                       # params.prefix_note
    suffix_note: str = ""                       # params.suffix_note
    comment: str = ""                           # customer_report_block.comment


@dataclass
class RenderResult:
    html: str
    latex: str
    figures: list[FigureRef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# LATEX ESCAPE
# ---------------------------------------------------------------------------

# Порядок важен: backslash должен быть первым!
LATEX_SPECIAL = {
    "\\": r"\textbackslash{}",
    "&":  r"\&",
    "%":  r"\%",
    "$":  r"\$",
    "#":  r"\#",
    "_":  r"\_",
    "{":  r"\{",
    "}":  r"\}",
    "~":  r"\textasciitilde{}",
    "^":  r"\textasciicircum{}",
}


_LATEX_ESCAPE_RE = re.compile(r"[\\&%\$#_{}~^]")


def latex_escape(s: Any) -> str:
    """Эскейп для LaTeX text-mode. Не для math-mode.

    Single-pass regex substitution — каждый special char обрабатывается
    один раз, без двойного эскейпа `{` и `}` внутри `\\textbackslash{}`.
    """
    if not isinstance(s, str):
        s = str(s)
    return _LATEX_ESCAPE_RE.sub(lambda m: LATEX_SPECIAL[m.group(0)], s)


# ---------------------------------------------------------------------------
# UTILITY (общие для HTML и LaTeX)
# ---------------------------------------------------------------------------


def _fmt(value: Any) -> str:
    """Форматирует числовое значение: None → '—', nan/inf → '—', float → 4 значащих."""
    if value is None:
        return "—"
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return "—"
        if f == int(f) and abs(f) < 1e6:
            return html_escape(str(int(f)))
        return html_escape(f"{f:.4g}")
    except (TypeError, ValueError):
        return html_escape(str(value))


def _fmt_latex(value: Any) -> str:
    """Форматирует числовое значение для LaTeX."""
    if value is None:
        return "---"
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return "---"
        if f == int(f) and abs(f) < 1e6:
            return str(int(f))
        return f"{f:.4g}"
    except (TypeError, ValueError):
        return latex_escape(str(value))


def _figure_stem(ctx: RenderContext, suffix: str) -> str:
    """Генерирует базовое имя файла PNG."""
    if ctx.block_id is not None:
        return f"obs_{ctx.block_id}_{suffix}"
    # Если block_id недоступен — используем хеш title
    import hashlib
    sha = hashlib.sha256((ctx.title or "").encode()).hexdigest()
    return f"obs_preview_{sha[:8]}_{suffix}"


# ---------------------------------------------------------------------------
# PHASE O1 — PARTS GATING + OPERATOR NOTES
# ---------------------------------------------------------------------------


def _part_on(ctx: RenderContext, key: str) -> bool:
    """Видима ли часть `key` блока. Default-on (см. observation_parts.part_visible)."""
    return part_visible(ctx.parts, key)


def _render_note_html(text: Any, css_class: str) -> str:
    """Операторское примечание (prefix/suffix/comment) → HTML <div>.

    Пустой/пробельный текст → '' (ничего не рендерится). Многострочный
    текст: переводы строк → <br>.
    """
    if text is None:
        return ""
    s = str(text).strip()
    if not s:
        return ""
    safe = html_escape(s).replace("\n", "<br>")
    return f'<div class="{css_class}">{safe}</div>'


def _render_note_latex(text: Any) -> str:
    """Операторское примечание → LaTeX (курсив, text-mode).

    Пустой текст → ''. Пустая строка-разделитель → \\par; одиночный
    перевод строки → пробел (примечания операторские, короткие).
    """
    if text is None:
        return ""
    s = str(text).strip()
    if not s:
        return ""
    esc = latex_escape(s)
    esc = re.sub(r"\n\s*\n", r"\\par ", esc)
    esc = esc.replace("\n", " ")
    return r"{\itshape " + esc + "}"


def _render_description_html(snapshot: dict) -> str:
    """Часть `description` — текстовое описание блока из snapshot.

    O1: narrative-текст НЕ добавляется (отдельная фаза observation_v1.1).
    Читается готовое строковое поле `snapshot['description']`, если есть.
    Отсутствует → '' (часть остаётся в каталоге, но без контента).
    """
    desc = snapshot.get("description") if isinstance(snapshot, dict) else None
    if not isinstance(desc, str) or not desc.strip():
        return ""
    safe = html_escape(desc.strip()).replace("\n", "<br>")
    return f'<div class="obs-description">{safe}</div>'


def _render_description_latex(snapshot: dict) -> str:
    """LaTeX-аналог _render_description_html (parity)."""
    desc = snapshot.get("description") if isinstance(snapshot, dict) else None
    if not isinstance(desc, str) or not desc.strip():
        return ""
    esc = latex_escape(desc.strip())
    esc = re.sub(r"\n\s*\n", r"\\par ", esc)
    esc = esc.replace("\n", " ")
    return esc


def _apply_block_notes(result: RenderResult, ctx: RenderContext) -> RenderResult:
    """Оборачивает результат render_* операторскими примечаниями.

    Порядок: prefix_note → контент блока → comment → suffix_note.
      * prefix_note / suffix_note — управляются частями parts;
      * comment — рендерится всегда при непустом значении (не часть parts).
    Renderer остаётся snapshot-only: примечания приходят из block-метаданных
    (params/comment), переданных в ctx, а не из БД.
    """
    html_pre = (
        _render_note_html(ctx.prefix_note, "obs-prefix-note")
        if _part_on(ctx, "prefix_note") else ""
    )
    html_post = (
        _render_note_html(ctx.suffix_note, "obs-suffix-note")
        if _part_on(ctx, "suffix_note") else ""
    )
    html_comment = _render_note_html(ctx.comment, "obs-comment")
    html = "\n".join(
        p for p in (html_pre, result.html, html_comment, html_post) if p
    )

    latex_pre = (
        _render_note_latex(ctx.prefix_note)
        if _part_on(ctx, "prefix_note") else ""
    )
    latex_post = (
        _render_note_latex(ctx.suffix_note)
        if _part_on(ctx, "suffix_note") else ""
    )
    latex_comment = _render_note_latex(ctx.comment)
    latex = "\n\n".join(
        c for c in (latex_pre, result.latex, latex_comment, latex_post) if c
    )

    return RenderResult(
        html=html,
        latex=latex,
        figures=result.figures,
        warnings=result.warnings,
    )


# ---------------------------------------------------------------------------
# BROKEN STATUS PLACEHOLDERS
# ---------------------------------------------------------------------------

_BROKEN_LABELS = {
    "corrupted":      "Повреждён",
    "invalid_schema": "Битая схема",
    "legacy_v0":      "Устарел",
}


def _render_broken_placeholder(ctx: RenderContext) -> RenderResult:
    """Для corrupted / invalid_schema — placeholder без data render."""
    title_h = html_escape(ctx.title or "(без заголовка)")
    title_l = latex_escape(ctx.title or "(без заголовка)")
    status_label = _BROKEN_LABELS.get(ctx.block_status, ctx.block_status)

    html = (
        f'<div class="obs-block obs-broken">'
        f'<h3>{title_h}</h3>'
        f'<p class="obs-broken-message">'
        f'<span class="obs-badge obs-badge-{html_escape(ctx.block_status)}">'
        f'{html_escape(status_label)}</span> '
        f'Блок не может быть отрендерен. Требуется пересчёт.'
        f'</p>'
        f'</div>'
    )

    latex = (
        f'\\subsection*{{{title_l}}}\n'
        f'\\textbf{{[Блок \\guillemotleft {latex_escape(status_label)}\\guillemotright]}} '
        f'Требуется пересчёт.\n'
    )

    return RenderResult(
        html=html,
        latex=latex,
        warnings=[f"Broken status: {ctx.block_status}"],
    )


def _render_legacy_v0_placeholder(snapshot: dict, ctx: RenderContext) -> RenderResult:
    """Для legacy_v0 — best-effort поверхностный render + badge 'устарел'."""
    title_h = html_escape(ctx.title or "(без заголовка)")
    title_l = latex_escape(ctx.title or "(без заголовка)")

    period = snapshot.get("period") or {}
    pf = html_escape(str(period.get("from", "?")))
    pt = html_escape(str(period.get("to", "?")))

    html = (
        f'<div class="obs-block obs-legacy">'
        f'<h3>{title_h}</h3>'
        f'<span class="obs-badge obs-badge-legacy_v0">Устарел (legacy v0)</span>'
        f'<p>Период: {pf} — {pt}</p>'
        f'<p class="obs-warning">Снапшот устарел. Рекомендуется пересчёт.</p>'
        f'</div>'
    )

    pf_l = latex_escape(str(period.get("from", "?")))
    pt_l = latex_escape(str(period.get("to", "?")))
    latex = (
        f'\\subsection*{{{title_l}}}\n'
        f'\\textbf{{[Устарел (legacy v0)]}} Период: {pf_l}~--~{pt_l}. '
        f'Рекомендуется пересчёт снапшота.\n'
    )

    return RenderResult(html=html, latex=latex, warnings=["legacy_v0 block: best-effort render"])


def _render_unknown_kind(kind: Any, ctx: RenderContext) -> RenderResult:
    kind_s = str(kind) if kind is not None else "(None)"
    title_h = html_escape(ctx.title or "(без заголовка)")
    title_l = latex_escape(ctx.title or "(без заголовка)")
    kind_h = html_escape(kind_s)
    kind_l = latex_escape(kind_s)

    html = (
        f'<div class="obs-block obs-unknown">'
        f'<h3>{title_h}</h3>'
        f'<span class="obs-badge obs-badge-unknown">неизвестный тип: {kind_h}</span>'
        f'</div>'
    )
    latex = (
        f'\\subsection*{{{title_l}}}\n'
        f'\\textbf{{[Неизвестный тип блока: {kind_l}]}} Нет обработчика.\n'
    )
    return RenderResult(html=html, latex=latex, warnings=[f"Unknown kind: {kind_s}"])


# ---------------------------------------------------------------------------
# LEGACY wz3obs_v1 — плоская схема блоков "Анализ наблюдения"
# (создаётся wizard'ом через "Прикрепить к отчёту"). RFC-валидатор её не
# знает, поэтому рендерим напрямую из плоского снапшота.
# ---------------------------------------------------------------------------

# (label, ключ_avg, ключ_median) — пара ср./мед.
_WZ3OBS_PAIR_ROWS = [
    ("Q общий, тыс.м³/сут",      "q_total_avg",    "q_total_median"),
    ("Q рабочий, тыс.м³/сут",    "q_working_avg",  "q_working_median"),
    ("ΔP, кгс/см²",              "dp_avg",         "dp_median"),
    ("P устьевое, кгс/см²",      "p_wellhead_avg", "p_wellhead_median"),
    ("P шлейфовое, кгс/см²",     "p_flowline_avg", "p_flowline_median"),
]
# (label, ключ) — одиночные показатели.
_WZ3OBS_SINGLE_ROWS = [
    ("Часов работы",          "working_hours"),
    ("Загрузка, %",           "utilization_pct"),
    ("Дней простоя",          "shutdown_days_count"),
    ("Простой, мин",          "shutdown_min_total"),
    ("Продувок",              "purge_count"),
    ("Вбросов реагента",      "reagent_count"),
    ("Порог ΔP, кгс/см²",     "dp_threshold"),
]


def render_wz3obs_v1_html(snapshot: dict, title: str) -> str:
    """HTML-рендер legacy-блока observation_analysis (схема wz3obs_v1).

    Снапшот — плоский dict метрик. Возвращает один .obs-block с таблицей.
    """
    title_h = html_escape(title or "(без заголовка)")
    snapshot = snapshot or {}
    d_from = html_escape(str(snapshot.get("date_from", "?")))
    d_to = html_escape(str(snapshot.get("date_to", "?")))
    days = snapshot.get("days")

    parts = [
        '<div class="obs-block">',
        f"<h3>{title_h}</h3>",
        f'<p class="obs-period">Период: {d_from} — {d_to}'
        + (f" ({_fmt(days)} сут.)" if days is not None else "")
        + "</p>",
        '<table class="obs-metrics-table">',
        "  <thead><tr><th>Показатель</th><th>Среднее</th>"
        "<th>Медиана</th></tr></thead>",
        "  <tbody>",
    ]
    for label, k_avg, k_med in _WZ3OBS_PAIR_ROWS:
        parts.append(
            f"    <tr><td>{html_escape(label)}</td>"
            f"<td>{_fmt(snapshot.get(k_avg))}</td>"
            f"<td>{_fmt(snapshot.get(k_med))}</td></tr>"
        )
    for label, key in _WZ3OBS_SINGLE_ROWS:
        if snapshot.get(key) is None:
            continue
        parts.append(
            f"    <tr><td>{html_escape(label)}</td>"
            f'<td colspan="2">{_fmt(snapshot.get(key))}</td></tr>'
        )
    parts += ["  </tbody>", "</table>", "</div>"]
    return "\n".join(parts)


def render_analysis(snapshot: dict, ctx: RenderContext) -> RenderResult:
    """Рендер блока observation_analysis с поддержкой 4 отдельных ключей графиков.

    Графики (управляются parts):
        chart_pressures    — P трубное + P линейное
        chart_dp           — Перепад ΔP
        chart_q            — Дебит Q(t)
        chart_utilization  — Рабочее время по дням
    """
    snapshot = snapshot or {}
    title_h = html_escape(ctx.title or "(без заголовка)")
    title_l = latex_escape(ctx.title or "(без заголовка)")
    d_from = html_escape(str(snapshot.get("date_from", "?")))
    d_to = html_escape(str(snapshot.get("date_to", "?")))
    days = snapshot.get("days")

    html_parts = [
        '<div class="obs-block">',
        f"<h3>{title_h}</h3>",
    ]

    # ─── Intro ───
    if _part_on(ctx, "intro"):
        html_parts.append(
            f'<p class="obs-period">Период: {d_from} — {d_to}'
            + (f" ({_fmt(days)} сут.)" if days is not None else "")
            + " · Источник: данные датчиков давления.</p>"
        )

    # ─── Metrics table ───
    if _part_on(ctx, "metrics_table"):
        html_parts.append('<table class="obs-metrics-table">')
        html_parts.append(
            "  <thead><tr><th>Показатель</th><th>Среднее</th>"
            "<th>Медиана</th></tr></thead>"
        )
        html_parts.append("  <tbody>")
        for label, k_avg, k_med in _WZ3OBS_PAIR_ROWS:
            html_parts.append(
                f"    <tr><td>{html_escape(label)}</td>"
                f"<td>{_fmt(snapshot.get(k_avg))}</td>"
                f"<td>{_fmt(snapshot.get(k_med))}</td></tr>"
            )
        for label, key in _WZ3OBS_SINGLE_ROWS:
            if snapshot.get(key) is None:
                continue
            html_parts.append(
                f"    <tr><td>{html_escape(label)}</td>"
                f'<td colspan="2">{_fmt(snapshot.get(key))}</td></tr>'
            )
        html_parts.extend(["  </tbody>", "</table>"])

    # ─── 4 графика (заглушки для HTML — Plotly рендерится JS) ───
    # В HTML-preview графики рендерятся клиентским JS (chapter_render.js)
    # Здесь только контейнеры если skip_figures=False (PDF mode)
    charts_shown = 0
    for chart_key, chart_title in [
        ("chart_pressures", "Давления (P трубное, P линейное)"),
        ("chart_dp", "Перепад ΔP"),
        ("chart_q", "Дебит Q(t)"),
        ("chart_utilization", "Рабочее время по дням"),
    ]:
        if _part_on(ctx, chart_key):
            charts_shown += 1
            # В HTML-preview отображается placeholder, Plotly рендерится JS
            html_parts.append(
                f'<div class="obs-chart-placeholder" data-chart="{chart_key}">'
                f'<span class="obs-chart-label">[График: {html_escape(chart_title)}]</span>'
                f'</div>'
            )

    # ─── Quality ───
    q = snapshot.get("quality") or {}
    if _part_on(ctx, "quality") and q:
        cov = q.get("coverage_pct")
        cov_str = f"{_fmt(cov)}%" if cov is not None else "—"
        html_parts.append(
            f'<p class="obs-quality">Качество данных: покрытие {cov_str}</p>'
        )

    # ─── Flags ───
    flags = snapshot.get("flags") or {}
    if _part_on(ctx, "flags") and flags:
        html_parts.append(_render_flags_list(flags))

    # ─── Description ───
    desc = snapshot.get("description") or ""
    if _part_on(ctx, "description") and desc.strip():
        html_parts.append(
            f'<div class="obs-description">{html_escape(desc)}</div>'
        )

    html_parts.append("</div>")
    html = "\n".join(html_parts)

    # ─── LaTeX ───
    latex_parts = [f"\\subsection*{{{title_l}}}"]

    if _part_on(ctx, "intro"):
        d_from_l = latex_escape(str(snapshot.get("date_from", "?")))
        d_to_l = latex_escape(str(snapshot.get("date_to", "?")))
        latex_parts.append(
            f"Период: {d_from_l} — {d_to_l}"
            + (f" ({_fmt(days)} сут.)" if days is not None else "")
            + " · Источник: данные датчиков давления.\n"
        )

    if _part_on(ctx, "metrics_table"):
        latex_parts.append("\\begin{tabular}{lrr}")
        latex_parts.append("\\toprule")
        latex_parts.append("Показатель & Среднее & Медиана \\\\")
        latex_parts.append("\\midrule")
        for label, k_avg, k_med in _WZ3OBS_PAIR_ROWS:
            latex_parts.append(
                f"{latex_escape(label)} & {_fmt(snapshot.get(k_avg))} & {_fmt(snapshot.get(k_med))} \\\\"
            )
        latex_parts.append("\\bottomrule")
        latex_parts.append("\\end{tabular}\n")

    # ─── Figures (4 графика) ───
    figures: list[FigureRef] = []
    if not ctx.skip_figures:
        from backend.services.observation_chart_renderer import (
            render_analysis_pressures_chart,
            render_analysis_dp_chart,
            render_analysis_q_chart,
            render_analysis_utilization_chart,
        )

        chart_renderers = [
            ("chart_pressures",   "analysis_pressures",   render_analysis_pressures_chart,
             "Давления (P трубное, P линейное)"),
            ("chart_dp",          "analysis_dp",          render_analysis_dp_chart,
             "Перепад ΔP"),
            ("chart_q",           "analysis_q",           render_analysis_q_chart,
             "Дебит Q(t)"),
            ("chart_utilization", "analysis_utilization", render_analysis_utilization_chart,
             "Рабочее время по дням"),
        ]

        for part_key, stem_suffix, render_fn, caption in chart_renderers:
            if not _part_on(ctx, part_key):
                continue
            stem = _figure_stem(ctx, stem_suffix)
            abs_path = os.path.join(ctx.output_dir, f"{stem}.png")
            rel_path = f"observation_charts/{stem}.png"
            try:
                render_fn(snapshot, abs_path)
                fig_ref = FigureRef(
                    relative_path=rel_path,
                    absolute_path=abs_path,
                    caption=latex_escape(f"{caption}: {ctx.title}"),
                    width_pt=360.0,
                )
                figures.append(fig_ref)
                latex_parts.append(_latex_includegraphics(fig_ref))
            except Exception as exc:
                latex_parts.append(f"% [figure error: {latex_escape(str(exc))}]")

    latex = "\n".join(latex_parts)

    return RenderResult(html=html, latex=latex, figures=figures)


# ---------------------------------------------------------------------------
# HTML COMPONENT HELPERS (из STUB, сохранены без изменений для backward-compat)
# ---------------------------------------------------------------------------


def _render_quality_badge(quality: dict) -> str:
    if not isinstance(quality, dict):
        return ""
    status = html_escape(str(quality.get("status", "unknown")))
    q_metrics = quality.get("metrics") or {}
    cov = q_metrics.get("coverage_pct")
    cov_str = f" ({_fmt(cov)}%)" if cov is not None else ""
    label_map = {
        "ok":         "Данные: норма",
        "sparse":     "Данные: редкие",
        "gap":        "Данные: пропуск",
        "suspicious": "Данные: подозрительные",
        "no_data":    "Данные: нет",
    }
    label = html_escape(label_map.get(quality.get("status", ""), f"Данные: {quality.get('status', '')}"))
    return f'<span class="badge badge-status-{status}">{label}{cov_str}</span>'


def _render_metrics_table(metrics: dict) -> str:
    if not metrics:
        return '<p class="obs-no-data">Метрики отсутствуют</p>'

    metric_keys = [
        ("p_tube", "Давление устья (атм)"),
        ("p_line", "Давление шлейфа (атм)"),
        ("dp",     "ΔP (атм)"),
        ("q",      "Дебит Q (тыс.м³/сут)"),
    ]

    rows: list[str] = [
        '<table class="obs-metrics-table">',
        "  <thead><tr>",
        "    <th>Показатель</th><th>Среднее</th><th>Медиана</th>"
        "<th>Мин</th><th>Макс</th><th>Std</th><th>Тренд</th>",
        "  </tr></thead>",
        "  <tbody>",
    ]

    for key, label in metric_keys:
        m = metrics.get(key)
        if not isinstance(m, dict):
            continue
        rows.append(
            f"    <tr>"
            f"<td>{html_escape(label)}</td>"
            f"<td>{_fmt(m.get('mean'))}</td>"
            f"<td>{_fmt(m.get('median'))}</td>"
            f"<td>{_fmt(m.get('min'))}</td>"
            f"<td>{_fmt(m.get('max'))}</td>"
            f"<td>{_fmt(m.get('std'))}</td>"
            f"<td>{html_escape(str(m.get('direction', '—')))}</td>"
            f"</tr>"
        )

    downtime = metrics.get("downtime")
    if isinstance(downtime, dict):
        rows.append(
            f"    <tr>"
            f"<td>Простой (ч / %)</td>"
            f"<td>{_fmt(downtime.get('total_hours'))}</td>"
            f"<td>—</td><td>—</td>"
            f"<td>{_fmt(downtime.get('downtime_pct_of_period'))}%</td>"
            f"<td>—</td><td>—</td>"
            f"</tr>"
        )

    rows += ["  </tbody>", "</table>"]
    return "\n".join(rows)


def _render_diagnostics_list(diagnostics: list) -> str:
    if not diagnostics:
        return ""

    verdict_labels = {
        "improvement":           "Улучшение",
        "degradation":           "Деградация",
        "no_significant_change": "Изменений нет",
        "insufficient_data":     "Недостаточно данных",
        "match":                 "Совпадение с заказчиком",
        "partial_match":         "Частичное совпадение",
        "diverge":               "Расхождение",
        "rising":                "Рост",
        "falling":               "Снижение",
        "stable":                "Стабильно",
        "detected":              "Обнаружен перелом",
        "borderline":            "Пограничный перелом",
    }

    items: list[str] = ['<ul class="obs-diagnostics-list">']
    for entry in diagnostics:
        if not isinstance(entry, dict):
            continue
        target  = html_escape(str(entry.get("target", "?")))
        context = html_escape(str(entry.get("context", "?")))
        verdict = str(entry.get("verdict", ""))
        label   = html_escape(verdict_labels.get(verdict, verdict))
        mag     = entry.get("magnitude")
        mag_str = ""
        if isinstance(mag, dict):
            parts_m = []
            if mag.get("abs") is not None:
                parts_m.append(f"abs={_fmt(mag.get('abs'))}")
            if mag.get("pct") is not None:
                parts_m.append(f"pct={_fmt(mag.get('pct'))}%")
            if mag.get("mape") is not None:
                parts_m.append(f"MAPE={_fmt(mag.get('mape'))}%")
            if parts_m:
                mag_str = f" ({', '.join(parts_m)})"
        items.append(f"  <li><b>{target}</b> [{context}]: {label}{html_escape(mag_str)}</li>")
    items.append("</ul>")
    return "\n".join(items)


def _render_flags_list(flags: dict) -> str:
    if not isinstance(flags, dict):
        return ""
    active = [k for k, v in flags.items() if v is True]
    if not active:
        return ""
    flag_labels = {
        "low_coverage":              "Низкое покрытие",
        "significant_gap":           "Значительный пропуск",
        "outlier_detected":          "Выброс в данных",
        "short_intersection":        "Мало дней с заказчиком",
        "baseline_mismatch_period":  "Baseline устарел (>90 дней)",
        "outdated_baseline_version": "Устаревший baseline",
        "invalid_comparison":        "Некорректное сравнение",
    }
    items = ['<ul class="obs-flags-list">']
    for k in active:
        label = html_escape(flag_labels.get(k, k))
        items.append(f'  <li class="obs-flag obs-flag--active">{label}</li>')
    items.append("</ul>")
    return "\n".join(items)


# g-fix-4: маппинг direction → человекочитаемый label.
_DIRECTION_LABEL = {
    "rising":            "рост",
    "falling":           "снижение",
    "stable":            "стабильно",
    "insufficient_data": "нет данных",
}


def _render_segments_table(segments: list) -> str:
    """g-fix-4: таблица сегментов с реальными ключами C2.

    Реальные ключи (observation_segment_service.py:720-733):
        num, start_date, end_date, duration_days, direction,
        mean_q, mean_dp, mean_p_tube, mean_p_line, slope_q_per_day.
    """
    if not segments:
        return '<p class="obs-no-data">Сегменты не обнаружены</p>'

    rows = [
        '<table class="obs-segments-table">',
        "  <thead><tr>",
        "    <th>#</th><th>Начало</th><th>Конец</th><th>Дни</th>"
        "<th>Направление</th><th>Q</th><th>P_шл</th><th>P_уст</th><th>ΔP</th><th>Наклон</th>",
        "  </tr></thead>",
        "  <tbody>",
    ]
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        num = seg.get("num", "?")
        direction = str(seg.get("direction", "—"))
        direction_lbl = _DIRECTION_LABEL.get(direction, direction)
        rows.append(
            f"    <tr>"
            f"<td>{html_escape(str(num))}</td>"
            f"<td>{html_escape(str(seg.get('start_date', '?')))}</td>"
            f"<td>{html_escape(str(seg.get('end_date', '?')))}</td>"
            f"<td>{html_escape(str(seg.get('duration_days', '?')))}</td>"
            f"<td>{html_escape(direction_lbl)}</td>"
            f"<td>{_fmt(seg.get('mean_q'))}</td>"
            f"<td>{_fmt(seg.get('mean_p_flowline'))}</td>"
            f"<td>{_fmt(seg.get('mean_p_wellhead'))}</td>"
            f"<td>{_fmt(seg.get('mean_dp'))}</td>"
            f"<td>{_fmt(seg.get('slope_q_per_day'))}</td>"
            f"</tr>"
        )
    rows += ["  </tbody>", "</table>"]
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# LATEX COMPONENT HELPERS
# ---------------------------------------------------------------------------


def _latex_metrics_tabular(metrics: dict) -> str:
    """Генерирует LaTeX tabular для метрик."""
    if not metrics:
        return r"\textit{Метрики отсутствуют.}"

    metric_keys = [
        ("p_tube", r"$P_{\text{тр}}$, атм"),
        ("p_line", r"$P_{\text{лин}}$, атм"),
        ("dp",     r"$\Delta P$, атм"),
        ("q",      r"$Q$, тыс.м$^3$/сут"),
    ]

    rows = [
        r"{\footnotesize",
        r"\begin{tabular}{lrrrrrr}",
        r"\toprule",
        r"\textbf{Параметр} & \textbf{Среднее} & \textbf{Медиана} & \textbf{Мин} & \textbf{Макс} & \textbf{Std} & \textbf{Тренд} \\",
        r"\midrule",
    ]

    for key, label in metric_keys:
        m = metrics.get(key)
        if not isinstance(m, dict):
            continue
        direction = latex_escape(str(m.get("direction", "---")))
        rows.append(
            f"{label} & {_fmt_latex(m.get('mean'))} & {_fmt_latex(m.get('median'))} "
            f"& {_fmt_latex(m.get('min'))} & {_fmt_latex(m.get('max'))} "
            f"& {_fmt_latex(m.get('std'))} & {direction} \\\\"
        )

    downtime = metrics.get("downtime")
    if isinstance(downtime, dict):
        rows.append(
            f"Простой, ч / \\% & {_fmt_latex(downtime.get('total_hours'))} & --- "
            f"& --- & {_fmt_latex(downtime.get('downtime_pct_of_period'))}\\% & --- & --- \\\\"
        )

    rows += [r"\bottomrule", r"\end{tabular}", r"}"]
    return "\n".join(rows)


def _latex_quality_line(quality: dict) -> str:
    if not isinstance(quality, dict):
        return ""
    status = quality.get("status", "unknown")
    label_map = {
        "ok":         "норма",
        "sparse":     "редкие данные",
        "gap":        "пропуск данных",
        "suspicious": "подозрительные данные",
        "no_data":    "нет данных",
    }
    label = label_map.get(status, status)
    q_metrics = quality.get("metrics") or {}
    cov = q_metrics.get("coverage_pct")
    cov_str = f" (покрытие: {_fmt_latex(cov)}\\%)" if cov is not None else ""
    return f"\\textit{{Качество данных: {latex_escape(label)}{cov_str}}}"


def _latex_flags_itemize(flags: dict) -> str:
    if not isinstance(flags, dict):
        return ""
    active = [k for k, v in flags.items() if v is True]
    if not active:
        return ""
    flag_labels = {
        "low_coverage":              "Низкое покрытие",
        "significant_gap":           "Значительный пропуск",
        "outlier_detected":          "Выброс в данных",
        "short_intersection":        "Мало дней с заказчиком",
        "baseline_mismatch_period":  "Baseline устарел (>90 дней)",
        "outdated_baseline_version": "Устаревший baseline",
        "invalid_comparison":        "Некорректное сравнение",
    }
    lines = [r"\begin{itemize}\setlength{\itemsep}{-2pt}"]
    for k in active:
        label = latex_escape(flag_labels.get(k, k))
        lines.append(f"  \\item {label}")
    lines.append(r"\end{itemize}")
    return "\n".join(lines)


def _latex_diagnostics_itemize(diagnostics: list) -> str:
    if not diagnostics:
        return ""
    verdict_labels = {
        "improvement":           "Улучшение",
        "degradation":           "Деградация",
        "no_significant_change": "Изменений нет",
        "insufficient_data":     "Недостаточно данных",
        "match":                 "Совпадение с заказчиком",
        "partial_match":         "Частичное совпадение",
        "diverge":               "Расхождение",
        "rising":                "Рост",
        "falling":               "Снижение",
        "stable":                "Стабильно",
        "detected":              "Обнаружен перелом",
        "borderline":            "Пограничный перелом",
    }
    lines = [r"\begin{itemize}\setlength{\itemsep}{-2pt}"]
    for entry in diagnostics:
        if not isinstance(entry, dict):
            continue
        target  = latex_escape(str(entry.get("target", "?")))
        context = latex_escape(str(entry.get("context", "?")))
        verdict = str(entry.get("verdict", ""))
        label   = latex_escape(verdict_labels.get(verdict, verdict))
        mag     = entry.get("magnitude")
        mag_str = ""
        if isinstance(mag, dict):
            parts_m = []
            if mag.get("abs") is not None:
                parts_m.append(f"abs={_fmt_latex(mag.get('abs'))}")
            if mag.get("pct") is not None:
                parts_m.append(f"pct={_fmt_latex(mag.get('pct'))}\\%")
            if mag.get("mape") is not None:
                parts_m.append(f"MAPE={_fmt_latex(mag.get('mape'))}\\%")
            if parts_m:
                mag_str = f" ({', '.join(parts_m)})"
        lines.append(f"  \\item \\textbf{{{target}}} [{context}]: {label}{mag_str}")
    lines.append(r"\end{itemize}")
    return "\n".join(lines)


def _latex_segments_tabular(segments: list) -> str:
    """g-fix-4: LaTeX-таблица сегментов с реальными ключами C2 (parity с HTML)."""
    if not segments:
        return r"\textit{Сегменты не обнаружены.}"

    rows = [
        r"{\footnotesize",
        r"\begin{tabular}{cllrlrrrr}",
        r"\toprule",
        r"\textbf{\#} & \textbf{Начало} & \textbf{Конец} & \textbf{Дни} "
        r"& \textbf{Направление} & \textbf{$Q$} & \textbf{$P_\text{шл}$} & \textbf{$P_\text{уст}$} & \textbf{$\Delta P$} & \textbf{Наклон} \\",
        r"\midrule",
    ]
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        num = seg.get("num", "?")
        direction = str(seg.get("direction", "---"))
        direction_lbl = _DIRECTION_LABEL.get(direction, direction)
        rows.append(
            f"{latex_escape(str(num))} "
            f"& {latex_escape(str(seg.get('start_date', '?')))} "
            f"& {latex_escape(str(seg.get('end_date', '?')))} "
            f"& {latex_escape(str(seg.get('duration_days', '?')))} "
            f"& {latex_escape(direction_lbl)} "
            f"& {_fmt_latex(seg.get('mean_q'))} "
            f"& {_fmt_latex(seg.get('mean_p_flowline'))} "
            f"& {_fmt_latex(seg.get('mean_p_wellhead'))} "
            f"& {_fmt_latex(seg.get('mean_dp'))} "
            f"& {_fmt_latex(seg.get('slope_q_per_day'))} \\\\"
        )
    rows += [r"\bottomrule", r"\end{tabular}", r"}"]
    return "\n".join(rows)


def _latex_changepoints_tabular(changepoints: list) -> str:
    if not changepoints:
        return ""
    rows = [
        r"{\footnotesize",
        r"\begin{tabular}{lrr}",
        r"\toprule",
        r"\textbf{Дата} & \textbf{Величина, \%} & \textbf{Уверенность} \\",
        r"\midrule",
    ]
    for cp in changepoints:
        if not isinstance(cp, dict):
            continue
        cp_date = latex_escape(str(cp.get("date") or cp.get("changepoint_date") or "?"))
        mag = _fmt_latex(cp.get("magnitude_pct"))
        conf = _fmt_latex(cp.get("confidence"))
        rows.append(f"{cp_date} & {mag} & {conf} \\\\")
    rows += [r"\bottomrule", r"\end{tabular}", r"}"]
    return "\n".join(rows)


def _latex_includegraphics(fig: FigureRef) -> str:
    """Генерирует LaTeX includegraphics с подписью."""
    return (
        f"\\begin{{center}}\n"
        f"\\includegraphics[width={fig.width_pt}pt,keepaspectratio]{{{latex_escape(fig.absolute_path)}}}\n"
        f"\\end{{center}}\n"
        f"{{\\scriptsize {fig.caption}}}\n"
    )


# ---------------------------------------------------------------------------
# PER-KIND RENDERERS
# ---------------------------------------------------------------------------


def render_baseline(snapshot: dict, ctx: RenderContext) -> RenderResult:
    """Рендерит блок observation_baseline (HTML + LaTeX + 1 figure)."""
    period = snapshot.get("period") or {}
    metrics = snapshot.get("metrics") or {}
    quality = snapshot.get("quality") or {}
    flags = snapshot.get("flags") or {}

    pf = str(period.get("from", "?"))
    pt = str(period.get("to", "?"))
    title_h = html_escape(ctx.title or "(без заголовка)")
    title_l = latex_escape(ctx.title or "(без заголовка)")

    # ── HTML (parts-aware) ──
    html_parts = [
        f'<section class="obs-block obs-baseline">',
        f'<h3>{title_h}</h3>',
    ]
    meta_inner: list[str] = []
    if _part_on(ctx, "intro"):
        meta_inner.append(
            f'  <span class="obs-period">Период: '
            f'{html_escape(pf)} — {html_escape(pt)}</span>'
        )
    if _part_on(ctx, "quality"):
        meta_inner.append(f'  {_render_quality_badge(quality)}')
    if meta_inner:
        html_parts.append('<div class="obs-block__meta">')
        html_parts.extend(meta_inner)
        html_parts.append('</div>')
    if _part_on(ctx, "metrics_table"):
        html_parts.append(_render_metrics_table(metrics))

    # ── Plotly chart container (для клиентского рендеринга bar chart метрик) ──
    if _part_on(ctx, "chart_metrics"):
        import json
        block_id = ctx.block_id or "x"
        raw = snapshot.get("raw") or {}
        chart_payload = raw.get("chart_payload") or {}
        # Формируем данные для Plotly-рендера на клиенте
        chart_json = {
            "metrics": metrics,
            "raw": {"chart_payload": chart_payload},
        }
        chart_json_str = json.dumps(chart_json, ensure_ascii=False, default=str)
        chart_json_escaped = html_escape(chart_json_str, quote=True)
        html_parts.append(
            f'<div id="obs-baseline-chart-{block_id}" class="obs-baseline-chart-container" '
            f'style="height:280px; margin:12px 0; background:#fafafa; border-radius:6px;" '
            f'data-snapshot="{chart_json_escaped}">'
            '<span style="color:#9ca3af;padding:15px;display:block;text-align:center;">'
            'Загрузка графика...</span>'
            '</div>'
        )

    if _part_on(ctx, "flags"):
        html_parts.append(_render_flags_list(flags))
    if _part_on(ctx, "description"):
        desc_html = _render_description_html(snapshot)
        if desc_html:
            html_parts.append(desc_html)
    html_parts.append('</section>')
    html = "\n".join(html_parts)

    # ── LaTeX (parts-aware, parity с HTML) ──
    pf_l = latex_escape(pf)
    pt_l = latex_escape(pt)
    latex_parts = [f"\\subsection*{{{title_l}}}"]
    if _part_on(ctx, "intro"):
        latex_parts += [f"\\textit{{Период: {pf_l}~--~{pt_l}}}", ""]
    if _part_on(ctx, "quality"):
        latex_parts += [_latex_quality_line(quality), ""]
    if _part_on(ctx, "metrics_table"):
        latex_parts += [_latex_metrics_tabular(metrics), ""]
    if _part_on(ctx, "flags"):
        latex_parts += [_latex_flags_itemize(flags)]
    if _part_on(ctx, "description"):
        desc_l = _render_description_latex(snapshot)
        if desc_l:
            latex_parts += ["", desc_l]
    latex_str = "\n".join(latex_parts)

    # ── Figures ──
    figures: list[FigureRef] = []
    if not ctx.skip_figures and _part_on(ctx, "chart_metrics"):
        stem = _figure_stem(ctx, "baseline_metrics")
        abs_path = os.path.join(ctx.output_dir, f"{stem}.png")
        rel_path = f"observation_charts/{stem}.png"
        try:
            from backend.services.observation_chart_renderer import render_baseline_chart
            render_baseline_chart(snapshot, abs_path)
            fig_ref = FigureRef(
                relative_path=rel_path,
                absolute_path=abs_path,
                caption=latex_escape(f"Метрики baseline: {ctx.title}"),
                width_pt=360.0,
            )
            figures.append(fig_ref)
            latex_str += "\n\n" + _latex_includegraphics(fig_ref)
        except Exception as exc:
            latex_str += f"\n% [figure error: {latex_escape(str(exc))}]\n"

    return RenderResult(html=html, latex=latex_str, figures=figures)


def render_period(snapshot: dict, ctx: RenderContext) -> RenderResult:
    """Рендерит блок observation_period (HTML + LaTeX + 2-3 figures)."""
    period = snapshot.get("period") or {}
    metrics = snapshot.get("metrics") or {}
    quality = snapshot.get("quality") or {}
    diagnostics = snapshot.get("diagnostics") or []
    flags = snapshot.get("flags") or {}
    comparisons = snapshot.get("comparisons") or {}

    pf = str(period.get("from", "?"))
    pt = str(period.get("to", "?"))
    title_h = html_escape(ctx.title or "(без заголовка)")
    title_l = latex_escape(ctx.title or "(без заголовка)")

    with_b1 = comparisons.get("with_b1") or {}
    b1_status = with_b1.get("status", "")

    # ── B1 comparison HTML ──
    if b1_status == "ok":
        b1_badge = '<span class="badge badge-status-ok">baseline: сравнение выполнено</span>'
        b1_section = _render_b1_deltas_table(with_b1, diagnostics)
    elif b1_status == "no_baseline":
        b1_badge = '<span class="badge badge-status-partial">без baseline</span>'
        b1_section = '<p class="obs-no-data">Baseline не выбран.</p>'
    elif b1_status == "baseline_corrupted":
        b1_badge = '<span class="badge badge-status-corrupted">baseline недоступен</span>'
        b1_section = '<p class="obs-no-data">Baseline повреждён.</p>'
    else:
        b1_badge = ""
        b1_section = ""

    # ── Customer comparison ──
    with_customer = comparisons.get("with_customer") or {}

    # ── HTML (parts-aware) ──
    html_parts = [
        f'<section class="obs-block obs-period">',
        f'<h3>{title_h}</h3>',
    ]
    meta_inner: list[str] = []
    if _part_on(ctx, "intro"):
        meta_inner.append(
            f'  <span class="obs-period">Период: '
            f'{html_escape(pf)} — {html_escape(pt)}</span>'
        )
        meta_inner.append(f'  {_render_quality_badge(quality)}')
    if _part_on(ctx, "comparison_with_b1") and b1_badge:
        meta_inner.append(f'  {b1_badge}')
    if meta_inner:
        html_parts.append('<div class="obs-block__meta">')
        html_parts.extend(meta_inner)
        html_parts.append('</div>')
    if _part_on(ctx, "metrics_table"):
        html_parts.append(_render_metrics_table(metrics))
    if _part_on(ctx, "comparison_with_b1"):
        html_parts.append(b1_section)
    if _part_on(ctx, "comparison_with_customer"):
        html_parts.append(_render_customer_comparison(
            with_customer, show_daily_table=_part_on(ctx, "daily_table"),
        ))
    if _part_on(ctx, "diagnostics"):
        html_parts.append(_render_diagnostics_list(diagnostics))
    if _part_on(ctx, "flags"):
        html_parts.append(_render_flags_list(flags))
    if _part_on(ctx, "description"):
        desc_html = _render_description_html(snapshot)
        if desc_html:
            html_parts.append(desc_html)
    html_parts.append('</section>')
    html = "\n".join(p for p in html_parts if p)

    # ── LaTeX (parts-aware, parity с HTML) ──
    pf_l = latex_escape(pf)
    pt_l = latex_escape(pt)
    latex_parts = [f"\\subsection*{{{title_l}}}"]
    if _part_on(ctx, "intro"):
        latex_parts += [
            f"\\textit{{Период: {pf_l}~--~{pt_l}}}",
            "",
            _latex_quality_line(quality),
            "",
        ]
    if _part_on(ctx, "metrics_table"):
        latex_parts += [_latex_metrics_tabular(metrics), ""]

    if _part_on(ctx, "comparison_with_b1") and b1_status == "ok":
        latex_parts += [
            r"\vspace{0.3em}",
            r"{\footnotesize \textbf{Сравнение с baseline (B1):}}",
            "",
            _latex_b1_deltas_tabular(with_b1, diagnostics),
            "",
        ]

    # g-fix-3: LaTeX customer comparison (раньше отсутствовал)
    if _part_on(ctx, "comparison_with_customer") and with_customer:
        latex_parts += [
            r"\vspace{0.3em}",
            _latex_customer_comparison_tabular(
                with_customer, show_daily_table=_part_on(ctx, "daily_table"),
            ),
            "",
        ]

    if _part_on(ctx, "diagnostics") and diagnostics:
        latex_parts += [
            r"{\footnotesize \textbf{Диагностика:}}",
            _latex_diagnostics_itemize(diagnostics),
            "",
        ]

    if _part_on(ctx, "flags"):
        latex_parts += [_latex_flags_itemize(flags)]
    if _part_on(ctx, "description"):
        desc_l = _render_description_latex(snapshot)
        if desc_l:
            latex_parts += ["", desc_l]
    latex_str = "\n".join(latex_parts)

    # ── Figures ──
    figures: list[FigureRef] = []
    raw = snapshot.get("raw") or {}
    chart_payload = raw.get("chart_payload") or {}
    has_chart_data = bool(chart_payload.get("dates"))

    if not ctx.skip_figures:
        from backend.services.observation_chart_renderer import (
            render_period_timeseries_chart,
            render_period_compare_b1_chart,
        )

        # Figure 1: time series (chart_timeseries)
        if has_chart_data and _part_on(ctx, "chart_timeseries"):
            stem_ts = _figure_stem(ctx, "period_timeseries")
            abs_ts = os.path.join(ctx.output_dir, f"{stem_ts}.png")
            rel_ts = f"observation_charts/{stem_ts}.png"
            try:
                render_period_timeseries_chart(snapshot, abs_ts)
                fig_ts = FigureRef(
                    relative_path=rel_ts,
                    absolute_path=abs_ts,
                    caption=latex_escape(f"Q и ΔP за период: {ctx.title}"),
                    width_pt=360.0,
                )
                figures.append(fig_ts)
                latex_str += "\n\n" + _latex_includegraphics(fig_ts)
            except Exception as exc:
                latex_str += f"\n% [timeseries figure error: {latex_escape(str(exc))}]\n"

        # Figure 2: compare B1 bar chart (chart_compare_b1)
        if b1_status == "ok" and _part_on(ctx, "chart_compare_b1"):
            stem_cmp = _figure_stem(ctx, "period_compare_b1")
            abs_cmp = os.path.join(ctx.output_dir, f"{stem_cmp}.png")
            rel_cmp = f"observation_charts/{stem_cmp}.png"
            try:
                render_period_compare_b1_chart(snapshot, abs_cmp)
                fig_cmp = FigureRef(
                    relative_path=rel_cmp,
                    absolute_path=abs_cmp,
                    caption=latex_escape(f"Сравнение с baseline: {ctx.title}"),
                    width_pt=360.0,
                )
                figures.append(fig_cmp)
                latex_str += "\n\n" + _latex_includegraphics(fig_cmp)
            except Exception as exc:
                latex_str += f"\n% [compare_b1 figure error: {latex_escape(str(exc))}]\n"

    return RenderResult(html=html, latex=latex_str, figures=figures)


def render_segment(snapshot: dict, ctx: RenderContext) -> RenderResult:
    """Рендерит блок observation_segment (HTML + LaTeX + 1-2 figures)."""
    period = snapshot.get("period") or {}
    quality = snapshot.get("quality") or {}
    diagnostics = snapshot.get("diagnostics") or []
    flags = snapshot.get("flags") or {}
    segments = snapshot.get("segments") or []
    changepoints = snapshot.get("changepoints") or []
    thresholds = snapshot.get("thresholds_used") or {}
    shutdown_clusters = snapshot.get("shutdown_clusters") or []

    pf = str(period.get("from", "?"))
    pt = str(period.get("to", "?"))
    title_h = html_escape(ctx.title or "(без заголовка)")
    title_l = latex_escape(ctx.title or "(без заголовка)")

    # ── Thresholds summary HTML ──
    thresholds_html = ""
    if thresholds:
        parts_t = []
        for k, v in thresholds.items():
            parts_t.append(f"{html_escape(str(k))}: {html_escape(str(v))}")
        thresholds_html = f'<p class="obs-thresholds">Параметры: {", ".join(parts_t)}</p>'

    # ── Changepoints HTML ──
    changepoints_html = _render_changepoints_list(changepoints)

    # ── Shutdown clusters HTML ──
    shutdown_html = ""
    if shutdown_clusters:
        items_sc = ["<ul class='obs-shutdown-clusters'>"]
        for sc in shutdown_clusters[:10]:  # не более 10 строк
            if isinstance(sc, dict):
                items_sc.append(
                    f"<li>{html_escape(str(sc.get('from', '?')))} — "
                    f"{html_escape(str(sc.get('to', '?')))}, "
                    f"продолжительность {html_escape(str(sc.get('duration_days', '?')))} сут.</li>"
                )
        items_sc.append("</ul>")
        shutdown_html = "\n".join(items_sc)

    # ── HTML (parts-aware) ──
    html_parts = [
        f'<section class="obs-block obs-segment">',
        f'<h3>{title_h}</h3>',
    ]
    if _part_on(ctx, "intro"):
        html_parts += [
            '<div class="obs-block__meta">',
            f'  <span class="obs-period">Период: '
            f'{html_escape(pf)} — {html_escape(pt)}</span>',
            f'  {_render_quality_badge(quality)}',
            f'  <span class="obs-seg-count">Сегментов: {len(segments)}</span>',
            '</div>',
        ]
        if thresholds_html:
            html_parts.append(thresholds_html)

    # ── Plotly chart container (для клиентского рендеринга) ──
    if _part_on(ctx, "chart_segments"):
        import json
        block_id = ctx.block_id or "x"
        raw = snapshot.get("raw") or {}
        chart_payload = raw.get("chart_payload") or {}
        # Формируем данные для Plotly-рендера на клиенте
        chart_json = {
            "raw": {"chart_payload": chart_payload},
            "segments": segments,
            "changepoints": changepoints,
        }
        chart_json_str = json.dumps(chart_json, ensure_ascii=False, default=str)
        chart_json_escaped = html_escape(chart_json_str, quote=True)
        html_parts.append(
            f'<div id="obs-seg-chart-{block_id}" class="obs-segment-chart-container" '
            f'style="height:350px; margin:12px 0; background:#fafafa; border-radius:6px;" '
            f'data-snapshot="{chart_json_escaped}">'
            '<span style="color:#9ca3af;padding:15px;display:block;text-align:center;">'
            'Загрузка графика сегментов...</span>'
            '</div>'
        )

    if _part_on(ctx, "segments_table"):
        html_parts.append(_render_segments_table(segments))
    if _part_on(ctx, "changepoints") and changepoints_html:
        html_parts.append(changepoints_html)
    # shutdown_clusters — рендерится всегда (ТЗ не выделяет в parts).
    if shutdown_html:
        html_parts.append(shutdown_html)
    if _part_on(ctx, "diagnostics"):
        html_parts.append(_render_diagnostics_list(diagnostics))
    if _part_on(ctx, "flags"):
        html_parts.append(_render_flags_list(flags))
    if _part_on(ctx, "description"):
        desc_html = _render_description_html(snapshot)
        if desc_html:
            html_parts.append(desc_html)
    html_parts.append('</section>')
    html = "\n".join(p for p in html_parts if p)

    # ── LaTeX (parts-aware, parity с HTML) ──
    pf_l = latex_escape(pf)
    pt_l = latex_escape(pt)
    latex_parts = [f"\\subsection*{{{title_l}}}"]
    if _part_on(ctx, "intro"):
        latex_parts += [
            f"\\textit{{Период: {pf_l}~--~{pt_l}, сегментов: {len(segments)}}}",
            "",
            _latex_quality_line(quality),
            "",
        ]
    if _part_on(ctx, "segments_table"):
        latex_parts += [_latex_segments_tabular(segments), ""]

    if _part_on(ctx, "changepoints") and changepoints:
        latex_parts += [
            r"{\footnotesize \textbf{Переломные точки:}}",
            _latex_changepoints_tabular(changepoints),
            "",
        ]

    if _part_on(ctx, "diagnostics") and diagnostics:
        latex_parts += [
            r"{\footnotesize \textbf{Диагностика:}}",
            _latex_diagnostics_itemize(diagnostics),
            "",
        ]

    if _part_on(ctx, "flags"):
        latex_parts += [_latex_flags_itemize(flags)]
    if _part_on(ctx, "description"):
        desc_l = _render_description_latex(snapshot)
        if desc_l:
            latex_parts += ["", desc_l]
    latex_str = "\n".join(latex_parts)

    # ── Figures ──
    figures: list[FigureRef] = []
    if not ctx.skip_figures and _part_on(ctx, "chart_segments"):
        from backend.services.observation_chart_renderer import render_segment_chart

        stem_seg = _figure_stem(ctx, "segment_chart")
        abs_seg = os.path.join(ctx.output_dir, f"{stem_seg}.png")
        rel_seg = f"observation_charts/{stem_seg}.png"
        try:
            render_segment_chart(snapshot, abs_seg)
            fig_seg = FigureRef(
                relative_path=rel_seg,
                absolute_path=abs_seg,
                caption=latex_escape(f"Сегментный анализ Q: {ctx.title}"),
                width_pt=360.0,
            )
            figures.append(fig_seg)
            latex_str += "\n\n" + _latex_includegraphics(fig_seg)
        except Exception as exc:
            latex_str += f"\n% [segment figure error: {latex_escape(str(exc))}]\n"

    return RenderResult(html=html, latex=latex_str, figures=figures)


# ---------------------------------------------------------------------------
# HTML HELPERS ДЛЯ PERIOD
# ---------------------------------------------------------------------------


# g-fix-2: маппинг ключей C1 deltas → diagnostics target.
# Source of truth: backend/services/observation_period_service.py:691-697 (deltas)
# и :942-994 (diagnostics targets).
# Каждая запись: (delta_key, diagnostic_target, label_html, has_pct_in_delta).
# Variant (a): renderer показывает ТОЛЬКО metric / delta abs / delta pct / verdict.
# baseline_value и current_value — НЕ показываются (renderer = snapshot-only, R2).
_B1_DELTA_METRICS = [
    ("p_tube_mean",  "p_tube",       "P_tube среднее (атм)",    True),
    ("p_line_mean",  "p_line",       "P_line среднее (атм)",    True),
    ("dp_mean",      "dp",           "ΔP среднее (атм)",        True),
    ("q_mean",       "q",            "Q среднее (тыс.м³/сут)",  True),
    ("shutdown_pct", "shutdown_pct", "Доля простоев (п.п.)",    False),
    ("cv_p_tube",    "cv_p_tube",    "CV P_tube (п.п.)",        False),
]


def _verdict_for(diagnostics: list | None, target: str, context: str) -> str:
    """g-fix-2: достать formal verdict из snapshot.diagnostics[].

    Renderer snapshot-only (R2): не вычисляет verdict, только читает.
    Не генерирует narrative — только enum-значение из diagnostics.

    Returns:
        verdict как str (улучшение/improvement/etc.) или "—" если нет matching entry.
    """
    if not diagnostics:
        return "—"
    for d in diagnostics:
        if not isinstance(d, dict):
            continue
        if d.get("target") == target and d.get("context") == context:
            v = d.get("verdict")
            if v:
                return str(v)
    return "—"


def _render_b1_deltas_table(with_b1: dict, diagnostics: list | None) -> str:
    """Таблица B1 deltas (variant a — без baseline/current_value).

    Реальные ключи C1 (observation_period_service.py:691-697):
        deltas["<metric>_mean"] = {"abs": x, "pct": y}
        deltas["shutdown_pct"]  = {"abs": x}      # без pct
        deltas["cv_p_tube"]     = {"abs": x}      # без pct

    Verdict читается из snapshot.diagnostics[] (target=metric, context="vs_b1").
    """
    deltas = with_b1.get("deltas") or {}
    if not deltas:
        return '<p class="obs-no-data">Нет данных о дельтах.</p>'

    rows = [
        '<table class="obs-deltas-table">',
        "  <thead><tr>",
        "    <th>Метрика</th><th>Δ абс.</th><th>Δ %</th><th>Вердикт</th>",
        "  </tr></thead>",
        "  <tbody>",
    ]
    has_any_row = False
    for delta_key, diag_target, label, has_pct in _B1_DELTA_METRICS:
        d = deltas.get(delta_key)
        if not isinstance(d, dict):
            continue
        has_any_row = True
        abs_val = _fmt(d.get("abs"))
        pct_val = _fmt(d.get("pct")) + "%" if has_pct and d.get("pct") is not None else "—"
        verdict = html_escape(_verdict_for(diagnostics, diag_target, "vs_b1"))
        rows.append(
            f"    <tr>"
            f"<td>{html_escape(label)}</td>"
            f"<td>{abs_val}</td>"
            f"<td>{pct_val}</td>"
            f"<td>{verdict}</td>"
            f"</tr>"
        )
    rows += ["  </tbody>", "</table>"]
    if not has_any_row:
        return '<p class="obs-no-data">Нет данных о дельтах.</p>'
    return "\n".join(rows)


# g-fix-3: customer_q / our_q селектор по q_source_used.
# Правило owner:
#   q_source_used="q_gas_working" → customer_q_working, our_q_working (fallback our_q_total)
#   q_source_used="q_gas_total"   → customer_q_total,   our_q_total   (fallback our_q_working)
def _pick_customer_q(row: dict, q_source_used: str) -> float | int | None:
    """Returns customer_q value по q_source_used. Pure read, без fallback."""
    if q_source_used == "q_gas_total":
        return row.get("customer_q_total")
    # default / "q_gas_working"
    return row.get("customer_q_working")


def _pick_our_q(row: dict, q_source_used: str) -> float | int | None:
    """Returns our_q value по q_source_used. С fallback на другой канал."""
    if q_source_used == "q_gas_total":
        primary, fallback = "our_q_total", "our_q_working"
    else:
        primary, fallback = "our_q_working", "our_q_total"
    v = row.get(primary)
    if v is None:
        v = row.get(fallback)
    return v


_CUSTOMER_STATUS_BADGE = {
    "ok":                      ('badge-status-ok',        "сравнение с заказчиком"),
    "partial_customer_data":   ('badge-status-partial',   "частичные данные заказчика"),
    "no_customer_data":        ('badge-status-no_data',   "данных заказчика нет"),
    "period_mismatch":         ('badge-status-partial',   "период заказчика не совпадает"),
    "insufficient_data":       ('badge-status-insufficient_data',
                                "недостаточно данных для сравнения"),
}


def _render_mape_html(mape_dict: dict | None) -> str:
    """Renders MAPE как HTML <p> с per-metric значениями (mape.q / mape.p_tube / mape.p_line)."""
    if not isinstance(mape_dict, dict):
        return ""
    parts = []
    for key, label in [("q", "Q"), ("p_tube", "P_tube"), ("p_line", "P_line")]:
        v = mape_dict.get(key)
        if v is not None:
            parts.append(f"{label}: {_fmt(v)}%")
    if not parts:
        return ""
    return '<p class="obs-mape">MAPE — ' + " · ".join(html_escape(p) for p in parts) + '</p>'


def _render_customer_comparison(with_customer: dict, show_daily_table: bool = True) -> str:
    """g-fix-3: HTML-секция сравнения с UzKorGaz.

    Phase O1: `show_daily_table` гейтит ТОЛЬКО суточную таблицу (часть
    `daily_table`). badge / источник Q / MAPE / покрытие — рендерятся всегда
    (это часть `comparison_with_customer`).

    Реальные ключи C1 (observation_period_service.py:786-790):
      status, customer_period, customer_days_available, customer_days_requested,
      mape (dict), q_source_used, daily_table[].

    daily_table row keys (RFC §9.Q1):
      date, our_q_total, our_q_working, customer_q_total, customer_q_working,
      diff_abs, diff_pct, data_status.
    """
    if not with_customer:
        return ""
    status = with_customer.get("status", "")
    badge_cls, badge_label = _CUSTOMER_STATUS_BADGE.get(
        status, ("badge-status-no_data", f"статус: {status or '—'}")
    )
    badge = f'<span class="badge {badge_cls}">{html_escape(badge_label)}</span>'

    # Для всех «отсутствует/несовпадающие/недостаточно» — только badge + сообщение,
    # таблицу не показываем (но renderer не падает).
    if status in ("no_customer_data", "period_mismatch", "insufficient_data"):
        msg = {
            "no_customer_data":  "Данных заказчика нет для этого периода.",
            "period_mismatch":   "Период данных заказчика не совпадает с периодом анализа.",
            "insufficient_data": "Недостаточно данных для сравнения.",
        }.get(status, "Сравнение недоступно.")
        return f'<div class="obs-customer">{badge}<p class="obs-no-data">{html_escape(msg)}</p></div>'

    # status ∈ {"ok", "partial_customer_data"} → показываем mape + table
    q_source_used = with_customer.get("q_source_used") or "q_gas_working"
    mape = with_customer.get("mape")
    daily_table = with_customer.get("daily_table") or []

    parts = [f'<div class="obs-customer">{badge}']
    if q_source_used:
        parts.append(
            f'<p class="obs-q-source">Источник Q заказчика: '
            f'<code>{html_escape(q_source_used)}</code></p>'
        )
    parts.append(_render_mape_html(mape))

    days_avail = with_customer.get("customer_days_available")
    days_req = with_customer.get("customer_days_requested")
    if days_avail is not None and days_req is not None:
        parts.append(
            f'<p class="obs-coverage">Данных заказчика: '
            f'{html_escape(str(days_avail))} из {html_escape(str(days_req))} дней</p>'
        )

    if show_daily_table:
        if not daily_table:
            parts.append(
                '<p class="obs-no-data">Нет суточных данных для сравнения.</p>'
            )
        else:
            parts += [
                '<table class="obs-customer-table">',
                "  <thead><tr><th>Дата</th><th>Наш Q</th><th>Q заказчика</th>"
                "<th>Δ абс.</th><th>Δ %</th><th>Статус</th></tr></thead>",
                "  <tbody>",
            ]
            shown = 0
            for r in daily_table:
                if not isinstance(r, dict):
                    continue
                our_q = _pick_our_q(r, q_source_used)
                cust_q = _pick_customer_q(r, q_source_used)
                diff_abs = r.get("diff_abs")
                diff_pct = r.get("diff_pct")
                diff_pct_str = _fmt(diff_pct) + "%" if diff_pct is not None else "—"
                data_status = str(r.get("data_status") or "—")
                parts.append(
                    f"    <tr>"
                    f"<td>{html_escape(str(r.get('date', '?')))}</td>"
                    f"<td>{_fmt(our_q)}</td>"
                    f"<td>{_fmt(cust_q)}</td>"
                    f"<td>{_fmt(diff_abs)}</td>"
                    f"<td>{diff_pct_str}</td>"
                    f"<td>{html_escape(data_status)}</td>"
                    f"</tr>"
                )
                shown += 1
                if shown >= 10:
                    break
            parts += ["  </tbody>", "</table>"]
            if len(daily_table) > shown:
                parts.append(
                    f'<p class="obs-table-note">Показано {shown} из '
                    f'{len(daily_table)} строк.</p>'
                )
    parts.append('</div>')
    return "\n".join(p for p in parts if p)


def _render_changepoints_list(changepoints: list) -> str:
    if not changepoints:
        return ""
    items = ['<ul class="obs-changepoints-list">']
    for cp in changepoints:
        if not isinstance(cp, dict):
            continue
        cp_date = html_escape(str(cp.get("date") or cp.get("changepoint_date") or "?"))
        mag = _fmt(cp.get("magnitude_pct"))
        conf = _fmt(cp.get("confidence"))
        items.append(f"  <li>{cp_date}: Δ={mag}%, уверенность={conf}</li>")
    items.append("</ul>")
    return "\n".join(items)


# ---------------------------------------------------------------------------
# LATEX HELPERS ДЛЯ PERIOD
# ---------------------------------------------------------------------------


# g-fix-2: LaTeX labels для метрик B1 deltas — соответствует _B1_DELTA_METRICS.
_B1_DELTA_METRICS_LATEX_LABELS = {
    "p_tube_mean":  r"$P_{\text{тр}}$ среднее",
    "p_line_mean":  r"$P_{\text{лин}}$ среднее",
    "dp_mean":      r"$\Delta P$ среднее",
    "q_mean":       r"$Q$ среднее",
    "shutdown_pct": r"Доля простоев",
    "cv_p_tube":    r"CV $P_{\text{тр}}$",
}


def _latex_b1_deltas_tabular(with_b1: dict, diagnostics: list | None) -> str:
    """LaTeX-таблица B1 deltas (variant a, parity с HTML)."""
    deltas = with_b1.get("deltas") or {}
    if not deltas:
        return r"\textit{Нет данных о дельтах.}"

    rows = [
        r"{\footnotesize",
        r"\begin{tabular}{lrrl}",
        r"\toprule",
        r"\textbf{Метрика} & \textbf{$\Delta$ абс.} & \textbf{$\Delta$ \%} & \textbf{Вердикт} \\",
        r"\midrule",
    ]
    has_any_row = False
    for delta_key, diag_target, _label_html, has_pct in _B1_DELTA_METRICS:
        d = deltas.get(delta_key)
        if not isinstance(d, dict):
            continue
        has_any_row = True
        label = _B1_DELTA_METRICS_LATEX_LABELS.get(delta_key, latex_escape(delta_key))
        abs_val = _fmt_latex(d.get("abs"))
        pct_val = (
            _fmt_latex(d.get("pct")) + r"\%"
            if has_pct and d.get("pct") is not None
            else "---"
        )
        verdict = latex_escape(_verdict_for(diagnostics, diag_target, "vs_b1"))
        rows.append(f"{label} & {abs_val} & {pct_val} & {verdict} \\\\")
    rows += [r"\bottomrule", r"\end{tabular}", r"}"]
    if not has_any_row:
        return r"\textit{Нет данных о дельтах.}"
    return "\n".join(rows)


# g-fix-3: LaTeX customer comparison.
_CUSTOMER_STATUS_LATEX_LABEL = {
    "ok":                    "сравнение с заказчиком",
    "partial_customer_data": "частичные данные заказчика",
    "no_customer_data":      "данных заказчика нет",
    "period_mismatch":       "период заказчика не совпадает",
    "insufficient_data":     "недостаточно данных для сравнения",
}


def _latex_mape_line(mape_dict: dict | None) -> str:
    """LaTeX-строка с MAPE per-metric (mape.q / mape.p_tube / mape.p_line)."""
    if not isinstance(mape_dict, dict):
        return ""
    parts = []
    for key, label in [("q", r"$Q$"), ("p_tube", r"$P_{\text{тр}}$"), ("p_line", r"$P_{\text{лин}}$")]:
        v = mape_dict.get(key)
        if v is not None:
            parts.append(f"{label}: {_fmt_latex(v)}\\%")
    if not parts:
        return ""
    return r"\textit{MAPE — " + r" $\cdot$ ".join(parts) + "}"


def _latex_customer_comparison_tabular(
    with_customer: dict, show_daily_table: bool = True,
) -> str:
    """g-fix-3: LaTeX-аналог _render_customer_comparison (parity с HTML).

    Phase O1: `show_daily_table` гейтит суточную таблицу (часть `daily_table`).
    """
    if not with_customer:
        return ""
    status = with_customer.get("status", "")
    label = _CUSTOMER_STATUS_LATEX_LABEL.get(status, f"статус: {status or '---'}")
    badge_latex = r"\textit{Сравнение с заказчиком: " + latex_escape(label) + "}"

    if status in ("no_customer_data", "period_mismatch", "insufficient_data"):
        msg = {
            "no_customer_data":  "Данных заказчика нет для этого периода.",
            "period_mismatch":   "Период данных заказчика не совпадает с периодом анализа.",
            "insufficient_data": "Недостаточно данных для сравнения.",
        }.get(status, "Сравнение недоступно.")
        return badge_latex + "\n\n" + r"\textit{" + latex_escape(msg) + "}"

    q_source_used = with_customer.get("q_source_used") or "q_gas_working"
    mape = with_customer.get("mape")
    daily_table = with_customer.get("daily_table") or []
    days_avail = with_customer.get("customer_days_available")
    days_req = with_customer.get("customer_days_requested")

    parts = [badge_latex, ""]
    parts.append(
        r"\textit{Источник Q заказчика: \texttt{" + latex_escape(q_source_used) + "}}"
    )
    mape_line = _latex_mape_line(mape)
    if mape_line:
        parts.append(mape_line)
    if days_avail is not None and days_req is not None:
        parts.append(
            r"\textit{Данных заказчика: " + latex_escape(str(days_avail))
            + " из " + latex_escape(str(days_req)) + " дней}"
        )

    if not show_daily_table:
        # Часть `daily_table` скрыта — badge/источник Q/MAPE/покрытие уже собраны.
        return "\n\n".join(parts)

    if not daily_table:
        parts.append(r"\textit{Нет суточных данных для сравнения.}")
        return "\n\n".join(parts)

    table_lines = [
        r"{\footnotesize",
        r"\begin{tabular}{lrrrrl}",
        r"\toprule",
        r"\textbf{Дата} & \textbf{Наш $Q$} & \textbf{$Q$ заказчика} "
        r"& \textbf{$\Delta$ абс.} & \textbf{$\Delta$ \%} & \textbf{Статус} \\",
        r"\midrule",
    ]
    shown = 0
    for r in daily_table:
        if not isinstance(r, dict):
            continue
        our_q = _pick_our_q(r, q_source_used)
        cust_q = _pick_customer_q(r, q_source_used)
        diff_abs = r.get("diff_abs")
        diff_pct = r.get("diff_pct")
        diff_pct_str = _fmt_latex(diff_pct) + r"\%" if diff_pct is not None else "---"
        data_status = latex_escape(str(r.get("data_status") or "---"))
        date_str = latex_escape(str(r.get("date", "?")))
        table_lines.append(
            f"{date_str} & {_fmt_latex(our_q)} & {_fmt_latex(cust_q)} "
            f"& {_fmt_latex(diff_abs)} & {diff_pct_str} & {data_status} \\\\"
        )
        shown += 1
        if shown >= 10:
            break
    table_lines += [r"\bottomrule", r"\end{tabular}", r"}"]
    if len(daily_table) > shown:
        table_lines.append(
            r"\textit{Показано " + str(shown) + " из "
            + str(len(daily_table)) + " строк.}"
        )
    # Заголовочные строки (badge/MAPE/...) разделены \n\n; таблица — единым блоком.
    return "\n\n".join(parts) + "\n" + "\n".join(table_lines)


# ---------------------------------------------------------------------------
# SEGMENT ANALYSIS (from customer_daily / SegmentBlocksWidget)
# ---------------------------------------------------------------------------


def _normalize_segment_chart_data(snapshot: dict) -> dict:
    """Нормализует chart_data для клиентского Plotly-рендеринга.

    Поддерживает три формата:
    1. segment_v1 (customer_data): chart_data.q_total, chart_data.dates
    2. segment_analysis_v1 (segment-demo): chart_data.primary.values → q_total
    3. obs_segment_v1 (observation): raw.chart_payload.dates/q → dates/q_total
       (также поддерживает legacy timestamps для старых блоков)

    Returns:
        dict с ключами: dates, q_total, q_working (опц.), shutdown_min (опц.)
    """
    # Формат 1: уже нормализован (segment_v1)
    chart_data = snapshot.get("chart_data") or {}
    if chart_data.get("q_total") and chart_data.get("dates"):
        return chart_data

    result: dict = {}

    # Формат 2: segment-demo (primary.values)
    if chart_data.get("primary") and chart_data["primary"].get("values"):
        result["dates"] = chart_data.get("dates", [])
        result["q_total"] = chart_data["primary"]["values"]
        # Извлекаем secondary series
        sec = chart_data.get("secondary") or {}
        for col_name, col_data in sec.items():
            if not col_data or not col_data.get("values"):
                continue
            vals = col_data["values"]
            if "flow" in col_name or col_name == "flow_rate_mean":
                if not result.get("q_working"):
                    result["q_working"] = vals
            elif "shutdown" in col_name or "downtime" in col_name:
                result["shutdown_min"] = vals
        return result

    # Формат 3: observation_segment (raw.chart_payload)
    raw = snapshot.get("raw") or {}
    chart_payload = raw.get("chart_payload") or {}
    # Поддержка обоих ключей: dates (новый) и timestamps (старые блоки)
    time_values = chart_payload.get("dates") or chart_payload.get("timestamps")
    if time_values and chart_payload.get("q"):
        # Конвертируем в ISO date strings (YYYY-MM-DD)
        dates = [ts[:10] if isinstance(ts, str) and len(ts) >= 10 else ts for ts in time_values]
        result["dates"] = dates
        result["q_total"] = chart_payload["q"]
        if chart_payload.get("shutdown_min"):
            result["shutdown_min"] = chart_payload["shutdown_min"]
        return result

    # Fallback: возвращаем исходный chart_data
    return chart_data


def render_segment_analysis(snapshot: dict, ctx: RenderContext) -> RenderResult:
    """Рендерит блок segment_analysis (схема segment_analysis_v1 из customer_daily).

    Отличается от observation_segment:
    - segments_extended вместо segments
    - chart_data для Plotly-графика
    - interpretation.summary / descriptions

    Используется для блоков, созданных через SegmentBlocksWidget на странице Наблюдение.
    """
    period = snapshot.get("period") or {}
    # Поддержка двух форматов: segments_extended (v2) или segments (v1/segment-demo)
    segments = snapshot.get("segments_extended") or snapshot.get("segments") or []
    changepoints = snapshot.get("cp_marks") or snapshot.get("changepoints") or []
    interpretation = snapshot.get("interpretation") or {}
    display_settings = snapshot.get("display_settings") or {}

    # Поддержка двух форматов snapshot:
    # 1) period.from / period.to (observation виджет, params fallback)
    # 2) date_from / date_to (segment_v1 из compute_segment_block)
    pf = str(period.get("from") or snapshot.get("date_from") or "?")
    pt = str(period.get("to") or snapshot.get("date_to") or "?")
    title_h = html_escape(ctx.title or "(без заголовка)")
    title_l = latex_escape(ctx.title or "(без заголовка)")

    # Segment type colors and labels
    seg_type_colors = {
        "stable": "#22c55e",
        "rising": "#3b82f6",
        "falling": "#ef4444",
        "volatile": "#f59e0b",
        "unknown": "#9ca3af",
    }
    seg_type_labels = {
        "stable": "Стабильный",
        "rising": "Рост",
        "falling": "Падение",
        "volatile": "Волатильный",
        "unknown": "Неопределён",
    }

    # ── Chart generation (only for PDF, skip_figures=False) ──
    figures: list[FigureRef] = []
    chart_latex = ""

    if not ctx.skip_figures and display_settings.get("show_chart", True):
        from backend.services.observation_chart_renderer import render_segment_analysis_chart

        stem = _figure_stem(ctx, "segment_analysis_chart")
        abs_path = os.path.join(ctx.output_dir, f"{stem}.png")
        rel_path = f"observation_charts/{stem}.png"

        try:
            render_segment_analysis_chart(snapshot, abs_path)
            fig_ref = FigureRef(
                relative_path=rel_path,
                absolute_path=abs_path,
                caption=f"Сегментный анализ: {pf} — {pt}",
                label=stem,
            )
            figures.append(fig_ref)
            # Используем абсолютный путь для xelatex
            chart_abs_path = str(Path(abs_path).resolve())
            chart_latex = f"""
\\begin{{figure}}[H]
\\centering
\\includegraphics[width=0.95\\textwidth]{{{chart_abs_path}}}
\\caption{{{latex_escape(fig_ref.caption)}}}
\\end{{figure}}
"""
        except Exception as e:
            # Логируем ошибку, но не падаем
            import logging
            logging.getLogger(__name__).warning("segment_analysis chart error: %s", e)

    # ── HTML (для превью в правой панели) ──
    block_id = ctx.block_id or 0
    html_parts = [
        '<section class="obs-block obs-segment-analysis">',
        f'<h3>{title_h}</h3>',
    ]

    # Intro
    if display_settings.get("show_chart", True) or _part_on(ctx, "intro"):
        html_parts += [
            '<div class="obs-block__meta">',
            f'  <span class="obs-period">Период: {html_escape(pf)} — {html_escape(pt)}</span>',
            f'  <span class="obs-seg-count">Сегментов: {len(segments)}</span>',
            '</div>',
        ]

    # Plotly chart container (клиентский рендеринг)
    if display_settings.get("show_chart", True) or _part_on(ctx, "q_segment_chart"):
        import json
        # Нормализуем chart_data для поддержки разных форматов snapshot
        normalized_chart_data = _normalize_segment_chart_data(snapshot)
        # Минимальные данные для Plotly: chart_data, segments_extended, cp_marks
        chart_json = {
            "chart_data": normalized_chart_data,
            "segments_extended": segments,
            "cp_marks": changepoints,
            "shutdown_clusters": snapshot.get("shutdown_clusters") or [],
        }
        # Используем двойные кавычки для атрибута и экранируем их в JSON
        chart_json_str = json.dumps(chart_json, ensure_ascii=False, default=str)
        chart_json_escaped = html_escape(chart_json_str, quote=True)
        html_parts.append(
            f'<div id="obs-seg-chart-{block_id}" class="obs-segment-chart-container" '
            f'style="height:400px; margin:10px 0;" '
            f'data-snapshot="{chart_json_escaped}">'
            f'<div style="color:#9ca3af; padding:20px; text-align:center;">Загрузка графика...</div>'
            f'</div>'
        )

    # Segments table
    if display_settings.get("show_segments_table", True) or _part_on(ctx, "segments_table"):
        if segments:
            table_rows = []
            for seg in segments:
                num = seg.get("num", "?")
                seg_type = seg.get("type", "unknown")
                color = seg_type_colors.get(seg_type, "#9ca3af")
                label = seg_type_labels.get(seg_type, seg_type)
                date_from = seg.get("start_date", "?")
                date_to = seg.get("end_date", "?")
                mean_val = seg.get("mean_value")
                slope = seg.get("slope")

                mean_str = f"{mean_val:.2f}" if mean_val is not None else "—"
                slope_str = f"{slope:+.4f}" if slope is not None else "—"

                table_rows.append(
                    f'<tr>'
                    f'<td><span style="display:inline-block;width:22px;height:22px;'
                    f'line-height:22px;text-align:center;border-radius:50%;'
                    f'background:{color};color:#fff;font-weight:600;font-size:0.78rem;">{num}</span></td>'
                    f'<td style="color:{color};font-weight:500;">{html_escape(label)}</td>'
                    f'<td>{html_escape(date_from)}</td>'
                    f'<td>{html_escape(date_to)}</td>'
                    f'<td>{mean_str}</td>'
                    f'<td style="font-family:monospace;font-size:0.82rem;">{slope_str}</td>'
                    f'</tr>'
                )
            html_parts.append(
                '<table class="obs-table obs-segments-table">'
                '<thead><tr><th>#</th><th>Тип</th><th>С</th><th>По</th><th>Среднее</th><th>Тренд</th></tr></thead>'
                '<tbody>' + '\n'.join(table_rows) + '</tbody></table>'
            )
        else:
            html_parts.append('<p class="obs-empty">Сегменты не обнаружены</p>')

    # Changepoints
    if changepoints and (display_settings.get("show_changepoints", True) or _part_on(ctx, "changepoints")):
        cp_items = []
        for cp in changepoints[:10]:
            if isinstance(cp, dict):
                cp_date = cp.get("date") or cp.get("timestamp", "?")
                cp_tag = cp.get("tag", "")
                cp_items.append(f'<li><strong>{html_escape(str(cp_date))}</strong>'
                               f'{" — " + html_escape(cp_tag) if cp_tag else ""}</li>')
        if cp_items:
            html_parts.append(
                '<div class="obs-changepoints">'
                '<strong>Точки перелома:</strong>'
                '<ul>' + '\n'.join(cp_items) + '</ul></div>'
            )

    # Interpretation
    if display_settings.get("show_interpretation", True) or _part_on(ctx, "description"):
        summary = interpretation.get("summary", "")
        descriptions = interpretation.get("descriptions", [])
        if summary or descriptions:
            html_parts.append('<div class="obs-interpretation">')
            if summary:
                html_parts.append(f'<p class="obs-summary">{html_escape(summary)}</p>')
            if descriptions:
                html_parts.append('<ul class="obs-descriptions">')
                for desc in descriptions:
                    html_parts.append(f'<li>{html_escape(desc)}</li>')
                html_parts.append('</ul>')
            html_parts.append('</div>')

    html_parts.append('</section>')
    html = "\n".join(p for p in html_parts if p)

    # ── LaTeX ──
    pf_l = latex_escape(pf)
    pt_l = latex_escape(pt)
    latex_parts = [
        f"\\subsection*{{{title_l}}}",
        f"\\textit{{Период: {pf_l}~--~{pt_l}, сегментов: {len(segments)}}}",
        "",
    ]

    # Segments table in LaTeX
    if segments and (display_settings.get("show_segments_table", True) or _part_on(ctx, "segments_table")):
        latex_parts.append(r"\begin{tabular}{|c|l|c|c|r|r|}")
        latex_parts.append(r"\hline")
        latex_parts.append(r"\textbf{\#} & \textbf{Тип} & \textbf{С} & \textbf{По} & \textbf{Ср.} & \textbf{Тренд} \\")
        latex_parts.append(r"\hline")
        for seg in segments:
            num = seg.get("num", "?")
            seg_type = seg.get("type", "unknown")
            label = seg_type_labels.get(seg_type, seg_type)
            date_from = latex_escape(str(seg.get("start_date", "?")))
            date_to = latex_escape(str(seg.get("end_date", "?")))
            mean_val = seg.get("mean_value")
            slope = seg.get("slope")
            mean_str = f"{mean_val:.2f}" if mean_val is not None else "—"
            slope_str = f"{slope:+.4f}" if slope is not None else "—"
            latex_parts.append(
                f"{num} & {latex_escape(label)} & {date_from} & {date_to} & {mean_str} & {slope_str} \\\\"
            )
        latex_parts.append(r"\hline")
        latex_parts.append(r"\end{tabular}")
        latex_parts.append("")

    # Interpretation in LaTeX
    if display_settings.get("show_interpretation", True) or _part_on(ctx, "description"):
        summary = interpretation.get("summary", "")
        if summary:
            latex_parts.append(f"\\par {latex_escape(summary)}")
            latex_parts.append("")

    # Insert chart LaTeX before everything else if generated
    if chart_latex:
        latex_parts.insert(2, chart_latex)  # After title and period info

    latex_str = "\n".join(latex_parts)

    return RenderResult(html=html, latex=latex_str, figures=figures)


def render_segment_comparison(snapshot: dict, ctx: RenderContext) -> RenderResult:
    """Рендерит блок segment_comparison (схема segment_comparison_v1 из customer_daily).

    Сравнение нескольких сегментов с overlay-графиком и таблицей различий.
    """
    segments_compared = snapshot.get("segments_compared") or []
    diff_table = snapshot.get("diff_table") or {}
    interpretation = snapshot.get("interpretation") or {}
    display_settings = snapshot.get("display_settings") or {}

    title_h = html_escape(ctx.title or "(без заголовка)")
    title_l = latex_escape(ctx.title or "(без заголовка)")

    # ── HTML ──
    block_id = ctx.block_id or 0
    html_parts = [
        '<section class="obs-block obs-segment-comparison">',
        f'<h3>{title_h}</h3>',
        '<div class="obs-block__meta">',
        f'  <span class="obs-seg-count">Сегментов в сравнении: {len(segments_compared)}</span>',
        '</div>',
    ]

    # Plotly chart container (клиентский рендеринг overlay)
    if display_settings.get("show_chart", True) or _part_on(ctx, "chart"):
        import json
        chart_json = {
            "chart_overlay": snapshot.get("chart_overlay") or {},
            "segments_compared": segments_compared,
        }
        chart_json_str = json.dumps(chart_json, ensure_ascii=False, default=str)
        chart_json_escaped = html_escape(chart_json_str, quote=True)
        html_parts.append(
            f'<div id="obs-cmp-chart-{block_id}" class="obs-comparison-chart-container" '
            f'style="height:350px; margin:10px 0;" '
            f'data-snapshot="{chart_json_escaped}">'
            f'<div style="color:#9ca3af; padding:20px; text-align:center;">Загрузка графика...</div>'
            f'</div>'
        )

    # Compared segments list
    if segments_compared:
        seg_items = []
        for seg in segments_compared:
            num = seg.get("segment_num", "?")
            label = seg.get("label", f"Сегмент {num}")
            color = seg.get("color", "#6b7280")
            period = seg.get("period", {})
            pf = period.get("from", "?")
            pt = period.get("to", "?")
            mean_val = seg.get("mean_value")
            mean_str = f"{mean_val:.2f}" if mean_val is not None else "—"
            seg_items.append(
                f'<li style="border-left:3px solid {color};padding-left:8px;margin-bottom:6px;">'
                f'<strong>{html_escape(label)}</strong>: {html_escape(pf)} — {html_escape(pt)}, '
                f'среднее = {mean_str}</li>'
            )
        html_parts.append(
            '<div class="obs-cmp-segments">'
            '<ul style="list-style:none;padding:0;margin:10px 0;">' +
            '\n'.join(seg_items) + '</ul></div>'
        )

    # Diff table
    if display_settings.get("show_diff_table", True) or _part_on(ctx, "diff_table"):
        deltas = diff_table.get("deltas", {})
        if deltas:
            delta_rows = []
            metric_labels = {
                "mean": "Среднее",
                "std": "Станд. откл.",
                "min": "Минимум",
                "max": "Максимум",
                "slope": "Тренд",
            }
            for metric, delta in deltas.items():
                if not isinstance(delta, dict):
                    continue
                label = metric_labels.get(metric, metric)
                abs_val = delta.get("abs", "—")
                pct_val = delta.get("pct")
                abs_str = f"{abs_val:.2f}" if isinstance(abs_val, (int, float)) else str(abs_val)
                pct_str = f"{pct_val}%" if pct_val is not None else "—"
                delta_rows.append(f'<tr><td>{html_escape(label)}</td><td>{abs_str}</td><td>{pct_str}</td></tr>')
            if delta_rows:
                html_parts.append(
                    '<table class="obs-table obs-diff-table">'
                    '<thead><tr><th>Метрика</th><th>Δ абс.</th><th>Δ %</th></tr></thead>'
                    '<tbody>' + '\n'.join(delta_rows) + '</tbody></table>'
                )

    # Interpretation
    if display_settings.get("show_interpretation", True) or _part_on(ctx, "description"):
        summary = interpretation.get("summary", "")
        if summary:
            html_parts.append(f'<p class="obs-summary">{html_escape(summary)}</p>')

    html_parts.append('</section>')
    html = "\n".join(p for p in html_parts if p)

    # ── LaTeX ──
    latex_parts = [
        f"\\subsection*{{{title_l}}}",
        f"\\textit{{Сравнение {len(segments_compared)} сегментов}}",
        "",
    ]

    # Segments list in LaTeX
    if segments_compared:
        latex_parts.append(r"\begin{itemize}")
        for seg in segments_compared:
            label = latex_escape(seg.get("label", f"Сегмент {seg.get('segment_num', '?')}"))
            period = seg.get("period", {})
            pf = latex_escape(str(period.get("from", "?")))
            pt = latex_escape(str(period.get("to", "?")))
            mean_val = seg.get("mean_value")
            mean_str = f"{mean_val:.2f}" if mean_val is not None else "—"
            latex_parts.append(f"  \\item {label}: {pf}~--~{pt}, среднее = {mean_str}")
        latex_parts.append(r"\end{itemize}")
        latex_parts.append("")

    # Diff table in LaTeX
    if display_settings.get("show_diff_table", True) or _part_on(ctx, "diff_table"):
        deltas = diff_table.get("deltas", {})
        if deltas:
            latex_parts.append(r"{\footnotesize \textbf{Различия:}}")
            latex_parts.append(r"\begin{tabular}{|l|r|r|}")
            latex_parts.append(r"\hline")
            latex_parts.append(r"\textbf{Метрика} & \textbf{Δ абс.} & \textbf{Δ \%} \\")
            latex_parts.append(r"\hline")
            metric_labels = {"mean": "Среднее", "std": "Станд. откл.", "min": "Мин.", "max": "Макс.", "slope": "Тренд"}
            for metric, delta in deltas.items():
                if not isinstance(delta, dict):
                    continue
                label = latex_escape(metric_labels.get(metric, metric))
                abs_val = delta.get("abs", "—")
                pct_val = delta.get("pct")
                abs_str = f"{abs_val:.2f}" if isinstance(abs_val, (int, float)) else str(abs_val)
                pct_str = f"{pct_val}\\%" if pct_val is not None else "—"
                latex_parts.append(f"{label} & {abs_str} & {pct_str} \\\\")
            latex_parts.append(r"\hline")
            latex_parts.append(r"\end{tabular}")
            latex_parts.append("")

    # Summary
    if display_settings.get("show_interpretation", True) or _part_on(ctx, "description"):
        summary = interpretation.get("summary", "")
        if summary:
            latex_parts.append(f"\\par {latex_escape(summary)}")

    latex_str = "\n".join(latex_parts)

    return RenderResult(html=html, latex=latex_str, figures=[])


# ---------------------------------------------------------------------------
# KIND DISPATCH
# ---------------------------------------------------------------------------

KIND_RENDERERS: dict[str, Callable[[dict, RenderContext], RenderResult]] = {
    "observation_analysis": render_analysis,
    "observation_baseline": render_baseline,
    "observation_period":   render_period,
    "observation_segment":  render_segment,
    "segment_analysis":     render_segment_analysis,
    "segment_comparison":   render_segment_comparison,
}

_BROKEN_STATUSES = {"corrupted", "invalid_schema"}


def render_block(block: dict, ctx: RenderContext) -> RenderResult:
    """Dispatch по kind. Broken statuses → placeholder. Unknown kind → placeholder.

    Phase O1: извлекает block-метаданные (params.parts / params.prefix_note /
    params.suffix_note / comment) в ctx. Для успешно отрендеренных kind-блоков
    оборачивает результат операторскими примечаниями (_apply_block_notes).
    Renderer остаётся snapshot-only: метаданные приходят в block dict от
    вызывающего (chapter-preview / PDF builder), renderer в БД не ходит.
    """
    kind = block.get("kind")
    snapshot = block.get("snapshot") or {}

    # ── Извлечение parts / notes / comment в ctx ──
    params = block.get("params")
    if isinstance(params, dict):
        raw_parts = params.get("parts")
        ctx.parts = raw_parts if isinstance(raw_parts, dict) else {}
        ctx.prefix_note = str(params.get("prefix_note") or "")
        ctx.suffix_note = str(params.get("suffix_note") or "")
    else:
        ctx.parts = {}
        ctx.prefix_note = ""
        ctx.suffix_note = ""
    ctx.comment = str(block.get("comment") or "")

    if ctx.block_status == "legacy_v0":
        return _render_legacy_v0_placeholder(snapshot, ctx)

    if ctx.block_status in _BROKEN_STATUSES:
        return _render_broken_placeholder(ctx)

    if kind not in KIND_RENDERERS:
        return _render_unknown_kind(kind, ctx)

    result = KIND_RENDERERS[kind](snapshot, ctx)
    return _apply_block_notes(result, ctx)


# ---------------------------------------------------------------------------
# BACKWARD-COMPAT PUBLIC API (Phase C4, используется в C4 /chapter-preview)
# ---------------------------------------------------------------------------


def render_observation_chapter(blocks: list[dict]) -> str:
    """
    HTML-only chapter render. skip_figures=True всегда (editor preview).
    Backward-compatible: сохраняет сигнатуру из Phase C4.
    """
    if not blocks:
        return (
            '<section class="obs-chapter obs-chapter--empty">'
            '<p class="obs-empty-msg">Нет блоков</p>'
            "</section>"
        )

    parts: list[str] = ['<section class="obs-chapter">']
    for block in blocks:
        try:
            ctx = RenderContext(
                output_dir="",
                block_id=block.get("block_id"),
                title=block.get("title") or block.get("snapshot", {}).get("period", {}).get("from", "") or f"Блок {block.get('block_id', '')}",
                block_status=block.get("block_status") or block.get("snapshot", {}).get("block_status", "ok"),
                skip_figures=True,
            )
            result = render_block(block, ctx)
            parts.append(result.html)
        except Exception as exc:
            block_id = block.get("block_id", "?")
            parts.append(
                f'<div class="obs-block obs-block--error">'
                f'<span class="badge badge-status-corrupted">ошибка рендера блока {html_escape(str(block_id))}: {html_escape(str(exc))}</span>'
                f"</div>"
            )
    parts.append("</section>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# NEW LATEX PUBLIC API
# ---------------------------------------------------------------------------


def render_observation_chapter_latex(
    blocks: list[dict],
    *,
    output_dir: str = "backend/generated/observation_charts",
    skip_figures: bool = False,
) -> str:
    r"""LaTeX fragment для вставки в adaptation_report.tex через Jinja2.

    При skip_figures=False — генерирует PNG в output_dir и включает
    \\includegraphics ссылки в LaTeX.
    """
    parts = [r"\section*{Глава наблюдения}", ""]
    if not blocks:
        parts.append(r"\textit{Нет блоков для отчёта.}")
        return "\n".join(parts)

    os.makedirs(output_dir, exist_ok=True)

    for block in blocks:
        ctx = RenderContext(
            output_dir=output_dir,
            block_id=block.get("block_id"),
            title=block.get("title") or "",
            block_status=block.get("block_status") or "ok",
            skip_figures=skip_figures,
        )
        try:
            result = render_block(block, ctx)
            parts.append(result.latex)
            parts.append("")
        except Exception as exc:
            block_id = block.get("block_id", "?")
            parts.append(
                f"% [render error block {latex_escape(str(block_id))}: "
                f"{latex_escape(str(exc))}]"
            )
            parts.append("")

    return "\n".join(parts)
