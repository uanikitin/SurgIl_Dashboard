"""
pressure_mask_service.py — Применение масок коррекции давления.

Маски НЕ изменяют pressure_raw. Коррекции применяются in-memory к DataFrame.
Используется как в chart pipeline, так и в flow rate pipeline.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import text

from backend.db import engine as pg_engine

log = logging.getLogger(__name__)


# ──────────────────── Загрузка масок из БД ────────────────────


def load_active_masks(
    well_id: int,
    dt_start: datetime,
    dt_end: datetime,
) -> list:
    """
    Загружает активные маски для скважины, пересекающиеся с периодом.
    Возвращает список объектов-словарей (не ORM, чтобы не тянуть сессию).
    """
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT id, well_id, problem_type, affected_sensor,
                       correction_method, dt_start, dt_end,
                       manual_delta_p, reason
                FROM pressure_mask
                WHERE well_id = :well_id
                  AND is_active = true
                  AND dt_start < :period_end
                  AND dt_end > :period_start
                ORDER BY dt_start
            """),
            {
                "well_id": well_id,
                "period_start": dt_start,
                "period_end": dt_end,
            },
        ).fetchall()

    masks = []
    for r in rows:
        masks.append({
            "id": r[0],
            "well_id": r[1],
            "problem_type": r[2],
            "affected_sensor": r[3],
            "correction_method": r[4],
            "dt_start": r[5],
            "dt_end": r[6],
            "manual_delta_p": r[7],
            "reason": r[8],
        })
    return masks


# ──────────────────── Применение масок ────────────────────


def apply_masks(
    df: pd.DataFrame,
    masks: list[dict],
) -> tuple[pd.DataFrame, int]:
    """
    Применяет маски коррекции к DataFrame давления.

    Parameters
    ----------
    df : DataFrame с индексом measured_at (UTC), колонками p_tube, p_line.
    masks : список масок (dict) из load_active_masks().

    Returns
    -------
    (corrected_df, total_corrected_points)

    Оригинальный df не изменяется (делается копия).
    """
    if not masks or df.empty:
        return df, 0

    df = df.copy()
    total_corrected = 0

    for mask in masks:
        affected = mask["affected_sensor"]  # 'p_tube' or 'p_line'
        method = mask["correction_method"]
        dt_start = mask["dt_start"]
        dt_end = mask["dt_end"]

        # Маска по времени
        time_mask = (df.index >= dt_start) & (df.index <= dt_end)
        n_affected = int(time_mask.sum())
        if n_affected == 0:
            continue

        total_corrected += n_affected

        if method == "median_1d":
            _apply_median(df, time_mask, affected, dt_start, window_days=1)

        elif method == "median_3d":
            _apply_median(df, time_mask, affected, dt_start, window_days=3)

        elif method == "delta_reconstruct":
            _apply_delta_reconstruct(
                df, time_mask, affected, dt_start,
                manual_delta_p=mask.get("manual_delta_p"),
            )

        elif method == "interpolate":
            df.loc[time_mask, affected] = np.nan
            df[affected] = df[affected].interpolate(method="linear")

        elif method == "exclude":
            df.loc[time_mask, affected] = np.nan

        log.debug(
            "mask %s: method=%s sensor=%s %d points",
            mask["id"], method, affected, n_affected,
        )

    # Заполняем оставшиеся NaN (от exclude / interpolate на краях)
    for col in ("p_tube", "p_line"):
        if col in df.columns:
            df[col] = df[col].ffill().bfill()

    return df, total_corrected


def _apply_median(
    df: pd.DataFrame,
    time_mask: pd.Series,
    affected: str,
    dt_start: datetime,
    window_days: int,
) -> None:
    """Заменяет affected_sensor медианой за window_days ДО начала проблемы."""
    window_start = dt_start - timedelta(days=window_days)
    pre_data = df.loc[
        (df.index >= window_start) & (df.index < dt_start),
        affected,
    ]
    if pre_data.empty or pre_data.isna().all():
        # Нет данных до проблемы — fallback к общей медиане
        median_val = df[affected].median()
    else:
        median_val = pre_data.median()

    if pd.notna(median_val):
        df.loc[time_mask, affected] = median_val


