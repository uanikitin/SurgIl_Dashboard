from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

# Абсолютный путь к .env (корень проекта)
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    # Конфиг pydantic-settings
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        extra="allow",
    )

    DATABASE_URL: str
    APP_TITLE: str = "СУРГИЛ · Оптимизация работы газовых скважин"

# 🔐 Секретный ключ для сессий
    SECRET_KEY: str = "change_me_in_env"

    # 🔐 Логин/пароль администратора (или общего аккаунта)
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin123"

    # ← Новые поля: учётка для обычного пользователя
    VIEW_USERNAME: str = "user"
    VIEW_PASSWORD: str = "userpass"


    MASTER_ADMIN_USERNAME: str = "admin"
    MASTER_ADMIN_PASSWORD: str = "admin123"   # ЗАДАЙ ЛЮБОЙ ПАРОЛЬ
    MASTER_ADMIN_EMAIL: str = "ua.nikitin@gmail.com"
    MASTER_ADMIN_FULL_NAME: str = "System Administrator"

    # === Notifications ===
    # Telegram
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_DEFAULT_CHAT_ID: str = ""

    # Email (SMTP)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    EMAIL_FROM: str = ""

    # === Background Jobs ===
    # Секретный ключ для API автозадач (Render Cron -> HTTP endpoint)
    JOB_API_SECRET: str = "change_me_job_secret"

    # === Pressure staleness alert ===
    # Через сколько минут отсутствия новых данных слать алерт в Telegram
    PRESSURE_STALE_ALERT_MIN: int = 90
    # Минимальный интервал между повторными алертами (антиспам)
    PRESSURE_STALE_COOLDOWN_MIN: int = 180

settings = Settings()

