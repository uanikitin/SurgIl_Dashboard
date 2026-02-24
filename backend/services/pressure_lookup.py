"""
Поиск ближайших давлений из pressure_raw (данные LoRa-датчиков).

Используется при автозаполнении давления в актах оборудования
и актах приёма/передачи скважин.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import text

from backend.db import engine as pg_engine

# Все timestamps в приложении — Кунград UTC+5
_KUNGRAD_OFFSET = timedelta(hours=5)


def get_nearest_from_pressure_raw(
    well_id: int,
    target_dt_local: datetime,
    *,
    max_gap_hours: int = 24,
    mode: str = "nearest",
) -> Optional[dict]:
    """
    Ближайшее ненулевое давление из pressure_raw.

    Parameters
    ----------
    well_id : int
        ID скважины (wells.id).
    target_dt_local : datetime
        Целевое время в Кунград UTC+5 (naive datetime).
    max_gap_hours : int
        Максимальное окно поиска в часах.
    mode : str
        'nearest' — ближайшее (до или после),
        'before'  — только ДО target.

    Returns
    -------
    dict с ключами p_tube, p_line, measured_at_local, delta_seconds
    или None если данных нет.
    """
    target_utc = target_dt_local - _KUNGRAD_OFFSET
    cutoff_before = target_utc - timedelta(hours=max_gap_hours)
    cutoff_after = target_utc + timedelta(hours=max_gap_hours)

    with pg_engine.connect() as conn:
        # Ближайшая запись ДО target
        before_row = conn.execute(text("""
            SELECT measured_at, p_tube, p_line
            FROM pressure_raw
            WHERE well_id = :wid
              AND measured_at BETWEEN :cutoff AND :target
              AND (NULLIF(p_tube, 0.0) IS NOT NULL
                   OR NULLIF(p_line, 0.0) IS NOT NULL)
            ORDER BY measured_at DESC
            LIMIT 1
        """), {"wid": well_id, "target": target_utc,
               "cutoff": cutoff_before}).fetchone()

        # Ближайшая запись ПОСЛЕ target (только в режиме nearest)
        after_row = None
        if mode == "nearest":
            after_row = conn.execute(text("""
                SELECT measured_at, p_tube, p_line
                FROM pressure_raw
                WHERE well_id = :wid
                  AND measured_at BETWEEN :target AND :cutoff
                  AND (NULLIF(p_tube, 0.0) IS NOT NULL
                       OR NULLIF(p_line, 0.0) IS NOT NULL)
                ORDER BY measured_at ASC
                LIMIT 1
            """), {"wid": well_id, "target": target_utc,
                   "cutoff": cutoff_after}).fetchone()

    # Выбираем ближайшую
    best = None
    if before_row and after_row:
        delta_b = abs((target_utc - before_row[0]).total_seconds())
        delta_a = abs((after_row[0] - target_utc).total_seconds())
        best = before_row if delta_b <= delta_a else after_row
    elif before_row:
        best = before_row
    elif after_row:
        best = after_row

    if best is None:
        return None

    measured_utc = best[0]
    p_tube_raw = best[1]
    p_line_raw = best[2]

    return {
        "p_tube": p_tube_raw if p_tube_raw and p_tube_raw != 0.0 else None,
        "p_line": p_line_raw if p_line_raw and p_line_raw != 0.0 else None,
        "measured_at_local": measured_utc + _KUNGRAD_OFFSET,
        "delta_seconds": abs((target_utc - measured_utc).total_seconds()),
    }