def _apply_delta_reconstruct(
    df: pd.DataFrame,
    time_mask: pd.Series,
    affected: str,
    dt_start: datetime,
    manual_delta_p: Optional[float] = None,
) -> None:
    """
    Восстанавливает плохой датчик через хороший + медиана ΔP.

    ΔP = p_tube - p_line (всегда).
    Если affected='p_line' → p_line = p_tube - median_ΔP
    Если affected='p_tube' → p_tube = p_line + median_ΔP
    """
    if manual_delta_p is not None:
        median_dp = manual_delta_p
    else:
        # Считаем медиану ΔP за 1 день ДО проблемы
        pre_start = dt_start - timedelta(days=1)
        pre_data = df.loc[
            (df.index >= pre_start) & (df.index < dt_start)
        ]
        if pre_data.empty:
            log.warning("delta_reconstruct: no pre-data, skipping mask")
            return
        dp = pre_data["p_tube"] - pre_data["p_line"]
        median_dp = dp.median()
        if pd.isna(median_dp):
            log.warning("delta_reconstruct: median_dp is NaN, skipping mask")
            return

    good_col = "p_tube" if affected == "p_line" else "p_line"

    if affected == "p_line":
        df.loc[time_mask, "p_line"] = df.loc[time_mask, good_col] - median_dp
    else:
        df.loc[time_mask, "p_tube"] = df.loc[time_mask, good_col] + median_dp


# ──────────────────── Авто-детекция аномалий ────────────────────


