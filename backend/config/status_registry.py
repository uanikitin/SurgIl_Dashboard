import json
from pathlib import Path

# Папка, где лежит этот файл: backend/config
CONFIG_DIR = Path(__file__).parent

# backend/config/statuses.json
STATUS_FILE = CONFIG_DIR / "statuses.json"


def load_statuses() -> list[dict]:
    """
    Читает statuses.json и возвращает список словарей со статусами.
    Каждый элемент:
      {
        "code": "status-watch",
        "label": "Наблюдение",
        "color": "#1d8cf8",
        "order": 10
      }
    """
    with STATUS_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # На всякий случай сортируем по полю order (если есть)
    data.sort(key=lambda s: s.get("order", 0))
    return data


# Готовый список статусов
STATUS_LIST: list[dict] = load_statuses()

# Быстрый доступ:
#   по коду CSS-класса: "status-watch" → {...}
STATUS_BY_CODE: dict[str, dict] = {s["code"]: s for s in STATUS_LIST}

#   по тексту (как хранится в БД: "Наблюдение") → {...}
STATUS_BY_LABEL: dict[str, dict] = {s["label"]: s for s in STATUS_LIST}

DEFAULT_STATUS_CODE = "status-other"


def css_by_label(label: str | None) -> str:
    """
    Из человекочитаемого статуса ("Наблюдение") → CSS-класс ("status-watch").
    Если статус не найден, возвращаем статус по умолчанию.
    """
    if not label:
        return DEFAULT_STATUS_CODE
    s = STATUS_BY_LABEL.get(label)
    if s:
        return s["code"]
    return DEFAULT_STATUS_CODE


def label_by_code(code: str | None) -> str | None:
    """
    Обратное преобразование: из CSS-кода ("status-watch") → подпись ("Наблюдение").
    """
    if not code:
        return None
    s = STATUS_BY_CODE.get(code)
    return s["label"] if s else None


def allowed_labels() -> list[str]:
    """
    Список всех доступных «подписей» статусов — удобно для дропдаунов и валидации.
    """
    return [s["label"] for s in STATUS_LIST]


def status_groups_for_sidebar() -> list[tuple[str, str]]:
    """
    Пары (code, label) для боковой панели на /visual.
    STATUS_LIST уже отсортирован по order.
    """
    return [(s["code"], s["label"]) for s in STATUS_LIST]