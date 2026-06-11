# SegmentBlocksWidget — Модуль управления блоками сегментного анализа

## Обзор

`SegmentBlocksWidget` — JavaScript-модуль для сохранения результатов сегментного анализа временных рядов как блоков отчёта. Поддерживает два типа блоков:

- **segment_analysis** — полный анализ периода (график, сегменты, вбросы, интерпретация)
- **segment_comparison** — сравнение нескольких сегментов (overlay-график, таблица различий)

Блоки сохраняются в таблицу `customer_report_block` и могут быть включены в PDF-отчёт.

---

## Установка

Файл модуля: `backend/static/js/segment_blocks_widget.js`

Подключение на странице:
```html
<script src="/static/js/segment_blocks_widget.js"></script>
```

CSS-стили инжектируются автоматически при загрузке модуля.

---

## Быстрый старт

### 1. HTML-разметка для плиток

```html
<!-- Панель сохранённых блоков -->
<div id="saved-blocks-panel" class="saved-blocks-panel" style="display:none;">
  <h3>📦 Сохранённые блоки анализа</h3>
  <div id="saved-blocks-grid" class="saved-blocks-grid"></div>
</div>
```

### 2. Инициализация виджета

```javascript
const blocksWidget = new SegmentBlocksWidget({
  wellId: 123,                              // ID скважины (обязательно)
  wellNumber: '123-бис',                    // Номер скважины для отображения
  apiBase: '/api/customer-daily/blocks',    // Базовый URL API
  panelSelector: '#saved-blocks-panel',     // Селектор панели
  gridSelector: '#saved-blocks-grid',       // Селектор сетки плиток
  kinds: ['segment_analysis', 'segment_comparison'],  // Типы блоков
  chapterFilter: null,                      // Фильтр по главе (опционально)
  onBlockSaved: (block) => {},              // Callback после сохранения
  onBlockDeleted: (blockId) => {}           // Callback после удаления
});
```

### 3. Загрузка существующих блоков

```javascript
// При загрузке страницы
blocksWidget.loadBlocks();
```

### 4. Сохранение анализа

```javascript
// При клике на кнопку "Сохранить анализ"
await blocksWidget.saveAnalysis({
  chartData: () => chartData,           // Getter для данных графика
  segments: () => segments,             // Getter для массива сегментов
  changepoints: () => changepoints,     // Getter для точек перелома
  eventsData: () => eventsData,         // Getter для событий (вбросы)
  period: { from: '2024-01-01', to: '2024-12-31' },
  series: 'flow_rate',
  generateSummary: () => 'Текст резюме' // Опционально
});
```

### 5. Сохранение сравнения

```javascript
// При клике на кнопку "Записать сравнение"
await blocksWidget.saveComparison({
  chartData: () => chartData,
  segments: () => segments,
  selectedForCompare: () => selectedForCompare,  // Set с номерами сегментов
  compareColors: ['#8b5cf6', '#0891b2', '#22c55e', '#f59e0b']
});
```

---

## API Reference

### Конструктор

```javascript
new SegmentBlocksWidget(config)
```

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| `wellId` | number | 0 | ID скважины в БД |
| `wellNumber` | string | '' | Номер скважины для отображения |
| `apiBase` | string | '/api/customer-daily/blocks' | Базовый URL API блоков |
| `panelSelector` | string | '#saved-blocks-panel' | CSS-селектор панели |
| `gridSelector` | string | '#saved-blocks-grid' | CSS-селектор сетки плиток |
| `kinds` | string[] | ['segment_analysis', 'segment_comparison'] | Фильтр типов блоков |
| `chapterFilter` | string | null | Фильтр по главе отчёта |
| `onBlockSaved` | function | () => {} | Callback после сохранения |
| `onBlockDeleted` | function | () => {} | Callback после удаления |

### Методы

#### `saveAnalysis(dataGetters): Promise<object|null>`

Сохраняет сегментный анализ как блок.

**Параметры dataGetters:**

