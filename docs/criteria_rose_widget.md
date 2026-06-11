# CriteriaRoseWidget — Модуль диагностики по критериям

## Обзор

`CriteriaRoseWidget` — JavaScript-модуль для диагностики состояния скважины по 6 критериям относительно её исторической нормы. Результаты отображаются в виде полярной диаграммы ("розы критериев") и сохраняются как блоки отчёта.

**Ключевая концепция:** Норма берётся из истории САМОЙ скважины (не парка) — распределение скользящих окон той же длины на том же штуцере.

---

## Установка

Файл модуля: `backend/static/js/criteria_rose_widget.js`

Подключение на странице:
```html
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>  <!-- обязательно -->
<script src="/static/js/criteria_rose_widget.js"></script>
```

---

## Быстрый старт

### 1. HTML-разметка

```html
<!-- Аккордеон диагностики -->
<details class="rose-section" id="rose-section">
  <summary>📊 Диагностика по критериям
    <span id="rose-period-badge"></span>
  </summary>

  <!-- Выбор режима -->
  <select id="rose-mode">
    <option value="balanced">balanced — сбалансированный</option>
    <option value="liquid">liquid — обводнение</option>
    <option value="gsp">gsp — газ-сепаратор</option>
    <option value="purge_cycles">purge_cycles — продувки</option>
  </select>
  <button id="rose-calc-btn">▶ Рассчитать</button>
  <span id="rose-status"></span>

  <!-- Результаты -->
  <div id="rose-result" style="display:none;">
    <div id="rose-score-value"></div>
    <div id="rose-score-bar"></div>
    <div id="rose-meta"></div>
    <div id="rose-chart"></div>
    <div id="rose-contributions"></div>
    <div id="rose-table"></div>
    <div id="rose-warnings"></div>

    <!-- Форма сохранения -->
    <input type="text" id="rose-save-title" placeholder="Название блока">
    <textarea id="rose-save-comment" placeholder="Комментарий"></textarea>
    <button id="rose-save-btn">☑ Добавить в отчёт</button>
    <span id="rose-save-status"></span>
  </div>

  <div id="rose-empty" style="display:none;">
    <span id="rose-empty-msg"></span>
  </div>
</details>
```

### 2. Инициализация виджета

```javascript
const roseWidget = new CriteriaRoseWidget({
  apiBase: '/api/customer-daily',
  blocksApiBase: '/api/customer-daily/blocks',
  getWellId: () => currentWellId,
  getWellNumber: () => document.getElementById('well-select').value,
  getPeriodFrom: () => document.getElementById('date-from').value,
  getPeriodTo: () => document.getElementById('date-to').value,
  getMode: () => document.getElementById('rose-mode').value,
  selectors: {
    calcBtn: '#rose-calc-btn',
    saveBtn: '#rose-save-btn',
    resultContainer: '#rose-result',
    polarChart: '#rose-chart',
    // ... остальные селекторы
  },
  onBlockSaved: (block) => {
    console.log('Блок сохранён:', block);
    refreshBlocks();
  }
});

roseWidget.init();
```

---

## 6 Критериев диагностики

| Ключ | Название | Единицы | Что измеряет |
|------|----------|---------|--------------|
| `decline` | Снижение Q | тыс.м³/сут/сут | Slope регрессии дебита (падение = хуже) |
| `p_wh_down` | Снижение P устья | кгс/см²/сут | Slope давления устья (падение = хуже) |
| `p_wh_cv` | Волатильность P | — | CV = σ/μ давления устья |
| `p_fl_up` | Рост P шлейфа | кгс/см²/сут | Slope давления шлейфа (рост = хуже) |
| `shutdown` | Доля простоя | доля | shutdown_min / (дни × 1440) |
| `freq` | Частота простоев | эпизод/30д | Количество эпизодов, нормировано |

---

## Режимы скоринга