def detect_anomalies(
    well_id: int,
    days: int = 30,
    dp_threshold_sigma: float = 3.0,
    min_duration_min: int = 30,
    exclude_windows: list[tuple[datetime, datetime]] | None = None,
) -> list[dict]:
    """
    Базовая эвристика: ищет участки с аномальным ΔP.
    exclude_windows — список (dt_start, dt_end) окон продувок, которые НЕ считаются аномалиями.

    Returns
    -------
    list of {dt_start, dt_end, affected_sensor, confidence, suggested_method, dp_deviation}
    """
    dt_end = datetime.utcnow()
    dt_start = dt_end - timedelta(days=days)

    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT measured_at,
                       NULLIF(p_tube, 0) AS p_tube,
                       NULLIF(p_line, 0) AS p_line
                FROM pressure_raw
                WHERE well_id = :well_id
                  AND measured_at >= :start
                  AND measured_at <= :end
                ORDER BY measured_at
            """),
            {"well_id": well_id, "start": dt_start, "end": dt_end},
        ).fetchall()

    if len(rows) < 100:
        return []

    df = pd.DataFrame(rows, columns=["measured_at", "p_tube", "p_line"])
    df["measured_at"] = pd.to_datetime(df["measured_at"])
    df = df.set_index("measured_at").sort_index()

    # Ресэмплинг до 5-минутного интервала для скорости
    df = df.resample("5min").median().dropna(how="all")
    if df.empty:
        return []

    # Маскируем окна продувок — заменяем на NaN чтобы не влияли на статистику
    if exclude_windows:
        for w_start, w_end in exclude_windows:
            purge_mask = (df.index >= w_start) & (df.index <= w_end)
            df.loc[purge_mask, ["p_tube", "p_line"]] = np.nan

    # Интерполируем NaN (от продувок) для расчёта ΔP
    df["p_tube"] = df["p_tube"].interpolate(method="linear")
    df["p_line"] = df["p_line"].interpolate(method="linear")
    df = df.dropna(subset=["p_tube", "p_line"])

    if df.empty:
        return []

    df["dp"] = df["p_tube"] - df["p_line"]

    # Скользящая медиана ΔP (окно 6 часов = 72 точки при 5 мин)
    window = 72
    df["dp_median"] = df["dp"].rolling(window=window, center=True, min_periods=10).median()
    df["dp_dev"] = (df["dp"] - df["dp_median"]).abs()

    # MAD — robust estimator of standard deviation
    global_mad = df["dp_dev"].median() * 1.4826  # scale to σ
    if global_mad < 0.1:
        global_mad = 0.1  # минимальный порог

    threshold = dp_threshold_sigma * global_mad

    # Маска аномалий
    df["is_anomaly"] = df["dp_dev"] > threshold

    # Исключить окна продувок (ещё раз — на случай если интерполяция оставила артефакты)
    # Margin 30 мин — продувка влияет на давление: стабилизация после stop + набор давления
    if exclude_windows:
        for w_start, w_end in exclude_windows:
            margin = timedelta(minutes=30)
            purge_mask = (df.index >= w_start - margin) & (df.index <= w_end + margin)
            df.loc[purge_mask, "is_anomaly"] = False

    # Группировка последовательных аномалий
    anomalies = []
    in_anomaly = False
    start_idx = None

    for idx, row in df.iterrows():
        if row["is_anomaly"] and not in_anomaly:
            in_anomaly = True
            start_idx = idx
        elif not row["is_anomaly"] and in_anomaly:
            in_anomaly = False
            duration = (idx - start_idx).total_seconds() / 60
            if duration >= min_duration_min:
                anomalies.append((start_idx, idx))

    # Закрыть последнюю аномалию
    if in_anomaly and start_idx is not None:
        last_idx = df.index[-1]
        duration = (last_idx - start_idx).total_seconds() / 60
        if duration >= min_duration_min:
            anomalies.append((start_idx, last_idx))

    # Определить affected_sensor для каждой аномалии
    results = []
    for a_start, a_end in anomalies:
        segment = df.loc[a_start:a_end]
        pre_start = a_start - timedelta(hours=6)
        pre_data = df.loc[pre_start:a_start]

        if pre_data.empty:
            continue

        pre_tube_median = pre_data["p_tube"].median()
        pre_line_median = pre_data["p_line"].median()
        seg_tube_median = segment["p_tube"].median()
        seg_line_median = segment["p_line"].median()

        tube_change = abs(seg_tube_median - pre_tube_median) if pd.notna(pre_tube_median) and pd.notna(seg_tube_median) else 0
        line_change = abs(seg_line_median - pre_line_median) if pd.notna(pre_line_median) and pd.notna(seg_line_median) else 0

        if tube_change > line_change:
            affected = "p_tube"
        else:
            affected = "p_line"

        # Confidence: насколько один датчик изменился больше другого
        total_change = tube_change + line_change
        if total_change > 0:
            confidence = round(max(tube_change, line_change) / total_change, 2)
        else:
            confidence = 0.5

        duration_hours = (a_end - a_start).total_seconds() / 3600
        suggested_method = "delta_reconstruct" if duration_hours > 2 else "interpolate"

        dp_deviation = round(float(segment["dp_dev"].mean()), 2)

        results.append({
            "dt_start": a_start.isoformat(),
            "dt_end": a_end.isoformat(),
            "affected_sensor": affected,
            "confidence": confidence,
            "suggested_method": suggested_method,
            "duration_hours": round(duration_hours, 1),
            "dp_deviation": dp_deviation,
        })

    log.info(
        "[detect_anomalies] well=%d days=%d found=%d anomalies (excluded %d purge windows)",
        well_id, days, len(results), len(exclude_windows or []),
    )
    return results


# ──────────────────── Авто-создание масок ────────────────────


KUNGRAD_OFFSET = timedelta(hours=5)


def _detect_purge_events(well_id: int, days: int) -> list[dict]:
    """
    Загружает маркированные продувки из events для скважины.
    Группирует start→stop в сессии и возвращает маски.

    events.event_time хранится в локальном времени (Кунград, UTC+5).
    Возвращает dt_start/dt_end в **UTC** (для совместимости с pressure_raw).
    """
    # events хранятся в локальном времени — запрашиваем с запасом
    dt_end_local = datetime.utcnow() + KUNGRAD_OFFSET
    dt_start_local = dt_end_local - timedelta(days=days)

    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT e.event_time, e.purge_phase, e.p_tube, e.p_line
                FROM events e
                JOIN wells w ON e.well = w.number::text
                WHERE e.event_type = 'purge'
                  AND e.purge_phase IN ('start', 'press', 'stop')
                  AND w.id = :well_id
                  AND e.event_time >= :dt_start
                ORDER BY e.event_time
            """),
            {"well_id": well_id, "dt_start": dt_start_local},
        ).fetchall()

    if not rows:
        return []

    # Группируем start → stop (press опционален)
    MAX_GAP = timedelta(hours=4)
    results = []
    current_start = None

    for event_time, phase, p_tube, p_line in rows:
        if phase == "start":
            if current_start is not None:
                results.append({
                    "dt_start": current_start - KUNGRAD_OFFSET,  # → UTC
                    "dt_end": current_start + timedelta(hours=1) - KUNGRAD_OFFSET,
                    "reason": "Продувка (незакрытая)",
                })
            current_start = event_time

        elif phase == "stop":
            if current_start is not None and (event_time - current_start) < MAX_GAP:
                results.append({
                    "dt_start": current_start - KUNGRAD_OFFSET,  # → UTC
                    "dt_end": event_time - KUNGRAD_OFFSET,       # → UTC
                    "reason": "Продувка",
                })
                current_start = None

    if current_start is not None:
        results.append({
            "dt_start": current_start - KUNGRAD_OFFSET,
            "dt_end": current_start + timedelta(hours=1) - KUNGRAD_OFFSET,
            "reason": "Продувка (незакрытая)",
        })

    log.info(
        "[_detect_purge_events] well=%d days=%d found=%d purges",
        well_id, days, len(results),
    )
    return results


