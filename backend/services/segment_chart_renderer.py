"""matplotlib-рендер для блока 'segment_analysis' (kind=segment_analysis).

PDF-figure: ОДИН PNG с двумя subplot:
  • верхний (≈75% высоты): Q общий + Q рабочий (реальные точки + линия),
    тренды Q общего (сплошные цветные slope_total × x + intercept_total),
    тренды Q рабочего (пунктирные того же цвета), вертикали переломов
    (3 стиля по cp_marks.source), фоновая подсветка кластеров простоев
    (alpha 0.10), теги причин (поворот 90°, цвет #8b0000) + ✓ префикс
    для confirmed (§7.3).
  • нижний (≈18% высоты): bar chart shutdown_min — дни внутри
    shutdown_clusters насыщенно-красные, одиночные простои розовые;
    отдельная шкала Y, shared X через sharex=True.

Источник данных — snapshot формата segment_v1, который посчитал
backend/services/segment_analysis_service.compute_segment_block.

ОГРАНИЧЕНИЯ (по согласованию PLAN B):
  • Renderer отвечает ТОЛЬКО за figure.
  • Никакой narrative interpretation внутри renderer.
  • Никаких causal claims, выводов, текстовых решений.
  • Реальные тренды только slope×x + intercept. Запрет горизонтальных
    средних (см. PLAN A regression).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def _setup_matplotlib():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import numpy as np
    return matplotlib, plt, mdates, np


def _parse_iso_dates(dates_iso: list[str]):
    """ISO-строки → list[datetime] для matplotlib.

    Пустые/невалидные элементы превращаются в None — matplotlib
    интерпретирует как разрыв линии.
    """
    from datetime import datetime
    out = []
    for s in dates_iso or []:
        if not s:
            out.append(None)
            continue
        try:
            out.append(datetime.fromisoformat(str(s)[:10]))
        except Exception:
            out.append(None)
    return out


def _safe_y(values: list) -> list[float]:
    """Безопасное приведение к float: None/NaN/строки → NaN."""
    import math
    out = []
    for v in values or []:
        if v is None:
            out.append(float("nan"))
            continue
        try:
            f = float(v)
            if math.isnan(f) or math.isinf(f):
                out.append(float("nan"))
            else:
                out.append(f)
        except (TypeError, ValueError):
            out.append(float("nan"))
    return out


def render_segment_q_chart(
    snapshot: dict,
    output_path: str | Path,
    *,
    figsize: tuple[float, float] = (12.5, 6.2),
    dpi: int = 150,
) -> Optional[Path]:
    """Главный PNG для подсекции «Сегментный анализ» в PDF.

    Args:
        snapshot: dict формата segment_v1 (см. compute_segment_block).
            Обязательные ключи: chart_data, segments_extended, cp_marks,
            shutdown_clusters.
        output_path: куда писать PNG (абсолютный путь — xelatex
            запускается с cwd=TEMP_DIR, относительные не разрешатся).
        figsize: размер figure в дюймах. По умолчанию широкая горизонталь
            (12.5×6.2) — подходит под \\textwidth A4 portrait.
        dpi: разрешение. 150 — стандарт для PDF.

    Returns:
        Path к созданному PNG, либо None если рендер не удался
        (нет данных, ошибка matplotlib и т.п.).
    """
    if not snapshot or not snapshot.get("ok"):
        log.info("render_segment_q_chart: snapshot невалиден или ok=False")
        return None

    cd = snapshot.get("chart_data") or {}
    segs = snapshot.get("segments_extended") or []
    cps = snapshot.get("cp_marks") or []
    clusters = snapshot.get("shutdown_clusters") or []

    dates_iso = cd.get("dates") or []
    if not dates_iso or not segs:
        log.info("render_segment_q_chart: пустые chart_data.dates или segments")
        return None

    _, plt, mdates, np = _setup_matplotlib()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dates = _parse_iso_dates(dates_iso)
    q_total = _safe_y(cd.get("q_total"))
    q_working = _safe_y(cd.get("q_working"))
    shutdown_min = _safe_y(cd.get("shutdown_min"))

    # Set индексов «день в кластере» — для раздельных bar цветов на нижнем subplot
    cluster_idx = set()
    for cl in clusters:
        s = cl.get("start_idx")
        e = cl.get("end_idx")
        if s is not None and e is not None:
            for i in range(int(s), int(e)):
                cluster_idx.add(i)

    # ──── Создаём figure с 2 subplot (sharex) ────────────────────
    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1, sharex=True,
        figsize=figsize, dpi=dpi,
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.08},
    )
    fig.patch.set_facecolor("#ffffff")

    # ════ ВЕРХНИЙ SUBPLOT ════════════════════════════════════════

    # Слой 5а: фоновая подсветка кластеров — ПЕРВЫМ (под остальными)
    for cl in clusters:
        sd = cl.get("start_date")
        ed = cl.get("end_date")
        if not sd or not ed:
            continue
        try:
            from datetime import datetime
            sd_dt = datetime.fromisoformat(str(sd)[:10])
            ed_dt = datetime.fromisoformat(str(ed)[:10])
            ax_top.axvspan(
                sd_dt, ed_dt,
                facecolor="#dc2626", alpha=0.10,
                linewidth=0, zorder=1,
            )
        except Exception:
            log.exception("cluster axvspan failed for %s..%s", sd, ed)

    # Слой 1: Q общий — реальные точки + линия
    ax_top.plot(
        dates, q_total,
        color="#1f77b4", linewidth=1.0,
        marker="o", markersize=3.5, alpha=0.85,
        label="Q общий, факт",
        zorder=3,
    )

    # Слой 2: Q рабочий — реальные точки + линия (если есть данные)
    if any(not np.isnan(v) for v in q_working) and not all(
        abs(a - b) < 1e-9 or np.isnan(a) or np.isnan(b)
        for a, b in zip(q_total, q_working)
    ):
        ax_top.plot(
            dates, q_working,
            color="#2ca02c", linewidth=1.0,
            marker="s", markersize=3.5, alpha=0.85,
            label="Q рабочий, факт",
            zorder=3,
        )

    # Слои 3 и 3а: РЕАЛЬНЫЕ тренды per-segment.
    # Запрет горизонтальных средних — формула slope × x_rel + intercept.
    for s in segs:
        start_idx = s.get("start_idx")
        end_idx = s.get("end_idx")
        if start_idx is None or end_idx is None:
            continue
        if start_idx >= len(dates) or end_idx > len(dates) or end_idx <= start_idx:
            continue
        color = s.get("color") or "#666666"
        span = max(1, end_idx - start_idx - 1)

        # Точки границ сегмента по оси X
        try:
            x0 = dates[start_idx]
            # end_idx — exclusive (модуль так возвращает); берём последний валидный
            x1 = dates[end_idx - 1]
        except IndexError:
            continue
        if x0 is None or x1 is None:
            continue

        # Слой 3: тренд Q общего — сплошная цветная
        slope_total = s.get("slope_total")
        intercept_total = s.get("intercept_total")
        if slope_total is not None and intercept_total is not None:
            y0 = intercept_total
            y1 = intercept_total + slope_total * span
            ax_top.plot(
                [x0, x1], [y0, y1],
                color=color, linewidth=2.5, linestyle="-",
                zorder=4,
                label=f"Сег {s.get('num')}: {s.get('type','')} ({slope_total:+.3f}/сут)",
            )

        # Слой 3а: тренд Q рабочего — пунктир того же цвета
        slope_working = s.get("slope_working")
        intercept_working = s.get("intercept_working")
        if slope_working is not None and intercept_working is not None:
            y0 = intercept_working
            y1 = intercept_working + slope_working * span
            ax_top.plot(
                [x0, x1], [y0, y1],
                color=color, linewidth=1.6, linestyle=":",
                zorder=4,
            )

    # Слой 4: вертикали переломов (3 стиля по cp.source)
    # Дублируем на обоих axes — paper-coords matplotlib не поддерживает
    # для line shapes, поэтому axvline на каждом subplot отдельно с
    # одинаковыми параметрами.
    from datetime import datetime as _dt
    for cp in cps:
        try:
            cp_date = _dt.fromisoformat(str(cp.get("date"))[:10])
        except Exception:
            continue
        source = cp.get("source") or "only_total"
        if source == "confirmed":
            col, lw, ls = "#b91c1c", 2.0, "-"
        elif source == "only_working":
            col, lw, ls = "#16a34a", 1.5, "--"
        else:  # only_total
            col, lw, ls = "#b91c1c", 1.0, "--"
        ax_top.axvline(cp_date, color=col, linewidth=lw, linestyle=ls, zorder=5)
        ax_bot.axvline(cp_date, color=col, linewidth=lw, linestyle=ls, zorder=5)

    # Слой 6: теги причин — поворот 90°, цвет #8b0000
    # §7.3: префикс ✓ для confirmed
    # Y-позиция: чуть выше верха axes (transform=ax.get_xaxis_transform()
    # ставит x в координатах данных, y в координатах axes [0..1])
    for cp in cps:
        try:
            cp_date = _dt.fromisoformat(str(cp.get("date"))[:10])
        except Exception:
            continue
        tag = cp.get("tag") or cp.get("date") or ""
        source = cp.get("source") or "only_total"
        label = tag
        if source == "confirmed":
            label = "✓ " + label
        if source == "only_working":
            label = label + " (⚠ только в Q раб)"
        try:
            ax_top.annotate(
                label, xy=(cp_date, 1.0),
                xycoords=("data", "axes fraction"),
                xytext=(0, 3), textcoords="offset points",
                rotation=90, color="#8b0000",
                fontsize=7, ha="right", va="bottom",
                annotation_clip=False,
            )
        except Exception:
            log.exception("annotate cp tag failed for %s", cp.get("date"))

    # Стиль верхнего subplot
    ax_top.set_ylabel("Q, тыс.м³/сут", fontsize=9, color="#374151")
    ax_top.grid(True, linestyle="-", color="#e5e7eb", linewidth=0.5, zorder=0)
    ax_top.tick_params(axis="both", labelsize=8, colors="#374151")
    for spine in ax_top.spines.values():
        spine.set_edgecolor("#9ca3af")
        spine.set_linewidth(0.6)
    # Y начинается с 0 (для лучшего сравнения масштабов)
    y_data = [v for v in (q_total + q_working) if not np.isnan(v)]
    if y_data:
        ymin = min(0.0, min(y_data) * 0.95)
        ymax = max(y_data) * 1.10
        ax_top.set_ylim(ymin, ymax)
    # Легенда — внутри верхнего subplot (правый верх), компактная.
    # Снаружи (bbox_to_anchor < 0) ломается tight_layout — поэтому inside.
    ax_top.legend(
        loc="upper left", fontsize=6.5, frameon=True,
        framealpha=0.85, ncol=1,
    )

    # ════ НИЖНИЙ SUBPLOT ═════════════════════════════════════════
    # Бары shutdown_min — раздельно cluster vs single, два разных цвета.
    has_downtime = any(v and v > 0 for v in shutdown_min if not np.isnan(v))
    if has_downtime:
        x_cluster, y_cluster = [], []
        x_single, y_single = [], []
        for i, (d, v) in enumerate(zip(dates, shutdown_min)):
            if d is None or np.isnan(v) or v <= 0:
                continue
            if i in cluster_idx:
                x_cluster.append(d)
                y_cluster.append(v)
            else:
                x_single.append(d)
                y_single.append(v)
        if x_cluster:
            ax_bot.bar(
                x_cluster, y_cluster,
                width=0.85, color="#b91c1c", alpha=0.78,
                linewidth=0, label="Простой (кластер)",
            )
        if x_single:
            ax_bot.bar(
                x_single, y_single,
                width=0.85, color="#fb7185", alpha=0.65,
                linewidth=0, label="Простой (одиночный)",
            )

    ax_bot.set_ylabel("Простой, мин", fontsize=8, color="#374151")
    ax_bot.grid(True, axis="y", linestyle="-", color="#e5e7eb", linewidth=0.4, zorder=0)
    ax_bot.tick_params(axis="both", labelsize=7, colors="#374151")
    for spine in ax_bot.spines.values():
        spine.set_edgecolor("#9ca3af")
        spine.set_linewidth(0.6)

    # Форматирование оси X (только на нижнем — sharex применяет к верхнему)
    ax_bot.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=12))
    ax_bot.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m.%y"))
    fig.autofmt_xdate(rotation=0, ha="center")

    # Сохраняем
    try:
        fig.tight_layout(pad=0.6)
        fig.savefig(output_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    finally:
        plt.close(fig)

    return output_path
