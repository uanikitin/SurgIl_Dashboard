"""
pressure_backfill_service — ретроактивный реимпорт CSV при задним числом
создании/правке equipment_installation для LoRa-датчика.

Проблема: при установке sensor'а задним числом (installed_at в прошлом)
строки CSV за период [installed_at, created_at] были уже пропущены
импортом — т.к. на момент импорта installation ещё не было в БД,
а `_find_installation` возвращал None → INSERT не производился.
tail-only оптимизация предыдущих импортов мешает переобработать эти строки.

Что делает бэкфилл:
  1. Находит CSV-файлы соответствующей csv_group, чьи данные
     покрывают период [installed_at, now].
  2. Сбрасывает csv_import_log: `rows_in_file = 0, file_sha256 = ''`
     → при следующем import_csv_file файл обрабатывается с нуля.
  3. Синхронно запускает import_all_csv (для этих файлов).
  4. Сразу запускает sync_raw_readings + agg_hourly + update_latest
     для затронутых скважин.

INSERT в pressure_readings использует `INSERT OR IGNORE`, поэтому
уже импортированные строки пропускаются, а недостающие добавляются.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import text

from backend.db_pressure import PressureSessionLocal, init_pressure_db
from backend.db import engine as pg_engine
from backend.services.pressure_import_csv import (
    FILENAME_RE,
    _ensure_log_schema,
    _load_installation_cache,
    _load_sensor_cache,
    import_csv_file,
)
from backend.services.pressure_aggregate_service import (
    sync_raw_to_pg,
    aggregate_to_hourly,
    update_latest,
)
from backend.services.sensor_assignment_service import load_assignment_cache

log = logging.getLogger(__name__)

# Каталог по умолчанию
_DEFAULT_CSV_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "lora"


def _list_files_for_group(csv_dir: Path, csv_group: int) -> list[Path]:
    """CSV файлы (*.group_arc.csv) отсортированные по дате в имени (DD.MM.YYYY)."""
    out = []
    for fp in csv_dir.glob(f"*.{csv_group}_arc.csv"):
        m = FILENAME_RE.match(fp.name)
        if not m:
            continue
        day, month, year, g = int(m[1]), int(m[2]), int(m[3]), int(m[4])
        if g != csv_group:
            continue
        try:
            start = datetime(year, month, day)
        except ValueError:
            continue
        out.append((start, fp))
    out.sort(key=lambda t: t[0])
    return [fp for _, fp in out]


def _files_covering_range(
    csv_dir: Path,
    csv_group: int,
    since: datetime,
    until: Optional[datetime] = None,
) -> list[Path]:
    """
    CSV CODESYS кумулятивные: файл с датой старта D содержит данные с D
    и до конца (пока его не ротируют на новый). Покрывают `since`, если:
      start <= since  (старт до нужной точки — в файле есть нужная дата)
      ИЛИ start между since и until (файл начался внутри диапазона).
    Берём последний файл с start <= since + все файлы с start > since (до until).
    """
    if until is None:
        until = datetime.utcnow() + timedelta(days=1)

    all_files = []
    for fp in csv_dir.glob(f"*.{csv_group}_arc.csv"):
        m = FILENAME_RE.match(fp.name)
        if not m:
            continue
        day, month, year, g = int(m[1]), int(m[2]), int(m[3]), int(m[4])
        if g != csv_group:
            continue
        try:
            start = datetime(year, month, day)
        except ValueError:
            continue
        all_files.append((start, fp))
    all_files.sort(key=lambda t: t[0])

    covering = []
    latest_before = None
    for start, fp in all_files:
        if start <= since:
            latest_before = fp
        elif start <= until:
            covering.append(fp)
    if latest_before is not None:
        covering.insert(0, latest_before)
    return covering


def _reset_csv_log(db, filenames: list[str]) -> int:
    """Сбрасывает rows_in_file + file_sha256 — чтобы import прогнал файлы заново."""
    if not filenames:
        return 0
    # SQLite не знает ANY(...) — делаем построчно
    total = 0
    for name in filenames:
        result = db.execute(
            text(
                "UPDATE csv_import_log "
                "SET rows_in_file = 0, file_sha256 = '', status = 'imported' "
                "WHERE filename = :name"
            ),
            {"name": name},
        )
        total += result.rowcount or 0
    db.commit()
    return total


def backfill_for_sensor(
    sensor_id: int,
    since: datetime,
    csv_dir: Optional[Path] = None,
    run_aggregation: bool = True,
) -> dict:
    """
    Ретро-реимпорт для одного датчика от `since` (local time Кунград).

    Returns: {
        "sensor_id": int,
        "csv_group": int,
        "files_reset": [filename, ...],
        "rows_imported": int,
        "affected_wells": [well_id, ...],
        "sync_rows": int,
    }
    """
    csv_dir = csv_dir or _DEFAULT_CSV_DIR
    result = {
        "sensor_id": sensor_id,
        "csv_group": None,
        "files_reset": [],
        "rows_imported": 0,
        "affected_wells": [],
        "sync_rows": 0,
    }

    # 1. csv_group датчика
    with pg_engine.connect() as conn:
        row = conn.execute(
            text("SELECT csv_group FROM lora_sensors WHERE id = :sid"),
            {"sid": sensor_id},
        ).fetchone()
    if not row:
        log.warning("backfill: sensor %s not found", sensor_id)
        return result
    csv_group = int(row[0])
    result["csv_group"] = csv_group

    # 2. Файлы, покрывающие диапазон
    files = _files_covering_range(csv_dir, csv_group, since)
    if not files:
        log.info("backfill: no CSV files covering sensor=%s since=%s",
                 sensor_id, since)
        return result

    filenames = [fp.name for fp in files]
    log.info("backfill: sensor=%s group=%s files=%s since=%s",
             sensor_id, csv_group, filenames, since)

    # 3. Сбросить csv_import_log → tail=0, sha mismatch → полный реимпорт
    init_pressure_db()
    db = PressureSessionLocal()
    _ensure_log_schema(db)
    try:
        _reset_csv_log(db, filenames)

        # 4. Прогнать импорт этих файлов с актуальными кэшами
        sensor_cache = _load_sensor_cache()
        installation_cache = _load_installation_cache()
        assignment_cache = load_assignment_cache()

        total_imported = 0
        affected_wells: set[int] = set()
        min_ts = None
        for fp in files:
            r = import_csv_file(
                fp, db, sensor_cache, installation_cache, assignment_cache,
            )
            total_imported += r.get("rows_imported", 0)
            affected_wells.update(r.get("affected_wells", set()))
            ts = r.get("first_ts")
            if isinstance(ts, datetime) and (min_ts is None or ts < min_ts):
                min_ts = ts
        result["rows_imported"] = total_imported
        result["affected_wells"] = sorted(affected_wells)
    finally:
        db.close()

    # 5. Синхронизация SQLite → PG (pressure_raw) + агрегация
    if run_aggregation:
        try:
            sync_res = sync_raw_to_pg(
                since=min_ts,
                well_ids=affected_wells if affected_wells else None,
            )
            result["sync_rows"] = sync_res.get("rows_synced", 0)
        except Exception as e:
            log.error("backfill: sync_raw_to_pg failed: %s", e)

        if affected_wells:
            try:
                aggregate_to_hourly(
                    well_ids=list(affected_wells), since=min_ts,
                )
            except Exception as e:
                log.error("backfill: aggregate_to_hourly failed: %s", e)
            try:
                update_latest(well_ids=affected_wells)
            except Exception as e:
                log.error("backfill: update_latest failed: %s", e)

    result["files_reset"] = filenames
    return result


def backfill_for_installation(
    sensor_id: int,
    well_id: int,
    installed_at: datetime,
    csv_dir: Optional[Path] = None,
) -> dict:
    """
    Вызывать сразу после создания/обновления equipment_installation,
    чтобы ретроактивно заполнить pressure_raw за период с `installed_at`.

    Запускает бэкфилл ТОЛЬКО если installed_at в прошлом (иначе нечего
    обрабатывать). `well_id` используется лишь для логов — фактическая
    привязка делается через installation_cache.
    """
    now = datetime.now()
    if installed_at > now:
        log.debug("backfill: installed_at %s in future, skip", installed_at)
        return {"skipped": "future_install"}

    # Не бэкфиллить слишком старые — ограничение 90 дней от сейчас,
    # чтобы случайно не переобработать всю историю.
    max_age = timedelta(days=90)
    since = max(installed_at, now - max_age)
    if since > installed_at:
        log.info(
            "backfill: clipping since from %s to %s (max_age=%s)",
            installed_at, since, max_age,
        )

    log.info(
        "backfill_for_installation: sensor=%s well=%s installed_at=%s",
        sensor_id, well_id, installed_at,
    )
    return backfill_for_sensor(sensor_id, since, csv_dir=csv_dir)


def get_sensor_id_for_equipment(db, serial_number: str) -> Optional[int]:
    """Вернуть lora_sensors.id по equipment.serial_number (или None)."""
    if not serial_number:
        return None
    row = db.execute(
        text("SELECT id FROM lora_sensors WHERE serial_number = :sn"),
        {"sn": serial_number},
    ).fetchone()
    return int(row[0]) if row else None


def maybe_backfill_after_install(
    equipment_serial: Optional[str],
    well_id: int,
    installed_at: datetime,
    *,
    in_background: bool = True,
) -> Optional[dict]:
    """
    Унифицированный hook — вызывать ПОСЛЕ commit создания/переноса
    equipment_installation. Работает только для LoRa-датчиков
    (equipment.serial_number есть в lora_sensors).

    Не падает при ошибках — логирует и возвращает None.

    in_background=True → запускает в daemon-потоке, чтобы не блокировать HTTP.
    """
    if not equipment_serial:
        return None

    now = datetime.now()
    if installed_at >= now - timedelta(minutes=1):
        # Будущая или «прямо сейчас» установка — нечего бэкфиллить.
        return None

    # Ленивый lookup sensor_id (отдельное соединение, чтобы не конфликтовать
    # с активной сессией роутера)
    with pg_engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM lora_sensors WHERE serial_number = :sn"),
            {"sn": equipment_serial},
        ).fetchone()
    if not row:
        return None
    sensor_id = int(row[0])

    def _run():
        try:
            res = backfill_for_installation(
                sensor_id, well_id, installed_at,
            )
            log.info(
                "backfill ok: sensor=%s well=%s rows=%s wells=%s",
                sensor_id, well_id, res.get("rows_imported"),
                res.get("affected_wells"),
            )
        except Exception as e:
            log.exception("backfill failed for sensor=%s: %s", sensor_id, e)

    if in_background:
        import threading
        t = threading.Thread(target=_run, daemon=True, name="pressure-backfill")
        t.start()
        return {"started": True, "sensor_id": sensor_id}

    _run()
    return {"sensor_id": sensor_id}
