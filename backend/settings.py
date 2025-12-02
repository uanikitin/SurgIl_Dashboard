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

    # ===== добавляем ↓↓↓ =====
    BASIC_AUTH_USERNAME: str = "admin"
    BASIC_AUTH_PASSWORD: str = "change_me"
    # ==========================


settings = Settings()