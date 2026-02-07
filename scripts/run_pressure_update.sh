#!/bin/bash
#
# Обёртка для pressure_pipeline.py
# Вызывается launchd каждую минуту, но сам решает — пора ли запускать.
# Логика: читает schedule_config.json, проверяет enabled/день-ночь/интервал.
#

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG="$SCRIPT_DIR/schedule_config.json"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/pressure_update.log"
PYTHON="$PROJECT_DIR/.venv/bin/python"

# Убедиться что лог-директория существует
mkdir -p "$LOG_DIR"

# Ротация лога: обрезать если > 1 МБ
if [ -f "$LOG_FILE" ]; then
    LOG_SIZE=$(stat -f%z "$LOG_FILE" 2>/dev/null || stat --format=%s "$LOG_FILE" 2>/dev/null || echo 0)
    if [ "$LOG_SIZE" -gt 1048576 ]; then
        tail -n 500 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
        echo "[$(date '+%H:%M:%S')] Log rotated (was ${LOG_SIZE} bytes)" >> "$LOG_FILE"
    fi
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# Проверить что конфиг существует
if [ ! -f "$CONFIG" ]; then
    log "ERROR: Config not found: $CONFIG"
    exit 1
fi

# Проверить что Python существует
if [ ! -f "$PYTHON" ]; then
    log "ERROR: Python not found: $PYTHON"
    exit 1
fi

# Читаем конфиг через Python (надёжный парсинг JSON)
CONFIG_DATA=$("$PYTHON" -c "
import json, sys
with open('$CONFIG') as f:
    c = json.load(f)
print(c.get('enabled', True))
print(c.get('day', {}).get('start', '07:00'))
print(c.get('day', {}).get('end', '22:00'))
print(c.get('day', {}).get('interval_min', 5))
print(c.get('night', {}).get('interval_min', 30))
print(c.get('last_run', ''))
" 2>/dev/null)

if [ -z "$CONFIG_DATA" ]; then
    log "ERROR: Failed to parse config"
    exit 1
fi

ENABLED=$(echo "$CONFIG_DATA" | sed -n '1p')
DAY_START=$(echo "$CONFIG_DATA" | sed -n '2p')
DAY_END=$(echo "$CONFIG_DATA" | sed -n '3p')
DAY_INTERVAL=$(echo "$CONFIG_DATA" | sed -n '4p')
NIGHT_INTERVAL=$(echo "$CONFIG_DATA" | sed -n '5p')
LAST_RUN=$(echo "$CONFIG_DATA" | sed -n '6p')

# Если выключено — выходим
if [ "$ENABLED" = "False" ] || [ "$ENABLED" = "false" ]; then
    exit 0
fi

# Определяем текущий час и минуту
CURRENT_HOUR=$(date '+%H')
CURRENT_MIN=$(date '+%M')
CURRENT_MINUTES=$((10#$CURRENT_HOUR * 60 + 10#$CURRENT_MIN))

# Парсим дневные границы
DAY_START_H=$(echo "$DAY_START" | cut -d: -f1)
DAY_START_M=$(echo "$DAY_START" | cut -d: -f2)
DAY_START_MINUTES=$((10#$DAY_START_H * 60 + 10#$DAY_START_M))

DAY_END_H=$(echo "$DAY_END" | cut -d: -f1)
DAY_END_M=$(echo "$DAY_END" | cut -d: -f2)
DAY_END_MINUTES=$((10#$DAY_END_H * 60 + 10#$DAY_END_M))

# Определяем день или ночь
if [ "$CURRENT_MINUTES" -ge "$DAY_START_MINUTES" ] && [ "$CURRENT_MINUTES" -lt "$DAY_END_MINUTES" ]; then
    INTERVAL=$DAY_INTERVAL
    PERIOD="day"
else
    INTERVAL=$NIGHT_INTERVAL
    PERIOD="night"
fi

# Проверяем: прошло ли достаточно времени с последнего запуска
if [ -n "$LAST_RUN" ]; then
    ELAPSED=$("$PYTHON" -c "
from datetime import datetime
try:
    last = datetime.fromisoformat('$LAST_RUN')
    now = datetime.now()
    print(int((now - last).total_seconds() / 60))
except:
    print(9999)
" 2>/dev/null)

    if [ -z "$ELAPSED" ]; then
        ELAPSED=9999
    fi

    if [ "$ELAPSED" -lt "$INTERVAL" ]; then
        # Ещё рано — выходим
        exit 0
    fi
fi

# Пора запускать!
log "=== Pipeline start ($PERIOD, interval=${INTERVAL}min) ==="

cd "$PROJECT_DIR"
"$PYTHON" -m backend.services.pressure_pipeline >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    log "=== Pipeline OK ==="
else
    log "=== Pipeline FAILED (exit code $EXIT_CODE) ==="
fi

# Обновляем last_run в конфиге
"$PYTHON" -c "
import json
from datetime import datetime

config_path = '$CONFIG'
with open(config_path) as f:
    config = json.load(f)

config['last_run'] = datetime.now().isoformat()

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
" 2>/dev/null

exit $EXIT_CODE
