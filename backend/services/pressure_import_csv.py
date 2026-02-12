"""
pressure_import_csv — импорт CSV файлов давлений из LoRa → pressure.db

Формат CSV (CODESYS):
  - Строка 1: заголовок-имя файла (пропускается)
  - Строка 2: Дата;Время;Ptr_1;Pshl_1;...;Ptr_5;Pshl_5  (cp1251)
  - Строка 3+: 2026-01-01;05:00:04;16,5;15,9;...
  - sep=';', decimal=','
  - Время в UTC+5 (Узбекистан)
  - Невалидные: -1.0 (офлайн), -2.0 (ошибка)

Имя файла: DD.MM.YYYY.{группа}_arc.csv
  группа 1..6

Архитектура импорта:
  1. Для каждой колонки CSV (Ptr_1, Pshl_1, ...) ищем датчик по (csv_group, csv_channel, csv_column)
  2. По equipment_installation → equipment → lora_sensors находим well_id на момент измерения
  3. Position определяется прошивкой: csv_column 'Ptr' → p_tube, 'Pshl' → p_line

Оптимизации:
  - Tail-only: для повторного импорта свежих файлов пропускаем уже обработанные строки
  - Batch INSERT: собираем все строки и вставляем одним executemany
  - Возвращает affected_wells для целевой агрегации
"""

import hashlib
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db_pressure import PressureSessionLocal, init_pressure_db
from backend.models.csv_import_log import CsvImportLog

log = logging.getLogger(__name__)

# UTC+5 (Узбекистан)
TZ_OFFSET = timedelta(hours=5)
TZ_UZB = timezone(TZ_OFFSET)

# Невалидные значения давлений
INVALID_VALUES = {-1.0, -2.0}

# Регулярка для имени файла: DD.MM.YYYY.{группа}_arc.csv
FILENAME_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})\.(\d+)_arc\.csv$")

# SQL для batch INSERT
_INSERT_SQL = text(
    "INSERT OR IGNORE INTO pressure_readings "
    "(well_id, channel, measured_at, p_tube, p_line, "
    " sensor_id_tube, sensor_id_line, source, source_file) "
    "VALUES (:well_id, :channel, :measured_at, :p_tube, :p_line, "
    " :sensor_id_tube, :sensor_id_line, 'csv', :source_file)"
)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_filename(name: str) -> Optional[tuple[int, int, int, int]]:
    """Извлекает (день, месяц, год, группа) из имени файла."""
    m = FILENAME_RE.match(name)
    if not m:
        return None
    day, month, year, group = int(m[1]), int(m[2]), int(m[3]), int(m[4])
    return day, month, year, group


def _clean_pressure(val) -> Optional[float]:
    """Возвращает None если значение невалидное или отсутствует."""
    if pd.isna(val):
        return None
    v = float(val)
    if v in INVALID_VALUES:
        return None
    return round(v, 3)


def _load_sensor_cache() -> dict:
    """
    Загружает маппинг (csv_group, csv_channel, csv_column) → sensor_id.
    """
    from backend.db import engine as pg_engine
    cache = {}

    with pg_engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, csv_group, csv_channel, csv_column
            FROM lora_sensors
        """)).fetchall()

    for sensor_id, csv_group, csv_channel, csv_column in rows:
        cache[(csv_group, csv_channel, csv_column)] = sensor_id

    return cache


def _load_installation_cache() -> dict:
    """
    Загружает историю установок датчиков через equipment_installation.
    Единый источник: equipment_installation → equipment → lora_sensors.
    Position определяется прошивкой: csv_column 'Ptr' → tube, 'Pshl' → line.

    Возвращает {sensor_id: [(installed_at, removed_at, well_id, position), ...]}
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


def _find_installation(
    sensor_id: int,
    measured_at: datetime,
    installation_cache: dict
) -> Optional[tuple[int, str]]:
    """
    Находит установку датчика на момент измерения.
    При перекрытии интервалов побеждает ПОСЛЕДНЯЯ установка (с наибольшим installed_at).

    ВАЖНО: measured_at хранится в UTC, а installed_at/removed_at в БД хранятся
    в локальном времени (Кунград UTC+5, из форм или datetime.now()).
    Конвертируем measured_at из UTC → UTC+5 для корректного сравнения.

    Возвращает (well_id, position) или None.
    """
    intervals = installation_cache.get(sensor_id, [])
    if not intervals:
        return None

    # Конвертируем UTC → Кунградское время (UTC+5) для сравнения с installed_at
    measured_local = measured_at + TZ_OFFSET

    # Обратный порядок: последняя установка проверяется первой
    for installed_at, removed_at, well_id, position in reversed(intervals):
        if installed_at and measured_local < installed_at:
            continue
        if removed_at and measured_local > removed_at:
            continue
        return (well_id, position)

    return None


def _ensure_log_schema(db: Session):
    """Добавляет новые колонки в csv_import_log если их нет (SQLite миграция)."""
    try:
        db.execute(text("ALTER TABLE csv_import_log ADD COLUMN rows_in_file INTEGER"))
        db.commit()
    except Exception:
        db.rollback()


