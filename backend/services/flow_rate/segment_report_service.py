"""
Генерация сравнительного PDF-отчёта по выбранным участкам (сегментам).

Создаёт:
  - Комбинированный график (все участки на одной кривой)
  - Индивидуальные графики по каждому участку
  - Сравнительные таблицы
  - Краткие выводы и описания
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from backend.utils.latex import find_xelatex as _find_xelatex

log = logging.getLogger(__name__)

LATEX_TEMPLATES_DIR = Path("backend/templates/latex")
OUTPUT_DIR = Path("backend/static/generated")
PDF_DIR = OUTPUT_DIR / "pdf"
TEMP_DIR = OUTPUT_DIR / "temp"
CHARTS_DIR = OUTPUT_DIR / "charts"

SEGMENT_COLORS = [
    "#3b82f6", "#ef4444", "#10b981", "#f59e0b",
    "#8b5cf6", "#ec4899", "#14b8a6", "#fb923c",
]


def _ensure_dirs():
    for d in [PDF_DIR, TEMP_DIR, CHARTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def _get_latex_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(LATEX_TEMPLATES_DIR)),
        block_start_string="\\BLOCK{",
        block_end_string="}",
        variable_start_string="\\VAR{",
        variable_end_string="}",
        comment_start_string="\\#{",
        comment_end_string="}",
        line_statement_prefix="%%",
        line_comment_prefix="%#",
        trim_blocks=True,
        autoescape=False,
    )


def _compile_latex(tex_source: str, base_name: str) -> Path:
    import subprocess
    _ensure_dirs()

    abs_temp = TEMP_DIR.resolve()
    tex_file = abs_temp / f"{base_name}.tex"
    tex_file.write_text(tex_source, encoding="utf-8")

    for pass_num in range(2):
        result = subprocess.run(
            [_find_xelatex(), "-interaction=nonstopmode",
             f"-output-directory={abs_temp}", str(tex_file)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(abs_temp),
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="ignore")
            stdout = result.stdout.decode("utf-8", errors="ignore")
            # Ищем строки с ошибками в логе
            error_lines = [l for l in stdout.split('\n') if '!' in l or 'Error' in l]
            log.error("xelatex pass %d failed. Errors:\n%s\nStderr:\n%s",
                      pass_num + 1, '\n'.join(error_lines[-20:]), stderr[-500:])
            # Не удаляем .tex для отладки
            raise RuntimeError(f"xelatex compilation failed (pass {pass_num + 1})")

    temp_pdf = abs_temp / f"{base_name}.pdf"
    final_pdf = PDF_DIR.resolve() / f"{base_name}.pdf"

    if not temp_pdf.exists():
        raise FileNotFoundError(f"PDF not created: {temp_pdf}")

    temp_pdf.rename(final_pdf)

    for ext in (".aux", ".log", ".out", ".tex"):
        f = abs_temp / f"{base_name}{ext}"
        if f.exists():
            f.unlink()

    return final_pdf


def _fmt(val, decimals=3) -> str:
    if val is None:
        return "\u2014"
    if isinstance(val, float):
        return f"{val:.{decimals}f}"
    return str(val)


def _tex_escape(text: str) -> str:
    replacements = {
        "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
        "_": r"\_", "{": r"\{", "}": r"\}",
        "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _compute_trend(flows: list) -> tuple[float, str]:
    """Вычисляет наклон тренда (за сутки) и текстовое описание."""
    valid = [f for f in flows if f is not None and f > 0]
    if len(valid) < 2:
        return 0.0, "недостаточно данных"

    import numpy as np
    x = np.arange(len(valid), dtype=float)
    y = np.array(valid)
    coeffs = np.polyfit(x, y, 1)
    # slope per data point → approximate slope per day
    # assuming ~288 points/day (5-min intervals)
    slope_per_point = coeffs[0]
    # We report slope per day (multiply by points_per_day, but timestamps aren't uniform)
    # Simpler: report per 1000 points as a proxy
    slope_per_day = slope_per_point * 288  # approximate for 5-min data

    if abs(slope_per_day) < 0.01:
        label = "стабильный"
    elif slope_per_day > 0:
        label = f"рост (+{slope_per_day:.3f} тыс.м³/сут²)"
    else:
        label = f"падение ({slope_per_day:.3f} тыс.м³/сут²)"

    return slope_per_day, label


def _stat(stats: dict, *keys, default=0):
    """Get stat value trying multiple key names (camelCase / snake_case)."""
    for k in keys:
        v = stats.get(k)
        if v is not None:
            return v
    return default


def _get_trend_val(st: dict, trend_key: str, field: str):
    """Extract a numeric value from nested trend dict."""
    t = st.get(trend_key)
    if t and isinstance(t, dict):
        v = t.get(field)
        if v is not None:
            return v
    return None


def _trend_text(tf: dict | None) -> str:
    """Человекочитаемое описание тренда."""
    if not tf or not isinstance(tf, dict):
        return "данные о тренде отсутствуют"
    slope = tf.get("slope_per_day", 0) or 0
    r2 = tf.get("r_squared")
    if abs(slope) < 0.001:
        word = "стабильный (изменение менее 0.001 тыс.м$^3$/сут$^2$)"
    elif slope > 0:
        word = f"положительный (рост): $+${abs(slope):.3f} тыс.м$^3$/сут$^2$"
    else:
        word = f"отрицательный (падение): $-${abs(slope):.3f} тыс.м$^3$/сут$^2$"
    if r2 is not None:
        word += f", $R^2 = {r2:.3f}$"
        if r2 < 0.3:
            word += " (слабая линейная зависимость)"
        elif r2 < 0.7:
            word += " (умеренная зависимость)"
        else:
            word += " (сильная зависимость)"
    return word


def _generate_segment_description(seg: dict, index: int) -> str:
    """Генерирует подробное текстовое описание участка."""
    st = seg.get("stats", {})
    name = _tex_escape(seg.get("name", f"Участок {index + 1}"))

    dur_h = _stat(st, "duration_hours", "durationH")
    dur_d = dur_h / 24 if dur_h else 0
    downtime_h = _stat(st, "downtime_hours", "downtimeH")
    working_h = max(dur_h - downtime_h, 0) if dur_h else 0
    util = _stat(st, "utilization_pct")
    points = int(_stat(st, "data_points", "dataPoints"))

    mean_f = _stat(st, "mean_flow", "meanFlow")
    median_f = _stat(st, "median_flow", "medianFlow")
    eff_f = _stat(st, "effective_daily")
    min_f = _stat(st, "min_flow")
    max_f = _stat(st, "max_flow")
    cum = _stat(st, "cumulative_flow", "cumFlow")

    p_tube = _stat(st, "mean_p_tube")
    p_line = _stat(st, "mean_p_line")
    dp = _stat(st, "mean_dp")
    purges = int(_stat(st, "purge_count", "purgeCount"))

    p = []  # paragraphs

    # 1. Период
    p.append(
        f"Участок \\textbf{{{name}}} охватывает период "
        f"с {seg.get('start_iso', '')[:16].replace('T', ' ')} "
        f"по {seg.get('end_iso', '')[:16].replace('T', ' ')} "
        f"общей продолжительностью \\textbf{{{dur_h:.1f} часов}} "
        f"({dur_d:.1f} суток). "
        f"Из них рабочее время составило {working_h:.1f} ч, "
        f"простои --- {downtime_h:.1f} ч"
        + (f" (КИВ = {util:.1f}\\%)" if util else "")
        + f". Всего {points} точек измерений."
    )

    # 2. Дебит
    p.append(
        f"\\textbf{{Дебит газа:}} средний {mean_f:.3f}, "
        f"медианный {median_f:.3f}"
        + (f", эффективный {eff_f:.3f}" if eff_f else "")
        + f" тыс.м$^3$/сут. "
        f"Диапазон: от {min_f:.3f} до {max_f:.3f} тыс.м$^3$/сут. "
        f"Накопленная добыча за период --- \\textbf{{{cum:.1f} тыс.м$^3$}}."
    )

    # 3. Давления
    if p_tube or p_line:
        p.append(
            f"\\textbf{{Давления:}} "
            f"среднее трубное {p_tube:.2f} кгс/см$^2$, "
            f"среднее линейное {p_line:.2f} кгс/см$^2$, "
            f"средний перепад $\\Delta P$ = {dp:.2f} кгс/см$^2$."
        )

    # 4. Продувки и простои
    if purges > 0:
        p.append(f"За период зафиксировано \\textbf{{{purges} продувок}}.")
    else:
        p.append("Продувок за период не зафиксировано.")

    if downtime_h > 0.1:
        p.append(
            f"Суммарные простои составили {downtime_h:.1f} часов "
            f"({downtime_h / dur_h * 100:.1f}\\% от общего времени)."
            if dur_h > 0 else
            f"Суммарные простои: {downtime_h:.1f} часов."
        )

    # 5. Тренд
    tf = st.get("trend_flow")
    p.append(f"\\textbf{{Тренд дебита:}} {_trend_text(tf)}.")

    td = st.get("trend_dp")
    if td and isinstance(td, dict) and td.get("slope_per_day") is not None:
        slope_dp = td["slope_per_day"]
        if abs(slope_dp) > 0.001:
            dir_dp = "рост" if slope_dp > 0 else "падение"
            p.append(
                f"Тренд перепада давления: {dir_dp} "
                f"({slope_dp:+.3f} кгс/см$^2$/сут)."
            )

    tp = st.get("trend_p_tube")
    if tp and isinstance(tp, dict) and tp.get("slope_per_day") is not None:
        slope_pt = tp["slope_per_day"]
        if abs(slope_pt) > 0.001:
            dir_pt = "рост" if slope_pt > 0 else "падение"
            p.append(
                f"Тренд трубного давления: {dir_pt} "
                f"({slope_pt:+.3f} кгс/см$^2$/сут)."
            )

    return "\n\n".join(p)


def _generate_conclusions(segments: list[dict]) -> str:
    """Генерирует подробные выводы по сравнительному анализу."""

    if len(segments) == 1:
        seg = segments[0]
        st = seg.get("stats", {})
        name = _tex_escape(seg.get("name", "Участок"))
        tf = st.get("trend_flow")
        return (
            f"По результатам анализа участка \\textbf{{{name}}}:\n\n"
            f"\\begin{{itemize}}\n"
            f"\\item Средний дебит: {_stat(st, 'mean_flow', 'meanFlow'):.3f}, "
            f"медианный: {_stat(st, 'median_flow', 'medianFlow'):.3f} тыс.м$^3$/сут\n"
            f"\\item Накопленная добыча: {_stat(st, 'cumulative_flow', 'cumFlow'):.1f} тыс.м$^3$\n"
            f"\\item Тренд дебита: {_trend_text(tf)}\n"
            f"\\item КИВ: {_stat(st, 'utilization_pct'):.1f}\\%\n"
            f"\\end{{itemize}}"
        )

    # --- Множественное сравнение ---
    lines = []

    # Примечание о разных периодах
    lines.append(
        "\\textit{{Примечание: участки имеют разную продолжительность, "
        "поэтому абсолютные накопленные показатели (тыс.м$^3$, часы) "
        "не подлежат прямому сравнению. "
        "Сравниваются удельные показатели (средние, медианы, тренды).}}\n"
    )

    lines.append("По результатам сравнительного анализа:\n")
    lines.append("\\begin{enumerate}")

    # 1. Сравнение дебитов
    ref = segments[0]
    ref_st = ref.get("stats", {})
    ref_name = _tex_escape(ref.get("name", ""))
    ref_mean = _stat(ref_st, "mean_flow", "meanFlow")
    ref_median = _stat(ref_st, "median_flow", "medianFlow")

    for seg in segments[1:]:
        st = seg.get("stats", {})
        sname = _tex_escape(seg.get("name", ""))
        s_mean = _stat(st, "mean_flow", "meanFlow")
        s_median = _stat(st, "median_flow", "medianFlow")

        if ref_mean > 0:
            d_mean = (s_mean - ref_mean) / ref_mean * 100
            d_word = "выше" if d_mean > 0 else "ниже"
            lines.append(
                f"\\item \\textbf{{Дебит:}} на участке \\textbf{{{sname}}} "
                f"средний дебит ({s_mean:.3f}) "
                f"на {abs(d_mean):.1f}\\% {d_word}, чем на \\textbf{{{ref_name}}} "
                f"({ref_mean:.3f} тыс.м$^3$/сут). "
                f"Медиана: {s_median:.3f} vs {ref_median:.3f}."
            )

    # 2. Сравнение давлений
    ref_dp = _stat(ref_st, "mean_dp")
    for seg in segments[1:]:
        st = seg.get("stats", {})
        sname = _tex_escape(seg.get("name", ""))
        s_dp = _stat(st, "mean_dp")
        s_pt = _stat(st, "mean_p_tube")
        ref_pt = _stat(ref_st, "mean_p_tube")
        if ref_dp > 0 and s_dp > 0:
            d_dp = s_dp - ref_dp
            lines.append(
                f"\\item \\textbf{{Давления:}} средний $\\Delta P$ на \\textbf{{{sname}}} "
                f"составил {s_dp:.2f} кгс/см$^2$ "
                f"({'больше' if d_dp > 0 else 'меньше'} на {abs(d_dp):.2f} "
                f"по сравнению с {ref_dp:.2f} на \\textbf{{{ref_name}}}). "
                f"P трубное: {s_pt:.2f} vs {ref_pt:.2f}."
            )

    # 3. Тренды
    for seg in segments:
        st = seg.get("stats", {})
        sname = _tex_escape(seg.get("name", ""))
        lines.append(
            f"\\item \\textbf{{Тренд {sname}:}} {_trend_text(st.get('trend_flow'))}."
        )

    # 4. КИВ
    utils = []
    for seg in segments:
        st = seg.get("stats", {})
        u = _stat(st, "utilization_pct")
        utils.append((seg.get("name", ""), u))
    if all(u > 0 for _, u in utils):
        parts = [f"{_tex_escape(n)}: {u:.1f}\\%" for n, u in utils]
        lines.append(
            f"\\item \\textbf{{Использование времени (КИВ):}} "
            + ", ".join(parts) + "."
        )

    # 5. Итог
    sorted_by_flow = sorted(
        segments,
        key=lambda s: _stat(s.get("stats", {}), "mean_flow", "meanFlow"),
        reverse=True,
    )
    best_name = _tex_escape(sorted_by_flow[0].get("name", ""))
    best_flow = _stat(sorted_by_flow[0].get("stats", {}), "mean_flow", "meanFlow")
    lines.append(
        f"\\item \\textbf{{Итого:}} наилучшие показатели дебита наблюдались "
        f"на участке \\textbf{{{best_name}}} "
        f"(средний {best_flow:.3f} тыс.м$^3$/сут)."
    )

    lines.append("\\end{enumerate}")
    return "\n".join(lines)


def generate_segment_comparison_report(
    segments: list[dict],
    well_number: str,
    well_name: str = "",
) -> str:
    """
    Генерирует PDF со сравнительным анализом участков.

    Returns: относительный путь к PDF (для static/).
    """
    _ensure_dirs()

    report_id = uuid.uuid4().hex[:8]

    # 1. Generate combined chart
    combined_chart_path = None
    try:
        from .segment_chart_renderer import render_combined_chart, render_segment_chart

        chart_png = CHARTS_DIR / f"segments_combined_{report_id}.png"
        render_combined_chart(
            segments, chart_png,
            title=f"Скважина {well_number} — Сравнение участков",
        )
        combined_chart_path = str(chart_png.resolve())
    except Exception as e:
        log.error("Combined chart failed: %s", e, exc_info=True)

    # 2. Generate individual charts
    for i, seg in enumerate(segments):
        try:
            from .segment_chart_renderer import render_segment_chart
            color = SEGMENT_COLORS[i % len(SEGMENT_COLORS)]
            seg_png = CHARTS_DIR / f"segment_{report_id}_{i}.png"
            render_segment_chart(
                seg, seg_png,
                color=color,
                title=f"{seg['name']}",
            )
            seg["_chart_path"] = str(seg_png.resolve())
        except Exception as e:
            log.error("Segment chart %d failed: %s", i, e)
            seg["_chart_path"] = None

    # 3. Compute trends and descriptions
    for i, seg in enumerate(segments):
        flows = [f for f in seg.get("flow_rate", []) if f is not None and f > 0]
        _, trend_label = _compute_trend(flows)
        seg["_trend_label"] = trend_label
        seg["_description"] = _generate_segment_description(seg, i)

    # 4. Prepare context
    def _tslope(st, key):
        """Extract trend slope."""
        t = st.get(key)
        if t and isinstance(t, dict) and t.get("slope_per_day") is not None:
            return _fmt(t["slope_per_day"])
        return "\u2014"

    def _tr2(st):
        t = st.get("trend_flow")
        if t and isinstance(t, dict) and t.get("r_squared") is not None:
            return _fmt(t["r_squared"])
        return "\u2014"

    seg_contexts = []
    for i, seg in enumerate(segments):
        st = seg.get("stats", {})
        dur_h = _stat(st, "duration_hours", "durationH")
        dur_d = dur_h / 24 if dur_h else 0
        downtime_h = _stat(st, "downtime_hours", "downtimeH")
        working_h = max(dur_h - downtime_h, 0) if dur_h else 0

        ctx = {
            "name": _tex_escape(seg.get("name", f"Участок {i + 1}")),
            "period_start": seg.get("start_iso", "")[:16].replace("T", " "),
            "period_end": seg.get("end_iso", "")[:16].replace("T", " "),
            "duration_h": _fmt(dur_h, 1),
            "duration_d": _fmt(dur_d, 1),
            "working_h": _fmt(working_h, 1),
            "downtime_h": _fmt(downtime_h, 1),
            "utilization_pct": _fmt(_stat(st, "utilization_pct"), 1),
            "mean_flow": _fmt(_stat(st, "mean_flow", "meanFlow")),
            "median_flow": _fmt(_stat(st, "median_flow", "medianFlow")),
            "effective_flow": _fmt(_stat(st, "effective_daily")),
            "min_flow": _fmt(_stat(st, "min_flow")),
            "max_flow": _fmt(_stat(st, "max_flow")),
            "cum_flow": _fmt(_stat(st, "cumulative_flow", "cumFlow"), 1),
            "mean_p_tube": _fmt(_stat(st, "mean_p_tube"), 2),
            "mean_p_line": _fmt(_stat(st, "mean_p_line"), 2),
            "mean_dp": _fmt(_stat(st, "mean_dp"), 2),
            "purge_count": str(int(_stat(st, "purge_count", "purgeCount"))),
            "trend_q_slope": _tslope(st, "trend_flow"),
            "trend_dp_slope": _tslope(st, "trend_dp"),
            "trend_pt_slope": _tslope(st, "trend_p_tube"),
            "trend_r2": _tr2(st),
            "data_points": str(int(_stat(st, "data_points", "dataPoints"))),
            "chart_path": seg.get("_chart_path"),
            "description": seg.get("_description", ""),
        }
        seg_contexts.append(ctx)

    conclusions = _generate_conclusions(segments)

    # Build diff_rows for comparison table (only for 2+ segments)
    diff_rows = []
    if len(segments) >= 2:
        all_stats = [seg.get("stats", {}) for seg in segments]

        # Metrics to compare: (label, keys, digits, show_diff?)
        compare_metrics = [
            ("Q среднее (тыс.м$^3$/сут)", ("mean_flow", "meanFlow"), 3, True),
            ("Q медиана", ("median_flow", "medianFlow"), 3, True),
            ("Q эффект.", ("effective_daily",), 3, True),
            ("Q мин", ("min_flow",), 3, True),
            ("Q макс", ("max_flow",), 3, True),
            ("КИВ (\\%)", ("utilization_pct",), 1, True),
            ("Накоплено (тыс.м$^3$)", ("cumulative_flow", "cumFlow"), 1, False),
            ("P труб. ср (кгс/см$^2$)", ("mean_p_tube",), 2, True),
            ("P лин. ср", ("mean_p_line",), 2, True),
            ("$\\Delta P$ ср", ("mean_dp",), 2, True),
            ("Продувок", ("purge_count", "purgeCount"), 0, False),
            ("Простои (ч)", ("downtime_hours", "downtimeH"), 1, False),
            ("Период (ч)", ("duration_hours", "durationH"), 1, False),
            ("Точек", ("data_points", "dataPoints"), 0, False),
            ("Тренд Q (/сут)", None, 3, True),
            ("Тренд $\\Delta P$ (/сут)", None, 3, True),
            ("Тренд P тр. (/сут)", None, 3, True),
            ("$R^2$ (Q)", None, 3, True),
        ]

        for label, keys, dig, show_diff in compare_metrics:
            vals_raw = []
            vals_str = []
            for st in all_stats:
                if keys is None:
                    # Special trend/R² rows
                    if "Q" in label and "R^2" not in label:
                        v = _get_trend_val(st, "trend_flow", "slope_per_day")
                    elif "Delta" in label or "delta" in label.lower():
                        v = _get_trend_val(st, "trend_dp", "slope_per_day")
                    elif "P тр" in label:
                        v = _get_trend_val(st, "trend_p_tube", "slope_per_day")
                    elif "R^2" in label:
                        v = _get_trend_val(st, "trend_flow", "r_squared")
                    else:
                        v = None
                else:
                    v = _stat(st, *keys)
                vals_raw.append(v)
                vals_str.append(_fmt(v, dig) if v else "\u2014")

            # Diff: last - first (where it makes sense)
            diff_str = ""
            if show_diff and len(vals_raw) == 2:
                v0, v1 = vals_raw[0], vals_raw[1]
                if v0 is not None and v1 is not None and v0 != 0:
                    d = v1 - v0
                    diff_str = f"{d:+.{dig}f}"

            diff_rows.append({
                "label": label,
                "vals": vals_str,
                "diff": diff_str if diff_str else "\u2014",
            })

    context = {
        "well_number": _tex_escape(well_number),
        "well_name": _tex_escape(well_name),
        "generated_at": datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC"),
        "segment_count": len(segments),
        "segments": seg_contexts,
        "combined_chart_path": combined_chart_path,
        "conclusions": conclusions,
        "diff_rows": diff_rows,
    }

    # 5. Render LaTeX
    env = _get_latex_env()
    template = env.get_template("segment_comparison_report.tex")
    latex_source = template.render(**context)

    # Debug: сохраняем rendered LaTeX для проверки
    debug_tex = TEMP_DIR / f"segment_comparison_{report_id}_debug.tex"
    debug_tex.write_text(latex_source, encoding="utf-8")
    log.info("Debug LaTeX saved: %s", debug_tex)

    # 6. Compile to PDF
    base_name = f"segment_comparison_{report_id}"
    _compile_latex(latex_source, base_name)

    log.info("Segment comparison PDF generated: %s", base_name)
    return f"generated/pdf/{base_name}.pdf"
