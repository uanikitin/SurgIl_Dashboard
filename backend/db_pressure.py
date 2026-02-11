"""
Локальный SQLite engine для хранения сырых данных давлений.

Отдельная БД (pressure.db) — не нагружаем PostgreSQL на Render.com.
Все сырые замеры хранятся здесь, а в PostgreSQL уходят только агрегаты.
"""

import os
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, declarative_base

# Путь к файлу БД: data/pressure.db рядом с data/lora/
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DATA_DIR.mkdir(exist_ok=True)

PRESSURE_DB_PATH = _DATA_DIR / "pressure.db"
PRESSURE_DB_URL = f"sqlite:///{PRESSURE_DB_PATH}"

pressure_engine = create_engine(
    PRESSURE_DB_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

# WAL mode + foreign keys для SQLite
@event.listens_for(pressure_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

PressureSessionLocal = sessionmaker(
    bind=pressure_engine, autoflush=False, autocommit=False
)

PressureBase = declarative_base()


def init_pressure_db():
    """Создать все таблицы в pressure.db (идемпотентно)."""
    PressureBase.metadata.create_all(bind=pressure_engine)

    # Миграция: добавить sensor_id столбцы если их нет
    with pressure_engine.connect() as conn:
        cols = {r[1] for r in conn.execute(
            text("PRAGMA table_info(pressure_readings)")
        )}
        if "sensor_id_tube" not in cols:
            conn.execute(text(
                "ALTER TABLE pressure_readings ADD COLUMN sensor_id_tube INTEGER"
            ))
            conn.execute(text(
                "ALTER TABLE pressure_readings ADD COLUMN sensor_id_line INTEGER"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_pr_sensor_tube "
                "ON pressure_readings (sensor_id_tube)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_pr_sensor_line "
                "ON pressure_readings (sensor_id_line)"
            ))
            conn.commit()


def get_pressure_db():
    """Dependency / context-manager для получения сессии pressure.db."""
    db = PressureSessionLocal()
    try:
        yield db
    finally:
        db.close()
