# Интеграция сегментного анализа в главу «Наблюдение»

## Обзор

Модуль `ObservationSegmentWidget` интегрирует полный сегментный анализ (из customer_daily) в страницу «Наблюдение» визарда адаптации. Результаты сохраняются как блоки kind=`segment_analysis` с `chapter='observation'` и включаются в PDF отчёт главы.

---

## Архитектура

```
┌─────────────────────────────────────────────────────────────────────┐
│  adaptation_wizard.html (Step 3 — Наблюдение)                       │
│                                                                     │
│  ┌─────────────────────────────────────┐                            │
│  │  ObservationSegmentWidget           │                            │
│  │  ├─ Выбор периода (date inputs)     │                            │
│  │  ├─ Расчёт → API segment-analysis   │                            │
│  │  ├─ График (Plotly)                 │                            │
│  │  ├─ Таблица сегментов               │                            │
│  │  └─ Сохранение в отчёт              │                            │
│  └─────────────────────────────────────┘                            │
│                     │                                               │
│                     ▼                                               │
│  POST /api/customer-daily/blocks                                    │
│  {kind: 'segment_analysis', params: {chapter: 'observation'}}       │
│                     │                                               │
│                     ▼                                               │
│  GET /api/observation/blocks → chapter_preview → PDF                │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Файлы

| Файл | Назначение |
|------|------------|
| `backend/static/js/observation_segment_widget.js` | JS виджет для UI |
| `backend/templates/adaptation_wizard.html` | HTML секция + инициализация |
| `backend/routers/observation.py` | Обработка passthrough kinds |
| `backend/services/observation_chapter_renderer.py` | HTML/LaTeX рендеринг |
| `backend/constants/observation_parts.py` | Каталог parts для блоков |

---

## Использование

### 1. HTML разметка (уже встроена)

```html
<div class="obs-seg-widget" id="obs-seg-container">
  <h4>📊 Сегментный анализ периода</h4>

  <div class="obs-seg-period-row">
    <label>Период:</label>
    <input type="date" id="obs-seg-from">
    <span>—</span>
    <input type="date" id="obs-seg-to">
    <button id="obs-seg-calc-btn">▶ Рассчитать</button>
  </div>

  <div id="obs-seg-result" style="display:none;">
    <div id="obs-seg-chart"></div>
    <div id="obs-seg-table"></div>
  </div>

  <div id="obs-seg-save-block" style="display:none;">
    <input type="text" id="obs-seg-save-title">
    <textarea id="obs-seg-save-comment"></textarea>
    <button id="obs-seg-save-btn">💾 Сохранить в отчёт</button>
  </div>

  <div id="obs-seg-blocks-panel" style="display:none;">
    <div id="obs-seg-blocks-grid"></div>
  </div>
</div>
```

### 2. Инициализация виджета

```javascript
window._obsSegmentWidget = new window.ObservationSegmentWidget({
  apiBase: '/api/customer-daily',
  blocksApiBase: '/api/customer-daily/blocks',
  getWellId: () => state.well?.id,
  getWellNumber: () => state.well?.number,
  getPeriodFrom: () => document.getElementById('obs-seg-from')?.value,
  getPeriodTo: () => document.getElementById('obs-seg-to')?.value,
  onBlockSaved: (block) => {
    // Обновить правую панель и список блоков
    window._obsChapterPanel?.refresh();
  },
});
window._obsSegmentWidget.init();
```

---

## API Reference

### ObservationSegmentWidget

#### Конструктор

```javascript
new ObservationSegmentWidget(config)
```

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| `apiBase` | string | '/api/customer-daily' | Базовый URL API сегментного анализа |
| `blocksApiBase` | string | '/api/customer-daily/blocks' | URL API блоков |
| `getWellId` | function | — | Getter ID скважины |
| `getWellNumber` | function | — | Getter номера скважины |
| `getPeriodFrom` | function | — | Getter начала периода |
| `getPeriodTo` | function | — | Getter конца периода |
| `selectors` | object | DEFAULT_SELECTORS | Карта CSS-селекторов |
| `onBlockSaved` | function | — | Callback после сохранения блока |
| `onCalculated` | function | — | Callback после расчёта |

#### Методы

| Метод | Описание |
|-------|----------|
| `init()` | Инициализация виджета, привязка событий |
| `calculate()` | Запуск расчёта сегментного анализа |
| `save()` | Сохранение результата как блока |
| `setWell(id, number)` | Установка текущей скважины |
| `refresh()` | Перезагрузка списка блоков |

---

## Структура snapshot (segment_analysis_v1)

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
      end_date: "2024-01-15",
      type: "stable",        // stable | rising | falling | volatile
      mean_value: 12.5,
      std_value: 0.8,
      slope: 0.002,
      intercept: 12.0,
      q_avg: 45.2,
    },
    // ...
  ],
  cp_marks: [
    { date: "2024-01-15", tag: "CP1" }
  ],
  interpretation: {
    summary: "Обнаружено 3 сегмента...",
    descriptions: ["Сегмент 1: стабильный, среднее 12.50"]
  },
  display_settings: {
    show_chart: true,
    show_segments_table: true,
    show_interpretation: true,
    in_report: true
  }
}
```

---

## Рендеринг в отчёт

### HTML (preview)

Блоки `segment_analysis` рендерятся в `render_observation_chapter()` через `render_segment_analysis()`:

- Заголовок блока
- Мета-информация (период, количество сегментов)
- Таблица сегментов с цветовой индикацией типа
- Точки перелома
- Текстовая интерпретация

### LaTeX (PDF)

Рендеринг через `render_segment_analysis()` → `RenderResult.latex`:

- `\subsection*{Название блока}`
- Таблица `tabular` с сегментами
- Текст интерпретации

---

## Цветовая схема сегментов

| Тип | Цвет | Описание |
|-----|------|----------|
| stable | #22c55e (зелёный) | Стабильный режим |
| rising | #3b82f6 (синий) | Рост |
| falling | #ef4444 (красный) | Падение |
| volatile | #f59e0b (жёлтый) | Волатильный |
| unknown | #9ca3af (серый) | Неопределён |

---

## Зависимости

- **Plotly.js** — для графика сегментов
- **customer_daily API** — `/api/customer-daily/segment-analysis/preview`
- **customer_daily blocks API** — CRUD блоков

---

## Changelog

### v1.0.0 (2024)
- Начальная интеграция в главу «Наблюдение»
- ObservationSegmentWidget с полным функционалом
- Рендеринг в HTML и LaTeX
