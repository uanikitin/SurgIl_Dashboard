"""
Единый пайплайн обновления давлений.

Запускает полную цепочку:
  1. Синхронизация CSV с Raspberry Pi
  2. Синхронизация SQLite Tracing с Raspberry Pi
  3. Импорт CSV → pressure.db
  4. Импорт SQLite → pressure.db
  5. Агрегация pressure.db → PostgreSQL (hourly + latest)

Может запускаться:
  - Вручную: python -m backend.services.pressure_pipeline
  - Из API: POST /api/pressure/refresh
  - По расписанию (cron/scheduler)
"""

from __future__ import annotations

import sys
import os
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pressure_pipeline")

# Пути
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # SurgIl_Dashboard
LORA_ROOT = PROJECT_ROOT.parent / "Lora"  # ../Lora (скрипты синхронизации)
CSV_DIR = PROJECT_ROOT / "data" / "lora"
SQLITE_DIR = PROJECT_ROOT / "data" / "lora_sqlite"


def run_pipeline(
    skip_sync: bool = False,
    trends: list[int] | None = None,
) -> dict:
    """
    Запуск полного пайплайна обновления давлений.

    Args:
        skip_sync: Пропустить шаги 1-2 (скачивание с Pi). Полезно если
                   файлы уже скачаны и нужно только импортировать.
        trends: Какие Trend файлы импортировать (по умолчанию [1, 2]).

    Returns:
        dict с результатами каждого шага.
    """
    if trends is None:
        trends = [1, 2]

    results = {
        "started_at": datetime.utcnow().isoformat(),
        "steps": {},
        "success": True,
        "error": None,
    }

    t_start = time.time()

    try:
        # === Шаг 1: Синхронизация CSV ===
        if not skip_sync:
            results["steps"]["sync_csv"] = _step_sync_csv()
            results["steps"]["sync_sqlite"] = _step_sync_sqlite()
        else:
            log.info("Шаги 1-2 пропущены (skip_sync=True)")
            results["steps"]["sync_csv"] = {"skipped": True}
            results["steps"]["sync_sqlite"] = {"skipped": True}

        # === Шаг 3: Импорт CSV → pressure.db ===
        results["steps"]["import_csv"] = _step_import_csv()

        # === Шаг 4: Импорт SQLite → pressure.db ===
        results["steps"]["import_sqlite"] = _step_import_sqlite(trends)

        # === Шаг 5: Агрегация → PostgreSQL ===
        results["steps"]["aggregate"] = _step_aggregate()

    except Exception as e:
        log.error(f"Пайплайн упал: {e}", exc_info=True)
        results["success"] = False
        results["error"] = str(e)

    results["duration_sec"] = round(time.time() - t_start, 1)
    results["finished_at"] = datetime.utcnow().isoformat()

    log.info(f"Пайплайн завершён за {results['duration_sec']}с, success={results['success']}")
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
        log.info(f"CSV sync: {result.downloaded} скачано, {result.skipped} пропущено, {result.errors} ошибок")
        return {
            "downloaded": result.downloaded,
            "skipped": result.skipped,
            "errors": result.errors,
        }
    except Exception as e:
        log.warning(f"CSV sync ошибка (Pi недоступен?): {e}")
        return {"error": str(e)}


def _step_sync_sqlite() -> dict:
    """Шаг 2: Скачать SQLite Tracing с Raspberry Pi."""
    log.info("=== Шаг 2: Синхронизация SQLite ===")
    try:
        sys.path.insert(0, str(LORA_ROOT))
        from sync_lora_sqlite import SyncConfig, run_sync

        config = SyncConfig(
            base_url="http://10.242.96.193:2223",
            dest_dir=str(SQLITE_DIR),
        )
        result = run_sync(config)
        log.info(f"SQLite sync: {result.downloaded} скачано, {result.skipped} пропущено, {result.errors} ошибок")
        return {
            "downloaded": result.downloaded,
            "skipped": result.skipped,
            "errors": result.errors,
        }
    except Exception as e:
        log.warning(f"SQLite sync ошибка (Pi недоступен?): {e}")
        return {"error": str(e)}


def _step_import_csv() -> dict:
    """Шаг 3: Импорт CSV файлов → pressure.db."""
    log.info("=== Шаг 3: Импорт CSV ===")
    try:
        from backend.services.pressure_import_csv import import_all_csv
        result = import_all_csv(CSV_DIR)
        log.info(
            f"CSV import: {result.get('imported', 0)} файлов, "
            f"{result.get('total_rows_imported', 0)} строк, "
            f"{result.get('skipped', 0)} пропущено"
        )
        return result
    except Exception as e:
        log.error(f"CSV import ошибка: {e}")
        return {"error": str(e)}


def _step_import_sqlite(trends: list[int]) -> dict:
    """Шаг 4: Импорт SQLite Tracing → pressure.db."""
    log.info(f"=== Шаг 4: Импорт SQLite (Trend{trends}) ===")
    try:
        from backend.services.pressure_import_sqlite import import_all_sqlite
        result = import_all_sqlite(SQLITE_DIR, trends=trends)
        log.info(
            f"SQLite import: {result.get('total_imported', 0)} строк из "
            f"{result.get('trends_processed', 0)} Trend файлов"
        )
        return result
    except Exception as e:
        log.error(f"SQLite import ошибка: {e}")
        return {"error": str(e)}


def _step_aggregate() -> dict:
    """Шаг 5: Агрегация pressure.db → PostgreSQL hourly + latest."""
    log.info("=== Шаг 5: Агрегация → PostgreSQL ===")
    try:
        from backend.services.pressure_aggregate_service import (
            aggregate_to_hourly,
        )
        # Агрегируем только последние 48 часов (инкрементально)
        result = aggregate_to_hourly()
        log.info(
            f"Aggregate: {result.get('groups_upserted', 0)} часовых групп, "
            f"{result.get('wells_updated', 0)} скважин"
        )
        return result
    except Exception as e:
        log.error(f"Aggregate ошибка: {e}")
        return {"error": str(e)}


# === Запуск из командной строки ===
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Пайплайн обновления давлений LoRa")
    parser.add_argument("--skip-sync", action="store_true",
                        help="Пропустить скачивание с Pi (только импорт + агрегация)")
    parser.add_argument("--trends", type=int, nargs="+", default=[1, 2],
                        help="Какие Trend файлы импортировать (по умолчанию 1 2)")
    args = parser.parse_args()

    result = run_pipeline(skip_sync=args.skip_sync, trends=args.trends)

    import json
    print("\n" + "=" * 60)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
