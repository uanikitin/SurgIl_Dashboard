"""Сервис управления пользовательскими порогами сегментного анализа.

Архитектурный принцип (по согласованию с пользователем — вариант B+):

  ┌────────────────────────────────────────────────────────────────────┐
  │ ДЕФОЛТЫ                — Python-константа SEGMENT_THRESHOLDS       │
  │                          в backend/services/segment_analysis_module│
  │                          ВСЕГДА доступны, всегда в git.            │
  │                                                                    │
  │ USER OVERRIDES         — JSON-файл backend/data/segment_thresholds │
  │                          .json — создаётся при первом сохранении   │
  │                          через UI. В .gitignore.                   │
  │                                                                    │
  │ ЭФФЕКТИВНОЕ ЗНАЧЕНИЕ   — {**defaults, **user_overrides}            │
  │                          (user-override побеждает; неизвестные     │
  │                          ключи игнорируются с warning'ом).         │
  │                                                                    │
  │ ОТСУТСТВИЕ ФАЙЛА       — НЕ ошибка. Возвращаются дефолты.          │
  │                          Перезатёрся файл при деплое — система     │
  │                          работает на дефолтах.                     │
  │                                                                    │
  │ КОНКУРЕНТНОСТЬ         — atomic rename (os.replace на временный    │
  │                          файл) — POSIX-стандарт.                   │
  └────────────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.services.segment_analysis_module import SEGMENT_THRESHOLDS

log = logging.getLogger(__name__)

# Persistent data dir. На продакшен-сервере должен быть смонтирован как
# persistent volume — см. .gitignore + backend/data/.gitkeep.
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_SETTINGS_FILE = _DATA_DIR / "segment_thresholds.json"


def _ensure_dir() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_defaults() -> dict[str, float]:
    """Дефолтные значения (всегда из Python-константы).

    Это иммутабельная копия — менять её опасно (изменения попадут
    в SEGMENT_THRESHOLDS module-wide).
    """
    return dict(SEGMENT_THRESHOLDS)


def get_user_overrides() -> dict[str, float]:
    """Только user-overrides (что пользователь сохранил через UI).

    Не включает дефолты. Используется для UI «показать что переопределено».
    """
    if not _SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        # Структура файла: {"updated_at": ..., "values": {key: value, ...}}
        # Если только values — старый формат, поддерживаем оба.
        if isinstance(data, dict) and "values" in data:
            return dict(data.get("values") or {})
        # Старый формат — словарь напрямую.
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not k.startswith("_")}
        return {}
    except Exception:
        log.exception("Failed to read segment_thresholds.json")
        return {}


def get_effective() -> dict[str, float]:
    """Эффективные значения = дефолты + пользовательские поверх.

    Это значение использует сегментный анализ при каждом запуске.
    Если файла нет — вернутся чистые дефолты.
    """
    merged = get_defaults()
    overrides = get_user_overrides()
    # Игнорируем ключи, которых нет в дефолтах — это защита от опечаток
    # / устаревших ключей при rollback версии алгоритма.
    unknown = set(overrides.keys()) - set(merged.keys())
    if unknown:
        log.warning("segment_thresholds.json contains unknown keys: %s", unknown)
    for k, v in overrides.items():
        if k in merged:
            merged[k] = v
    return merged


def get_metadata() -> dict[str, Any]:
    """Метаинформация о текущем состоянии: дата изменения, кем."""
    if not _SETTINGS_FILE.exists():
        return {"has_overrides": False, "updated_at": None}
    try:
        data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "values" in data:
            return {
                "has_overrides": bool(data.get("values")),
                "updated_at": data.get("updated_at"),
                "overrides_count": len(data.get("values") or {}),
            }
        return {"has_overrides": True, "updated_at": None,
                "overrides_count": len(data or {})}
    except Exception:
        return {"has_overrides": False, "updated_at": None}


def set_overrides(
    overrides: dict[str, Any],
    *,
    merge: bool = True,
) -> dict[str, Any]:
    """Сохранить пользовательские overrides.

    Args:
        overrides: словарь {key: value} с порогами, которые пользователь
            хочет переопределить. Только числовые значения.
        merge: если True — мерджит с уже существующими overrides
            (частичное обновление). Если False — полностью заменяет.

    Returns:
        Метаинфо: {ok, written, skipped_unknown, file}.

    Запись через atomic rename: пишем во временный файл в той же
    папке (чтобы rename был на том же файловой системе) → os.replace
    атомарно заменяет. Если процесс упадёт посреди записи — старый
    файл остаётся целым.
    """
    _ensure_dir()
    defaults = get_defaults()

    # Валидация: только известные ключи + числовые значения.
    clean: dict[str, float] = {}
    skipped: list[str] = []
    for k, v in (overrides or {}).items():
        if k not in defaults:
            skipped.append(k)
            continue
        try:
            clean[k] = float(v)
        except (TypeError, ValueError):
            skipped.append(k)

    # Merge с существующими (если запрошено).
    if merge:
        existing = get_user_overrides()
        existing.update(clean)
        final = existing
    else:
        final = clean

    payload = {
        "values": final,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    # Atomic write: пишем во временный файл в той же папке → os.replace
    fd, tmp_path = tempfile.mkstemp(
        prefix="segment_thresholds_", suffix=".json.tmp",
        dir=str(_DATA_DIR),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, _SETTINGS_FILE)
    except Exception:
        # При ошибке убрать незавершённый временный файл.
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise

    return {
        "ok": True,
        "written": len(final),
        "skipped_unknown": skipped,
        "file": str(_SETTINGS_FILE),
        "updated_at": payload["updated_at"],
    }


def reset_to_defaults() -> dict[str, Any]:
    """Удалить файл user-overrides → система возвращается к дефолтам."""
    if _SETTINGS_FILE.exists():
        try:
            _SETTINGS_FILE.unlink()
            return {"ok": True, "reset": True}
        except Exception as e:
            log.exception("Failed to reset segment_thresholds.json")
            return {"ok": False, "error": str(e)}
    return {"ok": True, "reset": False, "note": "no overrides to reset"}
