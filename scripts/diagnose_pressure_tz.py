#!/usr/bin/env python3
"""
Диагностика проблем с часовыми поясами в данных давлений.
Запуск: python scripts/diagnose_pressure_tz.py
"""

import sys
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Добавляем проект в путь
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Часовые пояса
TZ_UTC = timezone.utc
TZ_UZB = timezone(timedelta(hours=5))  # Кунград (UTC+5)
TZ_UA = timezone(timedelta(hours=2))   # Украина (UTC+2, зима) или +3 (лето)

print("=" * 70)
print("ДИАГНОСТИКА ЧАСОВЫХ ПОЯСОВ ДЛЯ ДАННЫХ ДАВЛЕНИЯ")
print("=" * 70)

# 1. Текущее время в разных зонах
now_utc = datetime.now(TZ_UTC)
now_uzb = datetime.now(TZ_UZB)
now_ua = datetime.now(TZ_UA)

print("\n1. ТЕКУЩЕЕ ВРЕМЯ:")
print(f"   UTC:        {now_utc.strftime('%Y-%m-%d %H:%M:%S')} (UTC+0)")
print(f"   Кунград:    {now_uzb.strftime('%Y-%m-%d %H:%M:%S')} (UTC+5)")
print(f"   Украина:    {now_ua.strftime('%Y-%m-%d %H:%M:%S')} (UTC+2)")
print(f"   Разница:    {(now_uzb.hour - now_ua.hour) % 24} часов между Узбекистаном и Украиной")

# 2. Проверяем pressure.db
pressure_db_path = PROJECT_ROOT / "data" / "pressure.db"
print(f"\n2. ПРОВЕРКА pressure.db: {pressure_db_path}")

if not pressure_db_path.exists():
    print("   ⚠ Файл pressure.db НЕ НАЙДЕН!")
else:
    conn = sqlite3.connect(str(pressure_db_path))
    cursor = conn.cursor()

    # Общая статистика
    cursor.execute("SELECT COUNT(*) FROM pressure_readings")
    total = cursor.fetchone()[0]
    print(f"   Всего записей: {total:,}")

    # Последние записи
    cursor.execute("""
        SELECT measured_at, well_id, p_tube, p_line, source
        FROM pressure_readings
        ORDER BY measured_at DESC
        LIMIT 5
    """)
    rows = cursor.fetchall()

    print("\n   Последние 5 записей (measured_at в UTC):")
    for row in rows:
        measured_at, well_id, p_tube, p_line, source = row
        print(f"     {measured_at} | well={well_id} | p_tube={p_tube} | p_line={p_line} | {source}")

    # Самая свежая запись
    cursor.execute("SELECT MAX(measured_at) FROM pressure_readings")
    max_ts = cursor.fetchone()[0]

    if max_ts:
        try:
            max_dt = datetime.strptime(max_ts[:19], "%Y-%m-%d %H:%M:%S")
            age_hours = (datetime.utcnow() - max_dt).total_seconds() / 3600

            print(f"\n   Самая свежая запись:")
            print(f"     UTC:     {max_ts}")
            print(f"     Возраст: {age_hours:.1f} часов назад")

            # Конвертируем в локальные времена
            max_dt_utc = max_dt.replace(tzinfo=TZ_UTC)
            max_dt_uzb = max_dt_utc.astimezone(TZ_UZB)
            max_dt_ua = max_dt_utc.astimezone(TZ_UA)

            print(f"     В Кунграде: {max_dt_uzb.strftime('%Y-%m-%d %H:%M:%S')} (UTC+5)")
            print(f"     В Украине:  {max_dt_ua.strftime('%Y-%m-%d %H:%M:%S')} (UTC+2)")

            if age_hours > 2:
                print(f"\n   ⚠ ВНИМАНИЕ: Данные устарели на {age_hours:.1f} часов!")
                print("     Возможные причины:")
                print("     - Pi недоступен для синхронизации")
                print("     - Проблема с импортом CSV/SQLite")
                print("     - Timezone mismatch при фильтрации")
        except ValueError as e:
            print(f"   ⚠ Ошибка парсинга даты: {e}")

    # Проверяем записи за последние 48 часов (как в aggregate)
    since_48h = datetime.utcnow() - timedelta(hours=48)
    cursor.execute("""
        SELECT COUNT(*)
        FROM pressure_readings
        WHERE measured_at >= ?
    """, (since_48h.strftime("%Y-%m-%d %H:%M:%S"),))
    count_48h = cursor.fetchone()[0]

    print(f"\n   Записей за последние 48ч (since {since_48h.strftime('%Y-%m-%d %H:%M')} UTC):")
    print(f"     {count_48h:,} записей")

    if count_48h == 0:
        print("   ⚠ НЕТ ДАННЫХ за последние 48 часов для агрегации!")

    # Проверяем формат stored timestamps
    cursor.execute("""
        SELECT measured_at
        FROM pressure_readings
        ORDER BY id DESC
        LIMIT 1
    """)
    sample_ts = cursor.fetchone()
    if sample_ts:
        print(f"\n   Формат хранения timestamp: '{sample_ts[0]}'")

    conn.close()

