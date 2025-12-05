from pydantic_settings import BaseSettings, SettingsConfigDict
# backend/config/__init__.py

from ..settings import settings  # или from backend.settings import settings

__all__ = ["settings"]
#
# class Settings(BaseSettings):
#     # Конфиг pydantic-settings
#     model_config = SettingsConfigDict(
#         env_file=".env",
#         env_file_encoding="utf-8",
#         extra="allow",  # разрешаем "лишние" переменные
#     )
#
#     DATABASE_URL: str
#     APP_TITLE: str = "СУРГИЛ · Оптимизация работы газовых скважин"
#
#     # Для Basic Auth (можно переопределить через переменные окружения)
#     BASIC_AUTH_USERNAME: str = "admin"
#     BASIC_AUTH_PASSWORD: str = "change_me"
#
#
# # Этот объект и будет использоваться по всему приложению
# settings = Settings()