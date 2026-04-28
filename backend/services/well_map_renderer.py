"""PNG-карта расположения скважины относительно соседей.

Используется для блока «Технические данные» в LaTeX-отчёте.

Бесплатные провайдеры тайлов из contextily (без ключей API):
  * `Esri.WorldTopoMap`       — рельеф + города/посёлки/дороги (по умолчанию)
  * `OpenTopoMap`             — рельеф + горизонтали (без городских объектов)
  * `Esri.WorldImagery`       — спутниковые снимки
  * `Esri.WorldShadedRelief`  — чисто рельеф без подписей
  * `Esri.WorldStreetMap`     — улицы / без рельефа
  * `OpenStreetMap.Mapnik`    — обычный OSM
  * `CartoDB.Positron`        — светлый, минималистичный
  * `CartoDB.Voyager`         — современный, мягкие цвета

Координаты переводим WGS84 → Web Mercator (EPSG:3857) для совместимости
с тайлами. Если тайлы недоступны — автоматически рисуем без подложки.

Соотношение сторон фигуры подобрано под печать на ширину textwidth A4
с высотой не более 0.3·textheight (≈ 18×8 cm → 2.25:1).
"""
from __future__ import annotations

import logging
from math import cos, radians
from pathlib import Path
from typing import Optional, Sequence

log = logging.getLogger(__name__)


# ── Геометрия фигуры ──
# textwidth = 18 cm, 0.3·textheight ≈ 8 cm на A4 с margin=1.5 cm  → ≈ 2.25:1
TARGET_FIG_W_IN = 13.5
TARGET_FIG_H_IN = 6.0
TARGET_ASPECT = TARGET_FIG_W_IN / TARGET_FIG_H_IN

# Минимальный размах bbox в метрах (Web Mercator), чтобы тайлы выбирались
# не на максимальном зум-уровне, когда у нас всего одна точка.
MIN_BBOX_METERS = 2_000.0