# 3. Проверяем CSV import log
print("\n3. ЖУРНАЛ ИМПОРТА CSV:")
if pressure_db_path.exists():
    conn = sqlite3.connect(str(pressure_db_path))
    cursor = conn.cursor()

    cursor.execute("""
        SELECT filename, status, rows_imported, imported_at
        FROM csv_import_log
        ORDER BY imported_at DESC
        LIMIT 5
    """)
    csv_logs = cursor.fetchall()

    if csv_logs:
        for log in csv_logs:
            filename, status, rows, imported_at = log
            print(f"   {filename}: {status}, {rows} rows, at {imported_at}")
    else:
        print("   Журнал пуст")

    conn.close()

# 4. Проверяем SQLite import state
print("\n4. СОСТОЯНИЕ ИМПОРТА SQLITE (Tracing):")
if pressure_db_path.exists():
    conn = sqlite3.connect(str(pressure_db_path))
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT trend_name, last_ts, rows_imported_total, updated_at
            FROM tracing_import_state
            ORDER BY trend_name
        """)
        states = cursor.fetchall()

        if states:
            for state in states:
                trend_name, last_ts, total, updated_at = state
                # last_ts в микросекундах
                if last_ts and last_ts > 0:
                    try:
                        last_dt = datetime.utcfromtimestamp(last_ts / 1_000_000)
                        age = (datetime.utcnow() - last_dt).total_seconds() / 3600
                        print(f"   {trend_name}: last={last_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC ({age:.1f}h ago), total={total}")
                    except (OSError, OverflowError):
                        print(f"   {trend_name}: last_ts={last_ts} (invalid), total={total}")
                else:
                    print(f"   {trend_name}: last_ts=0, total={total}")
        else:
            print("   Состояние не найдено (таблица пуста)")
    except sqlite3.OperationalError:
        print("   Таблица tracing_import_state не существует")

    conn.close()

# 5. Проверяем PostgreSQL (pressure_hourly, pressure_latest)
print("\n5. ПРОВЕРКА PostgreSQL (pressure_hourly + pressure_latest):")
try:
    from backend.db import engine as pg_engine
    from sqlalchemy import text

    with pg_engine.connect() as conn:
        # pressure_latest
        result = conn.execute(text("""
            SELECT well_id, p_tube, p_line, measured_at
            FROM pressure_latest
            ORDER BY measured_at DESC
            LIMIT 3
        """))
        rows = result.fetchall()

        print("   pressure_latest (последние 3):")
        for row in rows:
            well_id, p_tube, p_line, measured_at = row
            if measured_at:
                age = (datetime.utcnow() - measured_at).total_seconds() / 3600
                print(f"     well={well_id}: p_tube={p_tube}, p_line={p_line}, at {measured_at} ({age:.1f}h ago)")

        # pressure_hourly
        result = conn.execute(text("""
            SELECT well_id, hour_start, p_tube_avg, p_line_avg
            FROM pressure_hourly
            ORDER BY hour_start DESC
            LIMIT 3
        """))
        rows = result.fetchall()

        print("   pressure_hourly (последние 3 часа):")
        for row in rows:
            well_id, hour_start, p_tube_avg, p_line_avg = row
            print(f"     well={well_id}: hour={hour_start}, p_tube={p_tube_avg}, p_line={p_line_avg}")

except Exception as e:
    print(f"   ⚠ Ошибка подключения к PostgreSQL: {e}")

# 6. Проверяем файлы синхронизации
print("\n6. ФАЙЛЫ СИНХРОНИЗАЦИИ:")
csv_dir = PROJECT_ROOT / "data" / "lora"
sqlite_dir = PROJECT_ROOT / "data" / "lora_sqlite"

if csv_dir.exists():
    csv_files = list(csv_dir.glob("*_arc.csv"))
    if csv_files:
        # Сортируем по дате в имени файла
        csv_files_sorted = sorted(csv_files, key=lambda f: f.name, reverse=True)[:3]
        print(f"   CSV файлы ({len(csv_files)} всего), последние 3:")
        for f in csv_files_sorted:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            print(f"     {f.name} (modified: {mtime.strftime('%Y-%m-%d %H:%M')})")
    else:
        print("   CSV файлов нет")
else:
    print(f"   ⚠ Директория {csv_dir} не существует")

if sqlite_dir.exists():
    sqlite_files = list(sqlite_dir.glob("Trend*.sqlite"))
    if sqlite_files:
        print(f"   SQLite файлы:")
        for f in sorted(sqlite_files):
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            print(f"     {f.name} (modified: {mtime.strftime('%Y-%m-%d %H:%M')})")
    else:
        print("   SQLite файлов нет")
else:
    print(f"   ⚠ Директория {sqlite_dir} не существует")

print("\n" + "=" * 70)
print("РЕКОМЕНДАЦИИ:")
print("=" * 70)
print("""
Если данные устарели:
1. Проверьте доступность Pi: curl http://10.242.96.193:2224/
2. Запустите синхронизацию вручную: python -m backend.services.pressure_pipeline
3. Проверьте логи: tail -f logs/pressure_pipeline.log

Если проблема с timezone:
- Все данные должны храниться в UTC
- CSV приходит в UTC+5-времени → конвертируется в UTC при импорте
- SQLite Tracing хранит Unix timestamp в UTC
- Aggregation фильтрует по datetime.utcnow()
""")
