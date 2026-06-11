# TimelineBuilder — Универсальный компонент выбора периода

## Обзор

`TimelineBuilder` — переиспользуемый JS-компонент для визуального выбора временного периода на шкале. Используется в визарде адаптационного отчёта и может применяться в любых других разделах.

**Файл:** `/backend/static/js/timeline_builder.js`

## Быстрый старт

### 1. Подключение

```html
<script src="/static/js/timeline_builder.js"></script>
```

### 2. HTML-контейнер

```html
<div id="my-timeline"></div>
```

### 3. Инициализация

```javascript
const timeline = window.TimelineBuilder.create({
  containerId: 'my-timeline',
  wellId: 42,
  onChange: (from, to) => {
    console.log('Выбран период:', from, '→', to);
  },
});

await timeline.init();
```

## Полная конфигурация

```javascript
const timeline = window.TimelineBuilder.create({
  // ─── ОБЯЗАТЕЛЬНЫЕ ───
  containerId: 'my-timeline',    // ID контейнера в DOM
  wellId: 42,                    // ID скважины

  // ─── ОПЦИОНАЛЬНЫЕ: связь с input полями ───
  dateInputs: {
    from: 'input-from-id',       // ID input для даты "от"
    to: 'input-to-id',           // ID input для даты "до"
  },

  // ─── ОПЦИОНАЛЬНЫЕ: callbacks ───
  onChange: (from, to) => {},         // При изменении периода
  onLoad: (data) => {},               // После загрузки данных
  onSourceChange: (sourceKey) => {},  // При смене источника периода

  // ─── ОПЦИОНАЛЬНЫЕ: функции ───
  features: {
    bars: {
      customer: true,            // Полоса данных заказчика (well_daily)
      sensors: true,             // Полоса данных датчиков (pressure_raw)
    },
    stages: true,                // Этапы (Наблюдение, Адаптация, etc.)
    events: true,                // События (установка, вброс)
    yearFilter: true,            // Кнопки фильтра по году
    anchorMode: true,            // Режим anchor + кнопки ±N
    zoom: true,                  // Кнопки масштаба
    sourcesBlock: true,          // Блок «Источник периода» (radio-кнопки)
  },

  // ─── ОПЦИОНАЛЬНЫЕ: источники периода ───
  sources: [
    { key: 'timeline', label: 'Из таймлайна (выбор на шкале)' },
    { key: 'baseline', label: 'Из baseline B2', from: '2026-02-02', to: '2026-02-17' },
    { key: 'status', label: 'Из журнала статусов', from: '2026-02-02', to: '2026-02-18' },
    { key: 'manual', label: 'Указать вручную' },
  ],

  // ─── ОПЦИОНАЛЬНЫЕ: кастомизация ───
  apiEndpoint: '/api/adaptation-report/timeline',  // Endpoint для данных
  cssPrefix: 'tl',                                 // Префикс CSS классов
});
```

## Публичные методы

### `init()`

Загружает данные и рендерит компонент.

```javascript
await timeline.init();
```

### `getSelection()`

Возвращает текущий выбранный период.

```javascript
const { from, to } = timeline.getSelection();
// { from: '2026-02-02', to: '2026-02-17' }
```

### `setSelection(from, to)`

Программно устанавливает период.

```javascript
timeline.setSelection('2026-02-02', '2026-02-17');
```

### `setSources(sources)`

Обновляет список источников периода.

```javascript
timeline.setSources([
  { key: 'timeline', label: 'Из таймлайна' },
  { key: 'custom', label: 'Мой источник', from: '2026-01-01', to: '2026-01-31' },
]);
```

### `getSelectedSource()`

Возвращает ключ выбранного источника.

```javascript
const sourceKey = timeline.getSelectedSource();
// 'timeline' | 'baseline' | 'manual' | ...
```

### `setSelectedSource(key)`

Выбирает источник и применяет его период.

```javascript
timeline.setSelectedSource('baseline');
```

### `render()`

Перерисовывает компонент.

```javascript
timeline.render();
```

### `destroy()`

Очищает слушатели и удаляет DOM.

```javascript
timeline.destroy();
```

## Пример: Интеграция в главу визарда

