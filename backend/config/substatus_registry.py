import json
from pathlib import Path

CONFIG_DIR = Path(__file__).parent
SUBSTATUS_FILE = CONFIG_DIR / "substatuses.json"


def load_substatuses() -> list[dict]:
    with SUBSTATUS_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data.sort(key=lambda s: s.get("order", 0))
    return data


SUBSTATUS_LIST: list[dict] = load_substatuses()

SUBSTATUS_BY_CODE: dict[str, dict] = {s["code"]: s for s in SUBSTATUS_LIST}
SUBSTATUS_BY_LABEL: dict[str, dict] = {s["label"]: s for s in SUBSTATUS_LIST}

DEFAULT_SUBSTATUS_CODE = "substatus-working"
DEFAULT_SUBSTATUS_LABEL = "В работе"
DEFAULT_SUBSTATUS_COLOR = "#10b981"


def css_by_label(label: str | None) -> str:
    if not label:
        return DEFAULT_SUBSTATUS_CODE
    s = SUBSTATUS_BY_LABEL.get(label)
    return s["code"] if s else DEFAULT_SUBSTATUS_CODE


def color_by_label(label: str | None) -> str:
    if not label:
        return DEFAULT_SUBSTATUS_COLOR
    s = SUBSTATUS_BY_LABEL.get(label)
    return s["color"] if s else DEFAULT_SUBSTATUS_COLOR


def allowed_labels() -> list[str]:
    return [s["label"] for s in SUBSTATUS_LIST]
