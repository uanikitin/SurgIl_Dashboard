"""
Чтение данных давления и параметров скважин из PostgreSQL.

Использует существующий engine из backend.db —
не создаёт своего подключения.
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from sqlalchemy import text

from backend.db import engine as pg_engine

log = logging.getLogger(__name__)


def get_pressure_data(
    well_id: int,
    start: str,
    end: str,
) -> pd.DataFrame:
    """
    Поминутные замеры давления из pressure_raw (PostgreSQL).

    Returns
    -------
    DataFrame с колонками [p_tube, p_line], индекс = measured_at (UTC).
    Пустой DataFrame если данных нет.
    """
    query = text("""
        SELECT measured_at, p_tube, p_line
        FROM pressure_raw
        WHERE well_id = :well_id
          AND measured_at BETWEEN :start AND :end
          AND (p_tube IS NOT NULL OR p_line IS NOT NULL)
        ORDER BY measured_at
    """)
    with pg_engine.connect() as conn:
        df = pd.read_sql(
            query, conn,
            params={"well_id": well_id, "start": start, "end": end},
            parse_dates=["measured_at"],
            index_col="measured_at",
        )
    log.info(
        "pressure_raw: well_id=%d, period %s..%s → %d rows",
        well_id, start, end, len(df),
    )
    return df


def get_choke_mm(well_id: int) -> Optional[float]:
    """
    Диаметр штуцера (мм) из well_construction.

    Берёт самую свежую запись с непустым choke_diam_mm.
    Возвращает None если данных нет.

    Реализация:
      1) Получаем номер скважины из wells по id.
      2) Ищем строку в well_construction по строковому номеру (TRIM,
         защита от лишних пробелов / разной типизации). Тот же подход
         использует страница /well/{id}.
    """
    with pg_engine.connect() as conn:
        wrow = conn.execute(
            text("SELECT number FROM wells WHERE id = :wid"),
            {"wid": well_id},
        ).fetchone()
        if not wrow or wrow[0] is None:
            log.warning("get_choke_mm: well not found or has no number, id=%d", well_id)
            return None
        well_no = str(wrow[0]).strip()

        row = conn.execute(
            text("""
                SELECT choke_diam_mm
                FROM well_construction
                WHERE TRIM(well_no) = :wno
                  AND choke_diam_mm IS NOT NULL
                  AND choke_diam_mm > 0
                ORDER BY data_as_of DESC NULLS LAST, id DESC
                LIMIT 1
            """),
            {"wno": well_no},
        ).fetchone()
    if row is None:
        log.warning("choke_diam_mm not found for well_id=%d (well_no=%r)",
                    well_id, well_no)
        return None
    return float(row[0])


def get_well_info(well_id: int) -> Optional[dict]:
    """
    Базовая информация о скважине: id, number, name.
    """
    query = text("""
        SELECT id, number, name, current_status
        FROM wells
        WHERE id = :well_id
    """)
    with pg_engine.connect() as conn:
        row = conn.execute(query, {"well_id": well_id}).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "number": row[1],
        "name": row[2],
        "current_status": row[3],
    }


def get_purge_events(
    well_id: int,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """
    Маркеры продувок из таблицы events (PostgreSQL).

    Читает events с event_type='purge' для скважины well_id.
    JOIN wells для маппинга well_id → well_number → events.well.

    Returns
    -------
    DataFrame: event_time, purge_phase ('start'/'press'/'stop'), p_tube, p_line, description
    Отсортирован по event_time. Пустой если маркеров нет.
    """
    time_filter = ""
    params: dict = {"well_id": well_id}
    if start and end:
        time_filter = "AND e.event_time BETWEEN :start AND :end"
        params["start"] = start
        params["end"] = end

    query = text(f"""
        SELECT e.event_time, e.purge_phase, e.p_tube, e.p_line, e.description
        FROM events e
        JOIN wells w ON e.well = w.number::text
        WHERE w.id = :well_id
          AND e.event_type = 'purge'
          {time_filter}
        ORDER BY e.event_time
    """)
    with pg_engine.connect() as conn:
        df = pd.read_sql(query, conn, params=params, parse_dates=["event_time"])

    log.info(
        "purge_events: well_id=%d → %d markers%s",
        well_id, len(df),
        f" ({start}..{end})" if start else "",
    )
    return df


def list_wells_with_pressure(days: int = 7) -> list[dict]:
    """
    Скважины, у которых есть данные в pressure_raw за последние N дней.
    """
    query = text("""
        SELECT DISTINCT w.id, w.number, w.name, w.current_status
        FROM wells w
        JOIN pressure_raw pr ON pr.well_id = w.id
        WHERE pr.measured_at >= NOW() - MAKE_INTERVAL(days => :days)
        ORDER BY w.number
    """)
    with pg_engine.connect() as conn:
        rows = conn.execute(query, {"days": days}).fetchall()
    return [
        {"id": r[0], "number": r[1], "name": r[2], "current_status": r[3]}
        for r in rows
    ]