| Параметр | Тип | Описание |
|----------|-----|----------|
| `chartData` | function/object | Данные графика `{dates: [], primary: {values: []}}` |
| `segments` | function/array | Массив сегментов |
| `changepoints` | function/array | Массив индексов точек перелома |
| `eventsData` | function/object | События `{timeline_injections: [...]}` |
| `period` | object | `{from: 'YYYY-MM-DD', to: 'YYYY-MM-DD'}` |
| `series` | string | Название ряда данных |
| `generateSummary` | function | Генератор текстового резюме (опционально) |

**Возвращает:** объект созданного блока или `null` при ошибке/отмене.

---

#### `saveComparison(dataGetters): Promise<object|null>`

Сохраняет сравнение сегментов как блок.

**Параметры dataGetters:**

| Параметр | Тип | Описание |
|----------|-----|----------|
| `chartData` | function/object | Данные графика |
| `segments` | function/array | Массив сегментов |
| `selectedForCompare` | function/Set | Set номеров выбранных сегментов |
| `compareColors` | string[] | Массив цветов для сегментов |

**Возвращает:** объект созданного блока или `null`.

---

#### `loadBlocks(): Promise<void>`

Загружает и отображает плитки сохранённых блоков.

---

#### `deleteBlock(blockId): Promise<void>`

Удаляет блок после подтверждения пользователем.

---

#### `updateBlockInReport(blockId, value): Promise<void>`

Обновляет флаг `in_report` через API.

---

#### `updateBlockSetting(blockId, key, value): void`

Сохраняет настройку отображения в localStorage.

---

#### `SegmentBlocksWidget.injectStyles(): void` (static)

Вставляет CSS-стили в `<head>`. Вызывается автоматически при загрузке модуля.

---

## Структура snapshot

### segment_analysis_v1

```javascript
{
  _v: "segment_analysis_v1",
  schema_version: "1.0",
  computed_at: "2024-01-15T10:30:00.000Z",
  ok: true,
  well_id: 123,
  well_number: "123-бис",
  period: { from: "2024-01-01", to: "2024-01-31" },
  chart_data: { dates: [...], primary: { values: [...] } },
  segments_extended: [
    {
      num: 1,
      start_idx: 0,
      end_idx: 100,
      start_date: "2024-01-01",
      end_date: "2024-01-05",
      type: "stable",
      mean_value: 125.5,
      std_value: 3.2,
      min_value: 118.0,
      max_value: 132.0,
      slope: 0.02,
      intercept: 125.0
    },
    // ...
  ],
  cp_marks: [
    { idx: 100, date: "2024-01-05 10:00", tag: "CP1", source: "only_total" }
  ],
  injections_table: {
    total_count: 5,
    by_reagent: { "Пенный реагент": { count: 3, total_kg: 15.0 } },
    by_segment: [{ segment_num: 1, count: 2, reagents: ["Пенный реагент"] }],
    events: [{ date: "...", segment_num: 1, reagent: "...", amount_kg: 5.0 }]
  },
  interpretation: {
    summary: "Обнаружено 3 сегмента...",
    descriptions: ["Сегмент 1: stable, среднее 125.50"]
  },
  display_settings: {
    show_chart: true,
    show_injections_table: true,
    show_interpretation: true,
    in_report: true
  }
}
```

### segment_comparison_v1

```javascript
{
  _v: "segment_comparison_v1",
  schema_version: "1.0",
  computed_at: "2024-01-15T10:30:00.000Z",
  well_id: 123,
  well_number: "123-бис",
  segments_compared: [
    {
      segment_num: 1,
      period: { from: "2024-01-01", to: "2024-01-05" },
      color: "#8b5cf6",
      label: "Сегмент 1",
      mean_value: 125.5,
      std_value: 3.2,
      min_value: 118.0,
      max_value: 132.0,
      slope: 0.02,
      intercept: 125.0
    },
    // ...
  ],
  chart_overlay: {
    normalized_x: [0, 1, 2, ...],
    series: [
      { segment_num: 1, values: [...] },
      { segment_num: 2, values: [...] }
    ]
  },
  diff_table: {
    metrics: ["mean", "std", "min", "max", "slope"],
    values: [...],
    deltas: {
      mean: { abs: 5.2, pct: "4.1" },
      std: { abs: "0.50", pct: null }
    }
  },
  interpretation: {
    summary: "Сравнение 2 сегментов"
  },
  display_settings: {
    show_chart: true,
    show_diff_table: true,
    show_interpretation: true,
    in_report: true
  }
}
```

