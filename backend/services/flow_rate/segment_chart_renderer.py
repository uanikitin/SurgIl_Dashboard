"""
Генерация PNG-графиков для сравнительного отчёта по участкам.

- Комбинированный график (все участки на одной кривой)
- Индивидуальные графики для каждого участка
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

SEGMENT_COLORS = [
    "#3b82f6", "#ef4444", "#10b981", "#f59e0b",
    "#8b5cf6", "#ec4899", "#14b8a6", "#fb923c",
]

SEGMENT_FILLS = [
    "rgba(59,130,246,0.15)", "rgba(239,68,68,0.15)",
    "rgba(16,185,129,0.15)", "rgba(245,158,11,0.15)",
    "rgba(139,92,246,0.15)", "rgba(236,72,153,0.15)",
    "rgba(20,184,166,0.15)", "rgba(251,146,60,0.15)",
]


def _parse_ts(ts_list: list[str]) -> list[datetime]:
    out = []
    for t in ts_list:
        if isinstance(t, str):
            try:
                out.append(datetime.fromisoformat(t.replace("Z", "+00:00").rstrip("+")))
            except ValueError:
                out.append(datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S"))
        else:
            out.append(t)
    return out


def render_combined_chart(
    segments: list[dict],
    output_path: str | Path,
    title: str = "",
) -> Path:
    """
    Рисует все участки на одном графике с трендами.
    Каждый участок — своим цветом и пунктирным трендом.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import numpy as np

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(16, 10), dpi=150,
        gridspec_kw={"height_ratios": [2, 1]},
        sharex=True,
    )
    fig.subplots_adjust(hspace=0.08)

    ax1_twin = ax1.twinx()

    for i, seg in enumerate(segments):
        color = SEGMENT_COLORS[i % len(SEGMENT_COLORS)]
        fill = SEGMENT_FILLS[i % len(SEGMENT_FILLS)]
        dates = _parse_ts(seg["timestamps"])
        flows = [v or 0 for v in seg["flow_rate"]]
        cums = [v or 0 for v in seg["cumulative_flow"]]
        p_tubes = [v or 0 for v in seg["p_tube"]]
        p_lines = [v or 0 for v in seg["p_line"]]
        label = seg["name"]

        # Flow rate (filled area + line)
        ax1.fill_between(dates, flows, alpha=0.12, color=color)
        ax1.plot(dates, flows, color=color, linewidth=1.2, label=label)

        # Trend line — use pre-computed stats if available
        stats = seg.get("stats", {})
        tf = stats.get("trend_flow") if isinstance(stats.get("trend_flow"), dict) else None
        if tf and tf.get("intercept") is not None and tf.get("slope_per_day") is not None:
            # Use the same trend as shown on the web page
            start_dt = dates[0]
            dur_h = (dates[-1] - dates[0]).total_seconds() / 3600
            y_start = tf["intercept"]
            y_end = tf["intercept"] + (tf["slope_per_day"] / 24) * dur_h
            ax1.plot([dates[0], dates[-1]], [y_start, y_end],
                     color=color, linewidth=2, linestyle="--",
                     alpha=0.7, label=f"{label} (тренд)")
        else:
            # Fallback: compute from data
            valid = [(j, flows[j]) for j in range(len(flows)) if flows[j] is not None and flows[j] > 0]
            if len(valid) >= 2:
                x_idx = np.array([v[0] for v in valid], dtype=float)
                y_val = np.array([v[1] for v in valid])
                coeffs = np.polyfit(x_idx, y_val, 1)
                trend_y = np.polyval(coeffs, np.arange(len(dates), dtype=float))
                ax1.plot(dates, trend_y, color=color, linewidth=2, linestyle="--",
                         alpha=0.7, label=f"{label} (тренд)")

        # Cumulative on twin axis
        ax1_twin.plot(dates, cums, color=color, linewidth=0.8, linestyle=":",
                      alpha=0.5)

        # Pressures
        ax2.plot(dates, p_tubes, color=color, linewidth=1, label=f"{label} P тр.")
        ax2.plot(dates, p_lines, color=color, linewidth=1, linestyle="--",
                 alpha=0.6)

    ax1.set_ylabel("Дебит, тыс.м\u00b3/сут", fontsize=9)
    ax1.tick_params(axis="y", labelsize=8)
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="upper left", fontsize=8, ncol=2)
    ax1_twin.set_ylabel("Накопл., тыс.м\u00b3", fontsize=9, color="#666")
    ax1_twin.tick_params(axis="y", labelsize=8, labelcolor="#666")

    if title:
        ax1.set_title(title, fontsize=11, fontweight="bold")

    ax2.set_ylabel("Давление, кгс/см\u00b2", fontsize=9)
    ax2.set_xlabel("Дата", fontsize=9)
    ax2.tick_params(axis="both", labelsize=8)
    ax2.grid(True, alpha=0.3)
    ax2.legend(loc="upper right", fontsize=7, ncol=2)

    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    ax2.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=30)

    fig.savefig(str(output_path), bbox_inches="tight", dpi=150)
    plt.close(fig)
    log.info("Combined chart saved: %s", output_path)
    return output_path


