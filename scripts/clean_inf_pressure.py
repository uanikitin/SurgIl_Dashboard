#!/usr/bin/env python3
"""
Очистка inf значений из pressure_readings (SQLite) и pressure_hourly (PostgreSQL).

Проблема: IEEE 754 infinity значения из CODESYS Tracing импортировались
как обычные числа и ломали агрегацию (AVG возвращает inf).
"""

import sys
import math
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text


def clean_sqlite_pressure_db():
    """Очистка pressure.db от inf значений."""
    from backend.db_pressure import PressureSessionLocal, init_pressure_db

    init_pressure_db()
    db = PressureSessionLocal()

    try:
        # SQLite не поддерживает ISINF, но inf хранится как текст 'inf' или число
        # Проверим сколько записей с проблемными значениями

        # В SQLite infinity хранится как специальное значение
        # Найдём записи где p_tube или p_line содержит inf
        result = db.execute(text("""
            SELECT COUNT(*) FROM pressure_readings
            WHERE p_tube > 1000 OR p_line > 1000
        """)).scalar()
        print(f"SQLite: найдено {result} записей с p_tube/p_line > 1000 атм")

        # Обнулим такие значения
        affected = db.execute(text("""
            UPDATE pressure_readings
            SET p_tube = NULL
            WHERE p_tube > 1000
        """))
        print(f"  Обнулено p_tube: {affected.rowcount} записей")

        affected = db.execute(text("""
            UPDATE pressure_readings
            SET p_line = NULL
            WHERE p_line > 1000
        """))
        print(f"  Обнулено p_line: {affected.rowcount} записей")

        db.commit()
        print("SQLite: очистка завершена")

    finally:
        db.close()


def clean_postgres_hourly():
    """Очистка PostgreSQL pressure_hourly от inf значений."""
    from backend.db import engine

    with engine.connect() as conn:
        # Проверим сколько записей с проблемными значениями
        # Колонки: p_tube_avg, p_tube_min, p_tube_max, p_line_avg, p_line_min, p_line_max
        result = conn.execute(text("""
            SELECT COUNT(*) FROM pressure_hourly
            WHERE p_tube_avg > 1000 OR p_line_avg > 1000
               OR p_tube_max > 1000 OR p_line_max > 1000
               OR p_tube_avg = 'Infinity' OR p_line_avg = 'Infinity'
        """)).scalar()
        print(f"\nPostgreSQL hourly: найдено {result} записей с inf")

        # Удалим такие записи - они будут пересозданы агрегацией
        affected = conn.execute(text("""
            DELETE FROM pressure_hourly
            WHERE p_tube_avg > 1000 OR p_line_avg > 1000
               OR p_tube_max > 1000 OR p_line_max > 1000
               OR p_tube_avg = 'Infinity' OR p_line_avg = 'Infinity'
        """))
        print(f"  Удалено: {affected.rowcount} записей")

        conn.commit()
        print("PostgreSQL hourly: очистка завершена")


def clean_postgres_latest():
    """Очистка PostgreSQL pressure_latest от inf значений."""
    from backend.db import engine

    with engine.connect() as conn:
        # Проверим
        result = conn.execute(text("""
            SELECT well_id, p_tube, p_line FROM pressure_latest
            WHERE p_tube > 1000 OR p_line > 1000
               OR p_tube = 'Infinity' OR p_line = 'Infinity'
        """)).fetchall()

        if result:
            print(f"\nPostgreSQL latest: найдено {len(result)} скважин с inf:")
            for row in result:
                print(f"  well_id={row[0]}: p_tube={row[1]}, p_line={row[2]}")

            # Обнулим
            affected = conn.execute(text("""
                UPDATE pressure_latest
                SET p_tube = NULL, p_line = NULL
                WHERE p_tube > 1000 OR p_line > 1000
                   OR p_tube = 'Infinity' OR p_line = 'Infinity'
            """))
            print(f"  Обнулено: {affected.rowcount} записей")
            conn.commit()
        else:
            print("\nPostgreSQL latest: inf значений не найдено")

        print("PostgreSQL latest: очистка завершена")


def run_aggregation():
    """Перезапуск агрегации для исправления данных."""
    print("\n=== Перезапуск агрегации ===")
    from backend.services.pressure_aggregate_service import aggregate_to_hourly

    result = aggregate_to_hourly()
    print(f"Агрегация: {result.get('groups_upserted', 0)} часовых групп обновлено")
    print(f"          {result.get('wells_updated', 0)} скважин обновлено в pressure_latest")


if __name__ == "__main__":
    print("=== Очистка inf значений из баз давлений ===\n")

    clean_sqlite_pressure_db()
    clean_postgres_hourly()
    clean_postgres_latest()
    run_aggregation()

    print("\n=== Готово! ===")