def render_well_map_png(
    active: dict,
    neighbors: Sequence[dict],
    output_path: str | Path,
    *,
    dpi: int = 200,
    use_basemap: bool = True,
) -> Optional[Path]:
    """Нарисовать карту скважины + соседей в PNG с рельефной подложкой.

    Returns путь к PNG, либо None если у активной скважины нет координат.
    """
    if active is None:
        return None
    a_lat = active.get("lat")
    a_lon = active.get("lon")
    if a_lat is None or a_lon is None:
        return None

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patheffects import withStroke

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Точки соседей ──
    nb = [
        n for n in neighbors
        if n.get("lat") is not None and n.get("lon") is not None
        and (str(n.get("number")) != str(active.get("number")))
    ]

    # ── WGS84 → Web Mercator (EPSG:3857) для совместимости с тайлами ──
    from pyproj import Transformer
    transformer = Transformer.from_crs(
        "EPSG:4326", "EPSG:3857", always_xy=True,
    )
    a_x, a_y = transformer.transform(a_lon, a_lat)
    nb_xy: list[tuple[float, float]] = [
        transformer.transform(n["lon"], n["lat"]) for n in nb
    ]

    fig, ax = plt.subplots(figsize=(TARGET_FIG_W_IN, TARGET_FIG_H_IN), dpi=dpi)
    fig.patch.set_facecolor("#f7f3ea")
    ax.set_facecolor("#fbf8f1")

    # ── Bbox в метрах + подгонка под целевой aspect фигуры ──
    xs = [a_x] + [p[0] for p in nb_xy]
    ys = [a_y] + [p[1] for p in nb_xy]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    # Гарантируем минимум размаха
    if (x_max - x_min) < MIN_BBOX_METERS:
        cx = (x_min + x_max) / 2.0
        x_min, x_max = cx - MIN_BBOX_METERS / 2, cx + MIN_BBOX_METERS / 2
    if (y_max - y_min) < MIN_BBOX_METERS:
        cy = (y_min + y_max) / 2.0
        y_min, y_max = cy - MIN_BBOX_METERS / 2, cy + MIN_BBOX_METERS / 2

    # Базовый отступ 18%
    pad_x = (x_max - x_min) * 0.18
    pad_y = (y_max - y_min) * 0.18
    x_min -= pad_x; x_max += pad_x
    y_min -= pad_y; y_max += pad_y

    # Подгоняем под целевой aspect (в Web Mercator юниты одинаковые → проще)
    cur_aspect = (x_max - x_min) / max(y_max - y_min, 1e-6)
    if cur_aspect < TARGET_ASPECT:
        # Расширяем по X
        needed_w = (y_max - y_min) * TARGET_ASPECT
        extra = (needed_w - (x_max - x_min)) / 2
        x_min -= extra; x_max += extra
    else:
        # Расширяем по Y
        needed_h = (x_max - x_min) / TARGET_ASPECT
        extra = (needed_h - (y_max - y_min)) / 2
        y_min -= extra; y_max += extra

    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    ax.set_aspect("equal", adjustable="box")

    # ── Тайловая подложка ──
    # Для пустынного региона Сургила (Каракалпакстан) Esri.WorldImagery —
    # единственный провайдер, реально показывающий местность: дороги, посёлки,
    # инфраструктуру месторождения. Топо-карты в этом регионе пустые (Esri
    # рисует «воду» по историческим границам Арала; OpenTopoMap — пустые
    # горизонтали, OSM — почти нет объектов).
    basemap_added = False
    basemap_attribution = None
    if use_basemap:
        try:
            import contextily as ctx
            source = ctx.providers.Esri.WorldImagery
            basemap_attribution = (
                "Imagery © Esri — Source: Esri, Maxar, Earthstar Geographics, "
                "and the GIS User Community"
            )
            ctx.add_basemap(
                ax,
                crs="EPSG:3857",
                source=source,
                attribution=False,
                zoom=13,
            )
            basemap_added = True
        except Exception as e:
            log.warning("contextily basemap failed, fallback to plain: %s", e)

    if not basemap_added:
        ax.grid(True, which="major", linestyle="-",
                color="#d8d2c4", linewidth=0.6, zorder=1)
        ax.minorticks_on()
        ax.grid(True, which="minor", linestyle=":",
                color="#e6e0d2", linewidth=0.4, zorder=1)

    # ── Соседи (контрастная отделка для читаемости на спутнике) ──
    if nb_xy:
        nb_x = [p[0] for p in nb_xy]
        nb_y = [p[1] for p in nb_xy]
        # точки: жёлтый круг с чёрной обводкой — заметны на любой подложке
        ax.scatter(nb_x, nb_y, s=120, c="#fde68a", marker="o",
                   edgecolors="#0f172a", linewidths=1.4, zorder=4)
        # подписи: белый текст, чёрная обводка → читается и на свету, и на тенях
        for (px, py), n in zip(nb_xy, nb):
            ax.annotate(
                str(n.get("number") or ""),
                xy=(px, py),
                xytext=(7, 7), textcoords="offset points",
                fontsize=8.5, fontweight="700", color="#ffffff",
                path_effects=[withStroke(linewidth=2.8, foreground="#000000")],
                zorder=7,
            )

    # ── Активная: гало → звезда → подпись ──
    ax.scatter([a_x], [a_y], s=1100, c="#fef2f2", marker="*",
               edgecolors="#fca5a5", linewidths=0.0, zorder=5, alpha=0.7)
    ax.scatter([a_x], [a_y], s=420, c="#dc2626", marker="*",
               edgecolors="#ffffff", linewidths=1.8, zorder=6)
    ax.annotate(
        f"№{active.get('number')}",
        xy=(a_x, a_y),
        xytext=(11, 11), textcoords="offset points",
        fontsize=11.5, fontweight="bold", color="#7f1d1d",
        bbox=dict(
            boxstyle="round,pad=0.4,rounding_size=0.5",
            facecolor="#ffffff", edgecolor="#dc2626",
            linewidth=1.1, alpha=0.96,
        ),
        zorder=8,
    )

    # ── Стрелка севера + шкала километров ──
    _draw_north_arrow(ax)
    _draw_scale_bar_meters(ax)

    # ── Атрибуция и убираем подписи осей (в метрах не нужны) ──
    if basemap_added:
        ax.text(
            0.99, 0.015,
            basemap_attribution or "",
            transform=ax.transAxes, ha="right", va="bottom",
            fontsize=5.5, color="#0f172a",
            bbox=dict(facecolor="#ffffff", edgecolor="none",
                      alpha=0.85, pad=1.5),
            zorder=9,
        )
        # Скрываем тики и подписи осей — координаты в метрах не нужны
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_xlabel(""); ax.set_ylabel("")
    else:
        # Без подложки — оставляем сетку и подписи в градусах (преобразуем
        # текущие тики метров обратно в градусы).
        _format_degree_ticks(ax, transformer)

    for s in ax.spines.values():
        s.set_edgecolor("#9ca3af")
        s.set_linewidth(0.6)

    fig.tight_layout(pad=0.4)
    try:
        fig.savefig(output_path, dpi=dpi, bbox_inches="tight",
                    facecolor=fig.get_facecolor())
    finally:
        plt.close(fig)

    # Защита от битого PNG (LaTeX упадёт при попытке его прочитать).
    # Если сохранили совсем мелкий файл (<2 КБ) — что-то пошло не так,
    # отдаём None чтобы блок карты в LaTeX был пропущен.
    if not output_path.exists() or output_path.stat().st_size < 2048:
        log.warning("well map PNG looks broken (%d bytes) — skipping",
                    output_path.stat().st_size if output_path.exists() else 0)
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            pass
        return None
    return output_path


