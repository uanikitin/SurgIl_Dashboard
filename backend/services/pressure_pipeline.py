"""
Единый пайплайн обновления давлений (CSV-only).

Запускает полную цепочку:
  1. Синхронизация CSV с Raspberry Pi
  2. Импорт CSV → pressure.db (SQLite)
  3. Агрегация pressure.db → PostgreSQL (hourly)
  4. Синхронизация сырых данных → PostgreSQL (pressure_raw)
  5. Обновление pressure_latest из pressure_raw (PostgreSQL)

Оптимизации:
  - Шаг 2 возвращает affected_well_ids и min_timestamp
  - Шаги 3-5 обрабатывают только затронутые скважины и период
  - Если ничего не изменилось — шаги 3-5 пропускаются

Может запускаться:
  - Вручную: python -m backend.services.pressure_pipeline
  - Из API: POST /api/pressure/refresh
  - По расписанию (cron → scripts/run_pressure_update.py)
"""

from __future__ import annotations

import sys
import time
import logging
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pressure_pipeline")

# Пути
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LORA_ROOT = PROJECT_ROOT.parent / "Lora"
CSV_DIR = PROJECT_ROOT / "data" / "lora"


def run_pipeline(skip_sync: bool = False) -> dict:
    """
    Запуск полного пайплайна обновления давлений.

    Args:
        skip_sync: Пропустить скачивание CSV с Pi.
                   Полезно если файлы уже скачаны.

    Returns:
        dict с результатами каждого шага.
    """
    results = {
        "started_at": datetime.utcnow().isoformat(),
        "steps": {},
        "success": True,
        "error": None,
    }

    t_start = time.time()

    try:
        # === Шаг 1: Синхронизация CSV с Raspberry Pi ===
        if not skip_sync:
            results["steps"]["sync_csv"] = _step_sync_csv()
        else:
            log.info("Шаг 1 пропущен (skip_sync=True)")
            results["steps"]["sync_csv"] = {"skipped": True}

        # === Шаг 2: Импорт CSV → pressure.db ===
        import_result = _step_import_csv()
        results["steps"]["import_csv"] = {
            k: v for k, v in import_result.items()
            if k not in ("affected_well_ids", "min_timestamp")
        }

        # === Шаги 3-5: только если есть новые данные ===
        affected_wells = import_result.get("affected_well_ids", set())
        min_timestamp = import_result.get("min_timestamp")

        if affected_wells:
            # === Шаг 3: Агрегация hourly → PostgreSQL ===
            agg_result = _step_aggregate(
                well_ids=affected_wells,
                since=min_timestamp,
            )
            results["steps"]["aggregate"] = agg_result
            if agg_result.get("error"):
                results["success"] = False
                results["error"] = f"aggregate: {agg_result['error']}"

            # === Шаг 4: Синхронизация сырых данных → PostgreSQL ===
            sync_result = _step_sync_raw(
                well_ids=affected_wells,
                since=min_timestamp,
            )
            results["steps"]["sync_raw"] = sync_result
            if sync_result.get("error"):
                results["success"] = False
                results["error"] = f"sync_raw: {sync_result['error']}"

            # === Шаг 5: Обновление pressure_latest из pressure_raw (PG) ===
            latest_result = _step_update_latest(well_ids=affected_wells)
            results["steps"]["update_latest"] = latest_result
            if latest_result.get("error"):
                results["success"] = False
                results["error"] = f"update_latest: {latest_result['error']}"
        else:
            log.info("Шаги 3-5 пропущены (нет новых данных)")
            results["steps"]["aggregate"] = {"skipped": True, "reason": "no new data"}
            results["steps"]["sync_raw"] = {"skipped": True, "reason": "no new data"}
            results["steps"]["update_latest"] = {"skipped": True, "reason": "no new data"}

    except Exception as e:
        log.error(f"Пайплайн упал: {e}", exc_info=True)
        results["success"] = False
        results["error"] = str(e)

    results["duration_sec"] = round(time.time() - t_start, 1)
    results["finished_at"] = datetime.utcnow().isoformat()

    log.info(
        f"Пайплайн завершён за {results['duration_sec']}с, "
        f"success={results['success']}"
    )
    return results


