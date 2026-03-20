"""
sensor_daily_report_service — расчёт ежесуточной сводки по каждому датчику.

Вызывается ежедневным джобом (01:00 ночи) — считает данные за вчера.
Может быть вызван вручную для любой даты.
Идемпотентен: при повторном запуске обновляет (upsert) существующие записи.
"""
import json
import logging
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from backend.db import SessionLocal
from backend.db_pressure import PressureSessionLocal
from backend.models.pressure_reading import PressureReading
from backend.models.sensor_daily_report import SensorDailyReport
from backend.models.lora_sensor import LoRaSensor
from backend.models.wells import Well
from backend.models.equipment import Equipment, EquipmentInstallation
from backend.services.pressure_filter_service import (
    flag_false_zeros, hampel_filter, instant_spike_filter,
)

log = logging.getLogger(__name__)


def compute_daily_reports(target_date: date) -> list[dict]:
    """
    Compute and store daily sensor reports for all wells for a given date.

    Args:
        target_date: дата для расчёта (UTC сутки)

    Returns:
        list of dicts with summary for each well/sensor
    """
    dt_start = datetime(target_date.year, target_date.month, target_date.day)
    dt_end = dt_start + timedelta(days=1)

    results = []

    # 1. Get all wells with sensors
    pg_db = SessionLocal()
    try:
        wells = pg_db.query(Well).filter(Well.lat.isnot(None), Well.lon.isnot(None)).all()
        well_map = {w.id: w for w in wells}

        # Get sensor assignments: well_id -> {tube: {sensor_id, serial}, line: {...}}
        sensor_assignments = _get_sensor_assignments(pg_db)
    finally:
        pg_db.close()

    # 2. Load raw data from SQLite for all wells at once
    pdb = PressureSessionLocal()
    try:
        raw_rows = (
            pdb.query(
                PressureReading.well_id,
                PressureReading.measured_at,
                PressureReading.p_tube,
                PressureReading.p_line,
                PressureReading.sensor_id_tube,
                PressureReading.sensor_id_line,
            )
            .filter(
                PressureReading.measured_at >= dt_start,
                PressureReading.measured_at < dt_end,
            )
            .order_by(PressureReading.well_id, PressureReading.measured_at)
            .all()
        )
    finally:
        pdb.close()

    # Group by well_id
    from collections import defaultdict
    by_well = defaultdict(list)
    for r in raw_rows:
        by_well[r.well_id].append(r)

    # 3. Process each well
    for well_id, rows in by_well.items():
        well = well_map.get(well_id)
        if not well:
            continue

        p_tube = pd.Series([r.p_tube for r in rows], dtype="Float64")
        p_line = pd.Series([r.p_line for r in rows], dtype="Float64")
        timestamps = pd.Series([r.measured_at for r in rows])

        total = len(rows)

        # Sync analysis (before filtering)
        tube_ok = p_tube.notna() & (p_tube != 0)
        line_ok = p_line.notna() & (p_line != 0)
        sync_both_ok = int((tube_ok & line_ok).sum())
        sync_only_tube = int((tube_ok & ~line_ok).sum())
        sync_only_line = int((~tube_ok & line_ok).sum())
        sync_both_miss = int((~tube_ok & ~line_ok).sum())
        sync_both_ok_pct = round(sync_both_ok / total * 100, 1) if total > 0 else 0

        # Sensor IDs
        sid_tube = next((r.sensor_id_tube for r in rows if r.sensor_id_tube), None)
        sid_line = next((r.sensor_id_line for r in rows if r.sensor_id_line), None)

        # Sensor serials from assignments or direct lookup
        assignments = sensor_assignments.get(well_id, {})

        # Per-sensor stats
        tube_stats = _sensor_stats(p_tube, timestamps, total)
        line_stats = _sensor_stats(p_line, timestamps, total)

        # Per-sensor degradation detection
        tube_deg = _detect_degradation(p_tube, timestamps)
        line_deg = _detect_degradation(p_line, timestamps)

        # Build reports for tube and line
        for role, stats, sid, deg in [
            ("tube", tube_stats, sid_tube, tube_deg),
            ("line", line_stats, sid_line, line_deg),
        ]:
            assign = assignments.get(role, {})
            sensor_sn = assign.get("serial") or _lookup_sensor_sn(sid)

            # Sync fields: perspective of this sensor
            if role == "tube":
                s_only_this = sync_only_tube
                s_only_other = sync_only_line
            else:
                s_only_this = sync_only_line
                s_only_other = sync_only_tube

            # Quality grade (pass degradation info for flag)
            grade, flags = _compute_grade(stats, sync_both_ok_pct, deg)

            report = {
                "report_date": target_date,
                "well_id": well_id,
                "sensor_role": role,
                "lora_sensor_id": assign.get("sensor_id") or sid,
                "sensor_serial": sensor_sn,
                "well_name": well.name,
                "well_lat": well.lat,
                "well_lon": well.lon,
                "expected_readings": 1440,
                "actual_readings": total,
                "missing_count": stats["missing"],
                "false_zero_count": stats["zeros"],
                "out_of_range_count": stats["out_of_range"],
                "valid_count": stats["valid"],
                "uptime_pct": stats["uptime_pct"],
                "p_mean": stats["mean"],
                "p_std": stats["std"],
                "p_min": stats["p_min"],
                "p_max": stats["p_max"],
                "p_median": stats["median"],
                "spikes_hampel": stats["spikes_hampel"],
                "spikes_instant": stats["spikes_instant"],
                "spikes_pct": stats["spikes_pct"],
                "sync_both_ok": sync_both_ok,
                "sync_only_this": s_only_this,
                "sync_only_other": s_only_other,
                "sync_both_miss": sync_both_miss,
                "sync_both_ok_pct": sync_both_ok_pct,
                "gap_count": stats["gap_count"],
                "gap_max_minutes": stats["gap_max_minutes"],
                "gap_total_minutes": stats["gap_total_minutes"],
                "degradation_count": deg["count"],
                "degradation_total_min": deg["total_min"],
                "degradation_max_min": deg["max_min"],
                "quality_grade": grade,
                "quality_flags": json.dumps(flags, ensure_ascii=False) if flags else None,
            }
            results.append(report)

    # 4. Upsert into PostgreSQL
    _upsert_reports(results)

    log.info("Daily sensor report for %s: %d records", target_date, len(results))
    return results


