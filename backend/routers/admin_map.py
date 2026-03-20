"""
Admin Map tab: well map + custom map objects + distance tool.
"""
from __future__ import annotations
import math
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from backend.db import get_db, SessionLocal
from backend.auth import get_map_user
from backend.models.users import DashboardUser
from backend.models.wells import Well
from backend.models.map_object import MapObject

log = logging.getLogger(__name__)

# --- Page router (renders HTML) ---
pages_router = APIRouter(tags=["admin-map-pages"])

# --- API router (JSON endpoints) ---
router = APIRouter(prefix="/api/map-objects", tags=["map-objects"])


# =====================================================
# PAGE: /admin/map
# =====================================================
@pages_router.get("/admin/map", response_class=HTMLResponse)
def admin_map_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: DashboardUser = Depends(get_map_user),
):
    from backend.web.templates import templates, base_context
    from backend.models.well_status import WellStatus
    from backend.config.status_registry import css_by_label, STATUS_BY_CODE
    from sqlalchemy import func

    # All wells with coordinates
    wells = (
        db.query(Well)
        .filter(Well.lat.isnot(None), Well.lon.isnot(None))
        .order_by(Well.name.asc())
        .all()
    )

    # Load current status for each well (same logic as visual_page)
    well_ids = [w.id for w in wells]
    active_statuses = {}
    if well_ids:
        sub = (
            db.query(
                WellStatus.well_id,
                func.max(WellStatus.id).label("max_id"),
            )
            .filter(WellStatus.well_id.in_(well_ids), WellStatus.dt_end.is_(None))
            .group_by(WellStatus.well_id)
            .subquery()
        )
        rows = (
            db.query(WellStatus)
            .join(sub, WellStatus.id == sub.c.max_id)
            .all()
        )
        active_statuses = {s.well_id: s.status for s in rows}

    for w in wells:
        status_label = active_statuses.get(w.id)
        w._map_status = status_label or ""
        w._map_css = css_by_label(status_label) if status_label else "status-other"
        color_info = STATUS_BY_CODE.get(w._map_css, {})
        w._map_color = color_info.get("color", "#6b7280")

    map_objects = (
        db.query(MapObject)
        .order_by(MapObject.name.asc())
        .all()
    )

    # Load LoRa sensor info per well (active installations)
    from backend.models.lora_sensor import LoRaSensor
    from backend.models.equipment import Equipment, EquipmentInstallation
    well_sensors = {}  # well_id -> [{ serial_number, csv_group, csv_channel, csv_column }]
    try:
        sensor_rows = (
            db.query(
                EquipmentInstallation.well_id,
                EquipmentInstallation.installed_at,
                LoRaSensor.serial_number,
                LoRaSensor.csv_group,
                LoRaSensor.csv_channel,
                LoRaSensor.csv_column,
            )
            .join(Equipment, Equipment.id == EquipmentInstallation.equipment_id)
            .join(LoRaSensor, LoRaSensor.serial_number == Equipment.serial_number)
            .filter(EquipmentInstallation.removed_at.is_(None))
            .all()
        )
        for row in sensor_rows:
            well_sensors.setdefault(row.well_id, []).append({
                "serial_number": row.serial_number,
                "csv_group": row.csv_group,
                "csv_channel": row.csv_channel,
                "csv_column": row.csv_column,
                "installed_at": row.installed_at.isoformat() if row.installed_at else None,
            })
    except Exception:
        log.warning("Failed to load sensor data for map", exc_info=True)

    for w in wells:
        w._sensors = well_sensors.get(w.id, [])

    ctx = base_context(request)
    ctx.update({
        "wells": wells,
        "map_objects": map_objects,
    })

    return templates.TemplateResponse("admin_map.html", ctx)


# =====================================================
# CRUD API: /api/map-objects
# =====================================================
@router.get("")
def list_map_objects():
    db = SessionLocal()
    try:
        rows = db.query(MapObject).order_by(MapObject.name.asc()).all()
        return [_serialize(r) for r in rows]
    finally:
        db.close()


@router.post("")
def create_map_object(data: dict):
    name = (data.get("name") or "").strip()
    lat = data.get("lat")
    lon = data.get("lon")
    description = (data.get("description") or "").strip()
    icon_color = data.get("icon_color", "#e74c3c")
    icon_type = data.get("icon_type", "default")
    created_by = data.get("created_by")

    if not name or lat is None or lon is None:
        raise HTTPException(400, "name, lat, lon обязательны")

    db = SessionLocal()
    try:
        obj = MapObject(
            name=name[:200],
            lat=float(lat),
            lon=float(lon),
            description=description[:2000] if description else None,
            icon_color=icon_color,
            icon_type=icon_type[:30] if icon_type else "default",
            created_by=created_by,
        )
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return _serialize(obj)
    finally:
        db.close()


