"""
Генерация PNG-графика дебита для вставки в LaTeX-отчёт.

Использует matplotlib с Agg-бэкендом (без GUI).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def render_flow_chart_png(
    daily_results: list[dict],
    output_path: str | Path,
    baseline_results: list[dict] | None = None,
    title: str = "",
) -> Path:
    """
    Рисует график дебита по суточным результатам и сохраняет в PNG.

    Parameters
    ----------
    daily_results : list of dicts с ключами result_date, avg_flow_rate,
                    cumulative_flow, avg_p_tube, avg_p_line
    output_path : путь для сохранения PNG
    baseline_results : опциональные суточные результаты базового сценария
    title : заголовок графика

    Returns
    -------
    Path к сохранённому PNG
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from datetime import datetime

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Парсинг данных
    dates = [
        datetime.strptime(r["result_date"], "%Y-%m-%d")
        if isinstance(r["result_date"], str)
        else datetime.combine(r["result_date"], datetime.min.time())
        for r in daily_results
    ]
    avg_q = [r.get("avg_flow_rate") or 0 for r in daily_results]
    cum_q = [r.get("cumulative_flow") or 0 for r in daily_results]
    p_tube = [r.get("avg_p_tube") or 0 for r in daily_results]
    p_line = [r.get("avg_p_line") or 0 for r in daily_results]

    # Создаём фигуру 16x6 дюймов, 150 dpi
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 6), dpi=150,
                                     gridspec_kw={"height_ratios": [2, 1]},
                                     sharex=True)
    fig.subplots_adjust(hspace=0.08)

    # --- Верхний график: дебит ---
    ax1.fill_between(dates, avg_q, alpha=0.15, color="#ff9800")
    ax1.plot(dates, avg_q, color="#ff9800", linewidth=1.2, label="Дебит, тыс.м3/сут")

    # Накопленная добыча (вторая ось)
    ax1_twin = ax1.twinx()
    ax1_twin.plot(dates, cum_q, color="#1565c0", linewidth=1, linestyle="--",
                  label="Накопл., тыс.м3")
    ax1_twin.set_ylabel("Накопл. добыча, тыс.м3", fontsize=9, color="#1565c0")
    ax1_twin.tick_params(axis="y", labelcolor="#1565c0", labelsize=8)

    # Baseline (пунктир)
    if baseline_results:
        bl_dates = [
            datetime.strptime(r["result_date"], "%Y-%m-%d")
            if isinstance(r["result_date"], str)
            else datetime.combine(r["result_date"], datetime.min.time())
            for r in baseline_results
        ]
        bl_q = [r.get("avg_flow_rate") or 0 for r in baseline_results]
        ax1.plot(bl_dates, bl_q, color="#9e9e9e", linewidth=1, linestyle=":",
                 label="Базовый дебит")

    ax1.set_ylabel("Дебит, тыс.м3/сут", fontsize=9)
    ax1.tick_params(axis="y", labelsize=8)
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="upper left", fontsize=8)
    ax1_twin.legend(loc="upper right", fontsize=8)

    if title:
        ax1.set_title(title, fontsize=11, fontweight="bold")

    # --- Нижний график: давление ---
    ax2.plot(dates, p_tube, color="#e53935", linewidth=1, label="P трубн.")
    ax2.plot(dates, p_line, color="#43a047", linewidth=1, label="P сбор.")
    ax2.set_ylabel("Давление, кгс/см2", fontsize=9)
    ax2.set_xlabel("Дата", fontsize=9)
    ax2.tick_params(axis="both", labelsize=8)
    ax2.grid(True, alpha=0.3)
    ax2.legend(loc="upper right", fontsize=8)

    # Форматирование оси X
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    ax2.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=30)

    # Сохранение
    fig.savefig(str(output_path), bbox_inches="tight", dpi=150)
    plt.close(fig)

    log.info("Chart saved: %s", output_path)
    return output_path
