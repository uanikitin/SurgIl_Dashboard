"""
/api/widget/* — Lightweight API for desktop widgets (SwiftBar, etc.).

Returns combined well + pressure + flow rate data in a single call.
No authentication required.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import text

from backend.db import engine as pg_engine

router = APIRouter(prefix="/api/widget", tags=["widget"])

KUNGRAD_OFFSET = timedelta(hours=5)

# Flow rate formula constants (same as app.py / calculator.py)
_C1 = 2.919
_C2 = 4.654
_C3 = 286.95
_MULTIPLIER = 4.1
_CRIT_RATIO = 0.5


def _calc_flow(p_tube: float, p_line: float, choke_mm: float) -> float:
    if p_tube <= p_line or p_tube <= 0:
        return 0.0
    r = (p_tube - p_line) / p_tube
    choke_sq = (choke_mm / _C2) ** 2
    if r < _CRIT_RATIO:
        q = _C1 * choke_sq * p_tube * (1.0 - r / 1.5) * math.sqrt(max(r / _C3, 0.0))
    else:
        q = 0.667 * _C1 * choke_sq * p_tube * math.sqrt(0.5 / _C3)
    return max(q * _MULTIPLIER, 0.0)


def _r(val) -> float | None:
    """Round to 2 decimals, treat 0.0 as None (false sensor zeros)."""
    if val is None:
        return None
    v = float(val)
    if v == 0.0 or v < 0:
        return None
    return round(v, 2)


@router.get("/summary")
def widget_summary():
    """
    Combined summary for desktop widget:
    well number, status, p_tube, p_line, dp, flow_rate, measured_at.
    """
    now_kungrad = datetime.utcnow() + KUNGRAD_OFFSET
    today_start_utc = (
        now_kungrad.replace(hour=0, minute=0, second=0, microsecond=0)
        - KUNGRAD_OFFSET
    )

    with pg_engine.connect() as conn:
        # 1) Wells + current pressure + latest status from well_status
        rows = conn.execute(text("""
            SELECT w.id, w.number, w.name,
                   COALESCE(ws.status, w.current_status, '') AS status,
                   pl.p_tube, pl.p_line, pl.measured_at
            FROM wells w
            LEFT JOIN pressure_latest pl ON pl.well_id = w.id
            LEFT JOIN LATERAL (
                SELECT status FROM well_status
                WHERE well_id = w.id
                ORDER BY dt_start DESC
                LIMIT 1
            ) ws ON true
            ORDER BY w.number
        """)).fetchall()

        # 2) Choke diameters (latest per well)
        choke_rows = conn.execute(text("""
            SELECT DISTINCT ON (w.id)
                w.id AS well_id,
                wc.choke_diam_mm
            FROM wells w
            JOIN well_construction wc ON w.number::text = wc.well_no
            WHERE wc.choke_diam_mm IS NOT NULL
            ORDER BY w.id, wc.data_as_of DESC NULLS LAST
        """)).fetchall()

        # 3) Median pressures for today (for flow rate)
        well_ids = [r[0] for r in rows]
        if well_ids:
            well_id_csv = ",".join(str(int(w)) for w in well_ids)
            pressure_rows = conn.execute(
                text(f"""
                    SELECT
                        well_id,
                        PERCENTILE_CONT(0.5) WITHIN GROUP (
                            ORDER BY NULLIF(p_tube_avg, 0.0)
                        ) AS p_tube,
                        PERCENTILE_CONT(0.5) WITHIN GROUP (
                            ORDER BY NULLIF(p_line_avg, 0.0)
                        ) AS p_line
                    FROM pressure_hourly
                    WHERE well_id IN ({well_id_csv})
                      AND hour_start >= :today
                    GROUP BY well_id
                """),
                {"today": today_start_utc},
            ).fetchall()
        else:
            pressure_rows = []

    choke_map = {r[0]: float(r[1]) for r in choke_rows}
    flow_map: dict[int, float | None] = {}
    for pr in pressure_rows:
        wid, pt, pl = pr[0], pr[1], pr[2]
        choke = choke_map.get(wid)
        if pt is not None and pl is not None and choke is not None:
            q = round(_calc_flow(float(pt), float(pl), choke), 1)
            flow_map[wid] = q if q > 0 else None

    # Build response
    statuses: set[str] = set()
    wells = []
    for r in rows:
        wid = r[0]
        p_tube = _r(r[4])
        p_line = _r(r[5])
        dp = round(p_tube - p_line, 2) if p_tube is not None and p_line is not None else None
        status = r[3] or ""
        if status:
            statuses.add(status)

        measured_at = r[6]
        if measured_at:
            measured_local = measured_at + KUNGRAD_OFFSET
            measured_str = measured_local.strftime("%H:%M")
        else:
            measured_str = None

        wells.append({
            "id": wid,
            "number": r[1],
            "name": r[2],
            "status": status,
            "p_tube": p_tube,
            "p_line": p_line,
            "dp": dp,
            "flow_rate": flow_map.get(wid),
            "measured_at": measured_str,
        })

    return {
        "wells": wells,
        "statuses": sorted(statuses),
        "updated_at": now_kungrad.strftime("%H:%M:%S"),
    }
