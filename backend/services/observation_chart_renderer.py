"""
PNG generation для главы наблюдения — Phase F.

Pure functions: принимают snapshot dict + output_path, пишут PNG, возвращают None.
Не читают БД, не делают side-effects кроме file write.

ЗАПРЕЩЕНО: db, Session, query, execute.
РАЗРЕШЕНО: matplotlib Agg, pathlib, numpy.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless backend — ВАЖНО ставить до pyplot import
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["figure.dpi"] = 150
plt.rcParams["axes.grid"] = True
plt.rcParams["grid.alpha"] = 0.3

DEF_FIGSIZE = (7.5, 4.6)
DEF_DPI = 150


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _is_nan(v: Any) -> bool:
    if v is None:
        return True
    try:
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return True


def _safe_y(values: list) -> list[float]:
    """[None, 1, 'x', 2.0] → [nan, 1.0, nan, 2.0]"""
    import numpy as np

    out = []
    for v in values or []:
        if v is None:
            out.append(float("nan"))
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            out.append(float("nan"))
    return out


def _parse_dates(dates: list[str]) -> list:
    from datetime import datetime

    out = []
    for d in dates or []:
        if not d:
            out.append(None)
            continue
        try:
            out.append(datetime.fromisoformat(str(d)[:10]))
        except Exception:
            out.append(None)
    return out


def _style_axes(ax) -> None:
    ax.grid(True, which="major", linestyle="-", color="#e5e7eb", linewidth=0.6, zorder=0)
    ax.tick_params(axis="both", labelsize=9, colors="#374151", length=3)
    for s in ax.spines.values():
        s.set_edgecolor("#9ca3af")
        s.set_linewidth(0.6)


def _format_xdate(ax, mdates_mod) -> None:
    ax.xaxis.set_major_locator(mdates_mod.AutoDateLocator(minticks=5, maxticks=12))
    ax.xaxis.set_major_formatter(mdates_mod.DateFormatter("%d.%m.%y"))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right")


def _ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _no_data_figure(output_path: Path, title: str = "Нет данных") -> None:
    """Сохраняет пустую фигуру с текстом 'Нет данных'."""
    fig, ax = plt.subplots(figsize=DEF_FIGSIZE, dpi=DEF_DPI)
    fig.patch.set_facecolor("#ffffff")
    ax.text(
        0.5, 0.5, title, ha="center", va="center",
        fontsize=13, color="#6b7280", transform=ax.transAxes,
    )
    ax.set_axis_off()
    fig.tight_layout(pad=0.4)
    fig.savefig(output_path, dpi=DEF_DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 1. Baseline — 4-metric bar chart с error bars
# ---------------------------------------------------------------------------


def render_baseline_chart(snapshot: dict, output_path: str | Path) -> None:
    """
    4-metric bar chart: P_tube_mean, P_line_mean, ΔP_mean, Q_mean с error bars (std).
    NaN-safe: пропускает метрики с отсутствующими данными.
    """
    import numpy as np

    output_path = _ensure_dir(output_path)
    metrics = snapshot.get("metrics") or {}

    metric_defs = [
        ("p_tube", "P_tube\n(атм)", "#1d4ed8"),
        ("p_line", "P_line\n(атм)", "#16a34a"),
        ("dp",     "ΔP\n(атм)",    "#dc2626"),
        ("q",      "Q\n(тыс.м³/сут)", "#f59e0b"),
    ]

    labels, means, stds, colors = [], [], [], []
    for key, label, color in metric_defs:
        m = metrics.get(key)
        if not isinstance(m, dict):
            continue
        mean_v = m.get("mean")
        std_v = m.get("std")
        if _is_nan(mean_v):
            continue
        labels.append(label)
        means.append(float(mean_v))
        stds.append(float(std_v) if not _is_nan(std_v) else 0.0)
        colors.append(color)

    if not labels:
        _no_data_figure(output_path, "Нет метрик")
        return

    fig, ax = plt.subplots(figsize=(min(DEF_FIGSIZE[0], 2.2 * len(labels) + 2), DEF_FIGSIZE[1]), dpi=DEF_DPI)
    fig.patch.set_facecolor("#ffffff")

    x = np.arange(len(labels))
    bars = ax.bar(x, means, color=colors, edgecolor="#1f2937", linewidth=0.5,
                  yerr=stds, capsize=5, error_kw={"linewidth": 1.2, "ecolor": "#374151"}, zorder=3)

    # Подписи значений над барами
    for bar, mean_v in zip(bars, means):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            bar.get_height() + max(stds) * 0.05 + 0.01,
            f"{mean_v:.3g}",
            ha="center", va="bottom", fontsize=8.5, color="#111827",
        )

    ax.set_title("Метрики baseline (среднее ± std)", fontsize=11, color="#111827", pad=6)
    ax.set_ylabel("Значение", fontsize=9, color="#374151")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    _style_axes(ax)

    fig.tight_layout(pad=0.4)
    fig.savefig(output_path, dpi=DEF_DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 2. Period — time series Q + ΔP
# ---------------------------------------------------------------------------


def render_period_timeseries_chart(snapshot: dict, output_path: str | Path) -> None:
    """
    Q + ΔP time series из raw.chart_payload.
    NaN-safe.
    """
    output_path = _ensure_dir(output_path)
    raw = snapshot.get("raw") or {}
    chart_payload = raw.get("chart_payload") or {}

    dates = _parse_dates(chart_payload.get("dates") or [])
    q_vals = _safe_y(chart_payload.get("q") or [])
    dp_vals = _safe_y(chart_payload.get("dp") or [])

    valid_dates = [d for d in dates if d is not None]
    if not valid_dates or (
        all(_is_nan(v) for v in q_vals) and all(_is_nan(v) for v in dp_vals)
    ):
        _no_data_figure(output_path, "Нет данных временного ряда")
        return

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(DEF_FIGSIZE[0], DEF_FIGSIZE[1] * 1.3), dpi=DEF_DPI, sharex=True)
    fig.patch.set_facecolor("#ffffff")

    if q_vals and not all(_is_nan(v) for v in q_vals):
        ax1.plot(dates, q_vals, color="#1d4ed8", linewidth=1.3, marker="o",
                 markersize=2.5, label="Q (тыс.м³/сут)", zorder=3)
        ax1.set_ylabel("Q, тыс.м³/сут", fontsize=9, color="#374151")
        ax1.set_title("Дебит Q и перепад давления ΔP", fontsize=11, color="#111827", pad=6)
        _style_axes(ax1)
    else:
        ax1.text(0.5, 0.5, "Q: нет данных", ha="center", va="center",
                 fontsize=10, color="#6b7280", transform=ax1.transAxes)
        ax1.set_axis_off()

    if dp_vals and not all(_is_nan(v) for v in dp_vals):
        ax2.plot(dates, dp_vals, color="#dc2626", linewidth=1.3, marker="o",
                 markersize=2.5, label="ΔP (атм)", zorder=3)
        ax2.axhline(0, color="#9ca3af", linewidth=0.7, linestyle="--", zorder=1)
        ax2.set_ylabel("ΔP, атм", fontsize=9, color="#374151")
        _style_axes(ax2)
        _format_xdate(ax2, mdates)
    else:
        ax2.text(0.5, 0.5, "ΔP: нет данных", ha="center", va="center",
                 fontsize=10, color="#6b7280", transform=ax2.transAxes)
        ax2.set_axis_off()

    fig.tight_layout(pad=0.4)
    fig.savefig(output_path, dpi=DEF_DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 3. Period — comparison bar chart vs B1
# ---------------------------------------------------------------------------


def render_period_compare_b1_chart(snapshot: dict, output_path: str | Path) -> None:
    """
    Bar chart comparison: текущий период vs B1 baseline.
    Использует snapshot.comparisons.with_b1.deltas.
    """
    import numpy as np

    output_path = _ensure_dir(output_path)
    comparisons = snapshot.get("comparisons") or {}
    with_b1 = comparisons.get("with_b1") or {}

    if with_b1.get("status") != "ok":
        _no_data_figure(output_path, "Нет сравнения с baseline")
        return

    deltas = with_b1.get("deltas") or {}

    metric_defs = [
        ("p_tube", "P_tube"),
        ("p_line", "P_line"),
        ("dp",     "ΔP"),
        ("q",      "Q"),
    ]

    labels, baseline_vals, current_vals = [], [], []
    for key, label in metric_defs:
        d = deltas.get(key)
        if not isinstance(d, dict):
            continue
        bv = d.get("baseline_value")
        cv = d.get("current_value")
        if _is_nan(bv) or _is_nan(cv):
            continue
        labels.append(label)
        baseline_vals.append(float(bv))
        current_vals.append(float(cv))

    if not labels:
        _no_data_figure(output_path, "Нет данных для сравнения")
        return

    x = np.arange(len(labels))
    bar_w = 0.35
    fig, ax = plt.subplots(figsize=(max(DEF_FIGSIZE[0], 2.0 * len(labels) + 1), DEF_FIGSIZE[1]), dpi=DEF_DPI)
    fig.patch.set_facecolor("#ffffff")

    ax.bar(x - bar_w / 2, baseline_vals, bar_w, color="#60a5fa", edgecolor="#1f2937",
           linewidth=0.5, label="Baseline", zorder=3)
    ax.bar(x + bar_w / 2, current_vals, bar_w, color="#34d399", edgecolor="#1f2937",
           linewidth=0.5, label="Текущий период", zorder=3)

    ax.set_title("Сравнение с baseline (B1)", fontsize=11, color="#111827", pad=6)
    ax.set_ylabel("Значение", fontsize=9, color="#374151")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=2, fontsize=9, frameon=False)
    _style_axes(ax)

    fig.tight_layout(pad=0.4)
    fig.savefig(output_path, dpi=DEF_DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 4. Segment — Q time series с changepoints и colored bands
# ---------------------------------------------------------------------------


def render_segment_chart(snapshot: dict, output_path: str | Path) -> None:
    """
    Q time series с:
    - vertical lines на changepoints (красные пунктиры)
    - colored bands per segment (чередующиеся)
    Использует raw.chart_payload.q.
    NaN-safe.
    """
    import numpy as np
    from datetime import datetime

    output_path = _ensure_dir(output_path)
    raw = snapshot.get("raw") or {}
    chart_payload = raw.get("chart_payload") or {}
    segments = snapshot.get("segments") or []
    changepoints = snapshot.get("changepoints") or []

    dates = _parse_dates(chart_payload.get("dates") or [])
    q_vals = _safe_y(chart_payload.get("q") or [])

    valid_dates = [d for d in dates if d is not None]
    if not valid_dates or all(_is_nan(v) for v in q_vals):
        # Пробуем нарисовать хотя бы схему из сегментов без временного ряда
        if not segments:
            _no_data_figure(output_path, "Нет данных для сегментного графика")
            return

    fig, ax = plt.subplots(figsize=DEF_FIGSIZE, dpi=DEF_DPI)
    fig.patch.set_facecolor("#ffffff")

    # Рисуем Q time series (если есть)
    has_timeseries = valid_dates and not all(_is_nan(v) for v in q_vals)
    if has_timeseries:
        ax.plot(dates, q_vals, color="#1d4ed8", linewidth=1.3, marker="o",
                markersize=2.5, label="Q (тыс.м³/сут)", zorder=4)

    # Colored bands per segment
    segment_colors = ["#dbeafe", "#fef3c7", "#d1fae5", "#fce7f3", "#ede9fe", "#ffedd5"]
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        # g-fix-4: реальные ключи C2 — start_date / end_date
        seg_from_str = seg.get("start_date")
        seg_to_str = seg.get("end_date")
        if not seg_from_str or not seg_to_str:
            continue
        try:
            seg_from = datetime.fromisoformat(str(seg_from_str)[:10])
            seg_to = datetime.fromisoformat(str(seg_to_str)[:10])
        except Exception:
            continue
        color = segment_colors[i % len(segment_colors)]
        ax.axvspan(seg_from, seg_to, alpha=0.25, color=color, zorder=1, label=f"Сегмент {i + 1}")

    # Vertical lines на changepoints
    for cp in changepoints:
        if not isinstance(cp, dict):
            continue
        cp_date_str = cp.get("date") or cp.get("changepoint_date")
        if not cp_date_str:
            continue
        try:
            cp_date = datetime.fromisoformat(str(cp_date_str)[:10])
        except Exception:
            continue
        ax.axvline(cp_date, color="#dc2626", linewidth=1.5, linestyle="--", alpha=0.8, zorder=5)
        # Подпись magnitude если есть
        mag = cp.get("magnitude_pct")
        if not _is_nan(mag):
            ax.annotate(
                f"{float(mag):+.1f}%",
                xy=(cp_date, ax.get_ylim()[1] if has_timeseries else 1.0),
                xytext=(4, -12), textcoords="offset points",
                fontsize=7.5, color="#dc2626", alpha=0.9, zorder=6,
            )

    ax.set_title("Сегментный анализ Q", fontsize=11, color="#111827", pad=6)
    ax.set_ylabel("Q, тыс.м³/сут", fontsize=9, color="#374151")
    _style_axes(ax)

    if has_timeseries:
        _format_xdate(ax, mdates)

    # Легенда — не более 5 элементов
    handles, lbls = ax.get_legend_handles_labels()
    if handles:
        ax.legend(handles[:5], lbls[:5],
                  loc="upper center", bbox_to_anchor=(0.5, -0.15),
                  ncol=3, fontsize=8, frameon=False)

    fig.tight_layout(pad=0.4)
    fig.savefig(output_path, dpi=DEF_DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 5. Analysis — 4 отдельных графика (wz3obs_v1 формат)
# ---------------------------------------------------------------------------


def render_analysis_pressures_chart(snapshot: dict, output_path: str | Path) -> None:
    """
    График давлений: P трубное + P линейное.
    Данные из snapshot.chart: {dates, p_wellhead, p_flowline}.
    """
    output_path = _ensure_dir(output_path)
    chart = snapshot.get("chart") or {}

    dates = _parse_dates(chart.get("dates") or [])
    p_wellhead = _safe_y(chart.get("p_wellhead") or [])
    p_flowline = _safe_y(chart.get("p_flowline") or [])

    valid_dates = [d for d in dates if d is not None]
    has_wellhead = p_wellhead and not all(_is_nan(v) for v in p_wellhead)
    has_flowline = p_flowline and not all(_is_nan(v) for v in p_flowline)

    if not valid_dates or (not has_wellhead and not has_flowline):
        _no_data_figure(output_path, "Нет данных давлений")
        return

    # Проверка соответствия длин массивов
    if has_wellhead and len(dates) != len(p_wellhead):
        _no_data_figure(output_path, "Несоответствие данных")
        return
    if has_flowline and len(dates) != len(p_flowline):
        _no_data_figure(output_path, "Несоответствие данных")
        return

    fig, ax = plt.subplots(figsize=DEF_FIGSIZE, dpi=DEF_DPI)
    fig.patch.set_facecolor("#ffffff")

    if has_wellhead:
        ax.plot(dates, p_wellhead, color="#1f77b4", linewidth=1.3,
                label="P трубное", zorder=3)
    if has_flowline:
        ax.plot(dates, p_flowline, color="#d62728", linewidth=1.3,
                label="P линейное", zorder=3)

    ax.set_title("Давления, кгс/см²", fontsize=11, color="#111827", pad=6)
    ax.set_ylabel("кгс/см²", fontsize=9, color="#374151")
    ax.legend(loc="upper right", fontsize=8, frameon=False)
    _style_axes(ax)
    _format_xdate(ax, mdates)

    fig.tight_layout(pad=0.4)
    fig.savefig(output_path, dpi=DEF_DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_analysis_dp_chart(snapshot: dict, output_path: str | Path) -> None:
    """
    График перепада ΔP.
    Данные из snapshot.chart: {dates, dp}.
    """
    output_path = _ensure_dir(output_path)
    chart = snapshot.get("chart") or {}

    dates = _parse_dates(chart.get("dates") or [])
    dp_vals = _safe_y(chart.get("dp") or [])

    valid_dates = [d for d in dates if d is not None]
    if not valid_dates or not dp_vals or all(_is_nan(v) for v in dp_vals):
        _no_data_figure(output_path, "Нет данных ΔP")
        return

    # Проверка соответствия длин массивов
    if len(dates) != len(dp_vals):
        _no_data_figure(output_path, "Несоответствие данных")
        return

    fig, ax = plt.subplots(figsize=DEF_FIGSIZE, dpi=DEF_DPI)
    fig.patch.set_facecolor("#ffffff")

    ax.plot(dates, dp_vals, color="#ef6c00", linewidth=1.6, label="ΔP", zorder=3)
    ax.fill_between(dates, dp_vals, alpha=0.1, color="#ef6c00", zorder=2)
    ax.axhline(0, color="#9ca3af", linewidth=0.7, linestyle="--", zorder=1)

    ax.set_title("Перепад ΔP, кгс/см²", fontsize=11, color="#111827", pad=6)
    ax.set_ylabel("кгс/см²", fontsize=9, color="#374151")
    _style_axes(ax)
    _format_xdate(ax, mdates)

    fig.tight_layout(pad=0.4)
    fig.savefig(output_path, dpi=DEF_DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_analysis_q_chart(snapshot: dict, output_path: str | Path) -> None:
    """
    График дебита Q(t).
    Данные из snapshot.chart: {dates, q_gas_total | q_gas_working}.
    """
    output_path = _ensure_dir(output_path)
    chart = snapshot.get("chart") or {}

    dates = _parse_dates(chart.get("dates") or [])
    q_vals = _safe_y(chart.get("q_gas_total") or chart.get("q_gas_working") or [])

    valid_dates = [d for d in dates if d is not None]
    if not valid_dates or not q_vals or all(_is_nan(v) for v in q_vals):
        _no_data_figure(output_path, "Нет данных Q")
        return

    # Проверка соответствия длин массивов
    if len(dates) != len(q_vals):
        _no_data_figure(output_path, "Несоответствие данных")
        return

    fig, ax = plt.subplots(figsize=DEF_FIGSIZE, dpi=DEF_DPI)
    fig.patch.set_facecolor("#ffffff")

    ax.plot(dates, q_vals, color="#2e7d32", linewidth=1.6, label="Q факт.", zorder=3)

    ax.set_title("Дебит Q(t), тыс.м³/сут", fontsize=11, color="#111827", pad=6)
    ax.set_ylabel("тыс.м³/сут", fontsize=9, color="#374151")
    _style_axes(ax)
    _format_xdate(ax, mdates)

    fig.tight_layout(pad=0.4)
    fig.savefig(output_path, dpi=DEF_DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_analysis_utilization_chart(snapshot: dict, output_path: str | Path) -> None:
    """
    Рабочее время по дням (stacked bar chart).
    Данные из snapshot.chart: {dates, dp, p_wellhead}.
    """
    import numpy as np
    from collections import defaultdict

    output_path = _ensure_dir(output_path)
    chart = snapshot.get("chart") or {}

    dates_raw = chart.get("dates") or []
    dp_vals = chart.get("dp") or []
    p_vals = chart.get("p_wellhead") or []

    if not dates_raw:
        _no_data_figure(output_path, "Нет данных для расчёта времени")
        return

    # Группируем по дням
    by_day = defaultdict(lambda: {"work": 0, "down": 0})
    for i, d_str in enumerate(dates_raw):
        if not d_str:
            continue
        try:
            day = str(d_str)[:10]
        except Exception:
            continue
        dp_v = dp_vals[i] if i < len(dp_vals) else None
        p_v = p_vals[i] if i < len(p_vals) else None
        # Простой: dp <= 0.1 или нет данных
        if dp_v is None or _is_nan(dp_v) or dp_v <= 0.1 or p_v is None or _is_nan(p_v):
            by_day[day]["down"] += 1
        else:
            by_day[day]["work"] += 1

    if not by_day:
        _no_data_figure(output_path, "Нет данных для расчёта времени")
        return

    day_labels = sorted(by_day.keys())
    work_hours = [by_day[d]["work"] for d in day_labels]
    down_hours = [by_day[d]["down"] for d in day_labels]

    fig, ax = plt.subplots(figsize=(max(DEF_FIGSIZE[0], len(day_labels) * 0.5 + 2), DEF_FIGSIZE[1]), dpi=DEF_DPI)
    fig.patch.set_facecolor("#ffffff")

    x = np.arange(len(day_labels))
    ax.bar(x, work_hours, color="#16a34a", label="Рабочих", zorder=3)
    ax.bar(x, down_hours, bottom=work_hours, color="#dc2626", label="Простой", zorder=3)

    ax.set_title("Учёт рабочего времени по дням", fontsize=11, color="#111827", pad=6)
    ax.set_ylabel("часов", fontsize=9, color="#374151")
    ax.set_xticks(x)
    # Показываем только каждый N-й день если слишком много
    step = max(1, len(day_labels) // 10)
    ax.set_xticklabels([d[5:] if i % step == 0 else "" for i, d in enumerate(day_labels)],
                       fontsize=7, rotation=45, ha="right")
    ax.legend(loc="upper right", fontsize=8, frameon=False)
    _style_axes(ax)

    fig.tight_layout(pad=0.4)
    fig.savefig(output_path, dpi=DEF_DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------
# segment_analysis_v1 chart (from customer_daily SegmentBlocksWidget)
# ---------------------------------------------------------------------------


def render_segment_analysis_chart(snapshot: dict, output_path: str | Path) -> None:
    """Рендерит график для блока segment_analysis (v1 и v2).

    Поддерживаемые форматы snapshot:
        segment-demo (v1): chart_data: {dates, primary: {name, values}}
        customer-daily (v2): chart_data: {dates, q_total, q_working, ...}
        segments_extended: [{num, start_idx, end_idx, type, ...}, ...]
        cp_marks: [{idx, date, tag}, ...]
    """
    import numpy as np
    from datetime import datetime

    chart_data = snapshot.get("chart_data") or {}
    # Поддержка двух форматов: segments_extended (v2) или segments (v1/segment-demo)
    segments = snapshot.get("segments_extended") or snapshot.get("segments") or []
    cp_marks = snapshot.get("cp_marks") or snapshot.get("changepoints") or []

    dates_raw = chart_data.get("dates") or []

    # Поддержка обоих форматов: segment-demo (primary.values) и customer-daily (q_total)
    primary = chart_data.get("primary") or {}
    values = primary.get("values") or []
    series_name = primary.get("name", "Q дебит")

    # Если нет primary.values, пробуем customer-daily формат
    if not values:
        values = chart_data.get("q_total") or chart_data.get("q_working") or []
        series_name = "Q дебит, тыс.м³/сут"

    if not dates_raw or not values or len(dates_raw) != len(values):
        _no_data_figure(output_path, "Нет данных для графика")
        return

    # Parse dates
    dates = []
    for d in dates_raw:
        if not d:
            dates.append(None)
            continue
        try:
            if isinstance(d, str):
                if "T" in d:
                    dates.append(datetime.fromisoformat(d.replace("Z", "+00:00")))
                else:
                    dates.append(datetime.fromisoformat(d))
            else:
                dates.append(d)
        except Exception:
            dates.append(None)

    y_vals = _safe_y(values)

    # Segment colors
    seg_colors = {
        "stable": "#22c55e",
        "rising": "#3b82f6",
        "falling": "#ef4444",
        "volatile": "#f59e0b",
        "unknown": "#9ca3af",
    }

    fig, ax = plt.subplots(figsize=DEF_FIGSIZE, dpi=DEF_DPI)
    fig.patch.set_facecolor("#ffffff")

    # Plot main line
    valid_dates = [d for d in dates if d is not None]
    valid_y = [y for d, y in zip(dates, y_vals) if d is not None]
    ax.plot(valid_dates, valid_y, color="#6b7280", linewidth=0.7, alpha=0.6, zorder=2)

    # Color segments
    for seg in segments:
        si = seg.get("start_idx", 0)
        ei = seg.get("end_idx", len(dates))
        seg_type = seg.get("type", "unknown")
        color = seg_colors.get(seg_type, "#9ca3af")

        seg_dates = [d for d in dates[si:ei] if d is not None]
        seg_y = [y for d, y in zip(dates[si:ei], y_vals[si:ei]) if d is not None]

        if seg_dates and seg_y:
            ax.plot(seg_dates, seg_y, color=color, linewidth=1.8, zorder=3)
            ax.fill_between(seg_dates, seg_y, alpha=0.15, color=color, zorder=1)

            # Trend line
            slope = seg.get("slope")
            intercept = seg.get("intercept")
            if slope is not None and intercept is not None:
                x_idx = np.arange(len(seg_y))
                trend = slope * x_idx + intercept
                ax.plot(seg_dates, trend, color=color, linewidth=1.2,
                        linestyle="--", alpha=0.8, zorder=4)

    # Changepoint markers
    for cp in cp_marks:
        cp_idx = cp.get("idx")
        cp_tag = cp.get("tag", "")
        if cp_idx is not None and 0 <= cp_idx < len(dates) and dates[cp_idx] is not None:
            ax.axvline(dates[cp_idx], color="#ef4444", linewidth=1.5, linestyle="-",
                      alpha=0.8, zorder=5)
            ax.text(dates[cp_idx], ax.get_ylim()[1] * 0.98, cp_tag,
                    fontsize=7, color="#ef4444", ha="center", va="top", zorder=6)

    ax.set_title(f"{series_name} — сегментный анализ", fontsize=10, color="#111827", pad=8)
    ax.set_ylabel(series_name, fontsize=9, color="#374151")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.xticks(fontsize=8, rotation=30, ha="right")
    _style_axes(ax)

    fig.tight_layout(pad=0.4)
    fig.savefig(output_path, dpi=DEF_DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
