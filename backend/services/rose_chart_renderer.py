"""matplotlib-рендер диаграмм для блока «Роза критериев» (kind=criteria_rose).

Два PNG для PDF-отчёта:
  • render_rose_chart_png — полярная роза с зоной нормы / зоной риска,
    лепестками и подписями рангов.
  • render_contributions_chart_png — горизонтальный stacked-бар «Вклад в балл».

Источник данных — snapshot, который сохранён в customer_report_block.data_snapshot
(см. backend/services/customer_rose_service.compute_rose).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


SCORING_KEYS = ("decline", "p_wh_down", "p_wh_cv", "p_fl_up", "shutdown", "freq")

LABELS_SHORT = {
    "decline":   "Q ↓",
    "p_wh_down": "P уст ↓",
    "p_wh_cv":   "P уст волат.",
    "p_fl_up":   "P шл ↑",
    "shutdown":  "Простой %",
    "freq":      "Эпизоды/30д",
}

LABELS_FULL = {
    "decline":   "Снижение Q",
    "p_wh_down": "Снижение P устья",
    "p_wh_cv":   "Волатильность P устья",
    "p_fl_up":   "Рост P шлейфа",
    "shutdown":  "Доля простоя",
    "freq":      "Частота простоев",
}


def _setup_matplotlib():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    return matplotlib, plt, np


def render_rose_chart_png(
    snapshot: dict, output_path: str | Path,
    *, figsize: tuple[float, float] = (6.0, 6.0), dpi: int = 150,
) -> Optional[Path]:
    """Полярная роза (см. ТЗ §2 «Геометрия и визуальные слои»).

    snapshot должен содержать поле `ranks` (dict key→0..100).
    """
    _, plt, np = _setup_matplotlib()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ranks_raw = (snapshot or {}).get("ranks") or {}
    ranks = [float(ranks_raw.get(k) if ranks_raw.get(k) is not None else 0.0)
             for k in SCORING_KEYS]

    days = (snapshot or {}).get("period_days")
    weak = isinstance(days, (int, float)) and 0 < days < 15

    n = len(SCORING_KEYS)
    angles = [i * 2 * np.pi / n for i in range(n)]
    bar_w = (2 * np.pi / n) * 0.55
    dense_theta = np.linspace(0, 2 * np.pi, 361)
    alpha_petal = 0.70 if weak else 0.90

    fig = plt.figure(figsize=figsize, dpi=dpi)
    fig.patch.set_facecolor("#ffffff")
    ax = fig.add_subplot(111, polar=True)

    # Слой 1: «зона нормы парка» — серая заливка (0..50)
    ax.fill_between(dense_theta, 0, 50, color="#dde3ed",
                    alpha=0.55, linewidth=0, zorder=0)
    # Слой 2: «зона риска» — светло-красное кольцо (75..100)
    ax.fill_between(dense_theta, 75, 100, color="#d62728",
                    alpha=0.10, linewidth=0, zorder=1)
    # Слой 3: пунктирная окружность r=50
    ax.plot(dense_theta, [50] * len(dense_theta),
            color="#5a6f8a", linewidth=1.0, linestyle="--", zorder=2)

    # Слой 4: лепестки + подписи рангов
    for ang, rank in zip(angles, ranks):
        if rank > 50:
            ax.bar([ang], [rank - 50], bottom=50, width=bar_w,
                   color="#d62728", alpha=alpha_petal,
                   edgecolor="#8b0000", linewidth=0.6, zorder=3)
        elif rank < 50:
            ax.bar([ang], [50 - rank], bottom=rank, width=bar_w,
                   color="#28a745", alpha=alpha_petal * 0.85,
                   edgecolor="#155724", linewidth=0.5, zorder=3)
        if rank >= 50:
            lbl_r, lbl_c = min(rank + 6, 98), "#8b0000"
        else:
            lbl_r, lbl_c = max(rank - 6, 4), "#155724"
        ax.text(ang, lbl_r, f"{rank:.0f}",
                ha="center", va="center", fontsize=8.5,
                weight="bold", color=lbl_c, zorder=6)

    # Слой 5: соединяющий полигон формы
    poly_th = list(angles) + [angles[0]]
    poly_r = list(ranks) + [ranks[0]]
    ax.plot(poly_th, poly_r,
            color="#d62728", linewidth=1.0, linestyle=":",
            alpha=0.6, zorder=5)

    # Слой 6: подписи осей + радиальная шкала
    ax.set_theta_zero_location("E")
    ax.set_theta_direction(1)
    ax.set_xticks(angles)
    ax.set_xticklabels([LABELS_SHORT.get(k, k) for k in SCORING_KEYS], fontsize=9)
    ax.set_ylim(0, 100)
    ax.set_yticks([25, 50, 75, 100])
    ax.set_yticklabels(["25", "50 — норма", "75", "100"],
                       fontsize=7, color="#777")
    ax.set_rlabel_position(30)
    ax.tick_params(axis="x", pad=10)
    ax.tick_params(axis="y", pad=1)
    ax.grid(color="#e0e0e0", linewidth=0.6)
    ax.spines["polar"].set_edgecolor("#9ca3af")
    ax.spines["polar"].set_linewidth(0.6)

    score = snapshot.get("score")
    title = "Роза критериев"
    if score is not None:
        title += f" · балл {float(score):.1f}"
    if weak and days:
        title += f"\n(мало данных: {int(days)} дн.)"
    ax.set_title(title, fontsize=11, color="#374151", pad=14)

    fig.tight_layout()
    fig.savefig(output_path, format="png", bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    return output_path


def render_contributions_chart_png(
    snapshot: dict, output_path: str | Path,
    *, figsize: tuple[float, float] = (8.0, 4.5), dpi: int = 150,
) -> Optional[Path]:
    """Горизонтальный stacked-бар «Вклад в балл» (см. ТЗ §11)."""
    _, plt, np = _setup_matplotlib()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    contrib = (snapshot or {}).get("contributions") or {}
    items = []
    for k in SCORING_KEYS:
        c = contrib.get(k) or {}
        actual = float(c.get("actual") or 0.0)
        mx = float(c.get("max") or 0.0)
        w = float(c.get("weight") or 0.0)
        items.append({
            "key": k, "label": LABELS_FULL.get(k, k),
            "actual": actual, "max": mx, "weight": w,
            "gap": max(0.0, mx - actual),
        })
    items.sort(key=lambda x: x["actual"], reverse=True)

    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    fig.patch.set_facecolor("#ffffff")
    y = np.arange(len(items))
    actual = np.array([it["actual"] for it in items])
    gap = np.array([it["gap"] for it in items])

    ax.barh(y, actual, color="#d62728", height=0.7, zorder=3)
    ax.barh(y, gap, left=actual, color="#e6e6e6", height=0.7, zorder=2)

    for i, it in enumerate(items):
        if it["actual"] > 1.0:
            ax.text(it["actual"] / 2, i, f"{it['actual']:.1f}",
                    ha="center", va="center", color="white",
                    weight="bold", fontsize=9, zorder=4)
        x_lbl = it["actual"] + it["gap"] + 0.5
        ax.text(x_lbl, i, f"вес {it['weight']:.2f}",
                ha="left", va="center", fontsize=8, color="#666")

    ax.set_yticks(y)
    ax.set_yticklabels([it["label"] for it in items], fontsize=9)
    ax.invert_yaxis()
    max_pts = max(it["max"] for it in items) if items else 50.0
    ax.set_xlim(0, max(45.0, max_pts * 1.35))
    ax.set_xlabel("Баллы (ранг × вес)", fontsize=9, color="#444")
    ax.grid(axis="x", color="#eee", linewidth=0.6, zorder=1)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", labelsize=8, colors="#374151", length=3)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_edgecolor("#9ca3af")
        ax.spines[s].set_linewidth(0.6)

    score = snapshot.get("score")
    title = "Вклад в балл"
    if score is not None:
        title += f" · итого {float(score):.1f}"
    ax.set_title(title, fontsize=11, color="#374151", pad=10)

    fig.tight_layout()
    fig.savefig(output_path, format="png", bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    return output_path
