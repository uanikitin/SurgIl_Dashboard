"""
Генерация LaTeX PDF-отчёта по сценарию анализа дебита.

Переиспользует xelatex-компиляцию из DocumentGenerator,
но с собственным шаблоном и контекстом.
"""
from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from backend.models.flow_analysis import FlowScenario, FlowResult, FlowCorrection

log = logging.getLogger(__name__)

# Директории
LATEX_TEMPLATES_DIR = Path("backend/templates/latex")
OUTPUT_DIR = Path("backend/static/generated")
PDF_DIR = OUTPUT_DIR / "pdf"
TEMP_DIR = OUTPUT_DIR / "temp"
CHARTS_DIR = OUTPUT_DIR / "charts"


def _ensure_dirs():
    for d in [PDF_DIR, TEMP_DIR, CHARTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def _get_latex_env() -> Environment:
    """Jinja2 environment с LaTeX-совместимыми разделителями."""
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
    """Компиляция LaTeX → PDF через xelatex (2 прохода)."""
    _ensure_dirs()

    tex_file = TEMP_DIR / f"{base_name}.tex"
    tex_file.write_text(tex_source, encoding="utf-8")

    for pass_num in range(2):
        result = subprocess.run(
            ["xelatex", "-interaction=nonstopmode",
             f"-output-directory={TEMP_DIR}", str(tex_file)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(TEMP_DIR),
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="ignore")
            stdout = result.stdout.decode("utf-8", errors="ignore")
            # Ищем конкретную ошибку в логе
            log.error("xelatex pass %d failed:\n%s", pass_num + 1, stderr[-500:])
            raise RuntimeError(f"xelatex compilation failed (pass {pass_num + 1})")

    temp_pdf = TEMP_DIR / f"{base_name}.pdf"
    final_pdf = PDF_DIR / f"{base_name}.pdf"

    if not temp_pdf.exists():
        raise FileNotFoundError(f"PDF not created: {temp_pdf}")

    temp_pdf.rename(final_pdf)

    # Cleanup temp files
    for ext in (".aux", ".log", ".out", ".tex"):
        f = TEMP_DIR / f"{base_name}{ext}"
        if f.exists():
            f.unlink()

    return final_pdf


def generate_flow_report(
    scenario: FlowScenario,
    db: Session,
    baseline_id: Optional[int] = None,
) -> str:
    """
    Генерирует PDF-отчёт для сценария анализа дебита.

    Returns: путь к PDF (относительно static/) для сохранения в meta.
    """
    _ensure_dirs()

    # 1. Загружаем данные
    results = (
        db.query(FlowResult)
        .filter(FlowResult.scenario_id == scenario.id)
        .order_by(FlowResult.result_date)
        .all()
    )

    corrections = (
        db.query(FlowCorrection)
        .filter(FlowCorrection.scenario_id == scenario.id)
        .order_by(FlowCorrection.sort_order)
        .all()
    )

    if not results:
        raise ValueError("Нет результатов расчёта. Сначала выполните расчёт.")

    # 2. Summary из meta
    summary = (scenario.meta or {}).get("summary", {})

    # 3. Генерируем PNG-график
    chart_path = None
    try:
        from .chart_renderer import render_flow_chart_png

        daily_dicts = [
            {
                "result_date": r.result_date.isoformat(),
                "avg_flow_rate": r.avg_flow_rate,
                "cumulative_flow": r.cumulative_flow,
                "avg_p_tube": r.avg_p_tube,
                "avg_p_line": r.avg_p_line,
            }
            for r in results
        ]

        baseline_dicts = None
        if baseline_id:
            bl_results = (
                db.query(FlowResult)
                .filter(FlowResult.scenario_id == baseline_id)
                .order_by(FlowResult.result_date)
                .all()
            )
            if bl_results:
                baseline_dicts = [
                    {
                        "result_date": r.result_date.isoformat(),
                        "avg_flow_rate": r.avg_flow_rate,
                    }
                    for r in bl_results
                ]

        well_num = scenario.well.number if scenario.well else scenario.well_id
        chart_png = CHARTS_DIR / f"flow_scenario_{scenario.id}.png"
        render_flow_chart_png(
            daily_dicts, chart_png,
            baseline_results=baseline_dicts,
            title=f"Скважина {well_num} · {scenario.name}",
        )
        # Абсолютный путь для LaTeX \includegraphics
        chart_path = str(chart_png.resolve())
    except ImportError:
        log.warning("matplotlib not installed, skipping chart generation")
    except Exception as e:
        log.error("Chart generation failed: %s", e)

    # 4. Comparison rows (если есть baseline)
    comparison_rows = []
    if baseline_id:
        try:
            from .scenario_service import compare_scenarios
            comp = compare_scenarios(scenario.id, baseline_id, "daily", db)
            for row in comp.get("rows", []):
                comparison_rows.append({
                    "period": row["period"],
                    "current": _fmt(row.get("current_avg_flow")),
                    "baseline": _fmt(row.get("baseline_avg_flow")),
                    "delta": _fmt(row.get("delta_flow")),
                    "delta_pct": _fmt(row.get("delta_flow_pct")),
                })
        except Exception as e:
            log.warning("Comparison failed: %s", e)

    # 5. Подготовка контекста
    well_number = scenario.well.number if scenario.well else str(scenario.well_id)
    well_name = scenario.well.name if scenario.well else ""

    context = {
        "well_number": _tex_escape(str(well_number)),
        "well_name": _tex_escape(well_name or ""),
        "scenario_name": _tex_escape(scenario.name),
        "scenario_description": _tex_escape(scenario.description or ""),
        "period_start": scenario.period_start.strftime("%d.%m.%Y") if scenario.period_start else "",
        "period_end": scenario.period_end.strftime("%d.%m.%Y") if scenario.period_end else "",
        "status": scenario.status,
        "is_baseline": scenario.is_baseline,
        "generated_at": datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC"),
        # Summary KPI
        "observation_days": _fmt(summary.get("observation_days")),
        "choke_mm": _fmt(summary.get("choke_mm")),
        "median_flow_rate": _fmt(summary.get("median_flow_rate")),
        "mean_flow_rate": _fmt(summary.get("mean_flow_rate")),
        "effective_flow_rate": _fmt(summary.get("effective_flow_rate")),
        "cumulative_flow": _fmt(summary.get("cumulative_flow")),
        "median_p_tube": _fmt(summary.get("median_p_tube")),
        "median_p_line": _fmt(summary.get("median_p_line")),
        "median_dp": _fmt(summary.get("median_dp")),
        "downtime_total_hours": _fmt(summary.get("downtime_total_hours")),
        "purge_loss_total": _fmt(summary.get("purge_loss_total")),
        "loss_coefficient_pct": _fmt(summary.get("loss_coefficient_pct")),
        "purge_venting_count": summary.get("purge_venting_count", 0),
        # Chart
        "chart_path": chart_path,
        # Params
        "multiplier": scenario.multiplier,
        "c1": scenario.c1,
        "c2": scenario.c2,
        "c3": scenario.c3,
        "critical_ratio": scenario.critical_ratio,
        "smooth_label": (
            f"Савицкий-Голай (окно={scenario.smooth_window}, порядок={scenario.smooth_polyorder})"
            if scenario.smooth_enabled else "Отключено"
        ),
        "exclude_purge_ids": scenario.exclude_purge_ids or "нет",
        # Daily results
        "daily_results": [
            {
                "result_date": r.result_date.strftime("%d.%m"),
                "avg_flow_rate": _fmt(r.avg_flow_rate),
                "min_flow_rate": _fmt(r.min_flow_rate),
                "max_flow_rate": _fmt(r.max_flow_rate),
                "cumulative_flow": _fmt(r.cumulative_flow),
                "avg_p_tube": _fmt(r.avg_p_tube),
                "avg_p_line": _fmt(r.avg_p_line),
            }
            for r in results
        ],
        # Corrections
        "corrections": [
            {
                "correction_type": _tex_escape(c.correction_type),
                "dt_start": c.dt_start.strftime("%d.%m %H:%M") if c.dt_start else "",
                "dt_end": c.dt_end.strftime("%d.%m %H:%M") if c.dt_end else "",
                "reason": _tex_escape(c.reason or ""),
            }
            for c in corrections
        ],
        # Comparison
        "comparison_rows": comparison_rows,
    }

    # 6. Рендерим LaTeX
    env = _get_latex_env()
    template = env.get_template("flow_analysis_report.tex")
    latex_source = template.render(**context)

    # 7. Компилируем в PDF
    base_name = f"flow_report_{scenario.id}"
    pdf_path = _compile_latex(latex_source, base_name)

    # 8. Сохраняем путь в meta
    meta = dict(scenario.meta) if scenario.meta else {}
    meta["pdf_path"] = str(pdf_path)
    meta["pdf_generated_at"] = datetime.utcnow().isoformat()
    scenario.meta = meta
    db.flush()

    log.info("PDF report generated: %s", pdf_path)
    return f"generated/pdf/{base_name}.pdf"


def _fmt(val) -> str:
    """Форматирование числа для LaTeX."""
    if val is None:
        return "—"
    if isinstance(val, float):
        return f"{val:.3f}"
    return str(val)


def _tex_escape(text: str) -> str:
    """Экранирование спецсимволов LaTeX."""
    replacements = {
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text
