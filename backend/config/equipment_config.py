"""
Конфигурация оборудования и статусов
Все настройки иконок, цветов и типов здесь
"""

"""
Конфигурация оборудования и статусов
Все настройки иконок, цветов и типов здесь
"""

EQUIPMENT_TYPES = {
    'PRESSURE_SENSOR': {
        'code': 'PRESSURE_SENSOR',
        'label': 'Датчик давления',
        'short': 'Дат.давл.',
        'color': '#3b82f6',
        'icon': '/static/icons/СSMOD_1.png',  # или CSMOD_1.png
        'border_color': '#3b82f6',
        'background_gradient': 'none',
        # 'background_gradient': 'linear-gradient(135deg, #dbeafe 0%, #bfdbfe 100%)',
        'category': 'sensor'
    },
    'GATEWAY': {
        'code': 'GATEWAY',
        'label': 'Шлюз устьевой',
        'short': 'Шлюз',
        'color': '#ef4444',
        'icon': '/static/icons/WH_launcher_1.png',
        'border_color': '#ef4444',
        # 'background_gradient': 'linear-gradient(135deg, #fee2e2 0%, #fecaca 100%)',
        'background_gradient': 'none',
        'category': 'gateway'
    },
    'WELLHEAD_SENSOR': {
        'code': 'WELLHEAD_SENSOR',
        'label': 'Датчик устья',
        'short': 'ДУ',
        'color': '#3b82f6',
        'icon': '/static/icons/SMOD_1.png',
        'border_color': '#3b82f6',
        # 'background_gradient': 'linear-gradient(135deg, #dbeafe 0%, #bfdbfe 100%)',
'background_gradient': 'none',
        'category': 'sensor'
    },
    'LINE_SENSOR': {
        'code': 'LINE_SENSOR',
        'label': 'Датчик шлейфа',
        'short': 'ДШ',
        'color': '#16a34a',
        'icon': '/static/icons/SMOD_1.png',
        'border_color': '#16a34a',
        'background_gradient': 'linear-gradient(135deg, #dcfce7 0%, #bbf7d0 100%)',
        'category': 'sensor'
    },
    'ANNULUS_SENSOR': {
        'code': 'ANNULUS_SENSOR',
        'label': 'Датчик затрубного пространства',
        'short': 'ДЗ',
        'color': '#f59e0b',
        'icon': '/static/icons/SMOD_1.png',
        'border_color': '#f59e0b',
        'background_gradient': 'linear-gradient(135deg, #fef3c7 0%, #fde68a 100%)',
        'category': 'sensor'
    },
    'TEMPERATURE_SENSOR': {
        'code': 'TEMPERATURE_SENSOR',
        'label': 'Датчик температуры',
        'short': 'Дат.темп.',
        'color': '#8b5cf6',
        'icon': '/static/icons/temp_sensor.png',
        'border_color': '#8b5cf6',
        'background_gradient': 'linear-gradient(135deg, #ede9fe 0%, #ddd6fe 100%)',
        'category': 'sensor'
    },
    'ESP': {
        'code': 'ESP',
        'label': 'Электроцентробежный насос',
        'short': 'ЭЦН',
        'color': '#ec4899',
        'icon': '/static/icons/ESP.png',
        'border_color': '#ec4899',
        'background_gradient': 'linear-gradient(135deg, #fce7f3 0%, #fbcfe8 100%)',
        'category': 'pump'
    }
}

# Алиасы для автоматического определения типа
EQUIPMENT_ALIASES = {
    # Русские названия
    'датчик давления': 'PRESSURE_SENSOR',
    'датчик давлен': 'PRESSURE_SENSOR',
    'сенсор давления': 'PRESSURE_SENSOR',
    'шлюз': 'GATEWAY',
    'шлюз устьевой': 'GATEWAY',
    'шлюз устья': 'GATEWAY',
    'датчик устья': 'WELLHEAD_SENSOR',
    'датчик шлейфа': 'LINE_SENSOR',
    'датчик затрубного': 'ANNULUS_SENSOR',
    'датчик температуры': 'TEMPERATURE_SENSOR',
    'датчик темп': 'TEMPERATURE_SENSOR',
    'эцн': 'ESP',
    'насос': 'ESP',
    'электроцентробежный': 'ESP',

    # Английские названия
    'pressure sensor': 'PRESSURE_SENSOR',
    'pressure gauge': 'PRESSURE_SENSOR',
    'gateway': 'GATEWAY',
    'wellhead sensor': 'WELLHEAD_SENSOR',
    'line sensor': 'LINE_SENSOR',
    'annulus sensor': 'ANNULUS_SENSOR',
    'temperature sensor': 'TEMPERATURE_SENSOR',
    'temp sensor': 'TEMPERATURE_SENSOR',
    'esp': 'ESP',
    'pump': 'ESP'
}

# Сопоставление кодов из базы данных с нашими типами
EQUIPMENT_MAPPING = {
    # Пример: если в базе equipment_type = 'CSMOD', то это PRESSURE_SENSOR
    'CSMOD': 'PRESSURE_SENSOR',
    'СSMOD': 'PRESSURE_SENSOR',
    'WH_LAUNCHER': 'GATEWAY',
    'WHLAUNCHER': 'GATEWAY',
    'ESP': 'ESP',
    'ЭЦН': 'ESP',
    'TEMP': 'TEMPERATURE_SENSOR',
    # Добавьте свои маппинги здесь
}


