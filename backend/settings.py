from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Конфиг pydantic-settings
    model_config = SettingsConfigDict(
        env_file=".env",
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

settings = Settings()

