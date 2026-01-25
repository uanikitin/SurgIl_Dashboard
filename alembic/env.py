from __future__ import annotations


import os
import sys
from logging.config import fileConfig
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool
from alembic import context

# === 1. Добавляем путь к проекту, чтобы видеть пакет backend ===
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)


load_dotenv()
# === 2. Импортируем Base и engine из твоего проекта ===
from backend.db import Base, engine  # ВАЖНО: здесь твой реальный Base и engine
# 👇 ОБЯЗАТЕЛЬНО импортируем модели, чтобы Alembic их увидел
# импорт всех моделей, чтобы Alembic их видел
from backend.models.wells import Well
from backend.documents.models import (
    DocumentType,
    Document,
    DocumentItem,
    DocumentSignature,
)
from backend.documents.models_notifications import (
    NotificationConfig,
    DocumentSendLog,
    JobExecutionLog,
)
import backend.documents.models
import backend.documents.models_notifications
# Это объект конфигурации Alembic, даёт доступ к .ini
config = context.config
db_url = os.getenv("DATABASE_URL")
if not db_url:
    raise RuntimeError("DATABASE_URL is not set (check .env)")

# Render часто требует SSL
if "render.com" in db_url and "sslmode=" not in db_url:
    db_url = db_url + ("&" if "?" in db_url else "?") + "sslmode=require"

config.set_main_option("sqlalchemy.url", db_url)
# Логирование Alembic (можно не трогать)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# === 3. Говорим Alembic, какие метаданные отслеживать ===
target_metadata = Base.metadata

# === 4. Подсовываем Alembic наш URL из engine ===
# чтобы не использовать заглушку "driver://user:pass@localhost/dbname"
config.set_main_option("sqlalchemy.url", str(engine.url))


def run_migrations_offline() -> None:
    """Запуск миграций в offline-режиме (генерация SQL без подключения к БД)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Обычный онлайн-режим миграций (подключаемся к БД и меняем схему)."""
    connectable = engine  # используем твой engine

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,  # отслеживать изменения типов/размеров
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()