def _sensor_stats(series: pd.Series, timestamps: pd.Series, total: int) -> dict:
    """
    Per-sensor quality statistics on raw series.

    Классификация значений в pressure_readings:
      NULL  → датчик не передал данные (CSV: -1.0 или -2.0, отфильтровано при импорте)
      0.0   → ложный ноль (нулевого давления не бывает)
      < 0   → переполнение буфера прошивки (напр. -384.2)
      > 100 → переполнение буфера прошивки (напр. 191.6)
      1-100 → нормальный диапазон
    """
    _P_MAX = 100.0  # физический предел давления

    missing = int(series.isna().sum())
    non_null = series.dropna()

    if len(non_null) == 0:
        return {
            "missing": missing, "zeros": 0, "out_of_range": 0,
            "spikes_hampel": 0, "spikes_instant": 0,
            "valid": 0, "uptime_pct": 0,
            "mean": None, "std": None, "p_min": None, "p_max": None, "median": None,
            "spikes_pct": 0,
            "gap_count": 0, "gap_max_minutes": 0, "gap_total_minutes": 0,
        }

    # 1. Out-of-range: < 0 or > 100 (buffer overflow artifacts)
    oor_mask = (non_null < 0) | (non_null > _P_MAX)
    out_of_range = int(oor_mask.sum())

    # 2. False zeros: == 0.0 (sensor glitch, no real zero pressure)
    _, zeros = flag_false_zeros(non_null)

    # 3. Clean series for spike detection (remove zeros and out-of-range)
    clean = non_null[(non_null != 0) & (non_null > 0) & (non_null <= _P_MAX)]
    if len(clean) > 0:
        _, hampel = hampel_filter(clean)
        _, instant = instant_spike_filter(clean)
    else:
        hampel = instant = 0

    valid = total - missing - zeros - out_of_range
    valid = max(valid, 0)
    uptime_pct = round(valid / total * 100, 1) if total > 0 else 0

    # Stats on clean values only
    if len(clean) > 0:
        mean_val = round(float(np.nanmean(clean.values)), 2)
        std_val = round(float(np.nanstd(clean.values)), 2)
        p_min = round(float(np.nanmin(clean.values)), 2)
        p_max = round(float(np.nanmax(clean.values)), 2)
        median_val = round(float(np.nanmedian(clean.values)), 2)
    else:
        mean_val = std_val = p_min = p_max = median_val = None

    spikes_total = hampel + instant
    spikes_pct = round(spikes_total / total * 100, 1) if total > 0 else 0

    # Gap analysis
    gap_count, gap_max_minutes, gap_total_minutes = _analyze_gaps(series, timestamps)

    return {
        "missing": missing,
        "zeros": zeros,
        "out_of_range": out_of_range,
        "spikes_hampel": hampel,
        "spikes_instant": instant,
        "valid": valid,
        "uptime_pct": uptime_pct,
        "mean": mean_val,
        "std": std_val,
        "p_min": p_min,
        "p_max": p_max,
        "median": median_val,
        "spikes_pct": spikes_pct,
        "gap_count": gap_count,
        "gap_max_minutes": gap_max_minutes,
        "gap_total_minutes": gap_total_minutes,
    }