def import_csv_file(
    csv_path: Path,
    db: Session,
    sensor_cache: dict,
    installation_cache: dict,
) -> dict:
    """
    Импортирует один CSV файл в pressure_readings.

    Returns: {"status": "imported"/"skipped"/"failed", "rows_imported": N,
              "affected_wells": set[int], "first_ts": datetime|None, ...}
    """
    filename = csv_path.name
    sha256 = _file_sha256(csv_path)

    # Проверяем журнал — был ли уже импортирован с таким же хешем
    existing = db.query(CsvImportLog).filter_by(filename=filename).first()
    if existing and existing.file_sha256 == sha256 and existing.status == "imported":
        # Свежие файлы (за последние 7 дней) — всегда реимпортируем
        parsed = _parse_filename(filename)
        is_recent = False
        if parsed:
            try:
                file_date = datetime(parsed[2], parsed[1], parsed[0]).date()
                is_recent = (datetime.now().date() - file_date).days < 7
            except ValueError:
                pass
        if not is_recent:
            return {"status": "skipped", "reason": "already imported, same hash",
                    "rows_imported": 0, "affected_wells": set()}

    # Парсим имя файла
    parsed = _parse_filename(filename)
    if not parsed:
        log.warning("Cannot parse filename: %s", filename)
        return {"status": "failed", "reason": f"bad filename: {filename}",
                "rows_imported": 0, "affected_wells": set()}

    _day, _month, _year, csv_group = parsed

    # Читаем CSV
    try:
        df = pd.read_csv(
            csv_path,
            sep=";",
            decimal=",",
            encoding="cp1251",
            skiprows=1,      # пропускаем строку-заголовок (имя файла)
            na_values=[],
        )
    except Exception as e:
        log.error("Failed to read %s: %s", filename, e)
        _update_log(db, filename, sha256, "failed", error=str(e))
        return {"status": "failed", "reason": str(e),
                "rows_imported": 0, "affected_wells": set()}

    if df.empty:
        _update_log(db, filename, sha256, "imported", rows=0, skipped=0, rows_in_file=0)
        return {"status": "imported", "rows_imported": 0, "affected_wells": set()}

    total_rows = len(df)

    # === Tail-only оптимизация ===
    # Если файл уже импортировался и стал длиннее — обрабатываем только новые строки.
    # CODESYS только дописывает строки, не изменяет старые.
    tail_offset = 0
    if (existing
            and existing.status == "imported"
            and existing.rows_in_file is not None
            and existing.rows_in_file > 0):
        if total_rows > existing.rows_in_file:
            tail_offset = existing.rows_in_file
            log.info("  %s: tail-only, skip %d → process %d new rows",
                     filename, tail_offset, total_rows - tail_offset)
        elif total_rows == existing.rows_in_file and existing.file_sha256 == sha256:
            # Тот же размер и хеш — нечего делать (но мы уже прошли проверку выше
            # для не-recent файлов, значит это recent файл с тем же содержимым)
            _update_log(db, filename, sha256, "imported",
                        rows=0, skipped=0, rows_in_file=total_rows)
            return {"status": "imported", "rows_imported": 0, "affected_wells": set(),
                    "reason": "recent file, no new rows"}

    df_to_process = df.iloc[tail_offset:] if tail_offset > 0 else df

    # Столбцы: Дата, Время, Ptr_1, Pshl_1, Ptr_2, Pshl_2, ..., Ptr_5, Pshl_5
    date_col = df.columns[0]
    time_col = df.columns[1]

    # === Сбор параметров для batch INSERT ===
    insert_params = []
    affected_wells = set()
    rows_skipped = 0
    first_ts = None
    last_ts = None

    for _, row in df_to_process.iterrows():
        # Парсим дату+время (формат: 2026-01-01 + 05:00:04), timezone UTC+5
        try:
            dt_str = f"{row[date_col]} {row[time_col]}"
            dt_local = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            dt_utc = dt_local.replace(tzinfo=TZ_UZB).astimezone(timezone.utc).replace(tzinfo=None)
        except (ValueError, TypeError):
            rows_skipped += 1
            continue

        if first_ts is None:
            first_ts = dt_utc
        last_ts = dt_utc

        # Собираем данные по скважинам: well_id → {'tube': value, 'line': value}
        well_data = defaultdict(dict)
        well_sensors = defaultdict(dict)  # well_id → {'tube': sensor_id, 'line': sensor_id}
        well_channels = {}  # well_id → csv_channel (для колонки channel в БД)

        # Обрабатываем все колонки давлений
        for csv_channel in range(1, 6):
            for csv_column in ['Ptr', 'Pshl']:
                col_name = f"{csv_column}_{csv_channel}"

                if col_name not in df.columns:
                    continue

                value = _clean_pressure(row.get(col_name))
                if value is None:
                    continue

                # Ищем датчик по прошивке
                sensor_id = sensor_cache.get((csv_group, csv_channel, csv_column))
                if sensor_id is None:
                    continue

                # Ищем установку на момент измерения
                installation = _find_installation(sensor_id, dt_utc, installation_cache)
                if installation is None:
                    continue

                well_id, position = installation
                well_data[well_id][position] = value
                well_sensors[well_id][position] = sensor_id
                well_channels[well_id] = (csv_group - 1) * 5 + csv_channel

        # Формируем параметры INSERT для каждой скважины
        for well_id, positions in well_data.items():
            p_tube = positions.get('tube')
            p_line = positions.get('line')

            if p_tube is None and p_line is None:
                continue

            insert_params.append({
                "well_id": well_id,
                "channel": well_channels.get(well_id, 1),
                "measured_at": dt_utc,
                "p_tube": p_tube,
                "p_line": p_line,
                "sensor_id_tube": well_sensors[well_id].get('tube'),
                "sensor_id_line": well_sensors[well_id].get('line'),
                "source_file": filename,
            })
            affected_wells.add(well_id)

    # === Batch INSERT (executemany) ===
    rows_imported = 0
    if insert_params:
        try:
            db.execute(_INSERT_SQL, insert_params)
            db.commit()
            rows_imported = len(insert_params)
        except Exception as e:
            log.error("Batch INSERT failed for %s: %s", filename, e)
            db.rollback()
            _update_log(db, filename, sha256, "failed", error=str(e),
                        rows_in_file=total_rows)
            return {"status": "failed", "reason": str(e),
                    "rows_imported": 0, "affected_wells": set()}
    else:
        db.commit()

    _update_log(
        db, filename, sha256, "imported",
        rows=rows_imported, skipped=rows_skipped,
        first_ts=first_ts, last_ts=last_ts,
        rows_in_file=total_rows,
    )

    return {
        "status": "imported",
        "rows_imported": rows_imported,
        "rows_skipped": rows_skipped,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "affected_wells": affected_wells,
    }


