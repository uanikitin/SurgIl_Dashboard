# Исправление алгоритма отображения оборудования и каналов на visual.html

## Проблема

На странице `visual.html` отображалось "Оборудование не установлено" и "Канал —" даже при наличии данных в базе.

**Причина:**
- Старый код пытался угадать структуру таблиц через SQL-запросы к `information_schema`
- Использовались несуществующие поля (`model`, `installation_date`, `location` в таблице `well_equipment`)
- Не использовалась актуальная система `Equipment` + `EquipmentInstallation`

## Решение

### ✅ Исправленный алгоритм (реализован)

#### 1. Получение активного оборудования

**Источник данных:** `Equipment` + `EquipmentInstallation` (новая система учёта)

```python
# Запрос активных установок через ORM
active_installations = (
    db.query(EquipmentInstallation, Equipment)
    .join(Equipment, Equipment.id == EquipmentInstallation.equipment_id)
    .filter(
        EquipmentInstallation.well_id.in_(well_ids),
        EquipmentInstallation.removed_at.is_(None),  # ещё установлено
        Equipment.deleted_at.is_(None)              # оборудование не удалено
    )
    .order_by(EquipmentInstallation.installed_at.desc())
    .all()
)
```

**Логика:**
- Оборудование считается **активным**, если:
  - `EquipmentInstallation.removed_at IS NULL` (ещё не демонтировано)
  - `Equipment.deleted_at IS NULL` (оборудование не удалено)
- Для каждого активного оборудования:
  - Определяем тип через `equipment_type` или `name`
  - Получаем конфигурацию (иконка, цвет) через `get_equipment_config()`
  - Формируем объект для шаблона с полями: `id`, `type_code`, `model`, `serial`, `installation_location`, `installation_date`, `config`

#### 2. Получение каналов связи

**Источник данных:** `well_channels` (таблица `WellChannel`)

```python
ch_rows = (
    db.query(WellChannel)
    .filter(
        WellChannel.well_id.in_(well_ids),
        WellChannel.ended_at.is_(None),  # активный канал
    )
    .all()
)
```

**Логика:**
- Канал считается **активным**, если `ended_at IS NULL`
- Если для одной скважины несколько активных каналов → берём самый свежий по `started_at`
- Присваиваем `w.current_channel = channel_by_well[well_id].channel`

---

## Связь канала с датчиком давления

### Текущая ситуация

- Канал связи хранится в `well_channels` (общий для скважины)
- Канал общий для **пары датчиков давления** (трубный + шлейфовой)
- Канал устанавливается администратором вручную
- На странице канал показывается отдельно от оборудования

### Варианты решения

#### Вариант 1: Показывать канал только если есть датчик давления (рекомендуемый)

**Логика:**
- Проверяем, есть ли на скважине активное оборудование типа "датчик давления"
- Если есть → показываем канал
- Если нет → скрываем канал или показываем "—"

**Реализация:**

```python
# После получения оборудования и каналов
for w in tiles:
    # Проверяем наличие датчиков давления
    has_pressure_sensor = False
    for eq in w.equipment_active:
        eq_type = eq.get('type_code', '').upper()
        # Проверяем по типу оборудования
        if any(keyword in eq_type for keyword in ['PRESSURE', 'PRESSURE_SENSOR', 'CSMOD', 'SMOD']):
            has_pressure_sensor = True
            break
    
    # Показываем канал только если есть датчик
    if has_pressure_sensor:
        current_ch = channel_by_well.get(w.id)
        w.current_channel = current_ch.channel if current_ch else None
    else:
        w.current_channel = None  # или можно оставить как есть
```

**Плюсы:**
- Логично: канал нужен только для датчиков давления
- Не показывает лишнюю информацию

**Минусы:**
- Если канал установлен, но датчик ещё не установлен → канал не виден

---

#### Вариант 2: Показывать канал всегда (если активен)

**Логика:**
- Канал показывается независимо от наличия оборудования
- Это текущая реализация

**Плюсы:**
- Простота
- Видно, что канал готов к использованию