def _analyze_gaps(series: pd.Series, timestamps: pd.Series, min_gap_min: int = 5) -> tuple:
    """
    Analyze gaps (periods of missing/zero data) in the series.

    Returns: (gap_count, gap_max_minutes, gap_total_minutes)
    """
    if len(series) < 2:
        return (0, 0, 0)

    # Mark bad points: NaN, 0, negative, or > 100 (out of range)
    bad = series.isna() | (series == 0) | (series < 0) | (series > 100)

    gap_count = 0
    gap_max = 0
    gap_total = 0
    current_gap_start = None

    for i in range(len(bad)):
        if bad.iloc[i]:
            if current_gap_start is None:
                current_gap_start = i
        else:
            if current_gap_start is not None:
                # Gap ended
                ts_start = timestamps.iloc[current_gap_start]
                ts_end = timestamps.iloc[i]
                gap_minutes = int((ts_end - ts_start).total_seconds() / 60)
                if gap_minutes >= min_gap_min:
                    gap_count += 1
                    gap_total += gap_minutes
                    gap_max = max(gap_max, gap_minutes)
                current_gap_start = None

    # Handle gap at end of series
    if current_gap_start is not None and current_gap_start < len(timestamps) - 1:
        ts_start = timestamps.iloc[current_gap_start]
        ts_end = timestamps.iloc[len(timestamps) - 1]
        gap_minutes = int((ts_end - ts_start).total_seconds() / 60)
        if gap_minutes >= min_gap_min:
            gap_count += 1
            gap_total += gap_minutes
            gap_max = max(gap_max, gap_minutes)

    return (gap_count, gap_max, gap_total)


# ---------------------------------------------------------------------------
#  Degradation segment detector
# ---------------------------------------------------------------------------

def _find_segments(
    mask: pd.Series,
    timestamps: pd.Series,
    min_duration_min: int = 5,
    merge_gap_min: int = 3,
) -> list[dict]:
    """
    Find contiguous True-segments in boolean mask, merge close ones,
    filter by min duration.

    Returns list of {"start": datetime, "end": datetime, "duration_min": int}.
    """
    segments: list[dict] = []
    seg_start = None

    for i in range(len(mask)):
        if mask.iloc[i]:
            if seg_start is None:
                seg_start = i
        else:
            if seg_start is not None:
                segments.append({"start_idx": seg_start, "end_idx": i - 1})
                seg_start = None
    # trailing segment
    if seg_start is not None:
        segments.append({"start_idx": seg_start, "end_idx": len(mask) - 1})

    if not segments:
        return []

    # Merge segments with gap < merge_gap_min
    merged = [segments[0]]
    for seg in segments[1:]:
        prev = merged[-1]
        ts_prev_end = timestamps.iloc[prev["end_idx"]]
        ts_seg_start = timestamps.iloc[seg["start_idx"]]
        gap_min = (ts_seg_start - ts_prev_end).total_seconds() / 60
        if gap_min <= merge_gap_min:
            prev["end_idx"] = seg["end_idx"]
        else:
            merged.append(seg)

    # Convert to timestamps and filter by min duration
    result = []
    for seg in merged:
        ts_start = timestamps.iloc[seg["start_idx"]]
        ts_end = timestamps.iloc[seg["end_idx"]]
        dur_min = int((ts_end - ts_start).total_seconds() / 60)
        if dur_min >= min_duration_min:
            result.append({
                "start": ts_start,
                "end": ts_end,
                "duration_min": dur_min,
            })

    return result


