"""
In-memory кэш для pressure chart данных.

TTL-based cache с автоматической очисткой устаревших записей.
Ключ: хэш параметров запроса.
"""

import hashlib
import logging
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Optional

log = logging.getLogger(__name__)

# Конфигурация
CACHE_TTL_SECONDS = 300  # 5 минут
MAX_CACHE_SIZE = 100  # Максимум записей в кэше


class PressureCache:
    """Thread-safe in-memory cache с TTL."""

    def __init__(self, ttl_seconds: int = CACHE_TTL_SECONDS, max_size: int = MAX_CACHE_SIZE):
        self._cache: dict[str, tuple[datetime, Any]] = {}
        self._lock = Lock()
        self._ttl = timedelta(seconds=ttl_seconds)
        self._max_size = max_size
        self._hits = 0
        self._misses = 0

    def _make_key(self, **params) -> str:
        """Создать ключ кэша из параметров."""
        # Сортируем параметры для стабильности
        sorted_params = sorted(params.items())
        key_str = str(sorted_params)
        return hashlib.md5(key_str.encode()).hexdigest()

    def get(self, **params) -> Optional[Any]:
        """Получить значение из кэша. None если не найдено или устарело."""
        key = self._make_key(**params)
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None

            timestamp, value = self._cache[key]
            if datetime.now() - timestamp > self._ttl:
                # Устарело
                del self._cache[key]
                self._misses += 1
                return None

            self._hits += 1
            return value

    def set(self, value: Any, **params) -> None:
        """Сохранить значение в кэш."""
        key = self._make_key(**params)
        with self._lock:
            # Очистка если превышен лимит
            if len(self._cache) >= self._max_size:
                self._cleanup_oldest()

            self._cache[key] = (datetime.now(), value)

    def _cleanup_oldest(self) -> None:
        """Удалить устаревшие и самые старые записи."""
        now = datetime.now()
        # Сначала удаляем устаревшие
        expired = [k for k, (ts, _) in self._cache.items() if now - ts > self._ttl]
        for k in expired:
            del self._cache[k]

        # Если всё ещё много — удаляем самые старые
        if len(self._cache) >= self._max_size:
            sorted_keys = sorted(self._cache.keys(), key=lambda k: self._cache[k][0])
            for k in sorted_keys[: len(sorted_keys) // 2]:
                del self._cache[k]

    def invalidate_well(self, well_id: int) -> int:
        """Инвалидировать все записи для скважины. Возвращает кол-во удалённых."""
        count = 0
        with self._lock:
            old_size = len(self._cache)
            self._cache.clear()
            count = old_size
        log.info("[cache] invalidated %d entries for well_id=%d", count, well_id)
        return count

    def clear(self) -> None:
        """Очистить весь кэш."""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict:
        """Статистика кэша."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total * 100) if total > 0 else 0
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "ttl_seconds": self._ttl.total_seconds(),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate_percent": round(hit_rate, 1),
            }


# Глобальный экземпляр кэша
pressure_chart_cache = PressureCache()
