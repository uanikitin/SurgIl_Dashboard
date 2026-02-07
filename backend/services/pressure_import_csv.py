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
  группа 1..6, каждая группа — 5 каналов
  channel = (группа - 1) * 5 + индекс (индекс = 1..5)
"""

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db_pressure import PressureSessionLocal, init_pressure_db
from backend.models.pressure_reading import PressureReading
from backend.models.csv_import_log import CsvImportLog

log = logging.getLogger(__name__)

# UTC+5 (Узбекистан)
TZ_OFFSET = timedelta(hours=5)
TZ_UZB = timezone(TZ_OFFSET)

# Невалидные значения давлений
INVALID_VALUES = {-1.0, -2.0}

# Регулярка для имени файла: DD.MM.YYYY.{группа}_arc.csv
FILENAME_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})\.(\d+)_arc\.csv$")


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


def _resolve_well_id(
    channel: int, measured_at: datetime, channel_cache: dict
) -> Optional[int]:
    """
    Определяет well_id по каналу и моменту времени.
    Использует кеш channel_cache: {channel: [(started, ended, well_id), ...]}
    """
    intervals = channel_cache.get(channel, [])
    for started, ended, well_id in intervals:
        if started and measured_at < started:
            continue
        if ended and measured_at > ended:
            continue
        return well_id
    # Если нет активного интервала — берём последний (ended=None)
    for started, ended, well_id in intervals:
        if ended is None:
            return well_id
    return None


def _load_channel_cache(pg_url: Optional[str] = None) -> dict:
    """
    Загружает маппинг channel → well_id из PostgreSQL.
    Возвращает {channel: [(started_at, ended_at, well_id), ...]}
    """
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


def import_csv_file(
    csv_path: Path,
    db: Session,
    channel_cache: dict,
) -> dict:
    """
    Импортирует один CSV файл в pressure_readings.

    Returns: {"status": "imported"/"skipped"/"failed", "rows_imported": N, ...}
    """
    filename = csv_path.name
    sha256 = _file_sha256(csv_path)

    # Проверяем журнал — был ли уже импортирован с таким же хешем
    existing = db.query(CsvImportLog).filter_by(filename=filename).first()
    if existing and existing.file_sha256 == sha256 and existing.status == "imported":
        # Свежие файлы (за последние 2 дня) — всегда реимпортируем,
        # т.к. Pi дописывает данные в CSV, а INSERT OR IGNORE защитит от дублей
        parsed = _parse_filename(filename)
        is_recent = False
        if parsed:
            try:
                file_date = datetime(parsed[2], parsed[1], parsed[0]).date()
                is_recent = (datetime.now().date() - file_date).days < 7
            except ValueError:
                pass
        if not is_recent:
            return {"status": "skipped", "reason": "already imported, same hash"}

    # Парсим имя файла
    parsed = _parse_filename(filename)
    if not parsed:
        log.warning("Cannot parse filename: %s", filename)
        return {"status": "failed", "reason": f"bad filename: {filename}"}

    _day, _month, _year, group = parsed

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
        return {"status": "failed", "reason": str(e)}

    if df.empty:
        _update_log(db, filename, sha256, "imported", rows=0, skipped=0)
        return {"status": "imported", "rows_imported": 0}

    # Столбцы: Дата, Время, Ptr_1, Pshl_1, Ptr_2, Pshl_2, ..., Ptr_5, Pshl_5
    # Первые два столбца — дата и время (позиционно)
    date_col = df.columns[0]
    time_col = df.columns[1]

    rows_imported = 0
    rows_skipped = 0
    first_ts = None
    last_ts = None

    for _, row in df.iterrows():
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

        # Для каждой пары Ptr/Pshl (индексы 1..5)
        for idx in range(1, 6):
            ptr_col = f"Ptr_{idx}"
            pshl_col = f"Pshl_{idx}"

            if ptr_col not in df.columns or pshl_col not in df.columns:
                continue

            p_tube = _clean_pressure(row.get(ptr_col))
            p_line = _clean_pressure(row.get(pshl_col))

            # Если оба None — пропуск
            if p_tube is None and p_line is None:
                rows_skipped += 1
                continue

            channel = (group - 1) * 5 + idx
            well_id = _resolve_well_id(channel, dt_utc, channel_cache)

            if well_id is None:
                rows_skipped += 1
                continue

            # INSERT OR IGNORE (дубли по UNIQUE(well_id, measured_at))
            try:
                db.execute(
                    text(
                        "INSERT OR IGNORE INTO pressure_readings "
                        "(well_id, channel, measured_at, p_tube, p_line, source, source_file) "
                        "VALUES (:well_id, :channel, :measured_at, :p_tube, :p_line, 'csv', :source_file)"
                    ),
                    {
                        "well_id": well_id,
                        "channel": channel,
                        "measured_at": dt_utc,
                        "p_tube": p_tube,
                        "p_line": p_line,
                        "source_file": filename,
                    },
                )
                rows_imported += 1
            except Exception:
                rows_skipped += 1

    db.commit()
    _update_log(
        db, filename, sha256, "imported",
        rows=rows_imported, skipped=rows_skipped,
        first_ts=first_ts, last_ts=last_ts,
    )

    return {
        "status": "imported",
        "rows_imported": rows_imported,
        "rows_skipped": rows_skipped,
        "first_ts": str(first_ts),
        "last_ts": str(last_ts),
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
        ))
    db.commit()


def import_all_csv(
    csv_dir: Optional[Path] = None,
    limit: Optional[int] = None,
) -> dict:
    """
    Импортирует все CSV файлы из директории.
    Возвращает сводку: {"total_files": N, "imported": N, "skipped": N, "failed": N}
    """
    if csv_dir is None:
        csv_dir = Path(__file__).resolve().parent.parent.parent / "data" / "lora"

    init_pressure_db()
    channel_cache = _load_channel_cache()

    csv_files = sorted(csv_dir.glob("*_arc.csv"))
    if limit:
        csv_files = csv_files[:limit]

    log.info("Found %d CSV files in %s", len(csv_files), csv_dir)

    db = PressureSessionLocal()
    summary = {"total_files": len(csv_files), "imported": 0, "skipped": 0, "failed": 0}
    total_rows = 0

    try:
        for i, fpath in enumerate(csv_files, 1):
            result = import_csv_file(fpath, db, channel_cache)
            status = result["status"]
            summary[status] = summary.get(status, 0) + 1
            total_rows += result.get("rows_imported", 0)

            if i % 10 == 0 or i == len(csv_files):
                log.info(
                    "Progress: %d/%d files, %d rows imported",
                    i, len(csv_files), total_rows,
                )
    finally:
        db.close()

    summary["total_rows_imported"] = total_rows
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = import_all_csv()
    print(f"\nImport complete: {result}")