def _detect_degradation(
    series: pd.Series,
    timestamps: pd.Series,
    min_duration_min: int = 5,
    merge_gap_min: int = 3,
) -> dict:
    """
    Detect sustained anomalous segments (sensor malfunction / degradation).

    LoRa sensors don't see purges (verified on 123 marked cycles — 0% visible),
    so dramatic pressure shifts in sensor data = sensor degradation, not physics.

    Algorithm:
      1. baseline = median of valid values (0 < v <= 100)
      2. threshold = max(baseline * 0.3, 3.0 atm)
      3. degraded = point is valid AND |value - baseline| > threshold
      4. Find contiguous segments >= min_duration_min, merge gaps < merge_gap_min

    Returns: {"count": N, "total_min": M, "max_min": K}
    """
    _P_MAX = 100.0

    valid = series.dropna()
    valid = valid[(valid != 0) & (valid > 0) & (valid <= _P_MAX)]

    if len(valid) < 10:
        return {"count": 0, "total_min": 0, "max_min": 0}

    baseline = float(valid.median())
    threshold = max(baseline * 0.3, 3.0)

    # Mark points that ARE transmitted but anomalously far from baseline
    is_valid = series.notna() & (series != 0) & (series > 0) & (series <= _P_MAX)
    deviation = (series - baseline).abs()
    degraded = is_valid & (deviation > threshold)

    segments = _find_segments(degraded, timestamps, min_duration_min, merge_gap_min)

    total_min = sum(s["duration_min"] for s in segments)
    max_min = max((s["duration_min"] for s in segments), default=0)

    return {"count": len(segments), "total_min": total_min, "max_min": max_min}


def _compute_grade(
    stats: dict,
    sync_pct: float,
    degradation: dict | None = None,
) -> tuple[str, list[str]]:
    """
    Compute quality grade (A/B/C/F) and flag list.

    A = uptime > 95%, no long gaps, few spikes
    B = uptime 85-95%
    C = uptime 70-85%
    F = uptime < 70% or gap > 120 min
    """
    flags = []
    uptime = stats["uptime_pct"]
    zeros = stats["zeros"]
    total = stats.get("missing", 0) + stats.get("valid", 0) + zeros

    # Flag: many zeros
    if total > 0 and zeros / total > 0.05:
        flags.append("many_zeros")

    # Flag: long gap
    if stats["gap_max_minutes"] > 60:
        flags.append("long_gap")

    # Flag: low sync
    if sync_pct < 80:
        flags.append("low_sync")

    # Flag: high noise
    if stats["std"] is not None and stats["mean"] is not None and stats["mean"] > 0:
        cv = stats["std"] / stats["mean"]
        if cv > 0.1:
            flags.append("high_noise")

    # Flag: many spikes
    if stats["spikes_pct"] > 3:
        flags.append("many_spikes")

    # Flag: sensor degradation (sustained anomalous segments)
    if degradation and degradation.get("total_min", 0) > 30:
        flags.append("sensor_degradation")

    # Grade
    if uptime < 70 or stats["gap_max_minutes"] > 120:
        grade = "F"
    elif uptime < 85:
        grade = "C"
    elif uptime < 95:
        grade = "B"
    else:
        grade = "A"

    return grade, flags


def _get_sensor_assignments(db) -> dict:
    """
    Get current sensor assignments: well_id -> {tube: {sensor_id, serial}, line: {...}}
    """
    result = {}
    try:
        rows = (
            db.query(
                EquipmentInstallation.well_id,
                LoRaSensor.id.label("sensor_id"),
                LoRaSensor.serial_number,
                LoRaSensor.csv_column,
            )
            .join(Equipment, Equipment.id == EquipmentInstallation.equipment_id)
            .join(LoRaSensor, LoRaSensor.serial_number == Equipment.serial_number)
            .filter(EquipmentInstallation.removed_at.is_(None))
            .all()
        )
        for r in rows:
            role = "tube" if r.csv_column == "Ptr" else "line"
            result.setdefault(r.well_id, {})[role] = {
                "sensor_id": r.sensor_id,
                "serial": r.serial_number,
            }
    except Exception:
        log.warning("Failed to load sensor assignments", exc_info=True)
    return result


def _lookup_sensor_sn(sensor_id: Optional[int]) -> Optional[str]:
    """Lookup serial number by lora_sensor.id."""
    if not sensor_id:
        return None
    db = SessionLocal()
    try:
        return db.query(LoRaSensor.serial_number).filter(LoRaSensor.id == sensor_id).scalar()
    finally:
        db.close()


def _upsert_reports(reports: list[dict]):
    """Insert or update daily reports (idempotent)."""
    if not reports:
        return
    db = SessionLocal()
    try:
        for r in reports:
            existing = (
                db.query(SensorDailyReport)
                .filter(
                    SensorDailyReport.report_date == r["report_date"],
                    SensorDailyReport.well_id == r["well_id"],
                    SensorDailyReport.sensor_role == r["sensor_role"],
                )
                .first()
            )
            if existing:
                for k, v in r.items():
                    setattr(existing, k, v)
            else:
                db.add(SensorDailyReport(**r))
        db.commit()
    except Exception:
        db.rollback()
        log.error("Failed to upsert daily reports", exc_info=True)
        raise
    finally:
        db.close()
