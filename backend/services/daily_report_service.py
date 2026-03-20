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


def _smart_event_label(event_type: str, description: str) -> str:
    """Generate a smart label for events based on type and description.

    - 'other': use description keywords instead of 'Другое'
    - 'equip': show 'Установка/снятие оборудования' + device details
    """
    desc_lower = (description or "").lower()

    if event_type == "other":
        # Try to detect what it actually is
        if "гидрат" in desc_lower or "гидрад" in desc_lower:
            return "Гидратообразование"
        if "продувка" in desc_lower and "штуцер" in desc_lower:
            return "Прод. штуцера"
        if "продувка" in desc_lower and "маном" in desc_lower:
            return "Прод. манометра"
        if "продувка" in desc_lower and "лубрикатор" in desc_lower:
            return "Прод. лубрикатора"
        if "продувка" in desc_lower:
            return "Продувка (прочее)"
        if "давлени" in desc_lower:
            return "Замер давления"
        # Show first meaningful words from description
        if description and len(description.strip()) > 2:
            short = description.strip()[:40].rstrip()
            if "[" in short:
                short = short[:short.index("[")].strip()
            return short if short else "Примечание"
        return "Примечание"

    if event_type == "equip":
        # Extract device details
        if "снято" in desc_lower:
            return "Снятие оборудования"
        if "установ" in desc_lower:
            return "Установка оборудования"
        # Try to detect device type from serial numbers
        if description and description.strip():
            short = description.strip()[:50]
            return f"Оборудование: {short}"
        return "Установка/снятие оборудования"

    return EVENT_TYPE_LABELS.get(event_type, event_type or "---")