**Минусы:**
- Может быть непонятно, для чего канал, если нет датчиков

---

#### Вариант 3: Визуальная связь канала с датчиком в UI

**Логика:**
- Канал показывается рядом с датчиком давления в блоке оборудования
- Или канал показывается только в блоке оборудования, если есть датчик

**Реализация в шаблоне:**

```html
{# В блоке оборудования #}
{% for eq in w.equipment_active %}
    {% set eq_type = eq.type_code|upper %}
    {% if 'PRESSURE' in eq_type or 'CSMOD' in eq_type or 'SMOD' in eq_type %}
        {# Датчик давления #}
        <div class="equipment-badge">
            <img src="{{ eq.config.icon }}" alt="{{ eq.config.label }}">
            <div class="equipment-info">
                <div>{{ eq.config.label }}</div>
                {% if w.current_channel %}
                    <small>Канал: {{ w.current_channel }}</small>
                {% endif %}
            </div>
        </div>
    {% else %}
        {# Другое оборудование #}
        <div class="equipment-badge">...</div>
    {% endif %}
{% endfor %}
```

**Плюсы:**
- Визуально понятно, что канал связан с датчиком
- Не дублируется информация

**Минусы:**
- Требует изменений в шаблоне
- Если два датчика давления → канал показывается дважды

---

#### Вариант 4: Хранить связь канал-датчик в БД

**Логика:**
- Добавить поле `channel_id` в `EquipmentInstallation` или `Equipment`
- Или создать связующую таблицу `equipment_channel` (many-to-many)

**Структура:**

```python
# Вариант A: Прямая связь в EquipmentInstallation
class EquipmentInstallation(Base):
    ...
    channel_id = Column(Integer, ForeignKey("well_channels.id"), nullable=True)
    # Канал привязывается к конкретной установке оборудования

# Вариант B: Связующая таблица
class EquipmentChannel(Base):
    __tablename__ = "equipment_channel"
    id = Column(Integer, primary_key=True)
    equipment_id = Column(Integer, ForeignKey("equipment.id"))
    channel_id = Column(Integer, ForeignKey("well_channels.id"))
    well_id = Column(Integer, ForeignKey("wells.id"))
    # Один канал может быть связан с несколькими датчиками
```

**Плюсы:**
- Точная связь в БД
- Можно отслеживать историю изменений

**Минусы:**
- Требует миграции БД
- Усложняет модель данных

---

## Рекомендация

**Для быстрого решения:** Вариант 1 (показывать канал только если есть датчик давления)

**Для долгосрочного решения:** Вариант 4 (хранить связь в БД) + Вариант 3 (визуальная связь в UI)

---

## Проверка работы

После исправления проверьте:

1. ✅ Оборудование отображается на плитках скважин
2. ✅ Иконки оборудования соответствуют типам
3. ✅ Канал связи показывается корректно
4. ✅ Статусы скважин отображаются правильно

### Тестовые данные

Для проверки создайте в БД:

```sql
-- Оборудование
INSERT INTO equipment (name, equipment_type, serial_number, status, deleted_at)
VALUES ('Датчик давления CSMOD', 'PRESSURE_SENSOR', 'CSMOD-001', 'installed', NULL);

-- Установка на скважину
INSERT INTO equipment_installation (equipment_id, well_id, installed_at, removed_at, installation_location)
VALUES (1, 1, NOW(), NULL, 'Устье');

-- Канал связи
INSERT INTO well_channels (well_id, channel, started_at, ended_at)
VALUES (1, 5, NOW(), NULL);
```

После этого на странице `/visual` должно отображаться:
- Оборудование: "Датчик давления" с иконкой
- Канал: "5"

---

## Файлы изменены

- `backend/app.py` (секция D функции `visual_page`, строки ~860-1053)

## Следующие шаги

1. Протестировать на реальных данных
2. Выбрать вариант связи канал-датчик (рекомендую Вариант 1 для начала)
3. При необходимости добавить визуальную связь в шаблоне
