"""
pressure_reassign_service — пересчёт well_id и заполнение sensor_ids.

Функции:
  1. backfill_sensor_ids()  — заполнить sensor_id_tube/line для старых данных (обратный поиск)
  2. reassign_well_ids()    — пересчитать well_id по текущим датам установки
  3. reassign_all()         — полный пересчёт (для импорта данных за 2025 год)

Порядок при пересчёте: сначала SQLite, потом PostgreSQL — чтобы sync не создал дубли.
"""

import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

from sqlalchemy import text

from backend.db_pressure import PressureSessionLocal, init_pressure_db

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════

def _load_installation_cache() -> dict:
    """
    {sensor_id: [(installed_at, removed_at, well_id, position), ...]}
    Переиспользует логику из pressure_import_csv.
    """
    from backend.db import engine as pg_engine

    cache = {}
    with pg_engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT ls.id AS sensor_id,
                   ei.well_id,
                   ls.csv_column,
                   ei.installed_at,
                   ei.removed_at
            FROM equipment_installation ei
            JOIN equipment e ON e.id = ei.equipment_id
            JOIN lora_sensors ls ON ls.serial_number = e.serial_number
            ORDER BY ls.id, ei.installed_at
        """)).fetchall()

    for sensor_id, well_id, csv_column, installed_at, removed_at in rows:
        position = 'tube' if csv_column == 'Ptr' else 'line'
        cache.setdefault(sensor_id, []).append(
            (installed_at, removed_at, well_id, position)
        )
    return cache


def _find_installation(sensor_id, measured_at, cache):
    """Находит (well_id, position) для датчика на момент измерения."""
    intervals = cache.get(sensor_id, [])
    for installed_at, removed_at, well_id, position in reversed(intervals):
        if installed_at and measured_at < installed_at:
            continue
        if removed_at and measured_at > removed_at:
            continue
        return (well_id, position)
    return None


def _make_pg_engine():
    from backend.db import engine as pg_engine
    return pg_engine


def _build_reverse_cache(installation_cache):
    """
    Обратный кэш: {(well_id, position): [(installed_at, removed_at, sensor_id), ...]}
    Для backfill — по (well_id, position, measured_at) найти sensor_id.
    """
    reverse = defaultdict(list)
    for sensor_id, intervals in installation_cache.items():
        for installed_at, removed_at, well_id, position in intervals:
            reverse[(well_id, position)].append(
                (installed_at, removed_at, sensor_id)
            )
    # Сортируем по installed_at для каждого ключа
    for key in reverse:
        reverse[key].sort(key=lambda x: x[0] if x[0] else datetime.min)
    return reverse


def _find_sensor_reverse(well_id, position, measured_at, reverse_cache):
    """Обратный поиск: по (well_id, position, time) → sensor_id."""
    intervals = reverse_cache.get((well_id, position), [])
    for installed_at, removed_at, sensor_id in reversed(intervals):
        if installed_at and measured_at < installed_at:
            continue
        if removed_at and measured_at > removed_at:
            continue
        return sensor_id
    return None


# ═══════════════════════════════════════════════════════════
# 1. Backfill sensor_ids
# ═══════════════════════════════════════════════════════════

def backfill_sensor_ids(
    batch_size: int = 10000,
    well_ids: Optional[list[int]] = None,
) -> dict:
    """
    Заполняет sensor_id_tube и sensor_id_line для строк, где они NULL.
    Обратный поиск: по (well_id, measured_at) находит какой датчик был установлен.

    Returns: {"rows_checked": N, "rows_updated": N, "rows_no_sensor": N}
    """
    init_pressure_db()

    installation_cache = _load_installation_cache()
    reverse_cache = _build_reverse_cache(installation_cache)

    log.info("Backfill: loaded %d sensor installations", len(installation_cache))

    # Запрашиваем строки без sensor_ids
    where_parts = [
        "(sensor_id_tube IS NULL OR sensor_id_line IS NULL)",
        "(p_tube IS NOT NULL OR p_line IS NOT NULL)",
    ]
    if well_ids:
        wid_csv = ",".join(str(int(w)) for w in well_ids)
        where_parts.append(f"well_id IN ({wid_csv})")

    where_sql = " AND ".join(where_parts)

    db = PressureSessionLocal()
    rows_checked = 0
    rows_updated = 0
    rows_no_sensor = 0

    try:
        total = db.execute(text(
            f"SELECT COUNT(*) FROM pressure_readings WHERE {where_sql}"
        )).scalar()
        log.info("Backfill: %d rows to process", total)

        offset = 0
        while True:
            rows = db.execute(text(f"""
                SELECT id, well_id, measured_at, p_tube, p_line,
                       sensor_id_tube, sensor_id_line
                FROM pressure_readings
                WHERE {where_sql}
                ORDER BY id
                LIMIT :limit OFFSET :offset
            """), {"limit": batch_size, "offset": offset}).fetchall()

            if not rows:
                break

            updates = []
            for row_id, wid, meas_at, p_tube, p_line, sid_tube, sid_line in rows:
                rows_checked += 1
                new_tube = sid_tube
                new_line = sid_line

                if sid_tube is None and p_tube is not None:
                    found = _find_sensor_reverse(wid, 'tube', meas_at, reverse_cache)
                    if found:
                        new_tube = found

                if sid_line is None and p_line is not None:
                    found = _find_sensor_reverse(wid, 'line', meas_at, reverse_cache)
                    if found:
                        new_line = found

                if new_tube == sid_tube and new_line == sid_line:
                    if (sid_tube is None and p_tube is not None) or \
                       (sid_line is None and p_line is not None):
                        rows_no_sensor += 1
                    continue

                updates.append({
                    "id": row_id,
                    "sensor_id_tube": new_tube,
                    "sensor_id_line": new_line,
                })

            if updates:
                db.execute(
                    text("""
                        UPDATE pressure_readings
                        SET sensor_id_tube = :sensor_id_tube,
                            sensor_id_line = :sensor_id_line
                        WHERE id = :id
                    """),
                    updates,
                )
                db.commit()
                rows_updated += len(updates)

            offset += batch_size
            if rows_checked % 50000 == 0:
                log.info("  backfill progress: %d/%d checked, %d updated",
                         rows_checked, total, rows_updated)

    finally:
        db.close()

    log.info("Backfill complete: checked=%d, updated=%d, no_sensor=%d",
             rows_checked, rows_updated, rows_no_sensor)

    # Синхронизируем обновлённые sensor_ids в PostgreSQL
    _sync_sensor_ids_to_pg(well_ids=well_ids)

    return {
        "rows_checked": rows_checked,
        "rows_updated": rows_updated,
        "rows_no_sensor": rows_no_sensor,
    }


def _sync_sensor_ids_to_pg(well_ids: Optional[list[int]] = None):
    """Обновляет sensor_ids в pressure_raw из SQLite (только NOT NULL значения)."""
    db = PressureSessionLocal()
    pg_engine = _make_pg_engine()

    where_parts = ["(sensor_id_tube IS NOT NULL OR sensor_id_line IS NOT NULL)"]
    if well_ids:
        wid_csv = ",".join(str(int(w)) for w in well_ids)
        where_parts.append(f"well_id IN ({wid_csv})")

    where_sql = " AND ".join(where_parts)

    try:
        rows = db.execute(text(f"""
            SELECT well_id, measured_at, sensor_id_tube, sensor_id_line
            FROM pressure_readings
            WHERE {where_sql}
        """)).fetchall()
    finally:
        db.close()

    if not rows:
        return

    log.info("Syncing %d sensor_id updates to PostgreSQL", len(rows))

    update_sql = text("""
        UPDATE pressure_raw
        SET sensor_id_tube = :sensor_id_tube,
            sensor_id_line = :sensor_id_line
        WHERE well_id = :well_id AND measured_at = :measured_at
    """)

    batch_size = 5000
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        params = [
            {
                "well_id": r[0],
                "measured_at": r[1],
                "sensor_id_tube": r[2],
                "sensor_id_line": r[3],
            }
            for r in batch
        ]
        with pg_engine.begin() as conn:
            conn.execute(update_sql, params)

    log.info("Sensor_id sync to PG complete: %d rows", len(rows))


# ═══════════════════════════════════════════════════════════
# 2. Reassign well_ids
# ═══════════════════════════════════════════════════════════

def reassign_well_ids(
    sensor_ids: Optional[list[int]] = None,
    dry_run: bool = False,
    batch_size: int = 5000,
) -> dict:
    """
    Пересчитывает well_id для строк с ненулевыми sensor_ids.

    Args:
        sensor_ids: Список sensor_id для пересчёта. None = все.
        dry_run: True = только подсчитать, не менять.

    Returns: {"rows_checked": N, "rows_changed": N, "errors": N}
    """
    init_pressure_db()

    installation_cache = _load_installation_cache()
    log.info("Reassign: loaded %d sensor installations", len(installation_cache))

    # Запрашиваем строки с sensor_ids
    where_parts = ["(sensor_id_tube IS NOT NULL OR sensor_id_line IS NOT NULL)"]
    if sensor_ids:
        sid_csv = ",".join(str(int(s)) for s in sensor_ids)
        where_parts.append(
            f"(sensor_id_tube IN ({sid_csv}) OR sensor_id_line IN ({sid_csv}))"
        )

    where_sql = " AND ".join(where_parts)

    db = PressureSessionLocal()
    pg_engine = _make_pg_engine()

    rows_checked = 0
    rows_changed = 0
    errors = 0

    try:
        total = db.execute(text(
            f"SELECT COUNT(*) FROM pressure_readings WHERE {where_sql}"
        )).scalar()
        log.info("Reassign: %d rows to check", total)

        offset = 0
        while True:
            rows = db.execute(text(f"""
                SELECT id, well_id, measured_at,
                       sensor_id_tube, sensor_id_line,
                       p_tube, p_line
                FROM pressure_readings
                WHERE {where_sql}
                ORDER BY id
                LIMIT :limit OFFSET :offset
            """), {"limit": batch_size, "offset": offset}).fetchall()

            if not rows:
                break

            sqlite_updates = []     # (id, old_well_id, new_well_id, measured_at)
            pg_updates = []         # same

            for row_id, old_wid, meas_at, sid_tube, sid_line, p_tube, p_line in rows:
                rows_checked += 1

                # Определяем правильный well_id из tube-датчика (приоритет),
                # fallback на line-датчик
                new_wid = None
                if sid_tube:
                    result = _find_installation(sid_tube, meas_at, installation_cache)
                    if result:
                        new_wid = result[0]

                if new_wid is None and sid_line:
                    result = _find_installation(sid_line, meas_at, installation_cache)
                    if result:
                        new_wid = result[0]

                if new_wid is None or new_wid == old_wid:
                    continue

                sqlite_updates.append({
                    "id": row_id,
                    "old_well_id": old_wid,
                    "new_well_id": new_wid,
                    "measured_at": meas_at,
                    "p_tube": p_tube,
                    "p_line": p_line,
                    "sensor_id_tube": sid_tube,
                    "sensor_id_line": sid_line,
                })

            if sqlite_updates and not dry_run:
                changed, errs = _apply_reassignment(
                    db, pg_engine, sqlite_updates
                )
                rows_changed += changed
                errors += errs
            elif sqlite_updates and dry_run:
                rows_changed += len(sqlite_updates)

            offset += batch_size
            if rows_checked % 50000 == 0:
                log.info("  reassign progress: %d/%d checked, %d changed",
                         rows_checked, total, rows_changed)

    finally:
        db.close()

    log.info("Reassign complete: checked=%d, changed=%d, errors=%d, dry_run=%s",
             rows_checked, rows_changed, errors, dry_run)

    return {
        "rows_checked": rows_checked,
        "rows_changed": rows_changed,
        "errors": errors,
        "dry_run": dry_run,
    }


def _apply_reassignment(db, pg_engine, updates: list) -> tuple[int, int]:
    """
    Применяет пересчёт well_id в SQLite и PostgreSQL.

    Порядок: SQLite → PostgreSQL (чтобы sync не создал дубли).

    Для каждой строки:
      1. Удалить конфликтную строку в SQLite (если есть) с (new_well_id, measured_at)
      2. UPDATE well_id в SQLite
      3. DELETE + INSERT в PostgreSQL (меняем уникальный ключ)

    Returns: (changed_count, error_count)
    """
    changed = 0
    errors = 0

    # === SQLite: update well_id ===
    for upd in updates:
        try:
            # Удалить потенциальный конфликт
            db.execute(text("""
                DELETE FROM pressure_readings
                WHERE well_id = :new_well_id
                  AND measured_at = :measured_at
                  AND id != :id
            """), {
                "new_well_id": upd["new_well_id"],
                "measured_at": upd["measured_at"],
                "id": upd["id"],
            })
            # Обновить well_id
            db.execute(text("""
                UPDATE pressure_readings
                SET well_id = :new_well_id
                WHERE id = :id
            """), {
                "new_well_id": upd["new_well_id"],
                "id": upd["id"],
            })
        except Exception as e:
            log.warning("SQLite reassign error for id=%d: %s", upd["id"], e)
            db.rollback()
            errors += 1
            continue

    db.commit()

    # === PostgreSQL: delete old + insert new ===
    try:
        with pg_engine.begin() as conn:
            for upd in updates:
                # Удалить старую строку
                conn.execute(text("""
                    DELETE FROM pressure_raw
                    WHERE well_id = :old_well_id AND measured_at = :measured_at
                """), {
                    "old_well_id": upd["old_well_id"],
                    "measured_at": upd["measured_at"],
                })
                # Удалить потенциальный конфликт в новом well_id
                conn.execute(text("""
                    DELETE FROM pressure_raw
                    WHERE well_id = :new_well_id AND measured_at = :measured_at
                """), {
                    "new_well_id": upd["new_well_id"],
                    "measured_at": upd["measured_at"],
                })
                # Вставить с новым well_id
                conn.execute(text("""
                    INSERT INTO pressure_raw
                        (well_id, measured_at, p_tube, p_line,
                         sensor_id_tube, sensor_id_line)
                    VALUES
                        (:well_id, :measured_at, :p_tube, :p_line,
                         :sensor_id_tube, :sensor_id_line)
                """), {
                    "well_id": upd["new_well_id"],
                    "measured_at": upd["measured_at"],
                    "p_tube": upd["p_tube"],
                    "p_line": upd["p_line"],
                    "sensor_id_tube": upd["sensor_id_tube"],
                    "sensor_id_line": upd["sensor_id_line"],
                })
                changed += 1
    except Exception as e:
        log.error("PostgreSQL reassign batch error: %s", e)
        errors += len(updates) - changed

    return changed, errors


# ═══════════════════════════════════════════════════════════
# 3. Reassign all
# ═══════════════════════════════════════════════════════════

def reassign_all(dry_run: bool = False) -> dict:
    """
    Полный пересчёт well_id для ВСЕХ строк с ненулевыми sensor_ids.
    Тяжёлая операция — для импорта данных за 2025 год.
    """
    return reassign_well_ids(sensor_ids=None, dry_run=dry_run)