| Режим | Применение | Веса |
|-------|------------|------|
| `balanced` | Сбалансированная оценка | decline=0.35, p_wh_down=0.20, p_wh_cv=0.10, p_fl_up=0.15, shutdown=0.10, freq=0.10 |
| `liquid` | Обводнение/жидкость | decline=0.40, p_wh_down=0.30, p_wh_cv=0.10, p_fl_up=0.00, shutdown=0.10, freq=0.10 |
| `gsp` | Газ-сепаратор/шлейф | decline=0.35, p_wh_down=0.05, p_wh_cv=0.05, p_fl_up=0.40, shutdown=0.10, freq=0.05 |
| `purge_cycles` | Продувочные циклы | decline=0.35, p_wh_down=0.15, p_wh_cv=0.25, p_fl_up=0.00, shutdown=0.10, freq=0.15 |

---

## API Reference

### Конструктор

```javascript
new CriteriaRoseWidget(config)
```

| Параметр | Тип | Описание |
|----------|-----|----------|
| `apiBase` | string | Базовый URL API (default: '/api/customer-daily') |
| `blocksApiBase` | string | URL API блоков (default: '/api/customer-daily/blocks') |
| `getWellId` | function | Getter для ID скважины |
| `getWellNumber` | function | Getter для номера скважины |
| `getPeriodFrom` | function | Getter для начала периода |
| `getPeriodTo` | function | Getter для конца периода |
| `getMode` | function | Getter для режима скоринга |
| `selectors` | object | Карта CSS-селекторов UI элементов |
| `createBlock` | function | Внешняя функция создания блока (опционально) |
| `onBlockSaved` | function | Callback после сохранения |
| `onCalculated` | function | Callback после расчёта |

### Селекторы

```javascript
{
  periodFrom: '#rose-period-from',
  periodTo: '#rose-period-to',
  wellSelect: '#rose-well-select',
  modeSelect: '#rose-mode',
  calcBtn: '#rose-calc-btn',
  saveBtn: '#rose-save-btn',
  resultContainer: '#rose-result',
  emptyContainer: '#rose-empty',
  emptyMsg: '#rose-empty-msg',
  statusText: '#rose-status',
  periodBadge: '#rose-period-badge',
  polarChart: '#rose-chart',
  contribChart: '#rose-contributions',
  scoreValue: '#rose-score-value',
  scoreBar: '#rose-score-bar',
  scoreMeta: '#rose-meta',
  metricsTable: '#rose-table',
  warningsContainer: '#rose-warnings',
  saveTitle: '#rose-save-title',
  saveComment: '#rose-save-comment',
  saveStatus: '#rose-save-status'
}
```

### Методы

#### `init(): void`
Инициализация виджета: привязка event listeners.

#### `calculate(): Promise<object|null>`
Запустить расчёт диагностики. Возвращает результат или null.

#### `renderAll(data): void`
Отрисовать все компоненты по данным.

#### `renderScore(data): void`
Отрисовать плашку со счётом.

#### `renderPolar(data): void`
Отрисовать полярную диаграмму (Plotly).

#### `renderContributions(data): void`
Отрисовать bar-chart вкладов.

#### `renderTable(data): void`
Отрисовать таблицу метрик.

#### `renderWarnings(data): void`
Отрисовать предупреждения.

#### `buildSnapshot(): object|null`
Получить snapshot для сохранения.

#### `save(): Promise<object|null>`
Сохранить результат как блок отчёта.

#### `getLastResult(): object|null`
Получить последний результат расчёта.

#### `hasValidResult(): boolean`
Проверить, есть ли валидный результат.

#### `updatePeriodBadge(): void`
Обновить бейдж с информацией о периоде.

---

## Структура результата compute_rose()

