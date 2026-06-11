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


def _normalize_chart_data_for_pdf(snapshot: dict) -> dict:
    """Нормализует chart_data для PDF-рендеринга из разных форматов snapshot.

    Поддерживает:
    1. segment_v1 (customer_data): chart_data.dates, chart_data.q_total
    2. segment_analysis_v1 (segment-demo): chart_data.primary.values → q_total
    3. obs_segment_v1 (observation): raw.chart_payload.dates/q → dates/q_total
       (также поддерживает legacy timestamps для старых блоков)

    Returns:
        dict с ключами: dates, q_total, q_working (опц.), shutdown_min (опц.)
    """
    # Формат 1: уже нормализован
    chart_data = snapshot.get("chart_data") or {}
    if chart_data.get("q_total") and chart_data.get("dates"):
        return chart_data

    result: dict = {}

    # Формат 2: segment-demo (primary.values)
    if chart_data.get("primary") and chart_data["primary"].get("values"):
        result["dates"] = chart_data.get("dates", [])
        result["q_total"] = chart_data["primary"]["values"]
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
        dates = [ts[:10] if isinstance(ts, str) and len(ts) >= 10 else ts for ts in time_values]
        result["dates"] = dates
        result["q_total"] = chart_payload["q"]
        if chart_payload.get("shutdown_min"):
            result["shutdown_min"] = chart_payload["shutdown_min"]
        return result

    # Fallback
    return chart_data