def render_segment_chart(
    segment: dict,
    output_path: str | Path,
    color: str = "#ff9800",
    title: str = "",
) -> Path:
    """
    Рисует индивидуальный график для одного участка
    с трендом и ключевыми метриками.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import numpy as np

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dates = _parse_ts(segment["timestamps"])
    flows = [v or 0 for v in segment["flow_rate"]]
    cums = [v or 0 for v in segment["cumulative_flow"]]
    p_tubes = [v or 0 for v in segment["p_tube"]]
    p_lines = [v or 0 for v in segment["p_line"]]

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(16, 10), dpi=150,
        gridspec_kw={"height_ratios": [2, 1]},
        sharex=True,
    )
    fig.subplots_adjust(hspace=0.08)

    # Flow rate
    ax1.fill_between(dates, flows, alpha=0.15, color=color)
    ax1.plot(dates, flows, color=color, linewidth=1.2, label="Дебит")

    # Trend — use pre-computed stats if available
    stats = segment.get("stats", {})
    tf = stats.get("trend_flow") if isinstance(stats.get("trend_flow"), dict) else None
    if tf and tf.get("intercept") is not None and tf.get("slope_per_day") is not None:
        dur_h = (dates[-1] - dates[0]).total_seconds() / 3600
        y_start = tf["intercept"]
        y_end = tf["intercept"] + (tf["slope_per_day"] / 24) * dur_h
        ax1.plot([dates[0], dates[-1]], [y_start, y_end],
                 color=color, linewidth=2, linestyle="--",
                 alpha=0.7, label="Тренд")
    else:
        valid = [(j, flows[j]) for j in range(len(flows)) if flows[j] and flows[j] > 0]
        if len(valid) >= 2:
            x_idx = np.array([v[0] for v in valid], dtype=float)
            y_val = np.array([v[1] for v in valid])
            coeffs = np.polyfit(x_idx, y_val, 1)
            trend_y = np.polyval(coeffs, np.arange(len(dates), dtype=float))
            ax1.plot(dates, trend_y, color=color, linewidth=2, linestyle="--",
                     alpha=0.7, label="Тренд")

    # Cumulative
    ax1_twin = ax1.twinx()
    ax1_twin.plot(dates, cums, color="#1565c0", linewidth=1, linestyle="--",
                  label="Накопл.")
    ax1_twin.set_ylabel("Накопл., тыс.м\u00b3", fontsize=9, color="#1565c0")
    ax1_twin.tick_params(axis="y", labelsize=8, labelcolor="#1565c0")

    ax1.set_ylabel("Дебит, тыс.м\u00b3/сут", fontsize=9)
    ax1.tick_params(axis="y", labelsize=8)
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="upper left", fontsize=8)
    ax1_twin.legend(loc="upper right", fontsize=8)

    if title:
        ax1.set_title(title, fontsize=11, fontweight="bold")

    # Pressures
    ax2.plot(dates, p_tubes, color="#e53935", linewidth=1, label="P трубн.")
    ax2.plot(dates, p_lines, color="#43a047", linewidth=1, label="P сбор.")
    ax2.set_ylabel("Давление, кгс/см\u00b2", fontsize=9)
    ax2.set_xlabel("Дата", fontsize=9)
    ax2.tick_params(axis="both", labelsize=8)
    ax2.grid(True, alpha=0.3)
    ax2.legend(loc="upper right", fontsize=8)

    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    ax2.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=30)

    fig.savefig(str(output_path), bbox_inches="tight", dpi=150)
    plt.close(fig)
    log.info("Segment chart saved: %s", output_path)
    return output_path
