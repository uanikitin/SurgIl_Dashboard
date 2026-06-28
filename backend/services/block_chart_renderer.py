"""Серверный рендер графиков для блоков отчёта (customer_report_block).

Зеркалит графики из UI превью блоков для встраивания в PDF:
  • render_segments_comparison_chart — сравнение участков (wz3cmp_v1):
    кривые `curve_x_hours`/`curve_y` всех сегментов на общей шкале Δt
    от начала, с линиями тренда (slope + intercept) поверх.
  • render_baseline_tile_charts — плитка B2/Адаптация: 3 PNG-графика
    (давления P трубное/P линейное, ΔP, Q дебит) через тот же
    `compute_full_flow`, что использует страница скважины (аксиома).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DEF_FIGSIZE = (12.0, 4.5)
DEF_DPI = 150


def _setup_matplotlib():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _safe_floats(values) -> list[float]:
    import numpy as np
    out: list[float] = []
    for v in values or []:
        if v is None:
            out.append(np.nan); continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            out.append(np.nan)
    return out


def render_segments_comparison_chart(
    snapshot: dict, output_path: str | Path,
    *, figsize: tuple[float, float] = DEF_FIGSIZE, dpi: int = DEF_DPI,
) -> Optional[Path]:
    """Снимок формата `wz3cmp_v1` → PNG графика сравнения участков.

    Каждый сегмент рисуется одной кривой (curve_y vs curve_x_hours/24,
    т.е. ось X в днях от начала сегмента). Поверх — пунктирная линия
    тренда по slope_per_hour + intercept.

    Возвращает Path к PNG или None если данных нет.
    """
    import numpy as np
    plt = _setup_matplotlib()

    segments = snapshot.get("segments") or []
    if not segments:
        return None

    # Готовим данные сегментов
    plotted = []
    for s in segments:
        xs = _safe_floats(s.get("curve_x_hours") or [])
        ys = _safe_floats(s.get("curve_y") or [])
        if not xs or not ys or len(xs) != len(ys):
            continue
        # Δt от начала в днях для удобства восприятия
        x_days = np.array(xs, dtype=float) / 24.0
        y_arr = np.array(ys, dtype=float)
        plotted.append({
            "letter":   s.get("letter") or "",
            "source":   s.get("source") or "",
            "color":    s.get("color") or "#3b82f6",
            "from":     s.get("from") or s.get("from_ts") or "",
            "to":       s.get("to")   or s.get("to_ts")   or "",
            "x":        x_days,
            "y":        y_arr,
            "slope_h":  s.get("slope_per_hour"),
            "intercept": s.get("intercept"),
            "r2":       s.get("r2"),
        })

    if not plotted:
        return None

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)

    metric_label = snapshot.get("metric_label") or snapshot.get("metric") or ""

    for seg in plotted:
        # Основная кривая
        label = f"{seg['letter']}. {('UzKorGaz' if seg['source'] == 'customer' else 'UniTool')}"
        label += f"  {str(seg['from'])[:16]} … {str(seg['to'])[:16]}"
        ax.plot(seg["x"], seg["y"], color=seg["color"],
                linewidth=1.0, label=label, alpha=0.9, zorder=3)

        # Линия тренда (slope в ед./час → в ед./день для согласования с осью X)
        if seg["slope_h"] is not None and seg["intercept"] is not None:
            try:
                slope_per_day = float(seg["slope_h"]) * 24.0
                # intercept был посчитан в исходных x — здесь восстановим линию
                # по точкам (x_min, x_max) текущего сегмента:
                x_min, x_max = float(seg["x"].min()), float(seg["x"].max())
                # y = slope_per_hour * x_hours + intercept = slope_per_day*x_days + intercept
                y0 = slope_per_day * x_min + float(seg["intercept"])
                y1 = slope_per_day * x_max + float(seg["intercept"])
                ax.plot([x_min, x_max], [y0, y1],
                        color=seg["color"], linestyle="--",
                        linewidth=1.5, alpha=0.7, zorder=4)
            except (TypeError, ValueError):
                pass

    ax.set_xlabel("Время от начала сегмента, дни", fontsize=9, color="#374151")
    ax.set_ylabel(metric_label, fontsize=9, color="#374151")
    ax.grid(True, which="major", linestyle="-",
            color="#e5e7eb", linewidth=0.5, zorder=0)
    ax.tick_params(axis="both", labelsize=8, colors="#374151", length=3)
    for sp in ax.spines.values():
        sp.set_edgecolor("#9ca3af"); sp.set_linewidth(0.6)

    leg = ax.legend(loc="best", fontsize=7, frameon=True,
                    facecolor="white", edgecolor="#d1d5db")
    if leg:
        leg.get_frame().set_alpha(0.85)

    fig.tight_layout()
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close(fig)
    log.info("Comparison chart rendered: %s", output_path)
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
#  Плитка B2 / Адаптация — 3 графика как в UI превью baseline
# ─────────────────────────────────────────────────────────────────────────────

def render_baseline_tile_charts(
    well_id: int,
    period_from,  # date | datetime
    period_to,
    output_prefix: str | Path,
    *, dpi: int = DEF_DPI,
) -> dict[str, Optional[str]]:
    """Рендерит 3 PNG для плитки B2/Адаптация и возвращает пути.

    Графики (минутные ряды через `compute_full_flow` — тот же pipeline,
    что страница скважины и `/api/flow-rate/calculate`):
      • pressures.png — P трубное (синяя) + P линейное (красная)
      • dp.png        — ΔP = max(0, P_тр - P_лин) (оранжевая)
      • flow.png      — мгновенный Q дебит (зелёная)

    Возвращает {'pressures': path|None, 'dp': path|None, 'flow': path|None}.
    Пути абсолютные, чтобы xelatex их разрешил.
    """
    from datetime import datetime, date, time, timedelta
    import numpy as np
    plt = _setup_matplotlib()

    out: dict[str, Optional[str]] = {"pressures": None, "dp": None, "flow": None}

    # Преобразуем period_from/to в строки ISO (Кунград). Если date — оборачиваем 00:00/23:59.
    def _to_kungrad_iso(v, is_end=False):
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, date):
            t = time(23, 59, 59) if is_end else time(0, 0, 0)
            return datetime.combine(v, t).isoformat()
        return str(v)

    iso_from = _to_kungrad_iso(period_from, is_end=False)
    iso_to   = _to_kungrad_iso(period_to,   is_end=True)

    # Берём поминутные данные через единый pipeline
    try:
        from backend.services.flow_rate.full_pipeline import compute_full_flow
        # full_pipeline ожидает Кунградские строки, конвертит UTC внутри.
        # Сначала вычитаем UTC+5, как делает api_calculate.
        kungrad_offset = timedelta(hours=5)
        dt_start = (datetime.fromisoformat(iso_from) - kungrad_offset).isoformat()
        dt_end   = (datetime.fromisoformat(iso_to)   - kungrad_offset).isoformat()
        result = compute_full_flow(well_id, dt_start, dt_end, smooth=True)
        df = result["df"]
    except Exception as exc:
        log.warning("baseline tile charts: compute_full_flow failed: %s", exc)
        return out

    if df is None or df.empty:
        return out

    output_prefix = Path(output_prefix)
    output_prefix.parent.mkdir(parents=True, exist_ok=True)

    x = df.index  # datetime64 (Кунград)
    p_tube = df["p_tube"].astype(float).values
    p_line = df["p_line"].astype(float).values
    flow = df["flow_rate"].astype(float).values if "flow_rate" in df.columns else None

    import matplotlib.dates as mdates

    # ── Pressures ──
    try:
        fig, ax = plt.subplots(figsize=(11.5, 3.5), dpi=dpi)
        ax.plot(x, p_tube, color="#2563eb", linewidth=0.7, label="P трубное")
        ax.plot(x, p_line, color="#dc2626", linewidth=0.7, label="P линейное")
        ax.set_title("Давления", fontsize=10, color="#374151")
        ax.set_ylabel("кгс/см²", fontsize=8, color="#374151")
        ax.grid(True, color="#e5e7eb", linewidth=0.5)
        ax.tick_params(axis="both", labelsize=7, colors="#374151")
        for sp in ax.spines.values():
            sp.set_edgecolor("#9ca3af"); sp.set_linewidth(0.5)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=10))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
        ax.legend(loc="best", fontsize=7, frameon=True, framealpha=0.85)
        fig.tight_layout()
        path = (output_prefix.parent / f"{output_prefix.name}_pressures.png").resolve()
        fig.savefig(path, dpi=dpi, bbox_inches="tight",
                    facecolor="white", edgecolor="none")
        plt.close(fig)
        out["pressures"] = str(path)
    except Exception as exc:
        log.warning("baseline tile: pressures plot failed: %s", exc)

    # ── ΔP ──
    try:
        dp = np.maximum(0.0, p_tube - p_line)
        fig, ax = plt.subplots(figsize=(11.5, 3.0), dpi=dpi)
        ax.plot(x, dp, color="#ea580c", linewidth=0.7, label="ΔP")
        ax.set_title("ΔP, кгс/см²", fontsize=10, color="#374151")
        ax.set_ylabel("кгс/см²", fontsize=8, color="#374151")
        ax.grid(True, color="#e5e7eb", linewidth=0.5)
        ax.tick_params(axis="both", labelsize=7, colors="#374151")
        for sp in ax.spines.values():
            sp.set_edgecolor("#9ca3af"); sp.set_linewidth(0.5)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=10))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
        fig.tight_layout()
        path = (output_prefix.parent / f"{output_prefix.name}_dp.png").resolve()
        fig.savefig(path, dpi=dpi, bbox_inches="tight",
                    facecolor="white", edgecolor="none")
        plt.close(fig)
        out["dp"] = str(path)
    except Exception as exc:
        log.warning("baseline tile: dp plot failed: %s", exc)

    # ── Q дебит ──
    if flow is not None:
        try:
            fig, ax = plt.subplots(figsize=(11.5, 3.0), dpi=dpi)
            ax.plot(x, flow, color="#16a34a", linewidth=0.7, label="Q")
            ax.set_title("Q дебит", fontsize=10, color="#374151")
            ax.set_ylabel("тыс.м³/сут", fontsize=8, color="#374151")
            ax.grid(True, color="#e5e7eb", linewidth=0.5)
            ax.tick_params(axis="both", labelsize=7, colors="#374151")
            for sp in ax.spines.values():
                sp.set_edgecolor("#9ca3af"); sp.set_linewidth(0.5)
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=10))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
            fig.tight_layout()
            path = (output_prefix.parent / f"{output_prefix.name}_flow.png").resolve()
            fig.savefig(path, dpi=dpi, bbox_inches="tight",
                        facecolor="white", edgecolor="none")
            plt.close(fig)
            out["flow"] = str(path)
        except Exception as exc:
            log.warning("baseline tile: flow plot failed: %s", exc)

    return out


def build_observation_chart_payload(
    well_id: int,
    period_from,  # date | datetime | iso str
    period_to,
    *, max_points: int = 4000,
) -> dict | None:
    """Строит chart-payload (dates / p_wellhead / p_flowline / dp / q_gas_total)
    за период через тот же `compute_full_flow`, что и matplotlib-плитка B2.

    Нужен, чтобы график B2 в §3.3 рисовался Plotly (renderChapter, формат
    observation_analysis) — паритет с §3.4. Прорежает ряд до `max_points`,
    NaN → None. Возвращает None, если данных нет.
    """
    from datetime import datetime, date, time, timedelta

    def _iso(v, is_end=False):
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, date):
            t = time(23, 59, 59) if is_end else time(0, 0, 0)
            return datetime.combine(v, t).isoformat()
        return str(v)

    try:
        from backend.services.flow_rate.full_pipeline import compute_full_flow
        off = timedelta(hours=5)  # Кунград → UTC, как в render_baseline_tile_charts
        ds = (datetime.fromisoformat(_iso(period_from)) - off).isoformat()
        de = (datetime.fromisoformat(_iso(period_to, True)) - off).isoformat()
        result = compute_full_flow(well_id, ds, de, smooth=True)
        df = result["df"]
    except Exception as exc:
        log.warning("observation chart payload: compute_full_flow failed: %s", exc)
        return None

    if df is None or df.empty:
        return None

    step = max(1, len(df) // max_points)
    d = df.iloc[::step]

    def _arr(series):
        out_list = []
        for v in series:
            fv = float(v)
            out_list.append(None if fv != fv else round(fv, 3))  # NaN → None
        return out_list

    p_tube = d["p_tube"].astype(float)
    p_line = d["p_line"].astype(float)
    dp = (p_tube - p_line).clip(lower=0.0)
    flow = d["flow_rate"].astype(float) if "flow_rate" in d.columns else None

    return {
        "dates": [t.isoformat() for t in d.index],
        "p_wellhead": _arr(p_tube),
        "p_flowline": _arr(p_line),
        "dp": _arr(dp),
        "q_gas_total": _arr(flow) if flow is not None else [],
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Рендер графиков из данных снапшота (не из БД!)
# ─────────────────────────────────────────────────────────────────────────────

def render_adaptation_charts_from_snapshot(
    chart_data: list[dict],
    events: list[dict] | None,
    output_prefix: str | Path,
    *, dpi: int = DEF_DPI,
) -> dict[str, Optional[str]]:
    """Рендерит 3 PNG из данных снапшота adaptation_period_analysis.

    Данные берутся из snap.adaptation.chart_data и snap.adaptation.events_for_chart,
    НЕ из базы данных. Это обеспечивает идентичность PDF тому, что было
    проанализировано и сохранено в снапшоте.

    Args:
        chart_data: список точек [{t, p_tube, p_line, dp, q}, ...]
        events: список событий [{t, type: 'purge'|'reagent', ...}, ...]
        output_prefix: префикс пути для PNG файлов

    Returns:
        {'pressures': path|None, 'dp': path|None, 'flow': path|None}
    """
    from datetime import datetime
    import numpy as np
    plt = _setup_matplotlib()
    import matplotlib.dates as mdates

    out: dict[str, Optional[str]] = {"pressures": None, "dp": None, "flow": None}

    if not chart_data:
        log.warning("render_adaptation_charts_from_snapshot: chart_data is empty")
        return out

    output_prefix = Path(output_prefix)
    output_prefix.parent.mkdir(parents=True, exist_ok=True)

    # Парсим данные снапшота
    dates = []
    p_tube_vals = []
    p_line_vals = []
    dp_vals = []
    q_vals = []

    for pt in chart_data:
        t_str = pt.get("t") or pt.get("ts") or pt.get("timestamp")
        if not t_str:
            continue
        try:
            if isinstance(t_str, str):
                dt = datetime.fromisoformat(t_str.replace("Z", "+00:00"))
            else:
                dt = t_str
            dates.append(dt)
            p_tube_vals.append(float(pt.get("p_tube") or 0))
            p_line_vals.append(float(pt.get("p_line") or 0))
            dp_vals.append(float(pt.get("dp") or max(0, p_tube_vals[-1] - p_line_vals[-1])))
            q_vals.append(float(pt.get("q") or pt.get("flow_rate") or 0))
        except (ValueError, TypeError):
            continue

    if len(dates) < 2:
        log.warning("render_adaptation_charts_from_snapshot: not enough data points (%d)", len(dates))
        return out

    dates = np.array(dates)
    p_tube = np.array(p_tube_vals)
    p_line = np.array(p_line_vals)
    dp = np.array(dp_vals)
    q = np.array(q_vals)

    # Парсим события для маркеров
    purge_dates = []
    reagent_dates = []
    if events:
        for ev in events:
            ev_t = ev.get("t") or ev.get("ts")
            ev_type = ev.get("type")
            if not ev_t:
                continue
            try:
                if isinstance(ev_t, str):
                    ev_dt = datetime.fromisoformat(ev_t.replace("Z", "+00:00"))
                else:
                    ev_dt = ev_t
                if ev_type == "purge":
                    purge_dates.append(ev_dt)
                elif ev_type == "reagent":
                    reagent_dates.append(ev_dt)
            except (ValueError, TypeError):
                continue

    def _add_event_markers(ax, y_data):
        """Добавляет маркеры событий на график."""
        y_max = np.nanmax(y_data) if len(y_data) > 0 else 1
        # Продувки — вертикальные линии
        for pd in purge_dates:
            ax.axvline(pd, color="#7c3aed", linewidth=1.0, linestyle="--", alpha=0.7, zorder=5)
        # Вбросы реагента — точки сверху
        for rd in reagent_dates:
            ax.scatter([rd], [y_max * 0.98], color="#22c55e", s=30, marker="v", zorder=6, alpha=0.8)

    # ── График давлений (P tube + P line) ──
    try:
        fig, ax = plt.subplots(figsize=(11.5, 3.5), dpi=dpi)
        ax.plot(dates, p_tube, color="#2563eb", linewidth=0.7, label="P трубное")
        ax.plot(dates, p_line, color="#dc2626", linewidth=0.7, label="P линейное")
        _add_event_markers(ax, np.concatenate([p_tube, p_line]))
        ax.set_title("Давления за период адаптации", fontsize=10, color="#374151")
        ax.set_ylabel("кгс/см²", fontsize=8, color="#374151")
        ax.grid(True, color="#e5e7eb", linewidth=0.5)
        ax.tick_params(axis="both", labelsize=7, colors="#374151")
        for sp in ax.spines.values():
            sp.set_edgecolor("#9ca3af"); sp.set_linewidth(0.5)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=10))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
        ax.legend(loc="best", fontsize=7, frameon=True, framealpha=0.85)
        fig.tight_layout()
        path = (output_prefix.parent / f"{output_prefix.name}_pressures.png").resolve()
        fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor="white", edgecolor="none")
        plt.close(fig)
        out["pressures"] = str(path)
    except Exception as exc:
        log.warning("snapshot chart pressures failed: %s", exc)

    # ── График ΔP ──
    try:
        fig, ax = plt.subplots(figsize=(11.5, 3.0), dpi=dpi)
        ax.plot(dates, dp, color="#ea580c", linewidth=0.7, label="ΔP")
        ax.fill_between(dates, dp, alpha=0.15, color="#ea580c")
        _add_event_markers(ax, dp)
        ax.set_title("Перепад давления ΔP", fontsize=10, color="#374151")
        ax.set_ylabel("кгс/см²", fontsize=8, color="#374151")
        ax.grid(True, color="#e5e7eb", linewidth=0.5)
        ax.tick_params(axis="both", labelsize=7, colors="#374151")
        for sp in ax.spines.values():
            sp.set_edgecolor("#9ca3af"); sp.set_linewidth(0.5)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=10))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
        fig.tight_layout()
        path = (output_prefix.parent / f"{output_prefix.name}_dp.png").resolve()
        fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor="white", edgecolor="none")
        plt.close(fig)
        out["dp"] = str(path)
    except Exception as exc:
        log.warning("snapshot chart dp failed: %s", exc)

    # ── График Q (дебит) ──
    try:
        fig, ax = plt.subplots(figsize=(11.5, 3.0), dpi=dpi)
        ax.plot(dates, q, color="#16a34a", linewidth=0.7, label="Q")
        ax.fill_between(dates, q, alpha=0.15, color="#16a34a")
        _add_event_markers(ax, q)
        ax.set_title("Расчётный дебит Q", fontsize=10, color="#374151")
        ax.set_ylabel("тыс.м³/сут", fontsize=8, color="#374151")
        ax.grid(True, color="#e5e7eb", linewidth=0.5)
        ax.tick_params(axis="both", labelsize=7, colors="#374151")
        for sp in ax.spines.values():
            sp.set_edgecolor("#9ca3af"); sp.set_linewidth(0.5)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=10))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
        fig.tight_layout()
        path = (output_prefix.parent / f"{output_prefix.name}_flow.png").resolve()
        fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor="white", edgecolor="none")
        plt.close(fig)
        out["flow"] = str(path)
    except Exception as exc:
        log.warning("snapshot chart flow failed: %s", exc)

    log.info("Adaptation charts from snapshot: %s", out)
    return out