def auto_create_masks(
    well_id: int,
    days: int = 7,
    source: str = "auto",
) -> dict:
    """
    Запускает все детекторы и создаёт записи PressureMask:
    1. detect_anomalies (ΔP — сбои датчиков)
    2. _detect_purge_events (продувки из маркеров событий)

    Не создаёт дубликаты (проверяет пересечение с существующими масками).
    Returns: {created: N, skipped_overlap: M, batch_id: str}
    """
    import uuid
    from backend.db import SessionLocal
    from backend.models.pressure_mask import PressureMask

    # 1. Продувки из маркеров событий (только для exclude_windows в ΔP-детекции)
    purge_events = _detect_purge_events(well_id, days=days)
    purge_windows = [(p["dt_start"], p["dt_end"]) for p in purge_events]

    # 2. ΔP аномалии (сбои датчиков), исключая окна маркированных продувок
    dp_anomalies = detect_anomalies(
        well_id, days=days, exclude_windows=purge_windows,
    )

    # Только sensor_fault — продувки НЕ создаются автоматически
    candidates = []

    for a in dp_anomalies:
        candidates.append({
            "dt_start": datetime.fromisoformat(a["dt_start"]),
            "dt_end": datetime.fromisoformat(a["dt_end"]),
            "problem_type": "sensor_fault",
            "affected_sensor": a["affected_sensor"],
            "correction_method": a.get("suggested_method", "delta_reconstruct"),
            "confidence": a.get("confidence"),
            "reason": f"ΔP deviation {a.get('dp_deviation', '?')} atm, "
                      f"duration {a.get('duration_hours', '?')}h",
        })

    if not candidates:
        return {"created": 0, "skipped_overlap": 0, "batch_id": None}

    batch_id = str(uuid.uuid4())[:8]
    db = SessionLocal()
    try:
        # Загрузить существующие активные маски для проверки пересечений
        existing = (
            db.query(PressureMask)
            .filter(
                PressureMask.well_id == well_id,
                PressureMask.is_active == True,
            )
            .all()
        )

        created = 0
        skipped = 0

        for c in candidates:
            dt_start = c["dt_start"]
            dt_end = c["dt_end"]

            # Проверка пересечения с существующими масками того же типа
            overlaps = False
            for ex in existing:
                if ex.dt_start < dt_end and ex.dt_end > dt_start:
                    overlaps = True
                    break

            if overlaps:
                skipped += 1
                continue

            m = PressureMask(
                well_id=well_id,
                problem_type=c["problem_type"],
                affected_sensor=c["affected_sensor"],
                correction_method=c["correction_method"],
                dt_start=dt_start,
                dt_end=dt_end,
                is_active=True,
                is_verified=c["problem_type"] == "purge",  # продувки из маркеров = verified
                source=source,
                detection_confidence=c.get("confidence"),
                batch_id=batch_id,
                reason=c["reason"],
            )
            db.add(m)
            existing.append(m)
            created += 1

        db.commit()

        log.info(
            "[auto_create_masks] well=%d created=%d skipped=%d batch=%s",
            well_id, created, skipped, batch_id,
        )
        return {
            "created": created,
            "skipped_overlap": skipped,
            "batch_id": batch_id if created > 0 else None,
        }
    finally:
        db.close()


# ──────────────────── Сводка масок за период ────────────────────


def get_mask_summary_for_period(
    well_id: int,
    dt_start: datetime,
    dt_end: datetime,
) -> dict:
    """Сводка масок за период для отчётов."""
    masks = load_active_masks(well_id, dt_start, dt_end)

    if not masks:
        return {
            "total_masks": 0,
            "by_type": {},
            "total_corrected_hours": 0,
        }

    by_type: dict[str, dict] = {}
    total_hours = 0.0

    for m in masks:
        ptype = m["problem_type"]
        duration_h = (m["dt_end"] - m["dt_start"]).total_seconds() / 3600

        if ptype not in by_type:
            by_type[ptype] = {"count": 0, "total_hours": 0.0}
        by_type[ptype]["count"] += 1
        by_type[ptype]["total_hours"] = round(by_type[ptype]["total_hours"] + duration_h, 1)
        total_hours += duration_h

    return {
        "total_masks": len(masks),
        "by_type": by_type,
        "total_corrected_hours": round(total_hours, 1),
    }
