"""
Поиск ближайших давлений из pressure_raw (данные LoRa-датчиков).

Используется при автозаполнении давления в актах оборудования
и актах приёма/передачи скважин.

Важно: каналы p_tube и p_line ломаются независимо (SMOD-PT-60 ~4% false-zeros),
поэтому поиск выполняется по каждому каналу отдельно.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import text

from backend.db import engine as pg_engine

# Все timestamps в приложении — Кунград UTC+5
_KUNGRAD_OFFSET = timedelta(hours=5)

# Физический диапазон датчика SMOD-PT-60 (кгс/см²)
_MIN_VALID = 0.0
_MAX_VALID = 85.0


def _valid(v) -> bool:
    return v is not None and _MIN_VALID < float(v) <= _MAX_VALID


def _find_channel_nearest(
    conn, well_id: int, target_utc: datetime, col: str,
    cutoff_before: datetime, cutoff_after: datetime, mode: str,
):
    """Ближайшая валидная запись по одному каналу (p_tube ИЛИ p_line)."""
    before = conn.execute(text(f"""
        SELECT measured_at, {col}
        FROM pressure_raw
        WHERE well_id = :wid
          AND measured_at BETWEEN :cutoff AND :target
          AND {col} IS NOT NULL
          AND {col} > 0 AND {col} <= 85
        ORDER BY measured_at DESC
        LIMIT 1
    """), {"wid": well_id, "target": target_utc,
           "cutoff": cutoff_before}).fetchone()

    after = None
    if mode == "nearest":
        after = conn.execute(text(f"""
            SELECT measured_at, {col}
            FROM pressure_raw
            WHERE well_id = :wid
              AND measured_at BETWEEN :target AND :cutoff
              AND {col} IS NOT NULL
              AND {col} > 0 AND {col} <= 85
            ORDER BY measured_at ASC
            LIMIT 1
        """), {"wid": well_id, "target": target_utc,
               "cutoff": cutoff_after}).fetchone()

    if before and after:
        db = abs((target_utc - before[0]).total_seconds())
        da = abs((after[0] - target_utc).total_seconds())
        return before if db <= da else after
    return before or after


def get_nearest_from_pressure_raw(
    well_id: int,
    target_dt_local: datetime,
    *,
    max_gap_hours: int = 24,
    mode: str = "nearest",
) -> Optional[dict]:
    """
    Ближайшее ненулевое давление из pressure_raw.

    Ищет p_tube и p_line **независимо** (каналы ломаются раздельно),
    поэтому возвращённые значения могут быть из разных строк.

    Returns dict: p_tube, p_line, measured_at_local (композит - ближайшая
    общая точка), delta_seconds, p_tube_at, p_line_at (времена каждого
    канала). При отсутствии данных — None.
    """
    target_utc = target_dt_local - _KUNGRAD_OFFSET
    cutoff_before = target_utc - timedelta(hours=max_gap_hours)
    cutoff_after = target_utc + timedelta(hours=max_gap_hours)

    with pg_engine.connect() as conn:
        tube_row = _find_channel_nearest(
            conn, well_id, target_utc, "p_tube",
            cutoff_before, cutoff_after, mode,
        )
        line_row = _find_channel_nearest(
            conn, well_id, target_utc, "p_line",
            cutoff_before, cutoff_after, mode,
        )

    if tube_row is None and line_row is None:
        return None

    p_tube = tube_row[1] if tube_row else None
    p_line = line_row[1] if line_row else None
    t_tube_utc = tube_row[0] if tube_row else None
    t_line_utc = line_row[0] if line_row else None

    # композитное время — ближайшее из найденных к target
    candidates_utc = [t for t in (t_tube_utc, t_line_utc) if t is not None]
    primary_utc = min(candidates_utc,
                      key=lambda t: abs((target_utc - t).total_seconds()))
    delta_seconds = abs((target_utc - primary_utc).total_seconds())

    return {
        "p_tube": p_tube if _valid(p_tube) else None,
        "p_line": p_line if _valid(p_line) else None,
        "measured_at_local": primary_utc + _KUNGRAD_OFFSET,
        "delta_seconds": delta_seconds,
        "p_tube_at_local": (t_tube_utc + _KUNGRAD_OFFSET) if t_tube_utc else None,
        "p_line_at_local": (t_line_utc + _KUNGRAD_OFFSET) if t_line_utc else None,
    }


def get_raw_candidates_around(
    well_id: int,
    target_dt_local: datetime,
    *,
    window_hours: int = 24,
    limit_each_side: int = 10,
) -> dict:
    """
    Список ближайших точек ДО и ПОСЛЕ целевого времени из pressure_raw.

    Отбрасывает строки, где оба канала NULL/невалидны. NULL/0 в одном канале
    допустим — отображается как прочерк в UI.

    Returns:
        {
          "target_local": "2026-04-21T13:45",
          "window_hours": 24,
          "before": [{measured_at_local, p_tube, p_line, delta_seconds}, ...],
          "after":  [...],
        }
    """
    target_utc = target_dt_local - _KUNGRAD_OFFSET
    cutoff_before = target_utc - timedelta(hours=window_hours)
    cutoff_after = target_utc + timedelta(hours=window_hours)

    with pg_engine.connect() as conn:
        before_rows = conn.execute(text("""
            SELECT measured_at, p_tube, p_line
            FROM pressure_raw
            WHERE well_id = :wid
              AND measured_at BETWEEN :cutoff AND :target
              AND ((p_tube IS NOT NULL AND p_tube > 0 AND p_tube <= 85)
                OR (p_line IS NOT NULL AND p_line > 0 AND p_line <= 85))
            ORDER BY measured_at DESC
            LIMIT :lim
        """), {"wid": well_id, "target": target_utc,
               "cutoff": cutoff_before, "lim": limit_each_side}).fetchall()

        after_rows = conn.execute(text("""
            SELECT measured_at, p_tube, p_line
            FROM pressure_raw
            WHERE well_id = :wid
              AND measured_at > :target
              AND measured_at <= :cutoff
              AND ((p_tube IS NOT NULL AND p_tube > 0 AND p_tube <= 85)
                OR (p_line IS NOT NULL AND p_line > 0 AND p_line <= 85))
            ORDER BY measured_at ASC
            LIMIT :lim
        """), {"wid": well_id, "target": target_utc,
               "cutoff": cutoff_after, "lim": limit_each_side}).fetchall()

    def _norm(row):
        at_utc, pt, pl = row
        at_local = at_utc + _KUNGRAD_OFFSET
        return {
            "measured_at_local": at_local.strftime("%Y-%m-%dT%H:%M:%S"),
            "measured_at_display": at_local.strftime("%d.%m.%Y %H:%M"),
            "p_tube": float(pt) if _valid(pt) else None,
            "p_line": float(pl) if _valid(pl) else None,
            "delta_seconds": int(abs((target_utc - at_utc).total_seconds())),
        }

    return {
        "target_local": target_dt_local.strftime("%Y-%m-%dT%H:%M:%S"),
        "window_hours": window_hours,
        "before": [_norm(r) for r in before_rows],
        "after": [_norm(r) for r in after_rows],
    }
