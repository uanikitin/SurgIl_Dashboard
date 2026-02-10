"""
pressure_import_sqlite — импорт из CODESYS Tracing SQLite → pressure.db

Формат Tracing SQLite:
  - 6 файлов: Trend{1-6}.sqlite
  - TblTrendData: TS (μs Unix epoch UTC), Val1..Val31 (IEEE 754 double as int64)
  - Интервал 30 секунд, ротация ~17 дней

Маппинг Val → давления:
  Для каждого Trend (группа g = 1..6), offset = (g-1)*5:
    Скважина 1 (idx=1): Ptr = Val31, Pshl = Val1
    Скважина 2 (idx=2): Ptr = Val2,  Pshl = Val3
    Скважина 3 (idx=3): Ptr = Val4,  Pshl = Val5
    Скважина 4 (idx=4): Ptr = Val6,  Pshl = Val7
    Скважина 5 (idx=5): Ptr = Val8,  Pshl = Val9
  channel = (g-1)*5 + idx

Невалидные значения: < -0.5 (отрицательные давления = ошибка/офлайн)
"""

import logging
import math
import struct
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db_pressure import PressureSessionLocal, init_pressure_db
from backend.models.sqlite_import_state import SqliteImportState

log = logging.getLogger(__name__)

# Невалидные: отрицательные давления
MIN_VALID_PRESSURE = -0.5

# Маппинг Val-столбцов к парам Ptr/Pshl для каждой скважины (idx 1..5)
# idx 1: Ptr=Val31, Pshl=Val1
# idx 2: Ptr=Val2,  Pshl=Val3
# idx 3: Ptr=Val4,  Pshl=Val5
# idx 4: Ptr=Val6,  Pshl=Val7
# idx 5: Ptr=Val8,  Pshl=Val9
WELL_COLUMN_MAP = {
    1: ("Val31", "Val1"),   # Ptr, Pshl
    2: ("Val2", "Val3"),
    3: ("Val4", "Val5"),
    4: ("Val6", "Val7"),
    5: ("Val8", "Val9"),
}


def _decode_float(int_val) -> Optional[float]:
    """Декодирует IEEE 754 double из int64."""
    if int_val is None:
        return None
    try:
        val = struct.unpack('d', struct.pack('q', int(int_val)))[0]
        # Фильтруем невалидные значения: отрицательные, infinity, NaN
        if val < MIN_VALID_PRESSURE or math.isinf(val) or math.isnan(val):
            return None
        return round(val, 3)
    except (struct.error, ValueError, OverflowError):
        return None


def _ts_to_datetime(ts_us: int) -> datetime:
    """Конвертирует timestamp в микросекундах (UTC) → datetime."""
    return datetime.utcfromtimestamp(ts_us / 1_000_000)


def _load_channel_cache() -> dict:
    """Загружает маппинг channel → well_id из PostgreSQL."""
    from backend.db import engine as pg_engine
    cache = {}
    with pg_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT channel, well_id, started_at, ended_at "
            "FROM well_channels ORDER BY channel, started_at"
        )).fetchall()
    for ch, well_id, started, ended in rows:
        cache.setdefault(ch, []).append((started, ended, well_id))
    return cache


def _resolve_well_id(channel: int, measured_at: datetime, channel_cache: dict) -> Optional[int]:
    """Определяет well_id по каналу и моменту времени."""
    intervals = channel_cache.get(channel, [])
    for started, ended, well_id in intervals:
        if started and measured_at < started:
            continue
        if ended and measured_at > ended:
            continue
        return well_id
    for started, ended, well_id in intervals:
        if ended is None:
            return well_id
    return None


