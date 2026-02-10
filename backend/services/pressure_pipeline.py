"""
Единый пайплайн обновления давлений (CSV-only).

Запускает полную цепочку:
  1. Синхронизация CSV с Raspberry Pi
  2. Импорт CSV → pressure.db (SQLite)
  3. Агрегация pressure.db → PostgreSQL (hourly + latest)

Может запускаться:
  - Вручную: python -m backend.services.pressure_pipeline
  - Из API: POST /api/pressure/refresh
  - По расписанию (launchd → scripts/run_pressure_update.py)
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
        results["steps"]["import_csv"] = _step_import_csv()

        # === Шаг 3: Агрегация → PostgreSQL ===
        results["steps"]["aggregate"] = _step_aggregate()

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
        log.info(
            f"CSV import: {result.get('imported', 0)} файлов, "
            f"{result.get('total_rows_imported', 0)} строк, "
            f"{result.get('skipped', 0)} пропущено"
        )
        return result
    except Exception as e:
        log.error(f"CSV import ошибка: {e}")
        return {"error": str(e)}


def _step_aggregate() -> dict:
    """Шаг 3: Агрегация pressure.db → PostgreSQL hourly + latest."""
    log.info("=== Шаг 3: Агрегация → PostgreSQL ===")
    try:
        from backend.services.pressure_aggregate_service import (
            aggregate_to_hourly,
        )
        result = aggregate_to_hourly()
        log.info(
            f"Aggregate: {result.get('hours_upserted', 0)} "
            f"часовых групп, "
            f"{result.get('wells_updated', 0)} скважин"
        )
        return result
    except Exception as e:
        log.error(f"Aggregate ошибка: {e}")
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