```javascript
{
  ok: true,
  well_number: "123",
  period_from: "2024-01-01",
  period_to: "2024-01-31",
  period_days: 31,
  mode: "balanced",

  weights_raw: { decline: 0.35, p_wh_down: 0.20, ... },
  weights: { decline: 0.35, p_wh_down: 0.20, ... },  // нормированные

  current_raw: {
    decline: 0.0023,
    p_wh_down: 0.015,
    p_wh_cv: 0.08,
    p_fl_up: 0.002,
    shutdown: 0.05,
    freq: 2.1
  },

  history_median_raw: {
    decline: 0.0018,
    p_wh_down: 0.012,
    // ...
  },

  history: {
    choke_mm: 8.0,
    rows_total: 365,
    windows_count: 320,
    window_days: 31,
    step_days: 1,
    history_from: "2023-01-01",
    history_to: "2024-01-31"
  },

  ranks: {
    decline: 65.2,      // 0..100, >50 = хуже нормы
    p_wh_down: 45.8,
    p_wh_cv: 72.1,
    p_fl_up: 38.5,
    shutdown: 55.0,
    freq: 48.3
  },

  contributions: {
    decline: { rank: 65.2, weight: 0.35, actual: 22.82, max: 35.0 },
    p_wh_down: { rank: 45.8, weight: 0.20, actual: 9.16, max: 20.0 },
    // ...
  },

  score: 52.4,  // Σ actual
  weak_data: false,
  warnings: [],

  labels: { decline: "Снижение Q", ... },
  labels_short: { decline: "Q ↓", ... },
  units: { decline: "тыс.м³/сут/сут", ... }
}
```

---

## Визуальные зоны

| Ранг | Зона | Цвет | Значение |
|------|------|------|----------|
| 0-50 | Лучше нормы | Зелёный #28a745 | Метрика лучше медианы истории |
| 50 | Норма | Серый | Медианное значение |
| 50-75 | Хуже нормы | Жёлтый | Внимание |
| 75-100 | Риск | Красный #d62728 | Требует вмешательства |

---

## Интеграция с другими страницами

### Пример: Страница наблюдения

```javascript
const obsRoseWidget = new CriteriaRoseWidget({
  apiBase: '/api/customer-daily',
  getWellId: () => observationWellId,
  getWellNumber: () => observationWellNumber,
  getPeriodFrom: () => obsDateFrom,
  getPeriodTo: () => obsDateTo,
  selectors: {
    calcBtn: '#obs-rose-calc',
    polarChart: '#obs-rose-chart',
    // ... кастомные селекторы для страницы наблюдения
  },
  onBlockSaved: (block) => {
    block.params.chapter = 'observation';  // привязка к главе
    refreshObservationBlocks();
  }
});

obsRoseWidget.init();
```

---

## Backend API

| Метод | URL | Описание |
|-------|-----|----------|
| POST | `/api/customer-daily/rose/preview` | Расчёт розы критериев |

### Request body

```json
{
  "well": "123",
  "period_from": "2024-01-01",
  "period_to": "2024-01-31",
  "mode": "balanced",
  "weights": null,
  "history_step_days": 1
}
```

### Response

См. "Структура результата compute_rose()" выше.

---

## Зависимости

- **Plotly.js** — для полярной диаграммы и bar-chart
- Нет других внешних зависимостей

---

## Файлы модуля

```
backend/
├── static/js/
│   └── criteria_rose_widget.js     # Модуль виджета
├── services/
│   ├── customer_rose_service.py    # Расчёт 6 критериев
│   └── rose_chart_renderer.py      # PNG-рендер для PDF
├── routers/
│   └── customer_daily.py           # API endpoint /rose/preview
├── templates/
│   ├── customer_daily.html         # Пример использования
│   └── latex/
│       └── adaptation_report.tex   # LaTeX-шаблон для PDF
└── models/
    └── customer_report_block.py    # ORM (kind='criteria_rose')
```

---

## Changelog

### v1.0.0 (2024)
- Начальная версия модуля
- Вынесен из customer_daily.html
- Полная документация API