def import_trend_file(
    trend_num: int,
    sqlite_path: Path,
    db: Session,
    channel_cache: dict,
) -> dict:
    """
    Импортирует один Trend файл в pressure_readings.

    Args:
        trend_num: Номер тренда (1-6)
        sqlite_path: Путь к Trend{N}.sqlite
        db: Сессия pressure.db
        channel_cache: Маппинг channel → well_id

    Returns: {"status": ..., "rows_imported": N, ...}
    """
    trend_name = f"Trend{trend_num}"
    group = trend_num  # группа = номер тренда

    if not sqlite_path.exists():
        return {"status": "skipped", "reason": f"{sqlite_path.name} not found"}

    # Получаем last_ts из tracing_import_state
    state = db.query(SqliteImportState).filter_by(trend_name=trend_name).first()
    last_ts = state.last_ts if state else 0

    # Подключаемся к Tracing SQLite (read-write для REINDEX)
    # Файлы скачиваются с live-базы и могут иметь повреждённые индексы
    try:
        src_conn = sqlite3.connect(str(sqlite_path))
        src_cursor = src_conn.cursor()
        # Восстанавливаем индексы (типичная проблема при копировании live SQLite)
        try:
            src_cursor.execute("REINDEX")
        except sqlite3.Error:
            pass
    except sqlite3.Error as e:
        log.error("Cannot open %s: %s", sqlite_path, e)
        return {"status": "failed", "reason": str(e)}

    # Определяем нужные столбцы
    val_columns = set()
    for ptr_col, pshl_col in WELL_COLUMN_MAP.values():
        val_columns.add(ptr_col)
        val_columns.add(pshl_col)
    val_columns_sorted = sorted(val_columns, key=lambda x: int(x.replace("Val", "")))

    columns_sql = ", ".join(["TS"] + val_columns_sorted)
    query = f"SELECT {columns_sql} FROM TblTrendData WHERE TS > ? ORDER BY TS"

    try:
        src_cursor.execute(query, (last_ts,))
    except sqlite3.Error as e:
        src_conn.close()
        log.error("Query failed on %s: %s", trend_name, e)
        return {"status": "failed", "reason": str(e)}

    rows_imported = 0
    rows_skipped = 0
    max_ts = last_ts
    batch_count = 0

    while True:
        rows = src_cursor.fetchmany(5000)
        if not rows:
            break

        for row in rows:
            # Создаём dict из row
            row_dict = dict(zip(["TS"] + val_columns_sorted, row))
            ts = row_dict["TS"]
            if ts > max_ts:
                max_ts = ts

            dt_utc = _ts_to_datetime(ts)

            for idx in range(1, 6):
                ptr_col, pshl_col = WELL_COLUMN_MAP[idx]
                p_tube = _decode_float(row_dict.get(ptr_col))
                p_line = _decode_float(row_dict.get(pshl_col))

                if p_tube is None and p_line is None:
                    rows_skipped += 1
                    continue

                channel = (group - 1) * 5 + idx
                well_id = _resolve_well_id(channel, dt_utc, channel_cache)

                if well_id is None:
                    rows_skipped += 1
                    continue

                try:
                    db.execute(
                        text(
                            "INSERT OR IGNORE INTO pressure_readings "
                            "(well_id, channel, measured_at, p_tube, p_line, source, source_file) "
                            "VALUES (:well_id, :channel, :measured_at, :p_tube, :p_line, 'sqlite', :source_file)"
                        ),
                        {
                            "well_id": well_id,
                            "channel": channel,
                            "measured_at": dt_utc,
                            "p_tube": p_tube,
                            "p_line": p_line,
                            "source_file": f"{trend_name}.sqlite",
                        },
                    )
                    rows_imported += 1
                except Exception:
                    rows_skipped += 1

        batch_count += 1
        if batch_count % 5 == 0:
            db.commit()

    db.commit()
    src_conn.close()

    # Обновляем tracing_import_state
    now = datetime.utcnow()
    if state:
        state.last_ts = max_ts
        state.rows_imported_total = (state.rows_imported_total or 0) + rows_imported
        state.updated_at = now
    else:
        db.add(SqliteImportState(
            trend_name=trend_name,
            last_ts=max_ts,
            rows_imported_total=rows_imported,
            updated_at=now,
        ))
    db.commit()

    return {
        "status": "imported",
        "trend": trend_name,
        "rows_imported": rows_imported,
        "rows_skipped": rows_skipped,
        "last_ts": max_ts,
    }


def import_all_sqlite(
    sqlite_dir: Optional[Path] = None,
    trends: Optional[list[int]] = None,
) -> dict:
    """
    Импортирует все Trend файлы из директории.

    Args:
        sqlite_dir: Путь к каталогу с Trend*.sqlite
        trends: Список номеров трендов (по умолчанию [1,2] — активные)

    Returns: Сводка
    """
    if sqlite_dir is None:
        sqlite_dir = Path(__file__).resolve().parent.parent.parent / "data" / "lora_sqlite"

    if trends is None:
        trends = [1, 2]   # Только активные группы

    init_pressure_db()
    channel_cache = _load_channel_cache()

    db = PressureSessionLocal()
    summary = {"total_trends": len(trends), "imported": 0, "skipped": 0, "failed": 0}
    total_rows = 0

    try:
        for trend_num in trends:
            sqlite_path = sqlite_dir / f"Trend{trend_num}.sqlite"
            log.info("Importing %s...", sqlite_path.name)

            result = import_trend_file(trend_num, sqlite_path, db, channel_cache)
            status = result["status"]

            if status == "imported":
                summary["imported"] += 1
                total_rows += result.get("rows_imported", 0)
                log.info(
                    "  %s: %d imported, %d skipped",
                    result.get("trend"), result.get("rows_imported"), result.get("rows_skipped"),
                )
            elif status == "skipped":
                summary["skipped"] += 1
                log.info("  Trend%d skipped: %s", trend_num, result.get("reason"))
            else:
                summary["failed"] += 1
                log.error("  Trend%d failed: %s", trend_num, result.get("reason"))
    finally:
        db.close()

    summary["total_rows_imported"] = total_rows
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = import_all_sqlite()
    print(f"\nImport complete: {result}")