def _step_sync_csv() -> dict:
    """Шаг 1: Скачать CSV с Raspberry Pi."""
    log.info("=== Шаг 1: Синхронизация CSV ===")
    try:
        sys.path.insert(0, str(LORA_ROOT))
        from sync_lora_csv import SyncConfig, run_sync

        config = SyncConfig(
            base_url="http://10.242.96.193:2224",
            dest_dir=str(CSV_DIR),
            force_recent_days=7,
        )
        result = run_sync(config)
        log.info(
            f"CSV sync: {result.downloaded} скачано, "
            f"{result.skipped} пропущено, "
            f"{result.errors} ошибок"
        )
        return {
            "downloaded": result.downloaded,
            "skipped": result.skipped,
            "errors": result.errors,
        }
    except Exception as e:
        log.warning(f"CSV sync ошибка (Pi недоступен?): {e}")
        return {"error": str(e)}


def _step_import_csv() -> dict:
    """Шаг 2: Импорт CSV файлов → pressure.db."""
    log.info("=== Шаг 2: Импорт CSV ===")
    try:
        from backend.services.pressure_import_csv import import_all_csv
        result = import_all_csv(CSV_DIR)
        affected = result.get("affected_well_ids", set())
        log.info(
            f"CSV import: {result.get('imported', 0)} файлов, "
            f"{result.get('total_rows_imported', 0)} строк, "
            f"{result.get('skipped', 0)} пропущено, "
            f"{len(affected)} скважин затронуто"
        )
        return result
    except Exception as e:
        log.error(f"CSV import ошибка: {e}")
        return {"error": str(e)}


def _step_aggregate(
    well_ids: set[int] = None,
    since: datetime = None,
) -> dict:
    """Шаг 3: Агрегация pressure.db → PostgreSQL hourly."""
    log.info("=== Шаг 3: Агрегация hourly → PostgreSQL ===")
    try:
        from backend.services.pressure_aggregate_service import (
            aggregate_to_hourly,
        )
        result = aggregate_to_hourly(since=since, well_ids=well_ids)
        log.info(
            f"Aggregate: {result.get('hours_upserted', 0)} часовых групп"
        )
        return result
    except Exception as e:
        log.error(f"Aggregate ошибка: {e}")
        return {"error": str(e)}


def _step_sync_raw(
    well_ids: set[int] = None,
    since: datetime = None,
) -> dict:
    """Шаг 4: Синхронизация сырых данных pressure.db → PostgreSQL pressure_raw."""
    log.info("=== Шаг 4: Синхронизация сырых данных → PostgreSQL ===")
    try:
        from backend.services.pressure_aggregate_service import sync_raw_to_pg
        result = sync_raw_to_pg(since=since, well_ids=well_ids)
        log.info(f"Raw sync: {result.get('rows_synced', 0)} записей")
        return result
    except Exception as e:
        log.error(f"Raw sync ошибка: {e}")
        return {"error": str(e)}


def _step_update_latest(well_ids: set[int] = None) -> dict:
    """Шаг 5: Обновление pressure_latest из pressure_raw (PostgreSQL)."""
    log.info("=== Шаг 5: Обновление pressure_latest (PG → PG) ===")
    try:
        from backend.services.pressure_aggregate_service import update_latest
        wells_updated = update_latest(well_ids=well_ids)
        log.info(f"Latest update: {wells_updated} скважин")
        return {"wells_updated": wells_updated}
    except Exception as e:
        log.error(f"Latest update ошибка: {e}")
        return {"error": str(e)}


# === Запуск из командной строки ===
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Пайплайн обновления давлений LoRa (CSV-only)"
    )
    parser.add_argument(
        "--skip-sync", action="store_true",
        help="Пропустить скачивание с Pi (только импорт + агрегация)",
    )
    args = parser.parse_args()

    result = run_pipeline(skip_sync=args.skip_sync)

    import json
    print("\n" + "=" * 60)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