def _update_log(
    db: Session,
    filename: str,
    sha256: str,
    status: str,
    rows: int = 0,
    skipped: int = 0,
    first_ts=None,
    last_ts=None,
    error: str = None,
    rows_in_file: int = None,
):
    existing = db.query(CsvImportLog).filter_by(filename=filename).first()
    now = datetime.utcnow()
    if existing:
        existing.file_sha256 = sha256
        existing.status = status
        existing.rows_imported = rows
        existing.rows_skipped = skipped
        existing.first_timestamp = first_ts
        existing.last_timestamp = last_ts
        existing.imported_at = now
        existing.error_message = error
        if rows_in_file is not None:
            existing.rows_in_file = rows_in_file
    else:
        db.add(CsvImportLog(
            filename=filename,
            file_sha256=sha256,
            status=status,
            rows_imported=rows,
            rows_skipped=skipped,
            first_timestamp=first_ts,
            last_timestamp=last_ts,
            imported_at=now,
            error_message=error,
            rows_in_file=rows_in_file,
        ))
    db.commit()


def import_all_csv(
    csv_dir: Optional[Path] = None,
    limit: Optional[int] = None,
) -> dict:
    """
    Импортирует все CSV файлы из директории.
    Возвращает сводку: {"total_files": N, "imported": N, "skipped": N, "failed": N,
                        "affected_well_ids": set, "min_timestamp": datetime}
    """
    if csv_dir is None:
        csv_dir = Path(__file__).resolve().parent.parent.parent / "data" / "lora"

    init_pressure_db()
    sensor_cache = _load_sensor_cache()
    installation_cache = _load_installation_cache()

    log.info("Loaded %d sensors, %d with installations",
             len(sensor_cache), len(installation_cache))

    csv_files = sorted(csv_dir.glob("*_arc.csv"))
    if limit:
        csv_files = csv_files[:limit]

    log.info("Found %d CSV files in %s", len(csv_files), csv_dir)

    db = PressureSessionLocal()

    # Миграция SQLite: добавляем новые колонки если их нет
    _ensure_log_schema(db)

    summary = {"total_files": len(csv_files), "imported": 0, "skipped": 0, "failed": 0}
    total_rows = 0
    all_affected_wells = set()
    min_timestamp = None

    try:
        for i, fpath in enumerate(csv_files, 1):
            result = import_csv_file(fpath, db, sensor_cache, installation_cache)
            status = result["status"]
            summary[status] = summary.get(status, 0) + 1
            total_rows += result.get("rows_imported", 0)

            # Собираем affected data для целевой агрегации
            file_wells = result.get("affected_wells", set())
            all_affected_wells.update(file_wells)

            file_first_ts = result.get("first_ts")
            if isinstance(file_first_ts, datetime):
                if min_timestamp is None or file_first_ts < min_timestamp:
                    min_timestamp = file_first_ts

            if i % 10 == 0 or i == len(csv_files):
                log.info(
                    "Progress: %d/%d files, %d rows imported",
                    i, len(csv_files), total_rows,
                )
    finally:
        db.close()

    summary["total_rows_imported"] = total_rows
    summary["affected_well_ids"] = all_affected_wells
    summary["min_timestamp"] = min_timestamp
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = import_all_csv()
    print(f"\nImport complete: {result}")
