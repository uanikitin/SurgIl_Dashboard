import json
from pathlib import Path

# Путь к JSON с типами оборудования: backend/equipment.json
EQUIPMENT_JSON_PATH = Path(__file__).resolve().parent.parent / "equipment.json"


def load_equipment_types():
    """Загрузить список типов оборудования из equipment.json.

    Возвращает:
        (equipment_list, equipment_by_code)
        equipment_list: list[dict]
        equipment_by_code: dict[str, dict]
    """
    with open(EQUIPMENT_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    by_code = {item["code"]: item for item in data}
    return data, by_code


# Загружаем один раз при импорте
EQUIPMENT_LIST, EQUIPMENT_BY_CODE = load_equipment_types()