```javascript
// В renderStepN(main):
function renderStep4(main) {
  const wellId = state.steps[0].data.wellId;
  const d = state.steps[4].data;

  main.innerHTML = `
    <h2>Этап адаптации (Step 4)</h2>

    <div class="wz-section">
      <h3>Выбор периода адаптации</h3>
      <div id="step4-timeline"></div>

      <div class="wz-row">
        <div class="wz-field">
          <label>Начало</label>
          <input type="date" id="step4-from" readonly>
        </div>
        <div class="wz-field">
          <label>Окончание</label>
          <input type="date" id="step4-to" readonly>
        </div>
      </div>

      <button id="step4-analyze" class="wz-btn wz-btn-primary">
        Проанализировать
      </button>
    </div>
  `;

  // Инициализация таймлайна
  const timeline = window.TimelineBuilder.create({
    containerId: 'step4-timeline',
    wellId: wellId,
    dateInputs: { from: 'step4-from', to: 'step4-to' },
    features: {
      bars: { customer: true, sensors: true },
      stages: true,
      events: true,
      yearFilter: true,
      anchorMode: true,
      zoom: true,
      sourcesBlock: true,
    },
    sources: [
      { key: 'timeline', label: 'Из таймлайна (выбор на шкале)' },
      {
        key: 'status',
        label: 'Из журнала статусов (well_status)',
        from: d.statusFrom,
        to: d.statusTo
      },
      { key: 'manual', label: 'Указать вручную' },
    ],
    onChange: (from, to) => {
      d.from = from;
      d.to = to;
      // Можно авто-запускать анализ:
      // runAnalysis();
    },
    onSourceChange: (sourceKey) => {
      d.source = sourceKey;
      const manual = sourceKey === 'manual';
      document.getElementById('step4-from').readOnly = !manual;
      document.getElementById('step4-to').readOnly = !manual;
    },
  });

  timeline.init();

  // Сохраняем ссылку для cleanup
  state.steps[4].timeline = timeline;
}
```

## Взаимодействие с пользователем

### Выбор периода этапа

1. **Клик по полоске этапа** на шкале → выбирается весь диапазон этапа
2. **Клик по точке этапа** → выбирается весь диапазон этапа
3. **Клик по кнопке этапа** под шкалой → выбирается весь диапазон этапа

### Произвольный выбор

1. **Клик по пустому месту** на шкале → устанавливается точка отсчёта (anchor)
2. **Кнопки ±N** → расширяют диапазон от точки отсчёта

### Точечные события

1. **Клик по кнопке события** (Установка, Первый вброс) → устанавливается точка отсчёта

## API данных

Компонент загружает данные из:

```
GET /api/adaptation-report/timeline?well_id={wellId}
```

Ожидаемый формат ответа:

```json
{
  "today": "2026-05-21",
  "customer_data": {
    "first_date": "2025-01-01",
    "last_date": "2026-05-21",
    "days_count": 507
  },
  "our_data": {
    "first_date": "2026-01-15",
    "last_date": "2026-05-21",
    "days_count": 127
  },
  "stages": [
    {
      "label": "Наблюдение",
      "dt_start": "2026-02-02T00:00:00",
      "dt_end": "2026-02-18T00:00:00",
      "color": "#22c55e"
    },
    {
      "label": "Адаптация",
      "dt_start": "2026-02-18T00:00:00",
      "dt_end": "2026-02-25T00:00:00",
      "color": "#3b82f6"
    }
  ],
  "events": [
    {
      "kind": "equip",
      "label": "Установка оборудования",
      "dt": "2026-02-02T10:30:00",
      "icon": "🔧",
      "color": "#6b7280"
    },
    {
      "kind": "inject",
      "label": "Первый вброс",
      "dt": "2026-02-17T14:00:00",
      "icon": "💧",
      "color": "#f59e0b"
    }
  ]
}
```

## CSS-классы

По умолчанию используется префикс `tl-`. Основные классы:

| Класс | Описание |
|-------|----------|
| `.tl-wrap` | Основной контейнер |
| `.tl-scale` | Область шкалы |
| `.tl-stage-band` | Полоска этапа |
| `.tl-stage-dot` | Точка начала этапа |
| `.tl-range` | Выбранный диапазон |
| `.tl-anchor-bar` | Панель ±N от точки |
| `.tl-sources-block` | Блок источников |

## Миграция с inline-кода

Если в шаблоне уже есть inline-реализация (как `wz3tl-*` в `adaptation_wizard.html`), можно мигрировать на `TimelineBuilder`:

1. Подключить `timeline_builder.js`
2. Заменить inline-код на вызов `TimelineBuilder.create()`
3. Убедиться, что API возвращает данные в нужном формате
4. Настроить callbacks для интеграции с формой

## Файлы

- `/backend/static/js/timeline_builder.js` — основной компонент
- `/backend/templates/adaptation_wizard.html` — пример использования (inline wz3tl-*)
- `/backend/static/js/observation_ui.js` — пример интеграции (опционально)
