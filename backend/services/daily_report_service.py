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


# ── Linear trend (copied from flow_rate router) ─────────

def _linear_trend(values: list, duration_hours: float) -> dict | None:
    """Linear regression Y(t) = a + b*t. Returns slope_per_day, direction, r_squared."""
    valid = [(i, v) for i, v in enumerate(values) if v is not None and not np.isnan(v)]
    if len(valid) < 10:
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

def _render_pressure_chart(df: pd.DataFrame, well_number: str, report_date: date) -> str | None:
    """Render intraday pressure chart (p_tube + p_line) → PNG. Returns absolute path."""
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


# ── Data aggregation ─────────────────────────────────────

def _kungrad_day_utc_range(report_date: date):
    """Return (day_start_utc, day_end_utc) for a Kungrad calendar day."""
    day_start_utc = datetime.combine(report_date - timedelta(days=1), time(19, 0))
    day_end_utc = datetime.combine(report_date, time(19, 0))
    return day_start_utc, day_end_utc


def build_well_day_data(db: Session, well: Well, report_date: date) -> dict:
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
    purge_cycles_data = []
    purge_total_venting_min = 0
    purge_total_buildup_min = 0
    downtime_periods_data = []
    downtime_total_min = 0
    chart_path = None
    flow_stats = None
    data_start_time = "---"
    data_end_time = "---"
    data_points_count = 0

    if not df_raw.empty:
        df = clean_pressure(df_raw)

        # Filter out false zeros for statistics
        pt = df["p_tube"].where(df["p_tube"] > 0)
        pl = df["p_line"].where(df["p_line"] > 0)
        dp = (pt - pl).dropna()

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
                "p_line_min": _fmt(float(pl.min())) if pl.dropna().shape[0] > 0 else "---",
                "p_line_max": _fmt(float(pl.max())) if pl.dropna().shape[0] > 0 else "---",
                "p_line_avg": _fmt(float(pl.mean())) if pl.dropna().shape[0] > 0 else "---",
                "p_line_median": _fmt(float(pl.median())) if pl.dropna().shape[0] > 0 else "---",
                "dp_min": _fmt(float(dp.min())) if len(dp) > 0 else "---",
                "dp_max": _fmt(float(dp.max())) if len(dp) > 0 else "---",
                "dp_avg": _fmt(float(dp.mean())) if len(dp) > 0 else "---",
                "dp_median": _fmt(float(dp.median())) if len(dp) > 0 else "---",
            }

        # ΔP trend analysis
        if len(dp) >= 10:
            duration_hours = (df.index.max() - df.index.min()).total_seconds() / 3600.0
            trend = _linear_trend(dp.tolist(), duration_hours)
            if trend and trend["r_squared"] > 0.1:
                dp_trend = trend

        # ── 3. Purge detection ──
        try:
            events_df = get_purge_events(well.id, utc_start_str, utc_end_str)
            detector = PurgeDetector()
            purge_cycles = detector.detect(df, events_df)
            active_cycles = [c for c in purge_cycles if not c.excluded]
            purge_count = len(active_cycles)

            for c in active_cycles:
                start_t = c.venting_start
                if start_t is not None:
                    start_local = start_t + KUNGRAD_OFFSET
                    purge_cycles_data.append({
                        "start_time": start_local.strftime("%H:%M"),
                        "venting_min": _fmt(c.venting_duration_min, 0),
                        "buildup_min": _fmt(c.buildup_duration_min, 0),
                        "p_start": _fmt(c.p_start),
                        "p_bottom": _fmt(c.p_bottom),
                        "p_end": _fmt(c.p_end),
                    })

            purge_total_venting_min = round(sum(c.venting_duration_min for c in active_cycles))
            purge_total_buildup_min = round(sum(c.buildup_duration_min for c in active_cycles))
        except Exception:
            log.exception("Purge detection failed for well %s", well_number)
            purge_cycles = []

        # ── 4. Downtime detection ──
        try:
            dt_periods = detect_downtime_periods(df)
            if not dt_periods.empty:
                for _, row_dt in dt_periods.iterrows():
                    start_local = row_dt["start"] + KUNGRAD_OFFSET
                    end_local = row_dt["end"] + KUNGRAD_OFFSET
                    downtime_periods_data.append({
                        "start_time": start_local.strftime("%H:%M"),
                        "end_time": end_local.strftime("%H:%M"),
                        "duration_min": _fmt(row_dt["duration_min"], 0),
                    })
                downtime_total_min = round(float(dt_periods["duration_min"].sum()))
        except Exception:
            log.exception("Downtime detection failed for well %s", well_number)
            dt_periods = pd.DataFrame()

        # ── 5. Flow rate ──
        # Try flow_result first (pre-computed baseline)
        fr = db.execute(text("""
            SELECT
                fr.avg_flow_rate, fr.median_flow_rate,
                fr.min_flow_rate, fr.max_flow_rate,
                fr.cumulative_flow, fr.purge_loss,
                fr.downtime_minutes
            FROM flow_result fr
            JOIN flow_scenario fs ON fs.id = fr.scenario_id
            WHERE fs.well_id = :wid
              AND fs.is_baseline = TRUE
              AND fr.result_date = :rd
            LIMIT 1
        """), {"wid": well.id, "rd": report_date}).fetchone()

        if fr:
            # Use pre-computed flow stats + compute КИВ
            total_min = 1440  # 24h
            dt_min = float(fr[6]) if fr[6] else 0
            utilization = ((total_min - dt_min) / total_min * 100.0) if total_min > 0 else 0
            eff_flow = float(fr[0]) if fr[0] else 0  # avg = effective for daily

            flow_stats = {
                "avg_flow_rate": _fmt(fr[0]),
                "median_flow_rate": _fmt(fr[1]),
                "min_flow_rate": _fmt(fr[2]) if fr[2] is not None else "---",
                "max_flow_rate": _fmt(fr[3]) if fr[3] is not None else "---",
                "effective_flow_rate": _fmt(eff_flow),
                "utilization_pct": _fmt(utilization, 1),
                "cumulative_flow": _fmt(fr[4]),
                "purge_loss": _fmt(fr[5]),
                "downtime_minutes": _fmt(dt_min, 0),
            }
        elif choke_mm and not df.empty:
            # Calculate flow rate from raw pressure data
            try:
                df_flow = calculate_flow_rate(df.copy(), choke_mm)
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
                    flow_stats = {
                        "avg_flow_rate": _fmt(summary.get("mean_flow_rate")),
                        "median_flow_rate": _fmt(summary.get("median_flow_rate")),
                        "min_flow_rate": _fmt(float(df_flow["flow_rate"].min())),
                        "max_flow_rate": _fmt(float(df_flow["flow_rate"].max())),
                        "effective_flow_rate": _fmt(summary.get("effective_flow_rate")),
                        "utilization_pct": _fmt(summary.get("utilization_pct"), 1),
                        "cumulative_flow": _fmt(summary.get("cumulative_flow")),
                        "purge_loss": _fmt(summary.get("purge_loss_total")),
                        "downtime_minutes": _fmt(summary.get("downtime_minutes"), 0),
                    }
            except Exception:
                log.exception("Flow calculation failed for well %s", well_number)

        # ── 6. Pressure chart ──
        try:
            chart_path = _render_pressure_chart(df, well_number, report_date)
        except Exception:
            log.exception("Chart rendering failed for well %s", well_number)

    # ── 7. Events ──
    event_rows = db.execute(text("""
        SELECT event_time, event_type, description, p_tube, p_line
        FROM events
        WHERE well = :wno
          AND event_time >= :start AND event_time <= :end
        ORDER BY event_time
    """), {"wno": well_number, "start": day_start_local, "end": day_end_local}).fetchall()

    events = []
    event_type_counts: dict[str, int] = {}
    for e in event_rows:
        et = e[1] or "other"
        label = EVENT_TYPE_LABELS.get(et, et)
        event_type_counts[label] = event_type_counts.get(label, 0) + 1
        events.append({
            "time_str": e[0].strftime("%H:%M") if e[0] else "",
            "type_label": _tex_escape(label),
            "description": _tex_escape(e[2] or ""),
            "p_tube": _fmt(e[3]) if e[3] and float(e[3]) > 0 else "---",
            "p_line": _fmt(e[4]) if e[4] and float(e[4]) > 0 else "---",
        })

    event_type_stats = [
        {"type_label": _tex_escape(k), "count": v}
        for k, v in sorted(event_type_counts.items(), key=lambda x: -x[1])
    ]

    # ── 8. Reagent stats + intervals + prev day comparison ──
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

    # Previous day comparison
    prev_date = report_date - timedelta(days=1)
    prev_start = datetime.combine(prev_date, time(0, 0))
    prev_end = datetime.combine(prev_date, time(23, 59, 59))

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
        # Pressure
        "pressure_stats": pressure_stats,
        "dp_trend": dp_trend,
        # Purges
        "purge_count": purge_count,
        "purge_cycles": purge_cycles_data,
        "purge_total_venting_min": purge_total_venting_min,
        "purge_total_buildup_min": purge_total_buildup_min,
        # Downtime
        "downtime_periods": downtime_periods_data,
        "downtime_total_min": downtime_total_min,
        # Flow
        "flow_stats": flow_stats,
        # Chart
        "chart_path": chart_path,
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

    if doc.well_id and not well_ids:
        well_ids = [doc.well_id]

    # Load wells
    wells = db.query(Well).filter(Well.id.in_(well_ids)).order_by(Well.number).all()
    if not wells:
        raise ValueError("Не найдены скважины для отчёта")

    # Build data for each well
    wells_data = []
    for w in wells:
        wd = build_well_day_data(db, w, report_date)
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
        cp = w.get("chart_path")
        if cp:
            p = Path(cp)
            if p.exists():
                p.unlink()

    log.info("Daily report PDF generated: %s", pdf_path)
    return f"generated/pdf/{base_name}.pdf"