def render_segment_q_chart(
    snapshot: dict,
    output_path: str | Path,
    *,
    figsize: tuple[float, float] = (13.0, 6.8),
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
    # Проверяем валидность snapshot: ok=True или block_status="ok"
    is_valid = (
        snapshot
        and (snapshot.get("ok") or snapshot.get("block_status") == "ok")
    )
    if not is_valid:
        log.warning(
            "render_segment_q_chart: snapshot невалиден (ok=%s, block_status=%s, keys=%s)",
            snapshot.get("ok") if snapshot else None,
            snapshot.get("block_status") if snapshot else None,
            list(snapshot.keys())[:10] if snapshot else [],
        )
        return None

    # Нормализация данных из разных форматов
    cd = _normalize_chart_data_for_pdf(snapshot)
    segs = snapshot.get("segments_extended") or snapshot.get("segments") or []
    cps = snapshot.get("cp_marks") or snapshot.get("changepoints") or []
    clusters = snapshot.get("shutdown_clusters") or []

    dates_iso = cd.get("dates") or []
    if not dates_iso or not segs:
        log.warning(
            "render_segment_q_chart: пустые dates или segments "
            "(dates=%d, segs=%d, chart_data_keys=%s)",
            len(dates_iso), len(segs), list(cd.keys()),
        )
        return None

    _, plt, mdates, np = _setup_matplotlib()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dates = _parse_iso_dates(dates_iso)
    q_total = _safe_y(cd.get("q_total"))
    q_working = _safe_y(cd.get("q_working"))
    shutdown_min = _safe_y(cd.get("shutdown_min"))

    # Проверяем что q_total не пустой и совпадает по длине с dates
    if not q_total or len(q_total) != len(dates):
        log.warning(
            "render_segment_q_chart: q_total пуст или не совпадает по длине с dates "
            "(q_total=%d, dates=%d, q_total[:3]=%s)",
            len(q_total), len(dates), q_total[:3] if q_total else [],
        )
        return None

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
        # PDF layout fix: НЕ даём per-segment label — для 6 сегментов это
        # 6 лишних записей легенды, перегружающих область графика. Тренды
        # читаются по цвету сегмента; одна generic-запись добавляется ниже.
        slope_total = s.get("slope_total")
        intercept_total = s.get("intercept_total")
        if slope_total is not None and intercept_total is not None:
            y0 = intercept_total
            y1 = intercept_total + slope_total * span
            ax_top.plot(
                [x0, x1], [y0, y1],
                color=color, linewidth=2.5, linestyle="-",
                zorder=4,
                label="_nolegend_",
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

    # Слой 6: теги причин — компактный PDF-режим.
    # ВАЖНО (PDF layout fix): раньше annotation размещались НАД axes
    # (xy y=1.0 axes fraction, xytext offset вверх, annotation_clip=False).
    # Длинные подписи + поворот 90° + bbox_inches="tight" → огромное
    # пустое поле сверху страницы. Теперь:
    #   • подпись размещается ВНУТРИ верхней части графика (y=0.985);
    #   • annotation_clip=True — не вылезает за пределы axes → bbox не растёт;
    #   • текст компактный: короткий tag (≤16 симв.) + 1-символьный
    #     маркер источника. Длинные пояснения — в описании ниже, не здесь.
    _SOURCE_MARK = {"confirmed": "✓", "only_working": "●", "only_total": ""}
    # Диапазон оси X — для edge-aware размещения подписей: метки у правого
    # края рисуем СЛЕВА от вертикали (ha="right"), чтобы annotation_clip
    # не срезал их по краю PNG.
    _valid_dates = [d for d in dates if d is not None]
    _x_min = min(_valid_dates) if _valid_dates else None
    _x_max = max(_valid_dates) if _valid_dates else None
    _x_span = (
        (_x_max - _x_min).total_seconds()
        if (_x_min is not None and _x_max is not None) else 0
    )
    for cp in cps:
        try:
            cp_date = _dt.fromisoformat(str(cp.get("date"))[:10])
        except Exception:
            continue
        tag = str(cp.get("tag") or cp.get("date") or "")
        # Компактный tag: обрезаем длинный текст, длинные пояснения уходят
        # в cp_descriptions (см. _augment_descriptions_for_r1).
        if len(tag) > 16:
            tag = tag[:15] + "…"
        source = cp.get("source") or "only_total"
        mark = _SOURCE_MARK.get(source, "")
        label = f"{mark} {tag}".strip() if mark else tag
        # Edge-aware: правые 12% диапазона → подпись слева от вертикали.
        near_right = False
        if _x_span > 0 and _x_max is not None:
            frac = (_x_max - cp_date).total_seconds() / _x_span
            near_right = frac < 0.12
        ha = "right" if near_right else "left"
        x_off = -2 if near_right else 2
        try:
            ax_top.annotate(
                label, xy=(cp_date, 0.985),
                xycoords=("data", "axes fraction"),
                xytext=(x_off, 0), textcoords="offset points",
                rotation=90, color="#8b0000",
                fontsize=6.5, ha=ha, va="top",
                annotation_clip=True,
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
    # Y начинается с 0 (для лучшего сравнения масштабов).
    # PDF layout fix: headroom 1.24 (было 1.10) — вертикальные подписи
    # переломов теперь ВНУТРИ графика (см. Слой 6), им нужен запас сверху,
    # чтобы не перекрывать кривые Q.
    y_data = [v for v in (q_total + q_working) if not np.isnan(v)]
    if y_data:
        ymin = min(0.0, min(y_data) * 0.95)
        ymax = max(y_data) * 1.24
        ax_top.set_ylim(ymin, ymax)
    # Легенда — компактная, горизонтальная, в правом верхнем углу.
    # Только основные кривые (Q общий / Q рабочий) — тренды сегментов
    # читаются по цвету, per-segment записи убраны (см. Слой 3).
    ax_top.legend(
        loc="upper right", fontsize=7, frameon=True,
        framealpha=0.9, ncol=2, handlelength=1.6,
        columnspacing=1.0, borderpad=0.4,
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

    # Сохраняем.
    # PDF layout fix: убран bbox_inches="tight" — он ловил вертикальные
    # annotation-подписи и раздувал картинку, оставляя огромное пустое
    # поле сверху. Теперь bbox фиксирован = figsize, поля задаются явным
    # subplots_adjust: график занимает почти всю площадь PNG, без рамки
    # пустого пространства. tight_layout не используется (конфликтует
    # с annotations и фиксированной раскладкой).
    try:
        fig.subplots_adjust(
            left=0.055, right=0.995, top=0.985, bottom=0.085,
            hspace=0.10,
        )
        fig.savefig(output_path, dpi=dpi, facecolor="white")
    finally:
        plt.close(fig)

    return output_path


# ═══════════════════════════════════════════════════════════════════════
#  Comparison chart: overlay нескольких сегментов
# ═══════════════════════════════════════════════════════════════════════


def render_segment_comparison_chart(
    segments_data: list[dict],
    output_path: str | Path,
    *,
    normalized: bool = False,
    show_trends: bool = True,
    figsize: tuple[float, float] = (12.0, 5.5),
    dpi: int = 150,
) -> Optional[Path]:
    """Overlay-график для сравнения нескольких сегментов.

    Args:
        segments_data: список сегментов для сравнения, каждый элемент:
            {
                "num": int,           # номер сегмента
                "label": str,         # подпись в легенде
                "color": str,         # цвет линии (#rrggbb)
                "values": list[float], # значения метрики
                "dates": list[str],   # ISO-даты (опционально, для x_mode='date')
                "slope": float | None,
                "intercept": float | None,
            }
        output_path: путь для сохранения PNG.
        normalized: если True — каждый сегмент нормализуется в диапазон 0-100%.
        show_trends: если True — рисовать пунктирные линии трендов.
        figsize: размер figure в дюймах.
        dpi: разрешение PNG.

    Returns:
        Path к PNG или None при ошибке.
    """
    if not segments_data:
        log.info("render_segment_comparison_chart: пустой segments_data")
        return None

    _, plt, _, np = _setup_matplotlib()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    fig.patch.set_facecolor("#ffffff")

    for seg in segments_data:
        values = _safe_y(seg.get("values") or [])
        if not values or all(np.isnan(v) for v in values):
            continue

        num = seg.get("num", "?")
        label = seg.get("label") or f"Сегмент {num}"
        color = seg.get("color") or "#666666"

        # Ось X: относительные дни от начала сегмента (0, 1, 2, ...)
        x = list(range(len(values)))

        # Нормализация 0-100% если запрошено
        y = values
        if normalized:
            valid_vals = [v for v in values if not np.isnan(v)]
            if valid_vals:
                vmin, vmax = min(valid_vals), max(valid_vals)
                span = vmax - vmin if vmax > vmin else 1.0
                y = [(v - vmin) / span * 100 if not np.isnan(v) else float("nan") for v in values]

        # Основная линия с данными
        ax.plot(
            x, y,
            color=color, linewidth=1.8,
            marker="o", markersize=3.5, alpha=0.85,
            label=label, zorder=3,
        )

        # Тренд (пунктир)
        if show_trends:
            slope = seg.get("slope")
            intercept = seg.get("intercept")
            if slope is not None and intercept is not None:
                # Если нормализовано — пересчитываем тренд
                if normalized and valid_vals:
                    # Пересчитываем intercept и slope в нормализованные координаты
                    y0_orig = intercept
                    y1_orig = intercept + slope * (len(values) - 1)
                    y0_norm = (y0_orig - vmin) / span * 100
                    y1_norm = (y1_orig - vmin) / span * 100
                    ax.plot(
                        [0, len(values) - 1], [y0_norm, y1_norm],
                        color=color, linewidth=1.5, linestyle="--",
                        alpha=0.7, zorder=2,
                    )
                else:
                    y0 = intercept
                    y1 = intercept + slope * (len(values) - 1)
                    ax.plot(
                        [0, len(values) - 1], [y0, y1],
                        color=color, linewidth=1.5, linestyle="--",
                        alpha=0.7, zorder=2,
                    )

    # Стиль графика
    ax.set_xlabel("День от начала сегмента", fontsize=9, color="#374151")
    ylabel = "Значение, % от диапазона" if normalized else "Q, тыс.м³/сут"
    ax.set_ylabel(ylabel, fontsize=9, color="#374151")
    ax.grid(True, linestyle="-", color="#e5e7eb", linewidth=0.5, zorder=0)
    ax.tick_params(axis="both", labelsize=8, colors="#374151")
    for spine in ax.spines.values():
        spine.set_edgecolor("#9ca3af")
        spine.set_linewidth(0.6)

    # Легенда
    ax.legend(
        loc="upper right", fontsize=8, frameon=True,
        framealpha=0.9, ncol=min(3, len(segments_data)),
        handlelength=1.8, columnspacing=1.0, borderpad=0.4,
    )

    # Заголовок
    ax.set_title("Сравнение сегментов", fontsize=10, color="#1f2937", pad=8)

    try:
        fig.tight_layout()
        fig.savefig(output_path, dpi=dpi, facecolor="white")
    finally:
        plt.close(fig)

    return output_path