# ───── Декорации карты ─────

def _draw_north_arrow(ax) -> None:
    """Стрелка севера в правом верхнем углу (axes-coords)."""
    from matplotlib.patheffects import withStroke
    ax.annotate(
        "С",
        xy=(0.96, 0.94), xycoords="axes fraction",
        xytext=(0.96, 0.84), textcoords="axes fraction",
        ha="center", va="bottom",
        fontsize=9.5, fontweight="bold", color="#0f172a",
        path_effects=[withStroke(linewidth=2.6, foreground="#ffffff")],
        arrowprops=dict(
            arrowstyle="-|>,head_width=0.5,head_length=0.6",
            color="#0f172a", linewidth=1.6,
        ),
        zorder=9,
    )


def _draw_scale_bar_meters(ax) -> None:
    """Горизонтальная шкала; данные в метрах (Web Mercator)."""
    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    # Длина шкалы — кратная «приятным» значениям, ≤ 25% от ширины
    span_m = xlim[1] - xlim[0]
    candidates_m = [200, 500, 1000, 2000, 5000, 10_000, 20_000, 50_000]
    target_m = candidates_m[0]
    for c in candidates_m:
        if c <= span_m * 0.25:
            target_m = c
    # Якорь — нижний левый, отступ 4%
    x0 = xlim[0] + (xlim[1] - xlim[0]) * 0.04
    y0 = ylim[0] + (ylim[1] - ylim[0]) * 0.07
    x1 = x0 + target_m
    # Линия + засечки
    ax.plot([x0, x1], [y0, y0], color="#0f172a", linewidth=2.0, zorder=9,
            solid_capstyle="butt")
    tick_h = (ylim[1] - ylim[0]) * 0.014
    for x in (x0, x1):
        ax.plot([x, x], [y0 - tick_h, y0 + tick_h],
                color="#0f172a", linewidth=1.6, zorder=9)
    label = f"{target_m // 1000} км" if target_m >= 1000 else f"{target_m} м"
    # подложка под подпись
    ax.text(
        (x0 + x1) / 2, y0 + tick_h * 1.3, label,
        ha="center", va="bottom",
        fontsize=8.5, fontweight="700", color="#0f172a",
        bbox=dict(facecolor="#ffffff", edgecolor="none",
                  alpha=0.8, pad=1.5),
        zorder=9,
    )


def _format_degree_ticks(ax, transformer) -> None:
    """Если тайлы не догрузились — конвертируем подписи метров обратно в °."""
    inv = transformer.transformer if hasattr(transformer, "transformer") else None
    # Пересоздаём обратный трансформер
    from pyproj import Transformer as _T
    back = _T.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    xticks = ax.get_xticks()
    yticks = ax.get_yticks()
    xlabels = [f"{back.transform(x, 0)[0]:.3f}" for x in xticks]
    ylabels = [f"{back.transform(0, y)[1]:.3f}" for y in yticks]
    ax.set_xticklabels(xlabels, fontsize=7, color="#6b7280")
    ax.set_yticklabels(ylabels, fontsize=7, color="#6b7280")
    ax.set_xlabel("Долгота, °", fontsize=8, color="#374151")
    ax.set_ylabel("Широта, °", fontsize=8, color="#374151")