def _count_anomalies_from_events(
    db, well_number: str, start: datetime, end: datetime,
) -> dict:
    """Count anomaly events by keyword search in description + event_type.

    Returns dict with: hydrate, choke_purge, manometer_purge counts.
    """
    rows = db.execute(text("""
        SELECT event_type, description, purge_phase
        FROM events
        WHERE well = :wno AND event_time >= :start AND event_time <= :end
    """), {"wno": well_number, "start": start, "end": end}).fetchall()

    hydrate = 0
    choke_purge = 0
    manometer_purge = 0

    for r in rows:
        et = (r[0] or "").lower()
        desc = (r[1] or "").lower()
        phase = (r[2] or "").lower()
        combined = f"{et} {desc} {phase}"

        if "гидрат" in combined or "гидрад" in combined:
            hydrate += 1
        if ("штуцер" in combined or "штуц" in combined) and ("продув" in combined or et == "purge" or et == "other"):
            choke_purge += 1
        if ("маном" in combined) and ("продув" in combined or et == "purge" or et == "other"):
            manometer_purge += 1

    return {
        "hydrate": hydrate,
        "choke_purge": choke_purge,
        "manometer_purge": manometer_purge,
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

    temp_pdf = TEMP_DIR / f"{base_name}.pdf"

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
            # nonstopmode may produce PDF despite warnings — only fail if no PDF
            if not temp_pdf.exists():
                log.error("xelatex pass %d failed (no PDF).\nSTDERR: %s\nSTDOUT (last 2000): %s",
                           pass_num + 1, stderr[-500:], stdout[-2000:])
                raise RuntimeError(
                    f"xelatex compilation failed (pass {pass_num + 1}): "
                    f"{stdout[-500:]}"
                )
            log.warning("xelatex pass %d returned non-zero but PDF exists (warnings)", pass_num + 1)
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

    # Apply verified pressure masks
    try:
        from backend.services.pressure_mask_service import (
            load_active_masks, apply_masks as _apply_masks,
        )
        masks = load_active_masks(
            well.id, utc_start, utc_end, verified_only=True,
        )
        if masks:
            df, _ = _apply_masks(df, masks)
    except Exception:
        pass

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
    if len(valid) < 2:
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
    purge_incomplete_count = 0
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
        label = _smart_event_label(et, e[2])
        if et != "purge":
            # Purges counted via sessions, not individual events
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

    masks_applied_count = 0

    if not df_raw.empty:
        df = clean_pressure(df_raw)

        # Per-channel valid counts (before ffill, exclude false zeros)
        p_tube_count = int(df_raw["p_tube"].dropna().pipe(lambda s: s[s > 0]).shape[0])
        p_line_count = int(df_raw["p_line"].dropna().pipe(lambda s: s[s > 0]).shape[0])

        # ── Apply verified pressure masks (before Hampel filter) ──
        try:
            from backend.services.pressure_mask_service import (
                load_active_masks, apply_masks as _apply_masks,
            )
            masks = load_active_masks(
                well.id, day_start_utc, day_end_utc, verified_only=True,
            )
            if masks:
                df, masks_applied_count = _apply_masks(df, masks)
        except Exception as exc:
            log.warning("[build_well_day_data] mask apply error: %s", exc)

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

        # 3a. Purge sessions from events (start/press/stop = 1 session)
        try:
            ps_result = _count_purge_sessions_from_events(
                db, well_number, day_start_local, day_end_local)
            purge_count = ps_result["complete"]
            purge_incomplete_count = ps_result["incomplete"]

            for sess in ps_result.get("sessions", []):
                dur = sess.get("duration_min", 0)
                is_complete = sess.get("complete", False)
                phases_str = "$\\rightarrow$".join(sess.get("phases", []))
                label = "Продувка (полн.)" if is_complete else "Продувка (неполн.)"

                start_local = sess["start_time"]
                end_local = sess["last_time"]

                # Convert to UTC for purge_ranges (overlap check with downtime)
                purge_ranges.append((
                    start_local - KUNGRAD_OFFSET,
                    end_local - KUNGRAD_OFFSET,
                ))

                stoppage_events.append({
                    "type_label": label,
                    "start_time": start_local.strftime("%H:%M"),
                    "end_time": end_local.strftime("%H:%M"),
                    "duration_min": _fmt(dur, 0),
                    "details": phases_str,
                    "standing_loss": "---",
                    "atm_loss": "---",
                    "total_loss": "---",
                    "_total_min": dur,
                    "_venting_min": dur if is_complete else 0,
                    "_p_start": 0,
                    "_p_bottom": 0,
                    "_standing_loss": 0.0,
                    "_atm_loss": 0.0,
                    "_sort_ts": start_local - KUNGRAD_OFFSET,
                })

            purge_total_venting_min = round(sum(
                s.get("duration_min", 0) for s in ps_result.get("sessions", [])
                if s.get("complete")))
            purge_total_buildup_min = 0  # not tracked in event-based approach

            # For pressure-based loss calc, try to get PurgeDetector cycles
            try:
                events_df = get_purge_events(well.id, utc_start_str, utc_end_str)
                detector = PurgeDetector()
                purge_cycles = detector.detect(df, events_df)
            except Exception:
                purge_cycles = []

        except Exception:
            log.exception("Purge session count failed for well %s", well_number)
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
        "choke_mm_raw": choke_mm,
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
        "masks_applied_count": masks_applied_count,
        # Pressure
        "pressure_stats": pressure_stats,
        "dp_trend": dp_trend,
        # Stoppages (unified purges + downtimes)
        "stoppage_events": stoppage_events,
        "purge_count": purge_count,
        "purge_incomplete_count": purge_incomplete_count,
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


# ── Loss base Q: working-hours-only median ─────────────────

# Minimum Q (тыс.м³/сут) to count hour as "working"
Q_WORKING_THRESHOLD = 2.0


def _compute_loss_base_q(
    db: Session, well_id: int, choke_mm: float | None,
    period_end: datetime, window_hours: int = 24,
) -> float | None:
    """Compute base Q for loss estimation: median of working hours only.

    Looks back `window_hours` from `period_end`, takes only hours where Q > threshold.
    If no working hours found, returns None.
    """
    if not choke_mm or choke_mm <= 0:
        return None

    from backend.services.flow_rate.config import DEFAULT_FLOW as cfg

    period_start = period_end - timedelta(hours=window_hours)
    rows = db.execute(text("""
        SELECT NULLIF(p_tube_avg, 0) AS pt, NULLIF(p_line_avg, 0) AS pl
        FROM pressure_hourly
        WHERE well_id = :wid
          AND hour_start >= :sd AND hour_start < :ed
          AND NULLIF(p_tube_avg, 0) IS NOT NULL
          AND NULLIF(p_line_avg, 0) IS NOT NULL
        ORDER BY hour_start
    """), {"wid": well_id, "sd": period_start, "ed": period_end}).fetchall()

    if not rows:
        return None

    choke_sq = (choke_mm / cfg.C2) ** 2
    working_q = []

    for r in rows:
        pt, pl = float(r[0]), float(r[1])
        if pt <= pl or pt <= 0:
            continue
        ratio = (pt - pl) / pt
        if ratio < cfg.critical_ratio:
            q = cfg.C1 * choke_sq * pt * (1.0 - ratio / 1.5) * (ratio / cfg.C3) ** 0.5
        else:
            q = 0.667 * cfg.C1 * choke_sq * pt * (0.5 / cfg.C3) ** 0.5
        q = max(q * cfg.multiplier, 0.0)

        if q > Q_WORKING_THRESHOLD:
            working_q.append(q)

    if not working_q:
        return None

    return float(np.median(working_q))


# ── Daily avg flow from pressure_hourly ────────────────────

def _get_daily_avg_flow(
    db: Session, well_id: int, choke_mm: float | None,
    start_date: date, end_date: date,
) -> list[tuple[date, float]]:
    """Compute avg daily flow rate from pressure_hourly.

    Returns list of (date, avg_q_per_day) where avg_q = cumulative_flow / 24.
    Uses flow formula on median hourly pressures per day.
    """
    if not choke_mm or choke_mm <= 0:
        return []

    from backend.services.flow_rate.config import DEFAULT_FLOW as cfg

    # Only hours where both pressures valid AND p_tube > p_line (well flowing)
    rows = db.execute(text("""
        SELECT hour_start::date AS d,
               AVG(p_tube_avg) AS pt,
               AVG(p_line_avg) AS pl,
               COUNT(*) AS cnt
        FROM pressure_hourly
        WHERE well_id = :wid
          AND hour_start >= :sd AND hour_start < :ed + INTERVAL '1 day'
          AND NULLIF(p_tube_avg, 0) IS NOT NULL
          AND NULLIF(p_line_avg, 0) IS NOT NULL
          AND p_tube_avg > p_line_avg
          AND (p_tube_avg - p_line_avg) > 0.1
        GROUP BY d
        HAVING COUNT(*) >= 3
        ORDER BY d
    """), {"wid": well_id, "sd": start_date, "ed": end_date}).fetchall()

    result = []
    choke_sq = (choke_mm / cfg.C2) ** 2

    for r in rows:
        d, pt, pl = r[0], r[1], r[2]
        if pt is None or pl is None or pt <= 0:
            continue
        pt, pl = float(pt), float(pl)
        if pt <= pl:
            continue  # skip — no valid flow

        ratio = (pt - pl) / pt
        if ratio < cfg.critical_ratio:
            q = cfg.C1 * choke_sq * pt * (1.0 - ratio / 1.5) * (ratio / cfg.C3) ** 0.5
        else:
            q = 0.667 * cfg.C1 * choke_sq * pt * (0.5 / cfg.C3) ** 0.5

        q = max(q * cfg.multiplier, 0.0)
        # q is instantaneous тыс.м³/сут — already daily rate
        # avg daily flow = this value (it's rate, not volume)
        result.append((d, round(q, 2)))

    return result


# ── Mini flow charts for summary report ────────────────────

def _render_monthly_flow_grid(
    db: Session, wells: list, report_date: date,
    chart_style: str = "line",
) -> str | None:
    """Render a grid of mini flow-rate charts (one per well) → single PNG.

    chart_style: 'line' | 'bar' | 'area' | 'stem'
    Returns absolute path to PNG or None.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        return None

    _ensure_dirs()

    month_start = report_date.replace(day=1)

    chart_data = []
    for w in wells:
        row = db.execute(text("""
            SELECT choke_diam_mm FROM well_construction
            WHERE well_no = :wno
            ORDER BY data_as_of DESC NULLS LAST LIMIT 1
        """), {"wno": str(w.number)}).fetchone()
        choke_mm = float(row[0]) if row and row[0] else None

        daily = _get_daily_avg_flow(db, w.id, choke_mm, month_start, report_date)
        if len(daily) >= 2:
            chart_data.append({
                "well_number": str(w.number),
                "dates": [d[0] for d in daily],
                "values": [d[1] for d in daily],
            })

    if not chart_data:
        return None

    ncols = 3
    nrows = (len(chart_data) + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(7.0, 2.2 * nrows), dpi=150)

    if nrows == 1 and ncols == 1:
        axes = np.array([[axes]])
    elif nrows == 1:
        axes = axes.reshape(1, -1)
    elif ncols == 1:
        axes = axes.reshape(-1, 1)

    clr = "#2e7d32"

    for idx, cd in enumerate(chart_data):
        r, c = divmod(idx, ncols)
        ax = axes[r][c]
        dates, vals = cd["dates"], cd["values"]

        if chart_style == "bar":
            ax.bar(dates, vals, color=clr, alpha=0.7, width=0.8)
        elif chart_style == "area":
            ax.fill_between(dates, vals, alpha=0.25, color=clr)
            ax.plot(dates, vals, color=clr, linewidth=0.8,
                    marker="o", markersize=2.5, markerfacecolor=clr)
        elif chart_style == "stem":
            markerline, stemlines, baseline = ax.stem(
                dates, vals, linefmt="-", markerfmt="o", basefmt="")
            stemlines.set_color(clr)
            stemlines.set_linewidth(1.2)
            markerline.set_color(clr)
            markerline.set_markersize(3)
            baseline.set_visible(False)
        else:  # line
            ax.plot(dates, vals, color=clr, linewidth=1.0,
                    marker="o", markersize=2.5, markerfacecolor=clr)

        # Median line
        med = float(np.median(vals))
        ax.axhline(med, color="#999", linewidth=0.7, linestyle="--")

        ax.set_title(f"Скв {cd['well_number']}  (мед. {med:.1f})", fontsize=7, pad=2)
        ax.tick_params(labelsize=6)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d"))
        ax.set_ylabel("тыс.м³/сут", fontsize=5)
        ax.grid(True, alpha=0.2, linewidth=0.3)
        ax.set_xlim(month_start, report_date)

    for idx in range(len(chart_data), nrows * ncols):
        r, c = divmod(idx, ncols)
        axes[r][c].set_visible(False)

    fig.tight_layout(pad=0.5, h_pad=0.8)

    chart_path = TEMP_DIR.resolve() / f"flowgrid{report_date.strftime('%Y%m%d')}.png"
    fig.savefig(str(chart_path), bbox_inches="tight")
    plt.close(fig)

    return str(chart_path)


# ── Purge session counting from events (same algorithm as dashboard) ────

def _classify_purge_event(desc: str, phase: str) -> str:
    """Classify purge type: скважина / штуцер / манометр."""
    txt = (desc + " " + phase).lower()
    if "штуцер" in txt or "штуц" in txt:
        return "штуцер"
    if "манометр" in txt or "маном" in txt:
        return "манометр"
    return "скважина"


def _count_purge_sessions_from_events(
    db: Session, well_number: str, day_start: datetime, day_end: datetime,
) -> dict:
    """Count purge sessions using start/press/stop grouping from events table.

    Returns dict with: total, complete, incomplete, by_type, avg_duration_min.
    """
    rows = db.execute(text("""
        SELECT event_time, description, purge_phase
        FROM events
        WHERE well = :wno AND event_type = 'purge'
          AND event_time >= :start AND event_time <= :end
        ORDER BY event_time
    """), {"wno": well_number, "start": day_start, "end": day_end}).fetchall()

    if not rows:
        return {"total": 0, "complete": 0, "incomplete": 0,
                "by_type": {}, "avg_duration_min": 0}

    MAX_GAP = timedelta(hours=4)
    sessions = []
    cur = None

    for r in rows:
        etime, desc, phase_raw = r[0], r[1] or "", (r[2] or "").lower().strip()
        ptype = _classify_purge_event(desc, phase_raw)

        if cur and etime - cur["last_time"] > MAX_GAP:
            cur["complete"] = False
            sessions.append(cur)
            cur = None

        if phase_raw == "start" or (not cur and phase_raw in ("", "press")):
            if cur:
                cur["complete"] = False
                sessions.append(cur)
            cur = {
                "type": ptype, "start_time": etime, "last_time": etime,
                "phases": [phase_raw or "?"], "complete": False,
            }
        elif phase_raw == "stop":
            if cur:
                cur["phases"].append("stop")
                cur["last_time"] = etime
                cur["duration_min"] = (etime - cur["start_time"]).total_seconds() / 60
                cur["complete"] = "start" in cur["phases"]
                sessions.append(cur)
                cur = None
            else:
                sessions.append({
                    "type": ptype, "start_time": etime, "last_time": etime,
                    "phases": ["stop"], "complete": False, "duration_min": 0,
                })
        else:
            if cur:
                cur["phases"].append(phase_raw or "?")
                cur["last_time"] = etime
            else:
                cur = {
                    "type": ptype, "start_time": etime, "last_time": etime,
                    "phases": [phase_raw or "?"], "complete": False,
                }

    if cur:
        cur["complete"] = False
        sessions.append(cur)

    for s in sessions:
        if "duration_min" not in s:
            s["duration_min"] = (s["last_time"] - s["start_time"]).total_seconds() / 60

    complete = [s for s in sessions if s["complete"]]
    incomplete = [s for s in sessions if not s["complete"]]

    by_type: dict[str, int] = {}
    for s in sessions:
        by_type[s["type"]] = by_type.get(s["type"], 0) + 1

    durations = [s["duration_min"] for s in sessions if s["duration_min"] > 0]
    avg_dur = sum(durations) / len(durations) if durations else 0

    return {
        "total": len(sessions),
        "complete": len(complete),
        "incomplete": len(incomplete),
        "by_type": by_type,
        "avg_duration_min": avg_dur,
        "sessions": sessions,
    }


# ── Summary PDF generation ───────────────────────────────

def _count_events_with_purge_sessions(wd: dict) -> tuple[int, list[dict]]:
    """Recount events: group purge start/press/stop into sessions.

    Returns (total_count, updated event_type_stats).
    """
    events = wd.get("events") or []
    raw_stats = wd.get("event_type_stats") or []

    # Count non-purge events as-is
    non_purge_count = 0
    result_stats = []
    for s in raw_stats:
        label = s.get("type_label", "")
        if "Продувка" in label:
            continue  # will recount below
        non_purge_count += s.get("count", 0)
        result_stats.append(s)

    # Count purge sessions from s_purge_total (already computed from events)
    purge_total = wd.get("s_purge_total", 0)
    purge_full = wd.get("s_purge_full", 0)
    purge_incomplete = wd.get("s_purge_incomplete", 0)

    if purge_total > 0:
        label = f"Продувка: {purge_full} полн."
        if purge_incomplete > 0:
            label += f" + {purge_incomplete} неполн."
        result_stats.insert(0, {
            "type_label": _tex_escape(label),
            "count": purge_total,
        })

    total = non_purge_count + purge_total
    return total, result_stats


def _enrich_for_summary(wd: dict) -> dict:
    """Add flattened summary fields (s_*) to well_day_data dict."""
    ps = wd.get("pressure_stats") or {}
    fs = wd.get("flow_stats") or {}

    wd["s_p_tube_median"] = ps.get("p_tube_median", "---")
    wd["s_p_line_median"] = ps.get("p_line_median", "---")
    wd["s_dp_median"] = ps.get("dp_median", "---")

    wd["s_median_flow"] = fs.get("median_flow_rate", "---")
    wd["s_avg_flow"] = fs.get("avg_flow_rate", "---")
    wd["s_month_cumulative"] = wd.get("month_cumulative", "---")

    # Working hours & utilization
    dt_min = wd.get("downtime_total_min", 0) or 0
    working_min = max(1440 - dt_min, 0)
    wd["s_working_hours"] = _fmt(working_min / 60.0, 1)
    wd["s_utilization"] = fs.get("utilization_pct", _fmt(working_min / 1440.0 * 100, 1))

    # Downtime in hours
    wd["s_downtime_hours"] = _fmt(dt_min / 60.0, 1)

    # Losses: downtime (standing) + purge atmospheric
    wd["s_downtime_loss"] = wd.get("downtime_loss_estimate", "---")
    wd["s_purge_atm_loss"] = wd.get("purge_total_atm_loss", "---")

    # Purge averages
    pc = wd.get("purge_count", 0) or 0
    if pc > 0:
        wd["s_avg_venting_min"] = _fmt(wd.get("purge_total_venting_min", 0) / pc, 1)
        wd["s_avg_buildup_min"] = _fmt(wd.get("purge_total_buildup_min", 0) / pc, 1)
    else:
        wd["s_avg_venting_min"] = "---"
        wd["s_avg_buildup_min"] = "---"

    # Reagent total count for this well
    rs = wd.get("reagent_stats") or []
    wd["s_reagent_total_count"] = sum(r.get("count", 0) for r in rs)

    # Purge defaults (may be overridden in generate_summary_report_pdf)
    wd["s_purge_full"] = wd.get("purge_count", 0) or 0
    wd["s_purge_incomplete"] = wd.get("purge_incomplete_count", 0) or 0
    wd["s_purge_total"] = wd["s_purge_full"] + wd["s_purge_incomplete"]
    wd.setdefault("s_purge_avg_dur", "---")
    wd.setdefault("s_purge_choke", 0)
    wd.setdefault("s_purge_manometer", 0)
    wd.setdefault("s_hydrate_count", 0)

    # Event summary counts for tiles
    wd["s_purge_count"] = wd["s_purge_total"]
    wd["s_reagent_inject_count"] = wd["s_reagent_total_count"]

    return wd


def _compute_summary_totals(wells_data: list[dict]) -> dict:
    """Compute totals row for summary tables."""
    total_purge = sum(w.get("purge_count", 0) or 0 for w in wells_data)
    total_dt_min = sum(w.get("downtime_total_min", 0) or 0 for w in wells_data)

    # Standing + atm losses (parse from formatted strings)
    def _parse(val):
        if not val or val == "---":
            return 0.0
        try:
            return float(str(val).replace(",", "."))
        except (ValueError, TypeError):
            return 0.0

    total_downtime_loss = sum(_parse(w.get("downtime_loss_estimate")) for w in wells_data)
    total_atm = sum(_parse(w.get("purge_total_atm_loss")) for w in wells_data)

    # Reagent totals
    total_reagent_count = sum(w.get("s_reagent_total_count", 0) for w in wells_data)

    # Event totals (use recounted s_event_count if available)
    total_events = sum(w.get("s_event_count", len(w.get("events") or [])) for w in wells_data)
    total_purge_events = sum(w.get("s_purge_total", 0) for w in wells_data)
    total_reagent_events = sum(w.get("s_reagent_inject_count", 0) for w in wells_data)

    return {
        "purge_count": total_purge,
        "downtime_loss": _fmt(total_downtime_loss, 3) if total_downtime_loss > 0 else "---",
        "atm_loss": _fmt(total_atm, 3) if total_atm > 0 else "---",
        "downtime_hours": _fmt(total_dt_min / 60.0, 1),
        "reagent_count": total_reagent_count,
        "reagent_qty": "---",  # filled below
        "events": total_events,
        "purge_events": total_purge_events,
        "reagent_events": total_reagent_events,
    }


def _compute_reagent_totals(wells_data: list[dict]) -> tuple[list[dict], str]:
    """Aggregate reagent usage across all wells by reagent type.

    Returns (reagent_totals list, total_qty_str).
    """
    by_name: dict[str, dict] = {}
    for w in wells_data:
        for r in w.get("reagent_stats") or []:
            name = r.get("name", "?")
            if name not in by_name:
                by_name[name] = {"name": name, "count": 0, "total_qty": 0.0}
            by_name[name]["count"] += r.get("count", 0)
            try:
                by_name[name]["total_qty"] += float(str(r.get("total_qty", "0")).replace(",", "."))
            except (ValueError, TypeError):
                pass

    result = []
    grand_qty = 0.0
    for name in sorted(by_name):
        entry = by_name[name]
        grand_qty += entry["total_qty"]
        result.append({
            "name": entry["name"],
            "count": entry["count"],
            "total_qty": _fmt(entry["total_qty"], 1) if entry["total_qty"] > 0 else "---",
        })

    return result, _fmt(grand_qty, 1) if grand_qty > 0 else "---"


def _compute_multiday_trend(
    db: Session, well_id: int, well_number: str, choke_mm: float | None,
    report_date: date, trend_days: int, trend_target: str,
) -> dict | None:
    """Compute trend from hourly data over N days.

    Uses hourly pressure_hourly rows → compute Q per hour → linear regression.
    Also computes % change: (Q_today - Q_yesterday) / Q_yesterday × 100.

    Returns dict with slope_per_day, direction, r_squared, delta_pct, or None.
    """
    from backend.services.flow_rate.config import DEFAULT_FLOW as cfg

    start_date = report_date - timedelta(days=trend_days - 1)

    # Get hourly data for the full period
    rows = db.execute(text("""
        SELECT hour_start,
               NULLIF(p_tube_avg, 0) AS pt,
               NULLIF(p_line_avg, 0) AS pl
        FROM pressure_hourly
        WHERE well_id = :wid
          AND hour_start >= :sd AND hour_start < :ed + INTERVAL '1 day'
          AND NULLIF(p_tube_avg, 0) IS NOT NULL
        ORDER BY hour_start
    """), {"wid": well_id, "sd": start_date, "ed": report_date}).fetchall()

    if len(rows) < 6:
        return None

    if trend_target == "flow" and choke_mm and choke_mm > 0:
        # Compute hourly Q from pressures
        choke_sq = (choke_mm / cfg.C2) ** 2
        hours = []
        values = []
        t0 = rows[0][0]
        for r in rows:
            pt, pl = r[1], r[2]
            if pt is None or pl is None or pt <= 0 or pt <= pl:
                continue
            ratio = (pt - pl) / pt
            if ratio < cfg.critical_ratio:
                q = cfg.C1 * choke_sq * pt * (1.0 - ratio / 1.5) * (ratio / cfg.C3) ** 0.5
            else:
                q = 0.667 * cfg.C1 * choke_sq * pt * (0.5 / cfg.C3) ** 0.5
            q = max(q * cfg.multiplier, 0.0)
            t_h = (r[0] - t0).total_seconds() / 3600.0
            hours.append(t_h)
            values.append(q)
    else:
        # ΔP trend from hourly data
        hours = []
        values = []
        t0 = rows[0][0]
        for r in rows:
            pt, pl = r[1], r[2]
            if pt is None or pl is None:
                continue
            dp = max(float(pt) - float(pl), 0.0)
            t_h = (r[0] - t0).total_seconds() / 3600.0
            hours.append(t_h)
            values.append(dp)

    if len(values) < 6:
        return None

    # Linear regression on hourly points
    t_arr = np.array(hours)
    v_arr = np.array(values)

    coeffs = np.polyfit(t_arr, v_arr, 1)
    slope_h = float(coeffs[0])
    slope_day = slope_h * 24.0

    predicted = np.polyval(coeffs, t_arr)
    ss_res = float(np.sum((v_arr - predicted) ** 2))
    ss_tot = float(np.sum((v_arr - np.mean(v_arr)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

    direction = "up" if slope_day > 0.001 else "down" if slope_day < -0.001 else "flat"

    # % change: avg Q today vs avg Q yesterday
    delta_pct = None
    yesterday = report_date - timedelta(days=1)
    daily = _get_daily_avg_flow(db, well_id, choke_mm, yesterday, report_date)
    daily_map = {d[0]: d[1] for d in daily}
    q_today = daily_map.get(report_date)
    q_yesterday = daily_map.get(yesterday)
    if q_today is not None and q_yesterday is not None and q_yesterday > 0.5:
        raw_pct = (q_today - q_yesterday) / q_yesterday * 100.0
        delta_pct = round(max(-99.9, min(raw_pct, 999.9)), 1)

    return {
        "slope_per_day": round(abs(slope_day), 2),
        "r_squared": round(r2, 2),
        "direction": direction,
        "delta_pct": delta_pct,
        "n_points": len(values),
    }


def generate_summary_report_pdf(doc, db: Session) -> str:
    """
    Generate summary PDF: all wells in compact tables (events, params, downtime, reagents).

    doc.meta must contain:
      - report_date (str YYYY-MM-DD)
      - well_ids (list[int])

    Returns: relative PDF path for doc.pdf_filename.
    """
    _ensure_dirs()

    meta = doc.meta or {}
    report_date = date.fromisoformat(meta["report_date"])
    well_ids = meta.get("well_ids", [])
    downtime_threshold_min = meta.get("downtime_threshold_min", 5)
    comparison_days = meta.get("comparison_days", 7)
    trend_target = meta.get("trend_target", "flow")   # 'flow' or 'dp'
    trend_days = meta.get("trend_days", 2)
    include_charts = meta.get("include_charts", True)
    chart_style = meta.get("chart_style", "line")

    wells = db.query(Well).filter(Well.id.in_(well_ids)).order_by(Well.number).all()
    if not wells:
        raise ValueError("Не найдены скважины для отчёта")

    day_start_local = datetime.combine(report_date, time(0, 0))
    day_end_local = datetime.combine(report_date, time(23, 59, 59))

    wells_data = []
    for w in wells:
        wd = build_well_day_data(
            db, w, report_date,
            downtime_threshold_min=downtime_threshold_min,
            comparison_days=comparison_days,
        )
        wd = _enrich_for_summary(wd)

        # Event-based purge sessions (start/press/stop from events table)
        try:
            ps = _count_purge_sessions_from_events(
                db, str(w.number), day_start_local, day_end_local)
            wd["s_purge_total"] = ps["total"]
            wd["s_purge_full"] = ps["complete"]
            wd["s_purge_incomplete"] = ps["incomplete"]
            wd["s_purge_by_type"] = ps["by_type"]
            wd["s_purge_avg_dur"] = _fmt(ps["avg_duration_min"], 0) if ps["avg_duration_min"] > 0 else "---"
            # Anomalies: manometer purge, hydrate
            wd["s_purge_manometer"] = ps["by_type"].get("манометр", 0)
            wd["s_purge_choke"] = ps["by_type"].get("штуцер", 0)
        except Exception:
            log.exception("Purge session count failed for well %s", w.number)

        # Recount events with purge sessions (not individual purge events)
        wd["s_event_count"], wd["s_event_type_stats"] = _count_events_with_purge_sessions(wd)

        # Anomalies by keyword search
        try:
            anomalies = _count_anomalies_from_events(
                db, str(w.number), day_start_local, day_end_local)
            wd["s_hydrate_count"] = anomalies["hydrate"]
            wd["s_purge_choke"] = anomalies["choke_purge"]
            wd["s_purge_manometer"] = anomalies["manometer_purge"]
        except Exception:
            log.exception("Anomaly count failed for well %s", w.number)

        # Compute multi-day trend
        try:
            trend = _compute_multiday_trend(
                db, w.id, str(w.number), wd.get("choke_mm_raw"),
                report_date, trend_days, trend_target)
            wd["summary_trend"] = trend
        except Exception:
            log.exception("Trend computation failed for well %s", w.number)
            wd["summary_trend"] = None

        wells_data.append(wd)

    # Sort by status (group) then by well number
    wells_data.sort(key=lambda w: (w.get("status", ""), w.get("well_number", "")))

    # Build grouped structure: list of {status, wells}
    from itertools import groupby
    wells_by_status = []
    for status, group in groupby(wells_data, key=lambda w: w.get("status", "---")):
        wells_by_status.append({"status": status, "wells": list(group)})

    totals = _compute_summary_totals(wells_data)
    reagent_totals, reagent_grand_qty = _compute_reagent_totals(wells_data)
    totals["reagent_qty"] = reagent_grand_qty

    # Full daily extract: all events for all selected wells
    well_numbers = [str(w.number) for w in wells]
    all_events_rows = db.execute(text("""
        SELECT well, event_time, event_type, description,
               p_tube, p_line, reagent, qty, purge_phase
        FROM events
        WHERE well = ANY(:wnos)
          AND event_time >= :start AND event_time <= :end
        ORDER BY event_time
    """), {"wnos": well_numbers, "start": day_start_local, "end": day_end_local}).fetchall()

    daily_extract = []
    for r in all_events_rows:
        daily_extract.append({
            "well": _tex_escape(str(r[0])),
            "time_str": r[1].strftime("%H:%M") if r[1] else "",
            "event_type": _tex_escape(_smart_event_label(r[2] or "other", r[3])),
            "description": _tex_escape(str(r[3])[:80]) if r[3] else "---",
            "p_tube": _fmt(r[4]) if r[4] and float(r[4]) > 0 else "",
            "p_line": _fmt(r[5]) if r[5] and float(r[5]) > 0 else "",
            "reagent": _tex_escape(str(r[6])) if r[6] else "",
            "qty": _fmt(float(r[7]), 1) if r[7] else "",
            "phase": _tex_escape(str(r[8])) if r[8] else "",
        })

    # Mini flow charts (optional)
    flow_grid_path = None
    if include_charts:
        try:
            flow_grid_path = _render_monthly_flow_grid(db, wells, report_date, chart_style)
        except Exception:
            log.exception("Flow grid chart rendering failed")

    now_kungrad = datetime.utcnow() + KUNGRAD_OFFSET

    trend_label = "Q" if trend_target == "flow" else "$\\Delta$P"

    context = {
        "doc_number": _tex_escape(doc.doc_number or f"ID{doc.id}"),
        "report_date": report_date.strftime("%d.%m.%Y"),
        "generated_at": now_kungrad.strftime("%d.%m.%Y %H:%M"),
        "wells_data": wells_data,
        "wells_by_status": wells_by_status,
        "total_wells": len(wells_data),
        "totals": totals,
        "reagent_totals": reagent_totals,
        "trend_target": trend_target,
        "trend_days": trend_days,
        "trend_label": trend_label,
        "daily_extract": daily_extract,
        "flow_grid_path": flow_grid_path,
    }

    env = _get_latex_env()
    template = env.get_template("daily_report_summary.tex")
    latex_source = template.render(**context)

    base_name = f"daily_summary_{doc.id}"
    pdf_path = _compile_latex(latex_source, base_name)

    # Cleanup chart PNG
    if flow_grid_path:
        p = Path(flow_grid_path)
        if p.exists():
            p.unlink()

    log.info("Summary report PDF generated: %s", pdf_path)
    return f"generated/pdf/{base_name}.pdf"


# ── Monthly report ───────────────────────────────────────

def _build_status_segments(
    db: Session, well_id: int, period_start: date, period_end: date,
) -> list[dict]:
    """Build list of status segments for a well within a period.

    Each segment: {status, date_from, date_to}.
    Day of status change belongs to the NEW status.
    Returns at least one segment. Wells without status records get 'Не задан'.
    """
    rows = db.execute(text("""
        SELECT status, dt_start::date, dt_end::date
        FROM well_status
        WHERE well_id = :wid
          AND (dt_end IS NULL OR dt_end::date >= :ps)
          AND dt_start::date <= :pe
        ORDER BY dt_start
    """), {"wid": well_id, "ps": period_start, "pe": period_end}).fetchall()

    if not rows:
        return [{"status": "Не задан", "date_from": period_start, "date_to": period_end}]

    segments = []
    for r in rows:
        status = r[0]
        seg_start = max(r[1], period_start)  # day of change = new status
        seg_end = min(r[2] - timedelta(days=1), period_end) if r[2] else period_end
        # Day of change belongs to new status, so previous ends day before
        if seg_start > period_end or seg_end < period_start:
            continue
        if seg_end < seg_start:
            continue
        segments.append({
            "status": status,
            "date_from": seg_start,
            "date_to": seg_end,
        })

    # Merge adjacent segments with same status
    merged = []
    for seg in segments:
        if merged and merged[-1]["status"] == seg["status"] and \
           seg["date_from"] <= merged[-1]["date_to"] + timedelta(days=1):
            merged[-1]["date_to"] = max(merged[-1]["date_to"], seg["date_to"])
        else:
            merged.append(dict(seg))

    return merged if merged else [{"status": "Не задан", "date_from": period_start, "date_to": period_end}]


def _aggregate_monthly_well(
    db: Session, well, month_start: date, month_end: date,
    choke_mm: float | None, downtime_threshold_min: int,
    loss_window_hours: int = 24,
    status_override: str | None = None,
    segment_start: date | None = None,
    segment_end: date | None = None,
) -> dict:
    """Aggregate one well's data over a period (full month or status segment).

    If segment_start/segment_end provided, uses those dates instead of month.
    status_override: force status label (for segment mode).
    Returns dict compatible with summary template (s_* fields).
    """
    well_number = str(well.number)
    # Use segment dates if provided
    eff_start = segment_start or month_start
    eff_end = segment_end or month_end
    num_days = (eff_end - eff_start).days + 1
    local_start = datetime.combine(eff_start, time(0, 0))
    local_end = datetime.combine(eff_end, time(23, 59, 59))
    # (local_end already set above from eff_end)

    # Status
    row = db.execute(text("""
        SELECT status FROM well_status
        WHERE well_id = :wid ORDER BY dt_start DESC LIMIT 1
    """), {"wid": well.id}).fetchone()
    status = row[0] if row else ""

    # Substatus (active at end of segment)
    sub_row = db.execute(text("""
        SELECT sub_status FROM well_sub_status
        WHERE well_id = :wid AND dt_end IS NULL
        ORDER BY dt_start DESC LIMIT 1
    """), {"wid": well.id}).fetchone()
    substatus = sub_row[0] if sub_row else ""

    # Horizon
    row = db.execute(text("""
        SELECT horizon FROM well_construction
        WHERE well_no = :wno
        ORDER BY data_as_of DESC NULLS LAST LIMIT 1
    """), {"wno": well_number}).fetchone()
    horizon = _tex_escape(str(row[0]).replace("\n", ", ")) if row and row[0] else "---"

    # Pressure stats (monthly medians from hourly data)
    pr = db.execute(text("""
        SELECT
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY NULLIF(p_tube_avg, 0)),
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY NULLIF(p_line_avg, 0)),
            PERCENTILE_CONT(0.5) WITHIN GROUP (
                ORDER BY NULLIF(p_tube_avg, 0) - NULLIF(p_line_avg, 0)
            )
        FROM pressure_hourly
        WHERE well_id = :wid
          AND hour_start >= :sd AND hour_start < :ed + INTERVAL '1 day'
          AND NULLIF(p_tube_avg, 0) IS NOT NULL
    """), {"wid": well.id, "sd": eff_start, "ed": eff_end}).fetchone()

    s_p_tube_median = _fmt(float(pr[0])) if pr and pr[0] else "---"
    s_p_line_median = _fmt(float(pr[1])) if pr and pr[1] else "---"
    s_dp_median = _fmt(float(pr[2])) if pr and pr[2] else "---"

    # Flow stats from daily averages
    daily_flow = _get_daily_avg_flow(db, well.id, choke_mm, eff_start, eff_end)
    q_values = [d[1] for d in daily_flow if d[1] > 0]

    s_median_flow = _fmt(float(np.median(q_values))) if q_values else "---"
    s_avg_flow = _fmt(float(np.mean(q_values))) if q_values else "---"
    working_days = len(q_values)

    # Cumulative flow for the month
    cumulative = sum(q_values) if q_values else 0
    s_month_cumulative = _fmt(cumulative) if cumulative > 0 else "---"

    # Working hours / downtime based on actual flow (ΔP > 0.1 = working)
    util_row = db.execute(text("""
        SELECT
            COUNT(*) AS hours_with_data,
            COUNT(*) FILTER (
                WHERE NULLIF(p_tube_avg, 0) IS NOT NULL
                  AND NULLIF(p_line_avg, 0) IS NOT NULL
                  AND (NULLIF(p_tube_avg, 0) - NULLIF(p_line_avg, 0)) > 0.1
            ) AS working_hours
        FROM pressure_hourly
        WHERE well_id = :wid
          AND hour_start >= :sd AND hour_start < :ed + INTERVAL '1 day'
    """), {"wid": well.id, "sd": eff_start, "ed": eff_end}).fetchone()
    hours_with_data = int(util_row[0]) if util_row else 0
    working_hours = int(util_row[1]) if util_row else 0
    downtime_hours = max(hours_with_data - working_hours, 0)
    utilization = (working_hours / hours_with_data * 100) if hours_with_data > 0 else 0

    # Losses: use working-hours-only median from end of period
    loss_q = _compute_loss_base_q(
        db, well.id, choke_mm,
        datetime.combine(eff_end, time(23, 59, 59)),
        window_hours=loss_window_hours,
    )
    if loss_q is None:
        # Fallback to monthly median
        loss_q = float(np.median(q_values)) if q_values else 0
    downtime_loss = loss_q * (downtime_hours / 24.0) if loss_q > 0 else 0

    # Purge sessions from events (whole month)
    ps = _count_purge_sessions_from_events(db, well_number, local_start, local_end)

    # Purge atmospheric loss estimate:
    # For each complete purge session, estimate gas vented to atmosphere
    # Q_atm = Cd × A × sqrt(2×ΔP/ρ) × venting_time
    from backend.services.flow_rate.config import DEFAULT_PURGE as _pcfg
    purge_atm_loss = 0.0
    if ps["total"] > 0 and ps.get("sessions"):
        area = np.pi * (_pcfg.choke_diameter_m / 2.0) ** 2
        for sess in ps["sessions"]:
            if sess.get("duration_min", 0) <= 0:
                continue
            # Use avg P_tube for the well as rough estimate
            p_avg_kgs = float(pr[0]) if pr and pr[0] else 0
            if p_avg_kgs <= 0:
                continue
            p_mpa = p_avg_kgs * _pcfg.kgscm2_to_MPa
            delta_p_pa = max((p_mpa - _pcfg.atm_pressure_MPa) * 1e6, 0)
            rho = max(
                (p_mpa * 1e6) * _pcfg.molar_mass
                / (_pcfg.gas_constant * _pcfg.standard_temp_K), 1e-6)
            q_m3s = _pcfg.discharge_coeff * area * np.sqrt(2.0 * delta_p_pa / rho)
            loss = q_m3s * sess["duration_min"] * 60.0 / 1000.0  # тыс м³
            purge_atm_loss += loss

    # Events
    # Structured event categorization
    evt_rows = db.execute(text("""
        SELECT event_type, description, purge_phase FROM events
        WHERE well = :wno AND event_time >= :start AND event_time <= :end
    """), {"wno": well_number, "start": local_start, "end": local_end}).fetchall()

    # Categorize each event into structured blocks
    cat_reagent = 0
    cat_pressure = 0
    cat_equip_install = 0
    cat_equip_remove = 0
    cat_hydrate = 0
    cat_choke_purge = 0
    cat_manometer_purge = 0
    cat_lubricator = 0
    cat_other = 0
    cat_other_details: list[str] = []

    for er in evt_rows:
        et = (er[0] or "other").lower()
        desc = (er[1] or "").lower()
        phase = (er[2] or "").lower()
        combined = f"{et} {desc} {phase}"

        if et == "purge":
            continue  # counted via sessions
        elif et == "reagent":
            cat_reagent += 1
        elif et == "pressure":
            cat_pressure += 1
        elif et == "equip":
            if "снято" in desc or "демонтаж" in desc:
                cat_equip_remove += 1
            else:
                cat_equip_install += 1
        elif "гидрат" in combined or "гидрад" in combined:
            cat_hydrate += 1
        elif "штуцер" in combined and ("продув" in combined or "прочист" in combined):
            cat_choke_purge += 1
        elif "маном" in combined and ("продув" in combined or "перезагруз" in combined):
            cat_manometer_purge += 1
        elif "лубрикатор" in combined:
            cat_lubricator += 1
        else:
            cat_other += 1
            # Capture unique short descriptions for "other"
            short = (er[1] or "")[:50].strip()
            if short and short not in cat_other_details and len(cat_other_details) < 3:
                cat_other_details.append(short)

    # Build structured stats list
    event_type_stats_raw = []
    total_events_raw = 0

    # 1. Purges (from sessions)
    if ps["total"] > 0:
        plabel = f"Продувка: {ps['complete']} полн."
        if ps["incomplete"] > 0:
            plabel += f" + {ps['incomplete']} неполн."
        event_type_stats_raw.append({"type_label": _tex_escape(plabel), "count": ps["total"]})
        total_events_raw += ps["total"]

    # 2. Reagent
    if cat_reagent:
        event_type_stats_raw.append({"type_label": "Вброс реагента", "count": cat_reagent})
        total_events_raw += cat_reagent

    # 3. Pressure
    if cat_pressure:
        event_type_stats_raw.append({"type_label": "Замер давления", "count": cat_pressure})
        total_events_raw += cat_pressure

    # 4. Equipment
    equip_total = cat_equip_install + cat_equip_remove
    if equip_total:
        parts = []
        if cat_equip_install:
            parts.append(f"уст. {cat_equip_install}")
        if cat_equip_remove:
            parts.append(f"сн. {cat_equip_remove}")
        event_type_stats_raw.append({
            "type_label": _tex_escape(f"Оборудование ({', '.join(parts)})"),
            "count": equip_total,
        })
        total_events_raw += equip_total

    # 5. Anomalies
    if cat_hydrate:
        event_type_stats_raw.append({"type_label": "Гидратообразование", "count": cat_hydrate})
        total_events_raw += cat_hydrate
    if cat_choke_purge:
        event_type_stats_raw.append({"type_label": _tex_escape("Прод. штуцера"), "count": cat_choke_purge})
        total_events_raw += cat_choke_purge
    if cat_manometer_purge:
        event_type_stats_raw.append({"type_label": _tex_escape("Прод. манометра"), "count": cat_manometer_purge})
        total_events_raw += cat_manometer_purge
    if cat_lubricator:
        event_type_stats_raw.append({"type_label": "Лубрикатор", "count": cat_lubricator})
        total_events_raw += cat_lubricator

    # 6. Other
    if cat_other:
        other_label = "Прочее"
        if cat_other_details:
            other_label += ": " + "; ".join(cat_other_details[:2])
        event_type_stats_raw.append({"type_label": _tex_escape(other_label), "count": cat_other})
        total_events_raw += cat_other

    # Anomalies (for section 3 table)
    anomalies = {
        "hydrate": cat_hydrate,
        "choke_purge": cat_choke_purge,
        "manometer_purge": cat_manometer_purge,
    }

    # Reagents
    reagent_rows = db.execute(text("""
        SELECT reagent, SUM(qty), COUNT(*) FROM events
        WHERE well = :wno AND event_type = 'reagent'
          AND event_time >= :start AND event_time <= :end
          AND reagent IS NOT NULL
        GROUP BY reagent ORDER BY reagent
    """), {"wno": well_number, "start": local_start, "end": local_end}).fetchall()

    reagent_stats = []
    for rr in reagent_rows:
        reagent_stats.append({
            "name": _tex_escape(rr[0]),
            "total_qty": _fmt(float(rr[1]), 1) if rr[1] else "0",
            "count": int(rr[2]),
        })

    # Hydrate events
    hyd_count = db.execute(text("""
        SELECT COUNT(*) FROM events
        WHERE well = :wno AND event_time >= :start AND event_time <= :end
          AND (LOWER(description) LIKE '%%гидрат%%' OR LOWER(event_type) LIKE '%%гидрат%%')
    """), {"wno": well_number, "start": local_start, "end": local_end}).scalar() or 0

    return {
        "well_number": well_number,
        "well_name": _tex_escape(well.name or ""),
        "status": _tex_escape(status_override or status),
        "status_period": f"{eff_start.strftime('%d.%m')}--{eff_end.strftime('%d.%m')}",
        "substatus": _tex_escape(substatus or ""),
        "horizon": horizon,
        "choke_mm": _fmt(choke_mm),
        "choke_mm_raw": choke_mm,
        # Pressure
        "s_p_tube_median": s_p_tube_median,
        "s_p_line_median": s_p_line_median,
        "s_dp_median": s_dp_median,
        # Flow
        "s_median_flow": s_median_flow,
        "s_avg_flow": s_avg_flow,
        "s_month_cumulative": s_month_cumulative,
        "s_working_hours": _fmt(working_hours, 0),
        "s_utilization": _fmt(utilization, 1),
        "s_downtime_hours": _fmt(downtime_hours, 0),
        # Losses
        "s_downtime_loss": _fmt(downtime_loss, 1) if downtime_loss > 0 else "---",
        "s_purge_atm_loss": "---",
        # Purges
        "purge_count": ps["total"],
        "s_purge_total": ps["total"],
        "s_purge_full": ps["complete"],
        "s_purge_incomplete": ps["incomplete"],
        "s_purge_avg_dur": _fmt(ps["avg_duration_min"], 0) if ps["avg_duration_min"] > 0 else "---",
        "s_purge_choke": anomalies["choke_purge"],
        "s_purge_manometer": anomalies["manometer_purge"],
        "s_hydrate_count": anomalies["hydrate"],
        "purge_total_standing_loss": "---",
        "purge_total_atm_loss": _fmt(purge_atm_loss, 3) if purge_atm_loss > 0 else "---",
        "s_purge_atm_loss": _fmt(purge_atm_loss, 3) if purge_atm_loss > 0 else "---",
        # Events
        "s_event_count": total_events_raw,
        "s_event_type_stats": event_type_stats_raw,
        "events": [None] * total_events_raw,  # dummy for |length
        "event_type_stats": event_type_stats_raw,
        # Reagents
        "reagent_stats": reagent_stats,
        "s_reagent_total_count": sum(r["count"] for r in reagent_stats),
        "s_reagent_inject_count": sum(r["count"] for r in reagent_stats),
        "s_purge_count": ps["total"],
        # Trend / DP (filled later)
        "dp_trend": None,
        "summary_trend": None,
        "downtime_total_min": downtime_hours * 60,
    }


def generate_monthly_report_pdf(doc, db: Session) -> str:
    """Generate monthly summary PDF — same layout as daily summary but for a calendar month.

    doc.meta must contain:
      - period_year (int), period_month (int)
      - well_ids (list[int])
    """
    import calendar
    from itertools import groupby

    _ensure_dirs()

    meta = doc.meta or {}
    year = meta["period_year"]
    month = meta["period_month"]
    well_ids = meta.get("well_ids", [])
    downtime_threshold_min = meta.get("downtime_threshold_min", 5)
    trend_target = meta.get("trend_target", "flow")
    include_charts = meta.get("include_charts", True)
    chart_style = meta.get("chart_style", "line")
    loss_window_hours = meta.get("loss_window_hours", 24)
    status_filter = set(meta.get("status_filter", []))

    month_start = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    # Use period_end_day if set (for current month = up to today)
    end_day = meta.get("period_end_day", last_day)
    month_end = date(year, month, min(end_day, last_day))
    num_days = month_end.day

    # Trend over the full month
    trend_days = num_days

    # Find ALL wells with events or pressure data in the period
    if well_ids:
        wells = db.query(Well).filter(Well.id.in_(well_ids)).order_by(Well.number).all()
    else:
        # Auto-detect: wells with events OR pressure data
        active_ids_q = db.execute(text("""
            SELECT DISTINCT w.id FROM wells w
            LEFT JOIN pressure_hourly ph ON ph.well_id = w.id
                AND ph.hour_start >= :sd AND ph.hour_start < :ed + INTERVAL '1 day'
            LEFT JOIN events e ON e.well = w.number::text
                AND e.event_time >= :sd AND e.event_time < :ed + INTERVAL '1 day'
            WHERE ph.id IS NOT NULL OR e.id IS NOT NULL
        """), {"sd": month_start, "ed": month_end}).fetchall()
        active_ids = [r[0] for r in active_ids_q]
        wells = db.query(Well).filter(Well.id.in_(active_ids)).order_by(Well.number).all() if active_ids else []

    if not wells:
        raise ValueError("Не найдены скважины для отчёта")

    # Build segments: each well × each status period = one row in report
    wells_data = []
    for w in wells:
        row = db.execute(text("""
            SELECT choke_diam_mm FROM well_construction
            WHERE well_no = :wno
            ORDER BY data_as_of DESC NULLS LAST LIMIT 1
        """), {"wno": str(w.number)}).fetchone()
        choke_mm = float(row[0]) if row and row[0] else None

        segments = _build_status_segments(db, w.id, month_start, month_end)

        # Filter by status if specified
        if status_filter:
            segments = [s for s in segments if s["status"] in status_filter]
            if not segments:
                continue

        for seg in segments:
            wd = _aggregate_monthly_well(
                db, w, month_start, month_end, choke_mm, downtime_threshold_min,
                loss_window_hours=loss_window_hours,
                status_override=seg["status"],
                segment_start=seg["date_from"],
                segment_end=seg["date_to"],
            )

            # Skip segments with no data at all
            if wd["s_event_count"] == 0 and wd["s_working_hours"] == "0":
                continue

            # Trend over the segment
            seg_days = (seg["date_to"] - seg["date_from"]).days + 1
            try:
                trend = _compute_multiday_trend(
                    db, w.id, str(w.number), choke_mm,
                    seg["date_to"], max(seg_days, 2), trend_target)
                wd["summary_trend"] = trend
            except Exception:
                log.exception("Monthly trend failed for well %s seg %s", w.number, seg["status"])

            wells_data.append(wd)

    # Sort by status then well number
    wells_data.sort(key=lambda w: (w.get("status", ""), w.get("well_number", "")))
    wells_by_status = []
    for status, group in groupby(wells_data, key=lambda w: w.get("status", "---")):
        wells_by_status.append({"status": status, "wells": list(group)})

    totals = _compute_summary_totals(wells_data)
    reagent_totals, reagent_grand_qty = _compute_reagent_totals(wells_data)
    totals["reagent_qty"] = reagent_grand_qty

    # Charts
    flow_grid_path = None
    if include_charts:
        try:
            flow_grid_path = _render_monthly_flow_grid(db, wells, month_end, chart_style)
        except Exception:
            log.exception("Monthly flow grid chart failed")

    now_kungrad = datetime.utcnow() + KUNGRAD_OFFSET
    trend_label = "Q" if trend_target == "flow" else "$\\Delta$P"

    # Month name in Russian
    month_names = [
        "", "январь", "февраль", "март", "апрель", "май", "июнь",
        "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
    ]
    period_str = f"01.{month:02d}.{year}--{last_day}.{month:02d}.{year} ({month_names[month]} {year})"

    context = {
        "doc_number": _tex_escape(doc.doc_number or f"ID{doc.id}"),
        "report_date": period_str,
        "generated_at": now_kungrad.strftime("%d.%m.%Y %H:%M"),
        "wells_data": wells_data,
        "wells_by_status": wells_by_status,
        "total_wells": len(wells_data),
        "totals": totals,
        "reagent_totals": reagent_totals,
        "trend_target": trend_target,
        "trend_days": trend_days,
        "trend_label": trend_label,
        "daily_extract": [],  # no daily extract for monthly
        "flow_grid_path": flow_grid_path,
    }

    env = _get_latex_env()
    template = env.get_template("daily_report_summary.tex")
    latex_source = template.render(**context)

    base_name = f"monthly_summary_{doc.id}"
    pdf_path = _compile_latex(latex_source, base_name)

    if flow_grid_path:
        p = Path(flow_grid_path)
        if p.exists():
            p.unlink()

    log.info("Monthly report PDF generated: %s", pdf_path)
    return f"generated/pdf/{base_name}.pdf"
