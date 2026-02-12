#!/usr/bin/env python3
"""
Обёртка для pressure_pipeline.py.
Вызывается launchd каждую минуту, но сам решает — пора ли запускать.

Логика:
  1. Читает schedule_config.json
  2. Если enabled=false → выход
  3. Определяет день/ночь по текущему часу
  4. Проверяет: прошло ли нужное кол-во минут с last_run
  5. Если да → запускает pipeline, обновляет last_run
"""

import json
import os
import sys
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Кунград (Каракалпакстан) — UTC+5
TZ_KUNGRAD = timezone(timedelta(hours=5))

# Пути
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
CONFIG_PATH = SCRIPT_DIR / "schedule_config.json"
LOG_DIR = PROJECT_DIR / "logs"
LOG_FILE = LOG_DIR / "pressure_update.log"

# Убедимся что каталог логов существует
LOG_DIR.mkdir(exist_ok=True)

# Ротация лога (> 1 МБ → обрезаем)
if LOG_FILE.exists() and LOG_FILE.stat().st_size > 1_048_576:
    lines = LOG_FILE.read_text().splitlines()
    LOG_FILE.write_text("\n".join(lines[-500:]) + "\n")

# Настройка логгирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
    ],
)
log = logging.getLogger("scheduler")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {
            "enabled": False,
            "day": {"start": "07:00", "end": "22:00", "interval_min": 5},
            "night": {"interval_min": 30},
            "last_run": None,
        }
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_config(config: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def time_to_minutes(t: str) -> int:
    """'07:30' → 450"""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def main():
    config = load_config()

    # 1. Проверяем enabled
    if not config.get("enabled", True):
        return  # Тихо выходим

    now = datetime.now(TZ_KUNGRAD)  # Время Кунграда (UTC+5)
    current_minutes = now.hour * 60 + now.minute

    # 2. День или ночь?
    day_cfg = config.get("day", {})
    night_cfg = config.get("night", {})

    day_start = time_to_minutes(day_cfg.get("start", "07:00"))
    day_end = time_to_minutes(day_cfg.get("end", "22:00"))

    if day_start <= current_minutes < day_end:
        interval = day_cfg.get("interval_min", 5)
        period = "day"
    else:
        interval = night_cfg.get("interval_min", 30)
        period = "night"

    # 3. Проверяем: прошло ли достаточно времени
    last_run_str = config.get("last_run")
    if last_run_str:
        try:
            last_run = datetime.fromisoformat(last_run_str)
            # now — aware (UTC+5), last_run — naive (тоже Кунградское)
            elapsed_min = (now.replace(tzinfo=None) - last_run).total_seconds() / 60
            if elapsed_min < interval:
                return  # Ещё рано
        except (ValueError, TypeError):
            pass  # Битая дата — запускаем

    # 4. Пора запускать!
    log.info(f"=== Pipeline start ({period}, interval={interval}min) ===")

    # Добавляем проект в sys.path
    sys.path.insert(0, str(PROJECT_DIR))
    os.chdir(str(PROJECT_DIR))

    try:
        from backend.services.pressure_pipeline import run_pipeline
        result = run_pipeline()

        if result.get("success"):
            log.info(f"=== Pipeline OK ({result.get('duration_sec', '?')}s) ===")
        else:
            log.error(f"=== Pipeline FAILED: {result.get('error')} ===")

    except Exception as e:
        log.error(f"=== Pipeline EXCEPTION: {e} ===")

    # 5. Обновляем last_run (в Кунградском времени для консистентности)
    config["last_run"] = datetime.now(TZ_KUNGRAD).replace(tzinfo=None).isoformat()
    save_config(config)


if __name__ == "__main__":
    main()