# Функция для нормализации типа оборудования
def normalize_equipment_type(equipment_type: str) -> str:
    """
    Нормализует тип оборудования из базы данных в наш стандартный код
    """
    if not equipment_type:
        return None

    # 1. Проверяем прямое совпадение с нашими кодами
    if equipment_type in EQUIPMENT_TYPES:
        return equipment_type

    # 2. Проверяем маппинг кодов
    equipment_type_upper = equipment_type.upper().strip()
    if equipment_type_upper in EQUIPMENT_MAPPING:
        return EQUIPMENT_MAPPING[equipment_type_upper]

    # 3. Проверяем алиасы (регистронезависимо)
    equipment_type_lower = equipment_type.lower().strip()
    for alias, code in EQUIPMENT_ALIASES.items():
        if alias in equipment_type_lower:
            return code

    # 4. Проверяем по ключевым словам в названии
    for keyword, code in EQUIPMENT_ALIASES.items():
        if any(keyword in part for part in equipment_type_lower.split()):
            return code

    return None


# Остальные функции оставляем как есть
def get_equipment_config(code=None, equipment_type=None):
    """Получить конфигурацию оборудования по коду или типу"""
    # Нормализуем тип
    normalized_code = None

    if code:
        normalized_code = normalize_equipment_type(code)
    elif equipment_type:
        normalized_code = normalize_equipment_type(equipment_type)

    # Получаем конфигурацию
    if normalized_code and normalized_code in EQUIPMENT_TYPES:
        return EQUIPMENT_TYPES[normalized_code]

    # Если не нашли - возвращаем конфигурацию по умолчанию
    return {
        'label': equipment_type or code or 'Неизвестный тип',
        'short': equipment_type or '?',
        'icon': '/static/icons/default.png',
        'color': '#6b7280',
        'border_color': '#6b7280',
        'background_gradient': 'linear-gradient(135deg, #f3f4f6 0%, #e5e7eb 100%)',
        'category': 'unknown'
    }


# Остальные функции (get_status_config, get_condition_config) остаются без изменений

# Поиск по ключевым словам (для резервного определения типа)
EQUIPMENT_KEYWORDS = {
    'датчик': {
        'icon': '/static/icons/SMOD.png',
        'border_color': '#3b82f6',
        'label_prefix': 'Датчик'
    },
    'сенсор': {
        'icon': '/static/icons/SMOD.png',
        'border_color': '#3b82f6',
        'label_prefix': 'Сенсор'
    },
    'шлюз': {
        'icon': '/static/icons/WH_launcer.png',
        'border_color': '#ef4444',
        'label_prefix': 'Шлюз'
    },
    'gateway': {
        'icon': '/static/icons/WH_launcer.png',
        'border_color': '#ef4444',
        'label_prefix': 'Gateway'
    }
}

# Статусы оборудования
STATUSES = {
    'status-watch': {
        'label': 'Наблюдение',
        'color': '#007bff',
        'icon': '/static/icons/status-watch.svg',
        'badge_style': 'background: linear-gradient(135deg, #007bff 0%, #0056b3 100%); color: white;'
    },
    'status-adapt': {
        'label': 'Адаптация',
        'color': '#17a2b8',
        'icon': '/static/icons/status-adapt.svg',
        'badge_style': 'background: linear-gradient(135deg, #17a2b8 0%, #117a8b 100%); color: white;'
    },
    'status-opt': {
        'label': 'Оптимизация',
        'color': '#28a745',
        'icon': '/static/icons/status-opt.svg',
        'badge_style': 'background: linear-gradient(135deg, #28a745 0%, #1e7e34 100%); color: white;'
    },
    'status-dev': {
        'label': 'Освоение',
        'color': '#ffc107',
        'icon': '/static/icons/status-dev.svg',
        'badge_style': 'background: linear-gradient(135deg, #ffc107 0%, #e0a800 100%); color: #212529;'
    },
    'status-off': {
        'label': 'Не обслуживается',
        'color': '#6c757d',
        'icon': '/static/icons/status-off.svg',
        'badge_style': 'background: linear-gradient(135deg, #6c757d 0%, #545b62 100%); color: white;'
    },
    'status-idle': {
        'label': 'Простой',
        'color': '#fd7e14',
        'icon': '/static/icons/status-idle.svg',
        'badge_style': 'background: linear-gradient(135deg, #fd7e14 0%, #e8590c 100%); color: white;'
    },
    'status-other': {
        'label': 'Другое',
        'color': '#343a40',
        'icon': '/static/icons/status-other.svg',
        'badge_style': 'background: linear-gradient(135deg, #343a40 0%, #212529 100%); color: white;'
    }
}

# Состояния оборудования
CONDITIONS = {
    'working': {
        'label': 'Робоче',
        'color': '#16a34a',
        'icon': '✓',
        'badge_style': 'color:#16a34a; background:#dcfce7;'
    },
    'needs_repair': {
        'label': 'Потребує ремонту',
        'color': '#ea580c',
        'icon': '⚠',
        'badge_style': 'color:#ea580c; background:#ffedd5;'
    },
    'broken': {
        'label': 'Не робоче',
        'color': '#dc2626',
        'icon': '✗',
        'badge_style': 'color:#dc2626; background:#fee2e2;'
    }
}



def get_status_config(status_code):
    """Получить конфигурацию статуса"""
    return STATUSES.get(status_code, STATUSES['status-other'])


def get_condition_config(condition):
    """Получить конфигурацию состояния"""
    return CONDITIONS.get(condition, {
        'label': 'Неизвестно',
        'color': '#6b7280',
        'icon': '?',
        'badge_style': 'color:#6b7280; background:#f3f4f6;'
    })