@router.put("/{obj_id}")
def update_map_object(obj_id: int, data: dict):
    db = SessionLocal()
    try:
        obj = db.query(MapObject).filter(MapObject.id == obj_id).first()
        if not obj:
            raise HTTPException(404, "Объект не найден")

        if "name" in data:
            obj.name = (data["name"] or "").strip()[:200]
        if "lat" in data:
            obj.lat = float(data["lat"])
        if "lon" in data:
            obj.lon = float(data["lon"])
        if "description" in data:
            obj.description = (data["description"] or "").strip()[:2000]
        if "icon_color" in data:
            obj.icon_color = data["icon_color"]
        if "icon_type" in data:
            obj.icon_type = (data["icon_type"] or "default")[:30]

        db.commit()
        db.refresh(obj)
        return _serialize(obj)
    finally:
        db.close()


@router.delete("/{obj_id}")
def delete_map_object(obj_id: int):
    db = SessionLocal()
    try:
        obj = db.query(MapObject).filter(MapObject.id == obj_id).first()
        if not obj:
            raise HTTPException(404, "Объект не найден")
        db.delete(obj)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# =====================================================
# DISTANCE API (server-side Haversine)
# =====================================================
@router.get("/distance")
def calculate_distance(
    lat1: float = Query(...), lon1: float = Query(...),
    lat2: float = Query(...), lon2: float = Query(...),
):
    """Haversine distance in meters between two points."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return {"distance_m": round(R * c, 1)}


def _serialize(obj: MapObject) -> dict:
    return {
        "id": obj.id,
        "name": obj.name,
        "lat": obj.lat,
        "lon": obj.lon,
        "description": obj.description,
        "icon_color": obj.icon_color,
        "icon_type": obj.icon_type or "default",
        "created_by": obj.created_by,
        "created_at": obj.created_at.isoformat() if obj.created_at else None,
    }


# =====================================================
# WELL-ANTENNA DISTANCE: CRUD
# =====================================================
@router.post("/well-antenna-distance")
def upsert_well_antenna_distance(data: dict):
    """Save or update distance from well to a map object (antenna)."""
    from backend.models.well_antenna_distance import WellAntennaDistance

    well_id = data.get("well_id")
    map_object_id = data.get("map_object_id")
    distance_m = data.get("distance_m")

    if well_id is None or map_object_id is None or distance_m is None:
        raise HTTPException(400, "well_id, map_object_id, distance_m обязательны")

    db = SessionLocal()
    try:
        row = (
            db.query(WellAntennaDistance)
            .filter(
                WellAntennaDistance.well_id == int(well_id),
                WellAntennaDistance.map_object_id == int(map_object_id),
            )
            .first()
        )
        if row:
            row.distance_m = float(distance_m)
        else:
            row = WellAntennaDistance(
                well_id=int(well_id),
                map_object_id=int(map_object_id),
                distance_m=float(distance_m),
            )
            db.add(row)
        db.commit()
        db.refresh(row)
        return {
            "id": row.id,
            "well_id": row.well_id,
            "map_object_id": row.map_object_id,
            "distance_m": row.distance_m,
        }
    finally:
        db.close()


@router.get("/well-antenna-distance/{well_id}")
def get_well_antenna_distances(well_id: int):
    """Get all antenna distances for a given well."""
    from backend.models.well_antenna_distance import WellAntennaDistance

    db = SessionLocal()
    try:
        rows = (
            db.query(WellAntennaDistance, MapObject.name)
            .join(MapObject, MapObject.id == WellAntennaDistance.map_object_id)
            .filter(WellAntennaDistance.well_id == well_id)
            .all()
        )
        return [
            {
                "id": r.WellAntennaDistance.id,
                "well_id": r.WellAntennaDistance.well_id,
                "map_object_id": r.WellAntennaDistance.map_object_id,
                "object_name": r.name,
                "distance_m": r.WellAntennaDistance.distance_m,
            }
            for r in rows
        ]
    finally:
        db.close()


@router.delete("/well-antenna-distance/{well_id}/{map_object_id}")
def delete_well_antenna_distance(well_id: int, map_object_id: int):
    """Delete a specific well-antenna distance record."""
    from backend.models.well_antenna_distance import WellAntennaDistance

    db = SessionLocal()
    try:
        row = (
            db.query(WellAntennaDistance)
            .filter(
                WellAntennaDistance.well_id == well_id,
                WellAntennaDistance.map_object_id == map_object_id,
            )
            .first()
        )
        if not row:
            raise HTTPException(404, "Запись не найдена")
        db.delete(row)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# =====================================================
# WELL DIAGNOSTICS: per-sensor stability assessment
# =====================================================
@router.get("/well-diagnostics/{well_id}")
def get_well_diagnostics(
    well_id: int,
    days: int = Query(1, ge=1, le=90),
):
    """
    Per-sensor diagnostics: separate tube/line stats, sync analysis.
    Works on raw unprocessed data from SQLite.
    """
    from datetime import datetime, timedelta
    from backend.models.pressure_reading import PressureReading
    from backend.models.lora_sensor import LoRaSensor
    from backend.db_pressure import PressureSessionLocal
    from backend.services.sensor_daily_report_service import _detect_degradation
    from backend.services.pressure_filter_service import (
        flag_false_zeros, hampel_filter, instant_spike_filter,
    )
    import numpy as np
    import pandas as pd

    now = datetime.utcnow()
    since = now - timedelta(days=days)

    empty_sensor = {
        "sensor_sn": None, "total": 0, "missing": 0, "zeros": 0,
        "spikes_hampel": 0, "spikes_instant": 0, "valid": 0,
        "uptime_pct": 0, "mean": None, "std": None,
    }
    empty_sync = {
        "both_ok": 0, "only_tube": 0, "only_line": 0,
        "both_missing": 0, "sync_pct": 0,
    }

    tube_stats = dict(empty_sensor)
    line_stats = dict(empty_sensor)
    sync_stats = dict(empty_sync)
    total_points = 0

    def _sensor_stats(series):
        """Compute per-sensor quality stats on raw series."""
        _P_MAX = 100.0
        total = len(series)
        missing = int(series.isna().sum())
        non_null = series.dropna()
        if len(non_null) == 0:
            return {
                "total": total, "missing": missing, "zeros": 0,
                "out_of_range": 0, "spikes_hampel": 0, "spikes_instant": 0,
                "valid": 0, "uptime_pct": 0, "mean": None, "std": None,
            }
        # Out-of-range: < 0 or > 100 (buffer overflow)
        oor_mask = (non_null < 0) | (non_null > _P_MAX)
        out_of_range = int(oor_mask.sum())
        _, zeros = flag_false_zeros(non_null)
        # Clean series for stats (no zeros, no out-of-range)
        clean = non_null[(non_null != 0) & (non_null > 0) & (non_null <= _P_MAX)]
        if len(clean) > 0:
            _, hampel = hampel_filter(clean)
            _, instant = instant_spike_filter(clean)
            mean_val = float(np.nanmean(clean.values))
            std_val = float(np.nanstd(clean.values))
        else:
            hampel = instant = 0
            mean_val = std_val = float('nan')
        valid = total - missing - zeros - out_of_range
        return {
            "total": total,
            "missing": missing,
            "zeros": zeros,
            "out_of_range": out_of_range,
            "spikes_hampel": hampel,
            "spikes_instant": instant,
            "valid": max(valid, 0),
            "uptime_pct": round(max(valid, 0) / total * 100, 1) if total > 0 else 0,
            "mean": round(mean_val, 2) if not np.isnan(mean_val) else None,
            "std": round(std_val, 2) if not np.isnan(std_val) else None,
        }

    try:
        pdb = PressureSessionLocal()
        try:
            raw_rows = (
                pdb.query(
                    PressureReading.measured_at,
                    PressureReading.p_tube,
                    PressureReading.p_line,
                    PressureReading.sensor_id_tube,
                    PressureReading.sensor_id_line,
                )
                .filter(
                    PressureReading.well_id == well_id,
                    PressureReading.measured_at >= since,
                )
                .order_by(PressureReading.measured_at)
                .all()
            )
            total_points = len(raw_rows)

            if raw_rows:
                p_tube = pd.Series(
                    [r.p_tube for r in raw_rows], dtype="Float64"
                )
                p_line = pd.Series(
                    [r.p_line for r in raw_rows], dtype="Float64"
                )
                timestamps = pd.Series(
                    [r.measured_at for r in raw_rows]
                )

                tube_stats = _sensor_stats(p_tube)
                line_stats = _sensor_stats(p_line)

                # Degradation detection
                tube_deg = _detect_degradation(p_tube, timestamps)
                line_deg = _detect_degradation(p_line, timestamps)
                tube_stats["degradation_count"] = tube_deg["count"]
                tube_stats["degradation_total_min"] = tube_deg["total_min"]
                line_stats["degradation_count"] = line_deg["count"]
                line_stats["degradation_total_min"] = line_deg["total_min"]

                # --- Sync analysis ---
                tube_ok = p_tube.notna() & (p_tube != 0)
                line_ok = p_line.notna() & (p_line != 0)
                both_ok = int((tube_ok & line_ok).sum())
                only_tube = int((tube_ok & ~line_ok).sum())
                only_line = int((~tube_ok & line_ok).sum())
                both_miss = int((~tube_ok & ~line_ok).sum())
                sync_stats = {
                    "both_ok": both_ok,
                    "only_tube": only_tube,
                    "only_line": only_line,
                    "both_missing": both_miss,
                    "sync_pct": round(both_ok / total_points * 100, 1) if total_points > 0 else 0,
                }

                # --- Sensor SN lookup ---
                sid_tube = next((r.sensor_id_tube for r in raw_rows if r.sensor_id_tube), None)
                sid_line = next((r.sensor_id_line for r in raw_rows if r.sensor_id_line), None)
        finally:
            pdb.close()

        # Lookup serial numbers from PostgreSQL
        if total_points > 0:
            db = SessionLocal()
            try:
                if sid_tube:
                    sn = db.query(LoRaSensor.serial_number).filter(LoRaSensor.id == sid_tube).scalar()
                    tube_stats["sensor_sn"] = sn
                if sid_line:
                    sn = db.query(LoRaSensor.serial_number).filter(LoRaSensor.id == sid_line).scalar()
                    line_stats["sensor_sn"] = sn
            finally:
                db.close()

    except Exception:
        log.warning("Failed to load raw pressure for diagnostics well=%s", well_id, exc_info=True)

    return {
        "well_id": well_id,
        "days": days,
        "total_points": total_points,
        "tube": tube_stats,
        "line": line_stats,
        "sync": sync_stats,
    }


# =====================================================
# SENSOR DAILY REPORTS: query + trigger
# =====================================================
@router.get("/sensor-daily-reports")
def list_sensor_daily_reports(
    well_id: int = Query(None),
    days: int = Query(30, ge=1, le=365),
    grade: str = Query(None),
    role: str = Query(None),
):
    """
    Query stored daily sensor reports.
    Filters: well_id, days back, grade (A/B/C/F), role (tube/line).
    """
    from datetime import date, timedelta
    from backend.models.sensor_daily_report import SensorDailyReport

    since = date.today() - timedelta(days=days)

    db = SessionLocal()
    try:
        q = db.query(SensorDailyReport).filter(
            SensorDailyReport.report_date >= since,
        )
        if well_id is not None:
            q = q.filter(SensorDailyReport.well_id == well_id)
        if grade:
            q = q.filter(SensorDailyReport.quality_grade == grade.upper())
        if role:
            q = q.filter(SensorDailyReport.sensor_role == role)

        q = q.order_by(
            SensorDailyReport.report_date.desc(),
            SensorDailyReport.well_name,
            SensorDailyReport.sensor_role,
        )

        rows = q.limit(500).all()
        return [_serialize_daily_report(r) for r in rows]
    finally:
        db.close()


@router.post("/sensor-daily-reports/compute")
def trigger_daily_report(data: dict):
    """Manually trigger daily report computation for a given date."""
    from datetime import date
    from backend.services.sensor_daily_report_service import compute_daily_reports

    date_str = data.get("date")
    if date_str:
        target = date.fromisoformat(date_str)
    else:
        target = date.today() - timedelta(days=1)

    results = compute_daily_reports(target)
    return {"ok": True, "date": str(target), "count": len(results)}


def _serialize_daily_report(r) -> dict:
    import json as _json
    return {
        "id": r.id,
        "report_date": r.report_date.isoformat(),
        "well_id": r.well_id,
        "well_name": r.well_name,
        "well_lat": r.well_lat,
        "well_lon": r.well_lon,
        "sensor_role": r.sensor_role,
        "sensor_serial": r.sensor_serial,
        "expected_readings": r.expected_readings,
        "actual_readings": r.actual_readings,
        "missing_count": r.missing_count,
        "false_zero_count": r.false_zero_count,
        "out_of_range_count": r.out_of_range_count or 0,
        "valid_count": r.valid_count,
        "uptime_pct": r.uptime_pct,
        "p_mean": r.p_mean,
        "p_std": r.p_std,
        "p_min": r.p_min,
        "p_max": r.p_max,
        "p_median": r.p_median,
        "spikes_hampel": r.spikes_hampel,
        "spikes_instant": r.spikes_instant,
        "spikes_pct": r.spikes_pct,
        "sync_both_ok": r.sync_both_ok,
        "sync_only_this": r.sync_only_this,
        "sync_only_other": r.sync_only_other,
        "sync_both_miss": r.sync_both_miss,
        "sync_both_ok_pct": r.sync_both_ok_pct,
        "gap_count": r.gap_count,
        "gap_max_minutes": r.gap_max_minutes,
        "gap_total_minutes": r.gap_total_minutes,
        "degradation_count": r.degradation_count or 0,
        "degradation_total_min": r.degradation_total_min or 0,
        "degradation_max_min": r.degradation_max_min or 0,
        "quality_grade": r.quality_grade,
        "quality_flags": _json.loads(r.quality_flags) if r.quality_flags else [],
    }
