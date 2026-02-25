"""
Суточный отчёт по скважинам — агрегация данных + генерация PDF.

Два режима:
  - daily_report_well  → одна скважина, один PDF
  - daily_report_all   → все скважины, один PDF (каждая на своей странице)
"""
from __future__ import annotations

import logging
import subprocess
from datetime import datetime, date, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models.wells import Well

log = logging.getLogger(__name__)

KUNGRAD_OFFSET = timedelta(hours=5)

# Directories
LATEX_TEMPLATES_DIR = Path("backend/templates/latex")
OUTPUT_DIR = Path("backend/static/generated")
PDF_DIR = OUTPUT_DIR / "pdf"
TEMP_DIR = OUTPUT_DIR / "temp"

# Event type labels (ru)
EVENT_TYPE_LABELS = {
    "purge": "Продувка",
    "reagent": "Вброс реагента",
    "pressure": "Замер давления",
    "equip": "Оборудование",
    "production": "Добыча",
    "maintenance": "Обслуживание",
    "other": "Другое",
}


def _ensure_dirs():
    for d in [PDF_DIR, TEMP_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def _get_latex_env() -> Environment:
    """Jinja2 environment with LaTeX-compatible delimiters."""
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
    """Compile LaTeX → PDF via xelatex (2 passes)."""
    _ensure_dirs()

    tex_file = TEMP_DIR / f"{base_name}.tex"
    tex_file.write_text(tex_source, encoding="utf-8")

    abs_temp = str(TEMP_DIR.resolve())
    abs_tex = str(tex_file.resolve())

    for pass_num in range(2):
        result = subprocess.run(
            ["xelatex", "-interaction=nonstopmode",
             f"-output-directory={abs_temp}", abs_tex],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=abs_temp,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="ignore")
            stdout = result.stdout.decode("utf-8", errors="ignore")
            log.error("xelatex pass %d failed.\nSTDERR: %s\nSTDOUT (last 2000): %s",
                       pass_num + 1, stderr[-500:], stdout[-2000:])
            raise RuntimeError(
                f"xelatex compilation failed (pass {pass_num + 1}): "
                f"{stdout[-500:]}"
            )

    temp_pdf = TEMP_DIR / f"{base_name}.pdf"
    final_pdf = PDF_DIR / f"{base_name}.pdf"

    if not temp_pdf.exists():
        raise FileNotFoundError(f"PDF not created: {temp_pdf}")

    temp_pdf.rename(final_pdf)

    # Cleanup
    for ext in (".aux", ".log", ".out", ".tex"):
        f = TEMP_DIR / f"{base_name}{ext}"
        if f.exists():
            f.unlink()

    return final_pdf


def _tex_escape(s: str) -> str:
    """Escape LaTeX special characters."""
    if not s:
        return ""
    for old, new in [
        ("&", r"\&"), ("%", r"\%"), ("$", r"\$"),
        ("#", r"\#"), ("_", r"\_"), ("{", r"\{"),
        ("}", r"\}"), ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
    ]:
        s = s.replace(old, new)
    return s


def _fmt(val, decimals: int = 2) -> str:
    """Format number for LaTeX, None → '—'."""
    if val is None:
        return "---"
    if isinstance(val, (float, np.floating)):
        return f"{val:.{decimals}f}"
    return str(val)


# ── Month-to-date flow from raw pressure data ─────────

def _compute_month_flow_from_raw(
    db, well, choke_mm: float,
    month_start: date, report_date: date,
    get_pressure_data_fn, clean_pressure_fn,
    calculate_flow_rate_fn, calculate_cumulative_fn,
    comparison_days: int = 7,
) -> dict | None:
    """
    Compute month-to-date cumulative flow, avg and median from raw pressure.
    Used as fallback when flow_result table has no records for the month.

    Loads the full month of pressure data at once and aggregates per day.
    Returns dict with month and week stats, or None.
    """
    from backend.services.flow_rate.scenario_service import aggregate_to_daily

    # Load full month of pressure data (UTC)
    utc_start = datetime.combine(month_start, time(0, 0)) - KUNGRAD_OFFSET
    utc_end = datetime.combine(report_date, time(23, 59, 59)) - KUNGRAD_OFFSET

    df_raw = get_pressure_data_fn(well.id, utc_start.isoformat(), utc_end.isoformat())
    if df_raw.empty:
        return None

    df = clean_pressure_fn(df_raw)
    if df.empty:
        return None

    df_flow = calculate_flow_rate_fn(df, choke_mm)
    if df_flow.empty or "flow_rate" not in df_flow.columns:
        return None

    df_flow = calculate_cumulative_fn(df_flow)

    # Aggregate per day
    daily_rows = aggregate_to_daily(df_flow)
    if not daily_rows:
        return None

    # Month stats
    month_cum = sum(r["cumulative_flow"] for r in daily_rows)
    month_medians = [r["median_flow_rate"] for r in daily_rows if r["median_flow_rate"] > 0]

    # Week stats (last N days)
    week_cutoff = report_date - timedelta(days=comparison_days)
    week_medians = [
        r["median_flow_rate"] for r in daily_rows
        if r["result_date"] >= week_cutoff and r["median_flow_rate"] > 0
    ]

    return {
        "month_cumulative": month_cum if month_cum > 0 else None,
        "month_avg_flow": float(np.mean(month_medians)) if month_medians else None,
        "month_median_flow": float(np.median(month_medians)) if month_medians else None,
        "working_days": len(month_medians),
        "week_avg_flow": float(np.mean(week_medians)) if week_medians else None,
        "week_median_flow": float(np.median(week_medians)) if week_medians else None,
    }


# ── Linear trend (copied from flow_rate router) ─────────

def _linear_trend(values: list, duration_hours: float) -> dict | None:
    """Linear regression Y(t) = a + b*t. Returns slope_per_day, direction, r_squared."""
    valid = [(i, v) for i, v in enumerate(values) if v is not None and not np.isnan(v)]
    if len(valid) < 3:
        return None

    idx = np.array([x[0] for x in valid], dtype=float)
    vals = np.array([x[1] for x in valid])

    n_total = len(values) if len(values) > 1 else 1
    t_hours = idx * (duration_hours / n_total)

    coeffs = np.polyfit(t_hours, vals, 1)
    slope_h = float(coeffs[0])
    intercept = float(coeffs[1])
    slope_day = slope_h * 24.0

    predicted = np.polyval(coeffs, t_hours)
    ss_res = float(np.sum((vals - predicted) ** 2))
    ss_tot = float(np.sum((vals - np.mean(vals)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

    direction = "up" if slope_day > 0.001 else "down" if slope_day < -0.001 else "flat"

    return {
        "slope_per_day": round(slope_day, 4),
        "r_squared": round(r2, 4),
        "direction": direction,
    }


# ── Pressure chart ──────────────────────────────────────

def _render_pressure_chart(
    df: pd.DataFrame,
    well_number: str,
    report_date: date,
    event_markers: list[dict] | None = None,
) -> str | None:
    """Render intraday pressure chart (p_tube + p_line) → PNG. Returns absolute path.

    event_markers: list of {"time_utc": datetime, "event_type": str} for overlay.
    """
    if df.empty or len(df) < 5:
        return None

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        log.warning("matplotlib not available, skipping chart")
        return None

    _ensure_dirs()

    # Convert UTC index to Kungrad time for display
    df_local = df.copy()
    df_local.index = df_local.index + KUNGRAD_OFFSET

    fig, ax = plt.subplots(figsize=(7.0, 2.5), dpi=150)

    ax.plot(df_local.index, df_local["p_tube"], color="#1f77b4",
            linewidth=0.7, label="P труб")
    ax.plot(df_local.index, df_local["p_line"], color="#d62728",
            linewidth=0.7, label="P линия")

    # Overlay event markers
    if event_markers:
        purge_drawn = False
        reagent_drawn = False
        for ev in event_markers:
            ev_local = ev["time_utc"] + KUNGRAD_OFFSET
            if ev["event_type"] == "purge":
                ax.axvline(ev_local, color="#9c27b0", alpha=0.7, linewidth=1.0,
                           linestyle="--", label="Продувка" if not purge_drawn else None)
                purge_drawn = True
            elif ev["event_type"] == "reagent":
                ax.axvline(ev_local, color="#4caf50", alpha=0.7, linewidth=1.0,
                           linestyle=":", label="Реагент" if not reagent_drawn else None)
                reagent_drawn = True

    ax.set_ylabel("кгс/см²", fontsize=8)
    ax.set_xlabel("")
    ax.legend(fontsize=7, loc="upper right")
    ax.tick_params(labelsize=7)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.grid(True, alpha=0.3, linewidth=0.5)

    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout(pad=0.5)

    chart_path = TEMP_DIR.resolve() / f"chart_{well_number}_{report_date.isoformat()}.png"
    fig.savefig(str(chart_path), bbox_inches="tight")
    plt.close(fig)

    return str(chart_path)


def _render_flow_chart(
    df_flow: pd.DataFrame,
    well_number: str,
    report_date: date,
) -> str | None:
    """Render intraday flow rate chart → PNG. Returns absolute path."""
    if df_flow.empty or "flow_rate" not in df_flow.columns or len(df_flow) < 5:
        return None

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        return None

    _ensure_dirs()

    df_local = df_flow.copy()
    df_local.index = df_local.index + KUNGRAD_OFFSET

    fig, ax = plt.subplots(figsize=(7.0, 2.5), dpi=150)

    ax.plot(df_local.index, df_local["flow_rate"], color="#2e7d32",
            linewidth=0.7, label="Q, тыс.м³/сут")
    ax.fill_between(df_local.index, df_local["flow_rate"], alpha=0.15, color="#2e7d32")

    ax.set_ylabel("тыс. м³/сут", fontsize=8)
    ax.set_xlabel("")
    ax.legend(fontsize=7, loc="upper right")
    ax.tick_params(labelsize=7)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.grid(True, alpha=0.3, linewidth=0.5)

    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout(pad=0.5)

    chart_path = TEMP_DIR.resolve() / f"flow_chart_{well_number}_{report_date.isoformat()}.png"
    fig.savefig(str(chart_path), bbox_inches="tight")
    plt.close(fig)

    return str(chart_path)


def _render_dp_chart(
    df: pd.DataFrame,
    well_number: str,
    report_date: date,
    dp_trend: dict | None = None,
) -> str | None:
    """Render ΔP chart with optional linear trend overlay → PNG."""
    if df.empty or len(df) < 5:
        return None

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        return None

    _ensure_dirs()

    # Calculate ΔP (exclude false zeros, clamp negatives to 0)
    pt = df["p_tube"].where(df["p_tube"] > 0)
    pl = df["p_line"].where(df["p_line"] > 0)
    dp = (pt - pl).dropna().clip(lower=0)

    if len(dp) < 5:
        return None

    dp_local = dp.copy()
    dp_local.index = dp_local.index + KUNGRAD_OFFSET

    fig, ax = plt.subplots(figsize=(7.0, 2.0), dpi=150)

    ax.plot(dp_local.index, dp_local.values, color="#e91e63",
            linewidth=0.7, label="ΔP")
    ax.fill_between(dp_local.index, dp_local.values, alpha=0.1, color="#e91e63")

    # Trend line overlay (always show if trend exists)
    if dp_trend:
        t0 = dp.index[0]
        t_hours = np.array([(t - t0).total_seconds() / 3600.0 for t in dp.index])
        coeffs = np.polyfit(t_hours, dp.values, 1)
        trend_y = np.polyval(coeffs, t_hours)

        dir_text = {"up": "Рост", "down": "Снижение", "flat": "Стабильно"}.get(
            dp_trend["direction"], "")
        label = f"Тренд ({dir_text}, {dp_trend['slope_per_day']:.4f}/сут, R²={dp_trend['r_squared']:.2f})"
        ax.plot(dp_local.index, trend_y, color="#333333",
                linewidth=1.2, linestyle="--", label=label)

    ax.set_ylabel("ΔP, кгс/см²", fontsize=8)
    ax.legend(fontsize=6, loc="upper right")
    ax.tick_params(labelsize=7)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.grid(True, alpha=0.3, linewidth=0.5)

    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout(pad=0.5)

    chart_path = TEMP_DIR.resolve() / f"dp_chart_{well_number}_{report_date.isoformat()}.png"
    fig.savefig(str(chart_path), bbox_inches="tight")
    plt.close(fig)

    return str(chart_path)


# ── Data aggregation ─────────────────────────────────────

def _kungrad_day_utc_range(report_date: date):
    """Return (day_start_utc, day_end_utc) for a Kungrad calendar day."""
    day_start_utc = datetime.combine(report_date - timedelta(days=1), time(19, 0))
    day_end_utc = datetime.combine(report_date, time(19, 0))
    return day_start_utc, day_end_utc


def build_well_day_data(
    db: Session,
    well: Well,
    report_date: date,
    downtime_threshold_min: int = 5,
    comparison_days: int = 7,
) -> dict:
    """
    Aggregate all daily data for a single well.

    Uses pressure_raw for detailed analytics: statistics with median,
    purge detection, downtime detection, ΔP trend, flow rate.
    """
    from backend.services.flow_rate.data_access import (
        get_pressure_data, get_purge_events,
    )
    from backend.services.flow_rate.cleaning import clean_pressure
    from backend.services.flow_rate.purge_detector import PurgeDetector
    from backend.services.flow_rate.downtime import detect_downtime_periods
    from backend.services.flow_rate.calculator import (
        calculate_flow_rate, calculate_cumulative, calculate_purge_loss,
    )
    from backend.services.flow_rate.purge_detector import recalculate_purge_loss_with_cycles
    from backend.services.flow_rate.summary import build_summary

    well_number = str(well.number)
    day_start_utc, day_end_utc = _kungrad_day_utc_range(report_date)

    # Kungrad naive timestamps for events
    day_start_local = datetime.combine(report_date, time(0, 0))
    day_end_local = datetime.combine(report_date, time(23, 59, 59))

    # ── 1. Well header ──
    row = db.execute(text("""
        SELECT status FROM well_status
        WHERE well_id = :wid
        ORDER BY dt_start DESC LIMIT 1
    """), {"wid": well.id}).fetchone()
    status = row[0] if row else ""

    row = db.execute(text("""
        SELECT choke_diam_mm, horizon FROM well_construction
        WHERE well_no = :wno
        ORDER BY data_as_of DESC NULLS LAST LIMIT 1
    """), {"wno": well_number}).fetchone()
    choke_mm = float(row[0]) if row and row[0] else None
    horizon_raw = str(row[1]).replace("\n", ", ").replace("\r", "") if row and row[1] else ""
    horizon = _tex_escape(horizon_raw) if horizon_raw else "---"

    # ── 2. Pressure data from pressure_raw ──
    utc_start_str = day_start_utc.isoformat()
    utc_end_str = day_end_utc.isoformat()

    df_raw = get_pressure_data(well.id, utc_start_str, utc_end_str)
    pressure_stats = None
    dp_trend = None
    purge_count = 0
    stoppage_events = []
    purge_total_venting_min = 0
    purge_total_buildup_min = 0
    downtime_short_count = 0
    downtime_short_total_min = 0
    downtime_total_min = 0
    chart_path = None
    flow_chart_path = None
    dp_chart_path = None
    flow_stats = None
    raw_avg_flow = None
    raw_median_flow = None
    downtime_loss_estimate = None
    downtime_short_loss = None
    purge_total_standing_loss = None
    purge_total_atm_loss = None
    data_start_time = "---"
    data_end_time = "---"
    data_points_count = 0
    p_tube_count = 0
    p_line_count = 0

    # ── 2a. Events query (moved before charts for overlay) ──
    event_rows = db.execute(text("""
        SELECT event_time, event_type, description, p_tube, p_line, reagent, qty
        FROM events
        WHERE well = :wno
          AND event_time >= :start AND event_time <= :end
        ORDER BY event_time
    """), {"wno": well_number, "start": day_start_local, "end": day_end_local}).fetchall()

    events = []
    event_type_counts: dict[str, int] = {}
    event_markers: list[dict] = []
    for e in event_rows:
        et = e[1] or "other"
        # Identify "other" events with pressure readings as equipment purge
        has_pressures = (e[3] and float(e[3]) > 0 and e[4] and float(e[4]) > 0)
        if et == "other" and has_pressures:
            label = "Прод. штуц./маном."
        else:
            label = EVENT_TYPE_LABELS.get(et, et)
        event_type_counts[label] = event_type_counts.get(label, 0) + 1

        # Build rich description: include reagent name & qty for reagent events
        desc_parts = []
        if et == "reagent" and e[5]:
            desc_parts.append(_tex_escape(str(e[5])))
            if e[6]:
                desc_parts.append(f"{float(e[6]):.1f}")
        if e[2]:
            desc_parts.append(_tex_escape(str(e[2])))
        description = ", ".join(desc_parts) if desc_parts else "---"

        events.append({
            "time_str": e[0].strftime("%H:%M") if e[0] else "",
            "type_label": _tex_escape(label),
            "description": description,
            "p_tube": _fmt(e[3]) if e[3] and float(e[3]) > 0 else "---",
            "p_line": _fmt(e[4]) if e[4] and float(e[4]) > 0 else "---",
        })
        # Build chart overlay markers (Kungrad naive → UTC for chart index)
        if e[0] and et in ("purge", "reagent"):
            event_markers.append({
                "time_utc": e[0] - KUNGRAD_OFFSET,
                "event_type": et,
            })

    event_type_stats = [
        {"type_label": _tex_escape(k), "count": v}
        for k, v in sorted(event_type_counts.items(), key=lambda x: -x[1])
    ]

    spikes_tube = 0
    spikes_line = 0

    if not df_raw.empty:
        df = clean_pressure(df_raw)

        # Per-channel valid counts (before ffill, exclude false zeros)
        p_tube_count = int(df_raw["p_tube"].dropna().pipe(lambda s: s[s > 0]).shape[0])
        p_line_count = int(df_raw["p_line"].dropna().pipe(lambda s: s[s > 0]).shape[0])

        # Hampel filter — detect and replace spikes (robust outlier detection)
        from backend.services.pressure_filter_service import hampel_filter
        df["p_tube"], spikes_tube = hampel_filter(df["p_tube"])
        df["p_line"], spikes_line = hampel_filter(df["p_line"])

        # Filter out false zeros for statistics, clamp ΔP < 0 to 0
        pt = df["p_tube"].where(df["p_tube"] > 0)
        pl = df["p_line"].where(df["p_line"] > 0)
        dp = (pt - pl).dropna().clip(lower=0)

        data_points_count = len(df)

        # Data period (in Kungrad time)
        first_ts = df.index.min() + KUNGRAD_OFFSET
        last_ts = df.index.max() + KUNGRAD_OFFSET
        data_start_time = first_ts.strftime("%H:%M")
        data_end_time = last_ts.strftime("%H:%M")

        # Pressure statistics
        if pt.dropna().shape[0] > 0:
            pressure_stats = {
                "p_tube_min": _fmt(float(pt.min())),
                "p_tube_max": _fmt(float(pt.max())),
                "p_tube_avg": _fmt(float(pt.mean())),
                "p_tube_median": _fmt(float(pt.median())),
                "p_tube_range": _fmt(float(pt.max()) - float(pt.min())),
                "p_line_min": _fmt(float(pl.min())) if pl.dropna().shape[0] > 0 else "---",
                "p_line_max": _fmt(float(pl.max())) if pl.dropna().shape[0] > 0 else "---",
                "p_line_avg": _fmt(float(pl.mean())) if pl.dropna().shape[0] > 0 else "---",
                "p_line_median": _fmt(float(pl.median())) if pl.dropna().shape[0] > 0 else "---",
                "p_line_range": _fmt(float(pl.max()) - float(pl.min())) if pl.dropna().shape[0] > 0 else "---",
                "dp_min": _fmt(float(dp.min())) if len(dp) > 0 else "---",
                "dp_max": _fmt(float(dp.max())) if len(dp) > 0 else "---",
                "dp_avg": _fmt(float(dp.mean())) if len(dp) > 0 else "---",
                "dp_median": _fmt(float(dp.median())) if len(dp) > 0 else "---",
                "dp_range": _fmt(float(dp.max()) - float(dp.min())) if len(dp) > 0 else "---",
            }

        # ΔP trend analysis (always show — even flat trend is informative)
        if len(dp) >= 3:
            duration_hours = (df.index.max() - df.index.min()).total_seconds() / 3600.0
            trend = _linear_trend(dp.tolist(), duration_hours)
            if trend:
                dp_trend = trend

        # ΔP chart with trend overlay
        try:
            dp_chart_path = _render_dp_chart(df, well_number, report_date, dp_trend)
        except Exception:
            log.exception("DP chart rendering failed for well %s", well_number)

        # ── 3. Purge + Downtime detection (unified) ──
        purge_ranges = []

        # 3a. Purge detection (confidence ≥ 0.95 = confirmed purge)
        try:
            events_df = get_purge_events(well.id, utc_start_str, utc_end_str)
            detector = PurgeDetector()
            purge_cycles = detector.detect(df, events_df)
            active_cycles = [c for c in purge_cycles if not c.excluded]

            confirmed = [c for c in active_cycles
                         if c.confidence >= 0.95 and c.venting_start]
            purge_count = len(confirmed)
            purge_total_venting_min = round(
                sum(c.venting_duration_min for c in confirmed))
            purge_total_buildup_min = round(
                sum(c.buildup_duration_min for c in confirmed))

            for c in confirmed:
                p_end_ts = c.buildup_end or c.venting_end or c.venting_start
                purge_ranges.append((c.venting_start, p_end_ts))

                start_local = c.venting_start + KUNGRAD_OFFSET
                end_local = p_end_ts + KUNGRAD_OFFSET
                vent_min = float(c.venting_duration_min) if c.venting_duration_min else 0
                build_min = float(c.buildup_duration_min) if c.buildup_duration_min else 0

                stoppage_events.append({
                    "type_label": "Продувка",
                    "start_time": start_local.strftime("%H:%M"),
                    "end_time": end_local.strftime("%H:%M"),
                    "duration_min": _fmt(vent_min + build_min, 0),
                    "details": (
                        f"start P={_fmt(c.p_start)} $\\rightarrow$ "
                        f"стравл. {_fmt(vent_min, 0)}м P={_fmt(c.p_bottom)} $\\rightarrow$ "
                        f"набор {_fmt(build_min, 0)}м $\\rightarrow$ "
                        f"пуск P={_fmt(c.p_end)}"
                    ),
                    "standing_loss": "---",
                    "atm_loss": "---",
                    "total_loss": "---",
                    "_total_min": vent_min + build_min,
                    "_venting_min": vent_min,
                    "_p_start": float(c.p_start) if c.p_start else 0,
                    "_p_bottom": float(c.p_bottom) if c.p_bottom else 0,
                    "_standing_loss": 0.0,
                    "_atm_loss": 0.0,
                    "_sort_ts": c.venting_start,
                })
        except Exception:
            log.exception("Purge detection failed for well %s", well_number)
            purge_cycles = []

        # 3b. Downtime detection (non-overlapping with confirmed purges)
        try:
            dt_periods = detect_downtime_periods(df)
            if not dt_periods.empty:
                for _, row_dt in dt_periods.iterrows():
                    dur = float(row_dt["duration_min"])
                    if dur < downtime_threshold_min:
                        downtime_short_count += 1
                        downtime_short_total_min += round(dur)
                    else:
                        dt_s = row_dt["start"]
                        dt_e = row_dt["end"]
                        overlaps = any(
                            not (dt_e < ps or dt_s > pe)
                            for ps, pe in purge_ranges
                        )
                        if not overlaps:
                            start_local = dt_s + KUNGRAD_OFFSET
                            end_local = dt_e + KUNGRAD_OFFSET
                            stoppage_events.append({
                                "type_label": "Простой",
                                "start_time": start_local.strftime("%H:%M"),
                                "end_time": end_local.strftime("%H:%M"),
                                "duration_min": _fmt(dur, 0),
                                "details": "---",
                                "standing_loss": "---",
                                "atm_loss": "---",
                                "total_loss": "---",
                                "_total_min": dur,
                                "_venting_min": 0,
                                "_p_start": 0,
                                "_p_bottom": 0,
                                "_standing_loss": 0.0,
                                "_atm_loss": 0.0,
                                "_sort_ts": dt_s,
                            })
                downtime_total_min = round(float(dt_periods["duration_min"].sum()))
        except Exception:
            log.exception("Downtime detection failed for well %s", well_number)
            dt_periods = pd.DataFrame()

        # Sort stoppages by time
        stoppage_events.sort(key=lambda x: x["_sort_ts"])

        # ── 5. Flow rate ──
        # Always compute df_flow for charts if possible
        df_flow_for_chart = None
        if choke_mm and not df.empty:
            try:
                df_flow_for_chart = calculate_flow_rate(df.copy(), choke_mm)
            except Exception:
                log.exception("Flow calc for chart failed for well %s", well_number)

        # Try flow_result first (pre-computed baseline)
        fr = db.execute(text("""
            SELECT
                fr.avg_flow_rate, fr.median_flow_rate,
                fr.min_flow_rate, fr.max_flow_rate,
                fr.cumulative_flow, fr.purge_loss,
                fr.downtime_minutes, fr.data_points
            FROM flow_result fr
            JOIN flow_scenario fs ON fs.id = fr.scenario_id
            WHERE fs.well_id = :wid
              AND fs.is_baseline = TRUE
              AND fr.result_date = :rd
            LIMIT 1
        """), {"wid": well.id, "rd": report_date}).fetchone()

        # Compute clean avg from df_flow (only working minutes where flow_rate > 0)
        clean_avg_flow = None
        if df_flow_for_chart is not None and "flow_rate" in df_flow_for_chart.columns:
            q_pos = df_flow_for_chart["flow_rate"][df_flow_for_chart["flow_rate"] > 0]
            if len(q_pos) > 0:
                clean_avg_flow = float(q_pos.mean())

        if fr:
            # Use pre-computed flow stats + compute КИВ
            total_min = 1440  # 24h
            dt_min = downtime_total_min
            utilization = ((total_min - dt_min) / total_min * 100.0) if total_min > 0 else 0
            raw_median_flow = float(fr[1]) if fr[1] else None
            cum_flow = float(fr[4]) if fr[4] else 0

            # Use clean avg (only working minutes) or fallback to stored avg
            display_avg = clean_avg_flow if clean_avg_flow else (float(fr[0]) if fr[0] else None)
            raw_avg_flow = display_avg

            flow_stats = {
                "avg_flow_rate": _fmt(display_avg),
                "median_flow_rate": _fmt(fr[1]),
                "min_flow_rate": _fmt(fr[2]),
                "max_flow_rate": _fmt(fr[3]),
                "effective_flow_rate": _fmt(display_avg),
                "utilization_pct": _fmt(utilization, 1),
                "cumulative_flow": _fmt(cum_flow),
                "purge_loss": _fmt(fr[5]) if purge_count > 0 else "0",
                "downtime_minutes": _fmt(dt_min, 0),
            }
        elif df_flow_for_chart is not None:
            # Calculate flow rate from raw pressure data
            try:
                df_flow = df_flow_for_chart.copy()
                df_flow = calculate_purge_loss(df_flow)
                df_flow = calculate_cumulative(df_flow)

                if purge_cycles:
                    df_flow = recalculate_purge_loss_with_cycles(df_flow, purge_cycles)

                if not hasattr(dt_periods, 'empty'):
                    dt_periods = pd.DataFrame()

                summary = build_summary(
                    df_flow, dt_periods, well.id, choke_mm,
                    purge_cycles if purge_cycles else None,
                )

                if "error" not in summary:
                    raw_median_flow = summary.get("median_flow_rate")
                    # Use clean avg (only working minutes)
                    display_avg = clean_avg_flow if clean_avg_flow else summary.get("mean_flow_rate")
                    raw_avg_flow = display_avg

                    flow_stats = {
                        "avg_flow_rate": _fmt(display_avg),
                        "median_flow_rate": _fmt(summary.get("median_flow_rate")),
                        "min_flow_rate": _fmt(summary.get("q1_flow_rate")),
                        "max_flow_rate": _fmt(summary.get("q3_flow_rate")),
                        "effective_flow_rate": _fmt(display_avg),
                        "utilization_pct": _fmt(summary.get("utilization_pct"), 1),
                        "cumulative_flow": _fmt(summary.get("cumulative_flow")),
                        "purge_loss": _fmt(summary.get("purge_loss_total")) if purge_count > 0 else "0",
                        "downtime_minutes": _fmt(downtime_total_min, 0),
                    }
            except Exception:
                log.exception("Flow calculation failed for well %s", well_number)

        # ── 5b. Historical flow comparison ──
        week_avg_flow = None
        week_median_flow = None
        month_cumulative = None
        month_avg_flow = None
        month_median_flow = None
        month_working_days = 0
        delta_q_month = None
        delta_q_month_pct = None

        if flow_stats:
            month_start = report_date.replace(day=1)

            # Weekly average + median (past N days)
            week_start = report_date - timedelta(days=comparison_days)
            week_row = db.execute(text("""
                SELECT
                    AVG(fr.median_flow_rate),
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY fr.median_flow_rate),
                    COUNT(*)
                FROM flow_result fr
                JOIN flow_scenario fs ON fs.id = fr.scenario_id
                WHERE fs.well_id = :wid
                  AND fs.is_baseline = TRUE
                  AND fr.result_date BETWEEN :ws AND :rd
            """), {"wid": well.id, "ws": week_start, "rd": report_date}).fetchone()

            if week_row:
                if week_row[0]:
                    week_avg_flow = float(week_row[0])
                if week_row[1]:
                    week_median_flow = float(week_row[1])

            # Month-to-date: cumulative, average, median, working days
            month_row = db.execute(text("""
                SELECT
                    SUM(fr.cumulative_flow),
                    AVG(fr.median_flow_rate),
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY fr.median_flow_rate),
                    COUNT(*)
                FROM flow_result fr
                JOIN flow_scenario fs ON fs.id = fr.scenario_id
                WHERE fs.well_id = :wid
                  AND fs.is_baseline = TRUE
                  AND fr.result_date BETWEEN :ms AND :rd
            """), {"wid": well.id, "ms": month_start, "rd": report_date}).fetchone()

            if month_row:
                if month_row[0]:
                    month_cumulative = float(month_row[0])
                if month_row[1]:
                    month_avg_flow = float(month_row[1])
                if month_row[2]:
                    month_median_flow = float(month_row[2])
                if month_row[3]:
                    month_working_days = int(month_row[3])

            # Fallback: if flow_result has no monthly data, compute from raw pressure
            if month_cumulative is None and choke_mm:
                try:
                    result = _compute_month_flow_from_raw(
                        db, well, choke_mm, month_start, report_date,
                        get_pressure_data, clean_pressure,
                        calculate_flow_rate, calculate_cumulative,
                        comparison_days=comparison_days,
                    )
                    if result:
                        month_cumulative = result["month_cumulative"]
                        month_avg_flow = result["month_avg_flow"]
                        month_median_flow = result["month_median_flow"]
                        month_working_days = result.get("working_days", 0)
                        if week_avg_flow is None:
                            week_avg_flow = result.get("week_avg_flow")
                        if week_median_flow is None:
                            week_median_flow = result.get("week_median_flow")
                except Exception:
                    log.exception("Month flow fallback failed for well %s", well_number)

            # Last fallback: use today's cumulative if still nothing
            if month_cumulative is None and flow_stats and flow_stats.get("cumulative_flow"):
                try:
                    today_cum = float(flow_stats["cumulative_flow"].replace(",", "."))
                    if today_cum > 0:
                        month_cumulative = today_cum
                        month_working_days = 1
                except (ValueError, AttributeError):
                    pass

            # ΔQ relative to month median
            current_q = raw_median_flow if raw_median_flow else raw_avg_flow
            if current_q and month_median_flow and month_median_flow > 0:
                delta_q_month = current_q - month_median_flow
                delta_q_month_pct = (delta_q_month / month_median_flow * 100.0)

        # ── 5c. Loss estimates (per-stoppage) ──
        med = raw_median_flow if raw_median_flow else raw_avg_flow
        if med:
            per_min_flow = med / 1440.0  # тыс м³/мин

            # Total downtime loss
            if downtime_total_min > 0:
                downtime_loss_estimate = per_min_flow * downtime_total_min

            # Short downtime loss
            if downtime_short_total_min > 0:
                downtime_short_loss = per_min_flow * downtime_short_total_min

            # Per-stoppage losses (standing + atmospheric)
            from backend.services.flow_rate.config import DEFAULT_PURGE as _purge_cfg
            for se in stoppage_events:
                # Standing loss: production lost while well idle
                sl = per_min_flow * se["_total_min"]
                se["standing_loss"] = _fmt(sl, 3)
                se["_standing_loss"] = sl

                # Atmospheric loss: only for confirmed purges
                if se["_venting_min"] > 0:
                    p_avg = (se["_p_start"] + se["_p_bottom"]) / 2.0
                    if p_avg > 0:
                        p_mpa = p_avg * _purge_cfg.kgscm2_to_MPa
                        delta_p_pa = max((p_mpa - _purge_cfg.atm_pressure_MPa) * 1e6, 0)
                        area = np.pi * (_purge_cfg.choke_diameter_m / 2.0) ** 2
                        rho = max(
                            (p_mpa * 1e6) * _purge_cfg.molar_mass
                            / (_purge_cfg.gas_constant * _purge_cfg.standard_temp_K),
                            1e-6,
                        )
                        q_m3s = _purge_cfg.discharge_coeff * area * np.sqrt(2.0 * delta_p_pa / rho)
                        al = q_m3s * se["_venting_min"] * 60.0 / 1000.0
                        se["atm_loss"] = _fmt(al, 3)
                        se["_atm_loss"] = al

            # Compute total_loss per stoppage (standing + atmospheric)
            for se in stoppage_events:
                tl = se["_standing_loss"] + se["_atm_loss"]
                se["total_loss"] = _fmt(tl, 3) if tl > 0 else "---"

            if purge_count > 0:
                purge_total_standing_loss = sum(
                    se["_standing_loss"] for se in stoppage_events
                    if se["_venting_min"] > 0
                )
                purge_total_atm_loss = sum(
                    se["_atm_loss"] for se in stoppage_events
                )

        # ── 6. Charts ──
        try:
            chart_path = _render_pressure_chart(
                df, well_number, report_date,
                event_markers=event_markers if event_markers else None,
            )
        except Exception:
            log.exception("Chart rendering failed for well %s", well_number)

        try:
            if df_flow_for_chart is not None:
                flow_chart_path = _render_flow_chart(df_flow_for_chart, well_number, report_date)
        except Exception:
            log.exception("Flow chart rendering failed for well %s", well_number)

    # ── 7. Reagent stats + intervals + prev day comparison ──
    reagent_rows = db.execute(text("""
        SELECT reagent, SUM(qty) AS total_qty, COUNT(*) AS cnt
        FROM events
        WHERE well = :wno
          AND event_type = 'reagent'
          AND event_time >= :start AND event_time <= :end
          AND reagent IS NOT NULL
        GROUP BY reagent
        ORDER BY reagent
    """), {"wno": well_number, "start": day_start_local, "end": day_end_local}).fetchall()

    reagent_stats = []
    for rr in reagent_rows:
        reagent_stats.append({
            "name": _tex_escape(rr[0]),
            "total_qty": _fmt(float(rr[1]), 1) if rr[1] else "0",
            "count": int(rr[2]),
        })

    # Reagent intervals (avg hours between injections)
    reagent_intervals = []
    interval_rows = db.execute(text("""
        SELECT reagent, event_time
        FROM events
        WHERE well = :wno
          AND event_type = 'reagent'
          AND event_time >= :start AND event_time <= :end
          AND reagent IS NOT NULL
        ORDER BY reagent, event_time
    """), {"wno": well_number, "start": day_start_local, "end": day_end_local}).fetchall()

    reagent_times: dict[str, list[datetime]] = {}
    for ri_row in interval_rows:
        rname = ri_row[0]
        rtime = ri_row[1]
        reagent_times.setdefault(rname, []).append(rtime)

    for rname, times_list in reagent_times.items():
        if len(times_list) >= 2:
            diffs = []
            for i in range(1, len(times_list)):
                diff_h = (times_list[i] - times_list[i - 1]).total_seconds() / 3600.0
                diffs.append(diff_h)
            avg_interval = sum(diffs) / len(diffs)
            reagent_intervals.append({
                "reagent": _tex_escape(rname),
                "interval_hours": _fmt(avg_interval, 1),
            })
        elif len(times_list) == 1:
            # Single injection today — use prev day's last injection
            prev_last = db.execute(text("""
                SELECT MAX(event_time) FROM events
                WHERE well = :wno AND event_type = 'reagent'
                  AND reagent = :rname AND event_time < :today_start
            """), {"wno": well_number, "rname": rname,
                   "today_start": day_start_local}).scalar()
            if prev_last:
                diff_h = (times_list[0] - prev_last).total_seconds() / 3600.0
                reagent_intervals.append({
                    "reagent": _tex_escape(rname),
                    "interval_hours": _fmt(diff_h, 1),
                })

    # Merge interval_hours into reagent_stats for unified table
    for rs in reagent_stats:
        rs["interval_hours"] = "---"
        for ri in reagent_intervals:
            if ri["reagent"] == rs["name"]:
                rs["interval_hours"] = ri["interval_hours"]
                break

    # Previous day comparison
    prev_date_r = report_date - timedelta(days=1)
    prev_start = datetime.combine(prev_date_r, time(0, 0))
    prev_end = datetime.combine(prev_date_r, time(23, 59, 59))

    prev_reagent_rows = db.execute(text("""
        SELECT reagent, COUNT(*) AS cnt
        FROM events
        WHERE well = :wno
          AND event_type = 'reagent'
          AND event_time >= :start AND event_time <= :end
          AND reagent IS NOT NULL
        GROUP BY reagent
    """), {"wno": well_number, "start": prev_start, "end": prev_end}).fetchall()

    prev_counts = {r[0]: int(r[1]) for r in prev_reagent_rows}

    reagent_prev_day = []
    all_reagents = set(r["name"] for r in reagent_stats)
    # Add reagents from prev day that might not exist today (after tex_escape)
    for rname_raw in prev_counts:
        all_reagents.add(_tex_escape(rname_raw))

    for rname_esc in sorted(all_reagents):
        # Find today's count
        today_count = 0
        for rs in reagent_stats:
            if rs["name"] == rname_esc:
                today_count = rs["count"]
                break

        # Find yesterday's count (match on original name)
        yesterday_count = 0
        for rname_raw, cnt in prev_counts.items():
            if _tex_escape(rname_raw) == rname_esc:
                yesterday_count = cnt
                break

        diff = today_count - yesterday_count
        diff_str = f"+{diff}" if diff > 0 else str(diff)

        reagent_prev_day.append({
            "reagent": rname_esc,
            "today_count": today_count,
            "yesterday_count": yesterday_count,
            "diff": diff_str,
        })

    # ── Flow formula coefficients for LaTeX description ──
    from backend.services.flow_rate.config import DEFAULT_FLOW, DEFAULT_PURGE
    flow_formula = {
        "C1": DEFAULT_FLOW.C1,
        "C2": DEFAULT_FLOW.C2,
        "C3": DEFAULT_FLOW.C3,
        "multiplier": DEFAULT_FLOW.multiplier,
        "purge_Cd": DEFAULT_PURGE.discharge_coeff,
        "purge_d_mm": int(DEFAULT_PURGE.choke_diameter_m * 1000),
    }

    return {
        "well_number": well_number,
        "well_name": _tex_escape(well.name or ""),
        "report_date": report_date.strftime("%d.%m.%Y"),
        "status": _tex_escape(status),
        "horizon": horizon,
        "choke_mm": _fmt(choke_mm),
        # Data coverage
        "data_start_time": data_start_time,
        "data_end_time": data_end_time,
        "data_points_count": data_points_count,
        "p_tube_count": p_tube_count,
        "p_line_count": p_line_count,
        "interpolated_tube": max(data_points_count - p_tube_count, 0),
        "interpolated_line": max(data_points_count - p_line_count, 0),
        "spikes_tube": spikes_tube,
        "spikes_line": spikes_line,
        # Pressure
        "pressure_stats": pressure_stats,
        "dp_trend": dp_trend,
        # Stoppages (unified purges + downtimes)
        "stoppage_events": stoppage_events,
        "purge_count": purge_count,
        "purge_total_venting_min": purge_total_venting_min,
        "purge_total_buildup_min": purge_total_buildup_min,
        "purge_total_standing_loss": _fmt(purge_total_standing_loss, 3) if purge_total_standing_loss else "---",
        "purge_total_atm_loss": _fmt(purge_total_atm_loss, 3) if purge_total_atm_loss else "---",
        "downtime_short_count": downtime_short_count,
        "downtime_short_total_min": downtime_short_total_min,
        "downtime_short_loss": _fmt(downtime_short_loss, 3) if downtime_short_loss else "---",
        "downtime_threshold_min": downtime_threshold_min,
        "downtime_total_min": downtime_total_min,
        "downtime_loss_estimate": _fmt(downtime_loss_estimate, 3) if downtime_loss_estimate else "---",
        # Flow
        "flow_stats": flow_stats,
        # Flow comparison
        "week_avg_flow": _fmt(week_avg_flow) if week_avg_flow else "---",
        "week_median_flow": _fmt(week_median_flow) if week_median_flow else "---",
        "comparison_days": comparison_days,
        "month_cumulative": _fmt(month_cumulative) if month_cumulative else "---",
        "month_avg_flow": _fmt(month_avg_flow) if month_avg_flow else "---",
        "month_median_flow": _fmt(month_median_flow) if month_median_flow else "---",
        "month_working_days": month_working_days,
        "month_calendar_days": report_date.day,
        "delta_q_month": _fmt(delta_q_month) if delta_q_month is not None else "---",
        "delta_q_month_pct": _fmt(delta_q_month_pct, 1) if delta_q_month_pct is not None else "---",
        # Formula coefficients
        "flow_formula": flow_formula,
        # Charts
        "chart_path": chart_path,
        "flow_chart_path": flow_chart_path,
        "dp_chart_path": dp_chart_path,
        # Events
        "events": events,
        "event_type_stats": event_type_stats,
        # Reagents
        "reagent_stats": reagent_stats,
        "reagent_intervals": reagent_intervals,
        "reagent_prev_day": reagent_prev_day,
    }


# ── PDF generation ───────────────────────────────────────

def generate_daily_report_pdf(doc, db: Session) -> str:
    """
    Generate PDF for a daily report document.

    doc.meta must contain:
      - report_date (str YYYY-MM-DD)
      - well_ids (list[int]) — one for single, multiple for summary

    Returns: relative PDF path for doc.pdf_filename.
    """
    _ensure_dirs()

    meta = doc.meta or {}
    report_date = date.fromisoformat(meta["report_date"])
    well_ids = meta.get("well_ids", [])
    downtime_threshold_min = meta.get("downtime_threshold_min", 5)
    comparison_days = meta.get("comparison_days", 7)

    if doc.well_id and not well_ids:
        well_ids = [doc.well_id]

    # Load wells
    wells = db.query(Well).filter(Well.id.in_(well_ids)).order_by(Well.number).all()
    if not wells:
        raise ValueError("Не найдены скважины для отчёта")

    # Build data for each well
    wells_data = []
    for w in wells:
        wd = build_well_day_data(
            db, w, report_date,
            downtime_threshold_min=downtime_threshold_min,
            comparison_days=comparison_days,
        )
        wells_data.append(wd)

    now_kungrad = datetime.utcnow() + KUNGRAD_OFFSET

    context = {
        "doc_number": _tex_escape(doc.doc_number or f"ID{doc.id}"),
        "report_date": report_date.strftime("%d.%m.%Y"),
        "generated_at": now_kungrad.strftime("%d.%m.%Y %H:%M"),
        "wells_data": wells_data,
        "is_summary": len(wells_data) > 1,
        "total_wells": len(wells_data),
    }

    # Render LaTeX
    env = _get_latex_env()
    template = env.get_template("daily_report.tex")
    latex_source = template.render(**context)

    # Compile
    base_name = f"daily_report_{doc.id}"
    pdf_path = _compile_latex(latex_source, base_name)

    # Cleanup chart PNGs
    for w in wells_data:
        for key in ("chart_path", "flow_chart_path", "dp_chart_path"):
            cp = w.get(key)
            if cp:
                p = Path(cp)
                if p.exists():
                    p.unlink()

    log.info("Daily report PDF generated: %s", pdf_path)
    return f"generated/pdf/{base_name}.pdf"
