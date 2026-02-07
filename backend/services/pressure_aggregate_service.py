"""
pressure_aggregate_service — агрегация pressure.db → PostgreSQL

1. Читает сырые данные из pressure.db (SQLite)
2. Группирует по (well_id, hour_start)
3. Считает AVG/MIN/MAX для p_tube и p_line
4. UPSERT в pressure_hourly (PostgreSQL)
5. Обновляет pressure_latest (последние давления по каждой скважине)

Использует raw SQL для PostgreSQL чтобы избежать зависимости
от всех ORM-моделей (Equipment и т.д.).
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import create_engine, text

from backend.db_pressure import PressureSessionLocal, init_pressure_db
from backend.settings import settings

log = logging.getLogger(__name__)


def aggregate_to_hourly(
    since: Optional[datetime] = None,
    batch_size: int = 500,
) -> dict:
    """
    Агрегирует сырые данные из pressure.db → pressure_hourly в PostgreSQL.

    Args:
        since: Начало периода агрегации (UTC). По умолчанию — last 48h.
        batch_size: Размер пакета для commit.

    Returns: {"hours_upserted": N, "wells_updated": N}
    """
    init_pressure_db()

    if since is None:
        since = datetime.utcnow() - timedelta(hours=48)

    log.info("Aggregating pressure data since %s", since)

    # 1. Читаем агрегаты из pressure.db
    sqlite_db = PressureSessionLocal()
    try:
        rows = sqlite_db.execute(
            text("""
                SELECT
                    well_id,
                    strftime('%Y-%m-%d %H:00:00', measured_at) as hour_start,
                    AVG(p_tube) as p_tube_avg,
                    MIN(p_tube) as p_tube_min,
                    MAX(p_tube) as p_tube_max,
                    AVG(p_line) as p_line_avg,
                    MIN(p_line) as p_line_min,
                    MAX(p_line) as p_line_max,
                    COUNT(*) as reading_count
                FROM pressure_readings
                WHERE measured_at >= :since
                    AND (p_tube IS NOT NULL OR p_line IS NOT NULL)
                GROUP BY well_id, strftime('%Y-%m-%d %H:00:00', measured_at)
                ORDER BY hour_start
            """),
            {"since": since},
        ).fetchall()
    finally:
        sqlite_db.close()

    if not rows:
        log.info("No data to aggregate")
        return {"hours_upserted": 0, "wells_updated": 0}

    log.info("Found %d (well, hour) groups to upsert", len(rows))

    # 2. UPSERT в PostgreSQL (raw SQL, пакетами)
    # Render.com free tier таймаутит длинные транзакции (~5 мин),
    # поэтому разбиваем на короткие транзакции по batch_size строк.
    hours_upserted = 0

    upsert_sql = text("""
        INSERT INTO pressure_hourly
            (well_id, hour_start, p_tube_avg, p_tube_min, p_tube_max,
             p_line_avg, p_line_min, p_line_max, reading_count, has_gaps)
        VALUES (:well_id, :hour_start, :p_tube_avg, :p_tube_min, :p_tube_max,
                :p_line_avg, :p_line_min, :p_line_max, :reading_count, :has_gaps)
        ON CONFLICT (well_id, hour_start) DO UPDATE SET
            p_tube_avg = EXCLUDED.p_tube_avg,
            p_tube_min = EXCLUDED.p_tube_min,
            p_tube_max = EXCLUDED.p_tube_max,
            p_line_avg = EXCLUDED.p_line_avg,
            p_line_min = EXCLUDED.p_line_min,
            p_line_max = EXCLUDED.p_line_max,
            reading_count = EXCLUDED.reading_count,
            has_gaps = EXCLUDED.has_gaps
    """)

    for batch_start in range(0, len(rows), batch_size):
        batch = rows[batch_start:batch_start + batch_size]
        params_list = []
        for row in batch:
            reading_count = row[8]
            params_list.append({
                "well_id": row[0],
                "hour_start": datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S"),
                "p_tube_avg": _round(row[2]),
                "p_tube_min": _round(row[3]),
                "p_tube_max": _round(row[4]),
                "p_line_avg": _round(row[5]),
                "p_line_min": _round(row[6]),
                "p_line_max": _round(row[7]),
                "reading_count": reading_count,
                "has_gaps": reading_count < 50,
            })

        # Каждый batch — свежее соединение (Render.com timeout-safe)
        _execute_pg_batch(upsert_sql, params_list)

        hours_upserted += len(batch)
        log.info("  upserted %d / %d", hours_upserted, len(rows))

    # 3. Обновляем pressure_latest
    wells_updated = _update_latest()

    log.info(
        "Aggregation complete: %d hours upserted, %d wells updated",
        hours_upserted, wells_updated,
    )
    return {"hours_upserted": hours_upserted, "wells_updated": wells_updated}


def aggregate_full_history() -> dict:
    """Агрегирует все данные (полная переиндексация)."""
    return aggregate_to_hourly(since=datetime(2020, 1, 1))


def _update_latest() -> int:
    """
    Обновляет pressure_latest из pressure.db.
    Берёт среднее за последние 3 минуты (по каждой скважине),
    чтобы сгладить кратковременные скачки давления.
    """
    sqlite_db = PressureSessionLocal()
    try:
        rows = sqlite_db.execute(
            text("""
                SELECT
                    pr.well_id,
                    MAX(pr.measured_at) as measured_at,
                    AVG(pr.p_tube) as p_tube,
                    AVG(pr.p_line) as p_line
                FROM pressure_readings pr
                INNER JOIN (
                    SELECT well_id, MAX(measured_at) as max_ts
                    FROM pressure_readings
                    WHERE p_tube IS NOT NULL OR p_line IS NOT NULL
                    GROUP BY well_id
                ) latest ON pr.well_id = latest.well_id
                WHERE pr.measured_at >= datetime(latest.max_ts, '-3 minutes')
                  AND (pr.p_tube IS NOT NULL OR pr.p_line IS NOT NULL)
                GROUP BY pr.well_id
            """)
        ).fetchall()
    finally:
        sqlite_db.close()

    now = datetime.utcnow()
    upsert_sql = text("""
        INSERT INTO pressure_latest (well_id, measured_at, p_tube, p_line, updated_at)
        VALUES (:well_id, :measured_at, :p_tube, :p_line, :updated_at)
        ON CONFLICT (well_id) DO UPDATE SET
            measured_at = EXCLUDED.measured_at,
            p_tube = EXCLUDED.p_tube,
            p_line = EXCLUDED.p_line,
            updated_at = EXCLUDED.updated_at
    """)
    params_list = [
        {
            "well_id": well_id,
            "measured_at": measured_at,
            "p_tube": _round(p_tube),
            "p_line": _round(p_line),
            "updated_at": now,
        }
        for well_id, measured_at, p_tube, p_line in rows
    ]

    _execute_pg_batch(upsert_sql, params_list)
    return len(params_list)


def _make_pg_engine():
    """Создаёт свежий engine для короткоживущих соединений к Render.com."""
    return create_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)


def _execute_pg_batch(sql, params_list: list, retries: int = 3):
    """
    Выполняет batch SQL-запросов к PostgreSQL с retry.
    Каждый вызов — новый engine + connection (Render.com timeout-safe).
    """
    for attempt in range(retries):
        engine = _make_pg_engine()
        try:
            with engine.begin() as conn:
                for params in params_list:
                    conn.execute(sql, params)
            return
        except Exception as e:
            engine.dispose()
            if attempt < retries - 1:
                wait = 2 ** attempt
                log.warning("  batch failed (attempt %d/%d): %s. Retrying in %ds...",
                            attempt + 1, retries, str(e)[:100], wait)
                time.sleep(wait)
            else:
                raise
        finally:
            engine.dispose()


def _round(val, decimals=2):
    """Округляет float, None/NaN/Inf пропускает."""
    import math
    if val is None:
        return None
    f = float(val)
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, decimals)


def get_wells_pressure_stats(
    db,
    well_ids: list[int],
    period: str = "1h",
) -> dict[int, dict]:
    """
    Получает статистику давлений для списка скважин за выбранный период.

    Args:
        db: SQLAlchemy session (PostgreSQL)
        well_ids: список well.id
        period: "10m", "1h", "1d", "1m" (10 минут, час, сутки, месяц)

    Returns:
        dict[well_id] = {
            "p_tube_avg": float,
            "p_line_avg": float,
            "p_diff_avg": float,  # разница p_tube - p_line
            "reading_count": int,
            "updated_at": datetime,
            "has_data": bool,
        }
    """
    if not well_ids:
        return {}

    # Определяем временной диапазон
    period_map = {
        "10m": timedelta(minutes=10),
        "1h": timedelta(hours=1),
        "1d": timedelta(days=1),
        "1m": timedelta(days=30),
    }
    delta = period_map.get(period, timedelta(hours=1))
    since = datetime.utcnow() - delta

    # Для коротких периодов (10m, 1h) используем pressure_latest
    # Для длинных (1d, 1m) используем pressure_hourly
    if period in ("10m", "1h"):
        # Используем последние данные из pressure_latest
        from backend.models.pressure_latest import PressureLatest

        rows = (
            db.query(PressureLatest)
            .filter(PressureLatest.well_id.in_(well_ids))
            .all()
        )

        result = {}
        for row in rows:
            p_tube = row.p_tube
            p_line = row.p_line
            p_diff = None
            if p_tube is not None and p_line is not None:
                p_diff = round(p_tube - p_line, 2)

            result[row.well_id] = {
                "p_tube_avg": round(p_tube, 2) if p_tube is not None else None,
                "p_line_avg": round(p_line, 2) if p_line is not None else None,
                "p_diff_avg": p_diff,
                "reading_count": 1,
                "updated_at": row.measured_at,  # время замера, а не обновления записи
                "has_data": p_tube is not None or p_line is not None,
            }
        return result
    else:
        # Для длинных периодов агрегируем из pressure_hourly
        from backend.models.pressure_hourly import PressureHourly
        from sqlalchemy import func

        rows = (
            db.query(
                PressureHourly.well_id,
                func.avg(PressureHourly.p_tube_avg).label("p_tube_avg"),
                func.avg(PressureHourly.p_line_avg).label("p_line_avg"),
                func.sum(PressureHourly.reading_count).label("reading_count"),
                func.max(PressureHourly.hour_start).label("last_hour"),
            )
            .filter(
                PressureHourly.well_id.in_(well_ids),
                PressureHourly.hour_start >= since,
            )
            .group_by(PressureHourly.well_id)
            .all()
        )

        result = {}
        for row in rows:
            p_tube = row.p_tube_avg
            p_line = row.p_line_avg
            p_diff = None
            if p_tube is not None and p_line is not None:
                p_diff = round(p_tube - p_line, 2)

            result[row.well_id] = {
                "p_tube_avg": round(p_tube, 2) if p_tube is not None else None,
                "p_line_avg": round(p_line, 2) if p_line is not None else None,
                "p_diff_avg": p_diff,
                "reading_count": row.reading_count or 0,
                "updated_at": row.last_hour,
                "has_data": p_tube is not None or p_line is not None,
            }
        return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    import sys
    if "--full" in sys.argv:
        result = aggregate_full_history()
    else:
        result = aggregate_to_hourly()

    print(f"\nAggregation complete: {result}")