---

## Интеграция с другими страницами

### Пример: Страница "Наблюдение"

```html
<!-- observation.html -->
<script src="/static/js/segment_blocks_widget.js"></script>

<div id="obs-blocks-panel" class="saved-blocks-panel" style="display:none;">
  <h3>📦 Блоки главы "Наблюдение"</h3>
  <div id="obs-blocks-grid" class="saved-blocks-grid"></div>
</div>

<script>
  const obsBlocksWidget = new SegmentBlocksWidget({
    wellId: {{ well_id }},
    wellNumber: "{{ well_number }}",
    panelSelector: '#obs-blocks-panel',
    gridSelector: '#obs-blocks-grid',
    chapterFilter: 'observation',  // <-- Фильтр по главе
    kinds: ['segment_analysis', 'segment_comparison']
  });

  // Загрузка блоков только для главы "observation"
  obsBlocksWidget.loadBlocks();

  // Кнопка сохранения
  document.getElementById('save-btn').onclick = async () => {
    await obsBlocksWidget.saveAnalysis({
      chartData: () => myChartData,
      segments: () => mySegments,
      changepoints: () => myChangepoints,
      eventsData: () => myEventsData,
      period: { from: periodStart, to: periodEnd },
      series: 'dp_avg'
    });
  };
</script>
```

### Фильтрация по главе (chapterFilter)

Если указан `chapterFilter`, виджет:
1. Добавляет `params.chapter` при создании блока
2. Передаёт `?chapter=...` при загрузке блоков

Для работы фильтрации API должен поддерживать параметр `chapter`.

---

## Backend API

Виджет использует следующие endpoints:

| Метод | URL | Описание |
|-------|-----|----------|
| GET | `/api/customer-daily/blocks?well_id=123` | Список блоков |
| POST | `/api/customer-daily/blocks` | Создание блока |
| PUT | `/api/customer-daily/blocks/{id}` | Обновление блока |
| DELETE | `/api/customer-daily/blocks/{id}` | Удаление блока |

### Модель данных

Таблица: `customer_report_block`

| Поле | Тип | Описание |
|------|-----|----------|
| id | int | PK |
| well_id | int | FK → wells.id |
| kind | string(32) | 'segment_analysis' / 'segment_comparison' |
| title | string(200) | Название блока |
| params | JSONB | Параметры (period, series, chapter) |
| data_snapshot | JSONB | Снимок данных |
| in_report | bool | Включить в отчёт |
| sort_order | int | Порядок сортировки |
| created_at | datetime | Дата создания |
| updated_at | datetime | Дата обновления |

---

## Кастомизация стилей

CSS-классы для переопределения:

```css
/* Панель */
.saved-blocks-panel { }

/* Сетка плиток */
.saved-blocks-grid { }

/* Карточка блока */
.saved-block-card { }
.saved-block-card.kind-segment_analysis { }
.saved-block-card.kind-segment_comparison { }

/* Элементы карточки */
.saved-block-card .block-kind-badge { }
.saved-block-card .block-title { }
.saved-block-card .block-metrics { }
.saved-block-card .block-settings { }
.saved-block-card .block-actions { }
```

---

## Зависимости

- Нет внешних зависимостей
- Совместим со всеми современными браузерами
- Использует Fetch API, async/await, ES6 классы

---

## Версионирование снапшотов

Каждый snapshot содержит поле `_v` с версией схемы:
- `segment_analysis_v1`
- `segment_comparison_v1`

При изменении структуры данных создаётся новая версия. Старые снапшоты остаются читаемыми.

---

## Файлы модуля

```
backend/
├── static/js/
│   └── segment_blocks_widget.js    # Модуль виджета
├── templates/
│   └── segment_result.html         # Пример использования
├── services/
│   └── customer_daily_service.py   # VALID_BLOCK_KINDS
└── models/
    └── customer_report_block.py    # ORM модель
```

---

## Changelog

### v1.0.0 (2024)
- Начальная версия модуля
- Поддержка segment_analysis и segment_comparison
- Автоматическая инжекция CSS
- Плитки с настройками отображения
