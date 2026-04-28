"""Серверный рендер 4 диаграмм блока «Анализ исходных данных» в PDF.

Зеркалит 4 графика со страницы /customer-daily через matplotlib (Agg):
  1. Давления — P_тр, затрубное, P_лин, P_статич. во времени
  2. Перепад давления (ΔP) во времени
  3. Дебиты + простой — Q_общ, Q_раб (линии) + простой (бары на 2-й оси)
  4. Помесячно — Q общий ср/мед, Q рабочий ср/мед (групп. бары)

Все функции принимают подготовленный payload (dict из well_chart_payload)
или monthly_stats list, и сохраняют PNG в указанный путь.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Соотношение сторон страничной графики PDF (~16:6 — широкая полоса)
DEF_FIGSIZE = (13.5, 5.0)
DEF_DPI = 150


# ───── Хелперы ─────

def _setup_matplotlib():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    return plt, mdates


def _parse_dates(dates: list[str]):
    from datetime import datetime
    out = []
    for d in dates:
        if not d:
            out.append(None); continue
        try:
            out.append(datetime.fromisoformat(d[:10]))
        except Exception:
            out.append(None)
    return out


def _safe_y(values: list) -> list[float]:
    """[None, 1, 'x', 2.0] → [nan, 1.0, nan, 2.0]"""
    import numpy as np
    out = []
    for v in values or []:
        if v is None:
            out.append(np.nan); continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            out.append(np.nan)
    return out


def _style_axes(ax) -> None:
    ax.grid(True, which="major", linestyle="-",
            color="#e5e7eb", linewidth=0.6, zorder=0)
    ax.tick_params(axis="both", labelsize=8, colors="#374151", length=3)
    for s in ax.spines.values():
        s.set_edgecolor("#9ca3af"); s.set_linewidth(0.6)


def _format_xdate(ax, mdates) -> None:
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=12))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m.%y"))


# ───── 1. Давления ─────

def render_pressures_chart(
    payload: dict, output_path: str | Path,
    *, figsize: tuple[float, float] = DEF_FIGSIZE, dpi: int = DEF_DPI,
) -> Optional[Path]:
    """P_тр + Затрубное + P_лин + P_статич. во времени."""
    plt, mdates = _setup_matplotlib()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dates = _parse_dates(payload.get("dates") or [])
    if not dates or all(d is None for d in dates):
        return None

    series = [
        ("Заказчик: устье",     "p_wellhead", "#1d4ed8", "o"),
        ("Заказчик: затрубное", "p_annular",  "#f59e0b", "s"),
        ("Заказчик: шлейф",     "p_flowline", "#16a34a", "o"),
        ("Заказчик: статич.",   "p_static",   "#dc2626", "o"),
    ]

    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    fig.patch.set_facecolor("#ffffff")

    plotted = False
    for label, key, color, marker in series:
        ys = _safe_y(payload.get(key) or [])
        if not ys or all(_is_nan(y) for y in ys):
            continue
        ax.plot(dates, ys, color=color, linewidth=1.2, marker=marker,
                markersize=3, label=label, zorder=3)
        plotted = True

    if not plotted:
        plt.close(fig)
        return None

    ax.set_title("Давления, кгс/см²", fontsize=11, color="#111827", pad=6)
    ax.set_ylabel("Давление, кгс/см²", fontsize=9, color="#374151")
    _style_axes(ax)
    _format_xdate(ax, mdates)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=4,
              fontsize=8, frameon=False)

    fig.tight_layout(pad=0.4)
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return output_path


# ───── 2. Перепад давления ─────

def render_dp_chart(
    payload: dict, output_path: str | Path,
    *, figsize: tuple[float, float] = DEF_FIGSIZE, dpi: int = DEF_DPI,
) -> Optional[Path]:
    plt, mdates = _setup_matplotlib()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dates = _parse_dates(payload.get("dates") or [])
    dp = _safe_y(payload.get("dp") or [])
    if not dates or not dp or all(_is_nan(y) for y in dp):
        return None

    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    fig.patch.set_facecolor("#ffffff")
    ax.plot(dates, dp, color="#dc2626", linewidth=1.4, marker="o",
            markersize=3, zorder=3, label="ΔP = P_уст − P_лин")
    ax.axhline(0, color="#9ca3af", linewidth=0.7, linestyle="--", zorder=1)
    ax.set_title("Перепад давления, кгс/см²", fontsize=11, color="#111827", pad=6)
    ax.set_ylabel("ΔP, кгс/см²", fontsize=9, color="#374151")
    _style_axes(ax)
    _format_xdate(ax, mdates)

    fig.tight_layout(pad=0.4)
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return output_path


# ───── 3. Дебиты и простой ─────

def render_flow_downtime_chart(
    payload: dict, output_path: str | Path,
    *, figsize: tuple[float, float] = DEF_FIGSIZE, dpi: int = DEF_DPI,
) -> Optional[Path]:
    """Q общий + рабочий (линии) + простой в мин/сут (бары, ось справа)."""
    plt, mdates = _setup_matplotlib()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dates = _parse_dates(payload.get("dates") or [])
    q_tot = _safe_y(payload.get("q_gas_total")   or [])
    q_wrk = _safe_y(payload.get("q_gas_working") or [])
    sd    = _safe_y(payload.get("shutdown_min")  or [])

    if not dates:
        return None
    if all(_is_nan(y) for y in q_tot) and all(_is_nan(y) for y in q_wrk):
        return None

    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    fig.patch.set_facecolor("#ffffff")

    import numpy as np
    from datetime import datetime as _dt

    def _monthly_trends(ys, dates_, color, base_label, drawn_label_set):
        """Помесячные линии регрессии: по каждому календарному месяцу
        отдельная пунктирная линия (start..end месяца) с подписью наклона.

        Параметр drawn_label_set нужен, чтобы общий «обобщённый» легендный
        пункт «<base_label>, тренд за месяц» добавлялся один раз.
        """
        if not ys or all(_is_nan(y) for y in ys):
            return
        # Группируем точки по (год, месяц)
        buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
        for d, y in zip(dates_, ys):
            if d is None or _is_nan(y):
                continue
            key = (d.year, d.month)
            buckets.setdefault(key, []).append((d.toordinal(), float(y)))

        legend_label = f"{base_label}, тренд за месяц"
        for (yy, mm), pts in sorted(buckets.items()):
            if len(pts) < 3:
                continue  # для регрессии нужно минимум 3 точки
            xs = np.array([p[0] for p in pts], dtype=float)
            ys_arr = np.array([p[1] for p in pts], dtype=float)
            try:
                slope, intercept = np.polyfit(xs, ys_arr, 1)
            except (np.linalg.LinAlgError, ValueError):
                continue
            x_fit = np.array([xs.min(), xs.max()])
            y_fit = slope * x_fit + intercept
            d_fit = [_dt.fromordinal(int(round(v))) for v in x_fit]
            label = legend_label if base_label not in drawn_label_set else None
            ax.plot(d_fit, y_fit, color=color, linewidth=1.6,
                    linestyle="--", alpha=0.85, zorder=5, label=label)
            # Подпись наклона рядом с серединой линии
            mid_x = (x_fit[0] + x_fit[1]) / 2.0
            mid_y = slope * mid_x + intercept
            ax.annotate(
                f"{slope:+.2f}",
                xy=(_dt.fromordinal(int(round(mid_x))), mid_y),
                xytext=(0, 6), textcoords="offset points",
                ha="center", fontsize=7.5, color=color, alpha=0.95,
                zorder=6,
            )
            drawn_label_set.add(base_label)

    drawn_labels: set[str] = set()
    if q_tot and not all(_is_nan(y) for y in q_tot):
        ax.plot(dates, q_tot, color="#1d4ed8", linewidth=1.3, marker="o",
                markersize=3, label="Q общий", zorder=4)
        _monthly_trends(q_tot, dates, "#1e40af", "Q общий", drawn_labels)
    if q_wrk and not all(_is_nan(y) for y in q_wrk):
        ax.plot(dates, q_wrk, color="#16a34a", linewidth=1.3, marker="o",
                markersize=3, label="Q рабочий", zorder=4)
        _monthly_trends(q_wrk, dates, "#166534", "Q рабочий", drawn_labels)

    ax.set_title("Дебиты и простой (с помесячным трендом)",
                 fontsize=11, color="#111827", pad=6)
    ax.set_ylabel("Q, тыс.м³/сут", fontsize=9, color="#374151")

    # Простой — бары на правой оси
    if sd and not all(_is_nan(y) for y in sd):
        import numpy as np
        ax2 = ax.twinx()
        sd_arr = np.array(sd)
        sd_arr = np.where(np.isnan(sd_arr), 0, sd_arr)
        # Бар по середине дня; ширина в днях → 0.85
        ax2.bar(dates, sd_arr, width=0.85, color="#fca5a5",
                edgecolor="#dc2626", linewidth=0.4, alpha=0.6,
                zorder=2, label="Простой")
        ax2.set_ylabel("Простой, мин/сут", fontsize=9, color="#7f1d1d")
        ax2.tick_params(axis="y", labelsize=8, colors="#7f1d1d")
        ax2.set_ylim(0, max(1500, float(sd_arr.max() or 0) * 1.05))
        for s in ax2.spines.values():
            s.set_edgecolor("#9ca3af"); s.set_linewidth(0.6)
        # Объединённая легенда
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2,
                  loc="upper center", bbox_to_anchor=(0.5, -0.18),
                  ncol=3, fontsize=8, frameon=False)
    else:
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18),
                  ncol=3, fontsize=8, frameon=False)

    _style_axes(ax)
    _format_xdate(ax, mdates)

    fig.tight_layout(pad=0.4)
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return output_path


# ───── 4. Помесячные бары ─────

def render_monthly_bars(
    months: list[dict], output_path: str | Path,
    *, figsize: tuple[float, float] = DEF_FIGSIZE, dpi: int = DEF_DPI,
) -> Optional[Path]:
    """Группированные бары: Q общ ср, Q общ мед, Q раб ср, Q раб мед — за месяц."""
    plt, mdates = _setup_matplotlib()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not months:
        return None

    import numpy as np
    labels = [m.get("month_label") or m.get("month") for m in months]
    series = [
        ("Q общ. ср.",  "mean_q_total",     "#1d4ed8"),
        ("Q общ. мед.", "median_q_total",   "#60a5fa"),
        ("Q раб. ср.",  "mean_q_working",   "#15803d"),
        ("Q раб. мед.", "median_q_working", "#86efac"),
    ]
    n_series = len(series)
    n_months = len(labels)
    bar_w = 0.8 / n_series

    fig, ax = plt.subplots(figsize=(min(figsize[0], 1.2 * max(n_months, 3) + 4),
                                    figsize[1]), dpi=dpi)
    fig.patch.set_facecolor("#ffffff")

    x = np.arange(n_months)
    for i, (label, key, color) in enumerate(series):
        ys = [m.get(key) for m in months]
        ys = [(np.nan if v is None else float(v)) for v in ys]
        ax.bar(x + (i - (n_series - 1) / 2) * bar_w, ys, width=bar_w,
               color=color, edgecolor="#1f2937", linewidth=0.4,
               label=label, zorder=3)

    ax.set_title("Помесячно: Q общий и рабочий", fontsize=11,
                 color="#111827", pad=6)
    ax.set_ylabel("Q, тыс.м³/сут", fontsize=9, color="#374151")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8.5, color="#374151")
    _style_axes(ax)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=4,
              fontsize=8, frameon=False)

    fig.tight_layout(pad=0.4)
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return output_path


# ───── Низкоуровневые ─────

def _is_nan(v) -> bool:
    import math
    if v is None:
        return True
    try:
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return True
