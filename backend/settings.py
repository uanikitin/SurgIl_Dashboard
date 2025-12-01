from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Конфиг: откуда брать env и как относиться к "лишним" переменным
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",  # <- ВАЖНО: игнорировать переменные, которых нет в классе
    )

    DATABASE_URL: str
    APP_TITLE: str = "СУРГИЛ · Оптимизация работы газовых скважин"


settings = Settings()