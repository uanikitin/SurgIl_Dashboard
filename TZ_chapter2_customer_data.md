# ТЗ: Глава «Анализ данных заказчика» (B1)

**Версия:** 2.1 (синхронизация с реализацией)
**Дата:** 2026-05-17
**Статус:** Финализирован

---

## 0. Принципы

1. **snapshot — единственный источник правды для PDF.** Backend НЕ пересчитывает данные при сборке отчёта — всё берётся из `customer_report_block.data_snapshot`.
2. **UI-элементы в отчёт НЕ идут.** Загрузка xlsx, таймлайн, плашки реквизитов, кнопки, модалки — это интерфейс оператора, не контент отчёта.
3. **Per-block настройки видимости** хранятся в `customer_report_block.params.parts.{ключ: bool}`. По умолчанию все элементы блока включены.
4. **Один shared-модуль для общего функционала.** Например, «Выбор участков для сравнения» — это `backend/static/js/cmp_segments.js`, подключается на нескольких страницах (wizard, customer-daily) без дублирования.
5. **Используем существующие функции — не переписываем и не дублируем.** Перед написанием нового кода проверяем что нет уже работающей реализации.
6. **prefix_note / suffix_note** — пользовательский текст до/после каждого блока. Хранится в `params.prefix_note` / `params.suffix_note`. По умолчанию пусто; если пусто — в PDF не печатается.

---

## 1. Концепция

Глава «Анализ данных заказчика» — раздел отчёта об адаптации, где аналитик:
1. Оценивает исходные данные заказчика (UzKorGaz → `well_daily`)
2. Проводит анализ выбранных периодов
3. Сравнивает с данными датчиков UniTool (если есть пересечение)
4. Считает диагностику по критериям (роза)
5. Выбирает и утверждает базовый период B1 для последующих сравнений

---

## 2. Сценарий работы оператора

```
1. ЗАГРУЗКА ДАННЫХ (отдельная страница /customer-daily/upload)
   └─ Импорт xlsx → well_daily

2. ВЫБОР СКВАЖИНЫ (sticky-полоса наверху)
   └─ Скв.№ ▼ + бейдж утверждённого B1 + плашка-мета хранилища

3. АНАЛИЗ ПЕРИОДА (главная зона слева, всегда развёрнут)
   └─ Интерактивный таймлайн
   └─ Выбор периода (с/по + пресеты)
   └─ «▶ Показать аналитику» → 4 графика + 2 таблицы + помесячный текст
   └─ Опционально: наложить UniTool overlay + строгое сравнение
   └─ «☑ Добавить в отчёт» → создаётся блок period_analysis
   └─ «✓ Утвердить как B1» → дополнительно создаётся блок baseline

4. ДИАГНОСТИКА ПО КРИТЕРИЯМ (аккордеон, свёрнут по умолч.)
   └─ Селектор режима скоринга (balanced / liquid / gsp / purge_cycles)
   └─ «▶ Рассчитать» → роза + бар-чарт + таблица 6 критериев
   └─ «☑ Добавить в отчёт» → блок criteria_rose

5. СРАВНЕНИЕ УЧАСТКОВ (аккордеон, свёрнут по умолч.)
   └─ Загрузка детальных данных UniTool
   └─ Drag-select участков на графиках
   └─ «💾 Сохранить» → блок comparison

6. ПРОСМОТР ОТЧЁТА (sticky-панель справа, 40%)
   └─ Список карточек блоков с галочками «в отчёт» + parts + комментарии
   └─ HTML-превью главы (живой, мгновенный)
   └─ Кнопка «🔁 Пересобрать PDF» → таб PDF с iframe
   └─ Бэдж «⚠ PDF устарел» когда блоки менялись
   └─ Кнопка «↗ В окно» → pop-out с синхронизацией через BroadcastChannel
```

---

## 3. Структура главы 2 в PDF

```
ГЛАВА 2. АНАЛИЗ ДАННЫХ ЗАКАЗЧИКА

  N подразделов по числу блоков с in_report=true.
  Порядок (по kind): baseline → period_analysis → criteria_rose → comparison

  Внутри каждого подраздела:
  ─ prefix_note (если задан, курсив)
  ─ контент блока (фильтрованный по parts.X)
  ─ suffix_note (если задан, курсив)
```

---

## 4. Типы блоков (текущая реализация)

| kind | Назначение | Создаётся кнопкой |
|------|------------|-------------------|
| `baseline` | Базовый период B1 (с метриками) | «✓ Утвердить как B1» |
| `period_analysis` | Анализ выбранного периода | «☑ Добавить в отчёт» (секция 4) |
| `criteria_rose` | Роза критериев + балл | «☑ Добавить в отчёт» (секция «Диагностика») |
| `comparison` | Сравнение участков drag-select | «💾 Сохранить» (секция «Сравнение участков») |

**Порядок сортировки в PDF** (`get_blocks_for_report` в `customer_daily_service.py`):

```
baseline → period_analysis → criteria_rose → comparison
```

Внутри одного kind — по `sort_order` (изменяется drag-reorder в UI).

---

## 5. Блок `period_analysis` / `baseline`

### 5.1. Метаданные

```json
{
  "id": 47,
  "well_id": 17,
  "kind": "period_analysis | baseline",
  "title": "Анализ периода 2026-01-01 … 2026-02-02 (скв. 128)",
  "params": {
    "date_from": "2026-01-01",
    "date_to": "2026-02-02",
    "well_number": "128",
    "customer_baseline_id": 50,            // только у baseline — связь с customer_baseline.id
    "prefix_note": "Текст ДО блока",
    "suffix_note": "Текст ПОСЛЕ блока",
    "parts": { ... }                       // см. ниже
  },
  "data_snapshot": { ... },                // см. §5.3
  "comment": "Комментарий пользователя",
  "in_report": true,
  "sort_order": 0
}
```

### 5.2. Parts (атомарные тогглы видимости в PDF)

Каждая часть включается/выключается независимо. По умолчанию все = true.

| Ключ | Контент в PDF |
|------|---------------|
| `prefix_note` | Курсивный текст до блока (params.prefix_note) |
| `intro` | Сводка периода (динамический текст 1-2 предложения) |
| `description` | Комментарий пользователя (block.comment) |
| `pressures_chart` | PNG: 4 давления + overlay UniTool |
| `dp_chart` | PNG: ΔP + overlay UniTool |
| `flow_dt_chart` | PNG: Q общий/рабочий + бары простоя + overlay UniTool |
| `monthly_chart` | PNG: помесячный групп. бар (Q ср./мед.) |
| `describe_table` | Таблица описательной статистики |
| `monthly_table` | Таблица помесячных трендов |
| `monthly_descriptions` | Помесячный анализ текстом |
| `overlap_text` | 2 текстовых блока «Анализ UzKorGaz» + «Анализ UniTool» |
| `strict_compare` | Таблица «Строгое сравнение UzKorGaz vs UniTool на пересечении дат» |
| `suffix_note` | Курсивный текст после блока (params.suffix_note) |

### 5.3. Структура `data_snapshot`

Формируется фронтом в `_buildPeriodSnapshot()`. `_v: 2`.

```json
{
  "_v": 2,
  "date_from": "2026-01-01", "date_to": "2026-02-02",
  "days": 32, "date_min": "2026-01-01", "date_max": "2026-02-02",

  // Сводные метрики
  "q_total_avg": 15.94, "q_total_median": 15.94,
  "q_working_avg": 15.68, "q_working_median": 16.41,
  "dp_avg": 1.77, "dp_median": 1.80,
  "p_wellhead_median": 16.60, "p_flowline_median": 14.80,
  "shutdown_min_total": 1215, "shutdown_days_count": 21,
  "q_trend": { "slope_per_day": -0.02 },

  // Данные для 4 графиков (массивы по суткам)
  "chart": {
    "dates":         ["2026-01-01", ...],
    "p_wellhead":    [16.6, ...],
    "p_annular":     [...], "p_flowline": [...], "p_static": [...],
    "dp":            [1.8, ...],
    "q_gas_total":   [16.2, ...], "q_gas_working": [16.4, ...],
    "shutdown_min":  [30, 70, 0, ...]
  },

  // Таблицы
  "describe":     [ { param, label, n, mean, std, min, q25, median, q75, max }, ... ],
  "monthly":      [ { month, month_label, days, mean_q_total, median_q_total, mean_dp, trend_q_total, ... }, ... ],
  "monthly_desc": [ { label, text }, ... ],

  // Overlay UniTool (если был наложен на странице)
  "unitool": {
    "first_date": "2026-02-02", "last_date": "2026-03-31",
    "days": 56, "equip_dt": "2026-02-02", "dropped_pre_equip": 1905,
    "choke_mm": 10, "has_flow": true, "well_number": "128",
    "describe_map": { "Дебит общий, тыс.м³/сут": { mean, median }, ... },
    "daily": {
      "dates":  ["2026-02-02", ...],
      "p_tube": [16.5, ...], "p_line": [15.0, ...],
      "dp":     [1.5, ...], "q": [14.5, ...]
    }
  },

  // Строгое сравнение UzKorGaz ∩ UniTool
  "strict_compare": {
    "dates_from": "2026-02-02", "dates_to": "2026-02-02", "days": 1,
    "cust_q_total":   { n, mean, median, min, max },
    "cust_q_working": { ... }, "cust_dp": { ... },
    "cust_p_tube":    { ... }, "cust_p_line": { ... },
    "our_q":   { ... }, "our_dp":   { ... },
    "our_p_tube": { ... }, "our_p_line": { ... }
  },

  // Готовый HTML 2 описательных блоков (UzKorGaz + UniTool).
  // Блок «Совместный период» удалён (бесполезен в отчёте, см. история v2.1).
  "overlap_html": "<div>... 2 блока ...</div>"
}
```

**Особенность для `kind=baseline`**: snapshot имеет ту же структуру что и `period_analysis`. Если B1 создан миграцией из `customer_baseline` (без графиков) — snapshot содержит только метрики (без `chart`/`describe`/`monthly`/`unitool`/`strict_compare`). Тогда в PDF попадает только таблица метрик.

---

## 6. Блок `criteria_rose`

### 6.1. Метаданные + parts

```json
{
  "kind": "criteria_rose",
  "title": "Роза критериев · скв.128 · 2026-01-01…2026-02-02 · balanced",
  "params": {
    "well_number": "128",
    "period_from": "2026-01-01", "period_to": "2026-02-02",
    "mode": "balanced",
    "prefix_note": "...", "suffix_note": "...",
    "parts": {
      "prefix_note": true,
      "rose_chart": true,
      "contributions_chart": true,
      "metrics_table": true,
      "warnings": true,
      "description": true,
      "suffix_note": true
    }
  }
}
```

### 6.2. Структура `data_snapshot`

Результат `compute_rose()` из `customer_rose_service.py`:

```json
{
  "ok": true,
  "well_number": "128",
  "period_from": "2026-01-01", "period_to": "2026-02-02", "period_days": 33,
  "mode": "balanced",
  "weights_raw":  { "decline": 0.35, ... },
  "weights":      { "decline": 0.35, ... },  // нормированные
  "current_raw":  { "decline": 0.42, "p_wh_down": 0.18, ... },
  "history_median_raw": { ... },
  "history": {
    "choke_mm": 10.0,
    "rows_total": 88, "windows_count": 58,
    "window_days": 33, "step_days": 1,
    "history_from": "2026-01-01", "history_to": "2026-03-31"
  },
  "ranks":        { "decline": 73, "p_wh_down": 41, ... },
  "contributions": {
    "decline":   { "actual": 25.55, "max": 35.0, "weight": 0.35, "rank": 73 },
    ...
  },
  "score": 55.0,
  "weak_data": false,
  "warnings": [],
  "labels":       { "decline": "Снижение Q", ... },
  "labels_short": { ... },
  "units":        { ... }
}
```

---

## 7. Блок `comparison`

### 7.1. Метаданные + parts

```json
{
  "kind": "comparison",
  "title": "Сравнение участков A+B · ΔP, кгс/см²",
  "params": {
    "prefix_note": "...", "suffix_note": "...",
    "parts": {
      "prefix_note": true,
      "segments_table": true,
      "chart": true,
      "description": true,
      "suffix_note": true
    }
  },
  "comment": "Описание сравнения от пользователя"
}
```

### 7.2. Структура `data_snapshot` (формат `wz3cmp_v1`)

```json
{
  "_v": "wz3cmp_v1",
  "metric": "dp", "metric_label": "ΔP, кгс/см²",
  "method": "linear",  // или "theil_sen"
  "toggles": { showCurve, showTrend, showForecast, showMean, ... },
  "segments": [
    {
      "letter": "A", "source": "our_pressure", "color": "#1d4ed8",
      "from": "2026-01-15", "to": "2026-01-31",
      "from_ts": "2026-01-15T00:00:00", "to_ts": "2026-01-31T23:59:59",
      "duration_hours": 408.0,
      "n": 1234,
      "mean": 0.752, "median": 0.681, "min": 0.5, "max": 1.2,
      "slope_per_hour": 0.0023, "slope_per_day": 0.055,
      "intercept": 0.5, "r2": 0.421,
      "curve_x_hours": [0.0, 1.0, 2.0, ...],   // downsampled до ≤600 точек
      "curve_y":       [0.5, 0.51, ...]
    },
    ...
  ]
}
```

---

## 8. UI карточки блока (в списке справа от sticky-панели)

```
┌─[Цветная полоса 4px по kind]──────────────────────────────────┐
│ ↑ [Вступительный текст — pre-note textarea]                   │
│ [Badge kind] [Title input ←inline edit] [📈] [👁] [✓ в отчёт] [✕]│
│   Параметры (даты, метрика, штуцер...)                        │
│   [Сводка метрик / краткое описание]                          │
│   [Комментарий пользователя — textarea]                       │
│ ▾ ⚙ Что включить в PDF (N из M)                              │
│    ☑ ↑ Вступительный текст                                    │
│    ☑ 📝 Сводка периода / 📊 Роза / ...                        │
│    ☑ ↓ Заключительный текст                                   │
│ ↓ [Заключительный текст — post-note textarea]                 │
└───────────────────────────────────────────────────────────────┘
```

**Цвета рамки per-kind:** `baseline=оранжевый`, `period_analysis=зелёный`, `comparison=фиолетовый`, `criteria_rose=красный`.

**Над списком блоков:** кнопки `[➕ Развернуть всё]` / `[➖ Свернуть всё]` для группового управления `<details>` с parts.

---

## 9. UX страницы `/customer-daily` (Two-Panel layout)

### 9.1. Общий layout (desktop ≥1280px)

```
┌──────────────────────────────────────────────────────────────────┐
│  STICKY ВЕРХНЯЯ ПОЛОСА (всегда видна при скролле):              │
│  [Скв.№▼] [📌 B1: дата1-дата2] [📁 Загрузить XLSX ↗] [мета]    │
├──────────────────────────────────────────────┬───────────────────┤
│  ЛЕВАЯ ЗОНА (60%): рабочий контент           │  ПРАВАЯ (40%):    │
│                                              │  sticky-панель    │
│  «Период анализа» (всегда развёрнут):        │                   │
│   ├─ Таймлайн (cdtl)                         │  [📑 HTML][📄 PDF]│
│   ├─ Поля С/по + чекбокс UniTool             │  ─────────────────│
│   ├─ Кнопка «▶ Показать аналитику»           │  Карточки блоков  │
│   ├─ Результат (4 графика + 2 таблицы +      │  (с галочками +   │
│   │  помесячный текст + строгое сравнение)   │  parts + комм.)   │
│   └─ Кнопки [☑ Добавить в отчёт] [✓ B1]     │  ─────────────────│
│                                              │  Live HTML/PDF    │
│  ▾ «📊 Диагностика по критериям» (свёрнут)  │  превью главы     │
│  ▾ «🔁 Сравнение участков» (свёрнут)        │  ─────────────────│
│                                              │  [🔁 PDF] [⬇] [↗]│
└──────────────────────────────────────────────┴───────────────────┘
```

### 9.2. Sticky-панель справа (40%)

**Шапка:** `📑 Глава «Анализ исходных данных»` + бэдж `«N блоков в отчёт»` + бэдж `«⚠ PDF устарел / ✓ актуален»`.

**Табы:**
- `📑 HTML` — JS-рендер главы из snapshot блоков. Мгновенный, обновляется при ЛЮБОМ изменении (toggle in_report/parts, правка note/comment, drag-reorder).
- `📄 PDF` — iframe с PDF (xelatex рендер). Перерисовывается по кнопке `🔁 Пересобрать`.

**Кнопки:**
- `🔁 Пересобрать` — генерация PDF (5-12 сек) через `POST /api/adaptation-report/preview-pdf?only_chapter=customer_data`
- `⬇ Скачать` — скачать готовый PDF
- `↗ В окно` — открыть pop-out

### 9.3. Логика «PDF устарел»

- При любом изменении блока → `markPdfStale()` → бэдж становится красным «⚠ PDF устарел».
- После клика `🔁 Пересобрать` → бэдж зелёный «✓ PDF актуален».

### 9.4. WYSIWYG в HTML-превью

Для каждого блока с `in_report=true` рендерятся **реальные графики и таблицы**, уважая `parts.X`:

- **`period_analysis` / `baseline`:** 4 Plotly-графика (с overlay UniTool) + таблица описательной статистики + помесячная таблица + помесячный текст + 2 описательных блока + таблица строгого сравнения.
- **`criteria_rose`:** Plotly роза + Plotly бар-чарт + таблица 6 критериев + предупреждения.
- **`comparison`:** Plotly график сегментов по оси «день от начала» (с fallback на линию тренда если в snapshot нет `curve_x_hours/curve_y`) + таблица сегментов с трендами.

### 9.5. Pop-out окно (`/customer-daily/preview-popout?well=N`)

- Только для **просмотра** (без галочек и редактирования).
- Синхронизация с основной страницей через `BroadcastChannel('cd-chapter-{well_number}')`.
- При изменении блока на основной странице → popout мгновенно перерисовывает HTML-превью.

### 9.6. Responsive (<1024px)

- Two-Panel схлопывается в одну колонку.
- Sticky-панель → всплывающая кнопка `[📑 Превью главы]` в правом нижнем углу. По клику разворачивается на весь экран. Закрывается ✕.

### 9.7. Аккордеоны розы и cmp

- Свёрнуты по умолчанию на каждой загрузке страницы.
- Состояние **не сохраняется** в localStorage (договорённость).
- Раскрываются кликом по заголовку (`<details>` HTML5).

### 9.8. Выделенная страница загрузки `/customer-daily/upload`

- Перенесена с главной для разгрузки UI.
- Содержит: drag-and-drop, проверка дубликатов (POST `/upload-check`), диалог «Перезаписать/Только новые/Отмена», POST `/upload`, сводка результата.
- После успешной загрузки — auto-redirect на `/customer-daily?well={back_well}`.

### 9.9. 5 улучшений UX карточек

1. **Цветовая рамка** карточки per-kind (4px полоса слева).
2. **Inline-редактирование** заголовка (input + debounced PUT).
3. **Кнопки «➕ Развернуть всё / ➖ Свернуть всё»** для `<details>` с parts.
4. **Подсветка** блока в HTML-превью при наведении на карточку (и наоборот).
5. **Drag-reorder** карточек блоков (HTML5 drag-and-drop → PUT sort_order).

---

## 10. API

### 10.1. CRUD блоков

```
GET    /api/customer-daily/blocks?well_id={id}     — список блоков
POST   /api/customer-daily/blocks                  — создать блок
PUT    /api/customer-daily/blocks/{id}             — обновить блок (title, params, comment, in_report, sort_order, data_snapshot)
DELETE /api/customer-daily/blocks/{id}             — удалить блок
```

### 10.2. Роза критериев (preview-расчёт без сохранения)

```
POST /api/customer-daily/rose/preview
body: { well, period_from, period_to, mode, weights?, history_step_days? }
```

### 10.3. Сборка PDF главы

```
POST /api/adaptation-report/preview-pdf
body: {
  well_id, obs_from, obs_to, adapt_from, adapt_to,
  with_charts: true,
  only_chapter: "customer_data"      // ключевое: только глава 2
}
```

### 10.4. Pop-out страница

```
GET  /customer-daily/preview-popout?well={N}
```

### 10.5. Страница загрузки

```
GET  /customer-daily/upload?back_well={N}
POST /api/customer-daily/upload-check  (проверка дубликатов)
POST /api/customer-daily/upload        (запись в well_daily)
```

---

## 11. LaTeX-рендеринг (текущая реализация)

Шаблон `backend/templates/latex/adaptation_report.tex`, секция `\BLOCK{ if include_sections.customer_data }`.

Для каждого блока в `customer_chapter.periods/comparisons/rose_blocks/baselines`:

```latex
\subsection*{2.\VAR{loop.index}. \VAR{p.title}}

% prefix_note
\BLOCK{ if p.parts.prefix_note and p.prefix_note_tex }
{\footnotesize \textit{\VAR{p.prefix_note_tex}}}
\BLOCK{ endif }

% Графики (под защитой parts)
\BLOCK{ if p.parts.pressures_chart and p.chart_paths.pressures }
\includegraphics{...}
\BLOCK{ endif }
% ... аналогично для dp/flow/monthly chart

% Таблицы
\BLOCK{ if p.parts.describe_table and p.describe }...\BLOCK{ endif }
\BLOCK{ if p.parts.monthly_table and p.months }...\BLOCK{ endif }
\BLOCK{ if p.parts.monthly_descriptions and p.month_descriptions }...\BLOCK{ endif }

% 2 описательных блока (UzKor / UniTool)
\BLOCK{ if p.parts.overlap_text and p.overlap_blocks }...\BLOCK{ endif }

% Таблица строгого сравнения
\BLOCK{ if p.parts.strict_compare and p.strict_compare }...\BLOCK{ endif }

% suffix_note
\BLOCK{ if p.parts.suffix_note and p.suffix_note_tex }
{\footnotesize \textit{\VAR{p.suffix_note_tex}}}
\BLOCK{ endif }
```

Аналогичная логика для `comparison` (со своими parts) и `criteria_rose` (rose_chart + contributions_chart + metrics_table + warnings).

---

## 12. Снимок состояния реализации (2026-05-17)

| # | Функция | Статус |
|---|---------|--------|
| 1 | Granular parts | ✅ Работает: галочки → секции появляются/исчезают в PDF |
| 2 | prefix/suffix_note | ✅ Курсив до/после блока в PDF и HTML-превью |
| 3 | Диагностика как отдельный блок | ✅ `criteria_rose` kind, рендер в PDF |
| 4 | Drag-reorder | ✅ sort_order меняется в БД, порядок в PDF меняется |
| 5 | baseline | ✅ Метрики в snapshot. Автосоздание блока при «✓ Утвердить как B1» |
| 6 | Snapshot → PDF | ✅ `_enrich_period_with_customer_data` читает из snapshot |
| 7 | Overlay UniTool | ✅ 4 графика с 2 слоями (UzKorGaz + UniTool) в PNG и Plotly |
| 8 | strict_compare | ✅ Таблица с колонками параметр/UzKor/UniTool/Δ |
| 9 | Two-Panel 60/40 | ✅ Sticky-полоса + рабочая зона + sticky-панель |
| 10 | Live HTML-превью | ✅ Мгновенный JS-рендер главы из snapshot |
| 11 | Кнопка «Пересобрать PDF» + iframe | ✅ В табе PDF правой панели |
| 12 | Бэдж «PDF устарел» | ✅ markPdfStale/Fresh |
| 13 | Pop-out + BroadcastChannel | ✅ `/customer-daily/preview-popout` |
| 14 | Responsive <1024px | ✅ Sticky → всплывающая кнопка |
| 15 | Аккордеоны розы/cmp | ✅ Свёрнуты по умолч., не запоминают состояние |
| 16 | Вынос загрузки на /upload | ✅ Отдельная страница, плитка в шапке |
| 17 | 5 улучшений (рамка/inline-title/свернуть-всё/подсветка/drag-reorder) | ✅ Все |
| 18 | Удаление «Совместного периода» из overlap_html | ✅ Из _buildOverlapDescription + миграция существующих snapshot |
| 19 | График сравнения с fallback на тренд | ✅ Если curve_x_hours пуст — рисуется slope+intercept |
| 20 | Миграция B1 → блок baseline для существующих скв. | ✅ Выполнено разово для скв.128 |

---

## 13. Будущие расширения (НЕ в текущем scope)

Зафиксировано в ТЗ как идеи на будущее, но **сейчас не реализуется**:

- **`period_analysis.subkind` = `general` / `detail`** — разделение на общий и детальные анализы участков с текстом перехода между ними.
- **Новый kind `chapter_summary`** — автогенерация резюме главы из утверждённого B1 (плейсхолдеры для метрик + текст перехода к следующей главе).
- **Группировка parts в UI по секциям** (Графики / Таблицы / Сравнение / Диагностика / Комментарии).
- **Endpoint `POST /api/customer-daily/blocks/{id}/recalc`** — пересчёт snapshot из исходных данных (если xlsx обновился).
- **`comparison.chart_base64`** — PNG в snapshot для самодостаточности (сейчас динамические массивы для Plotly).
- **Перенесение диагностики розы в parts блока `period_analysis`** — сейчас отдельный kind.
- **Автоматический подбор B1** (только ручной выбор сейчас).

---

## 14. Связанные файлы

```text
backend/
├── routers/
│   ├── customer_daily.py              # API + страницы /customer-daily, /upload, /preview-popout
│   └── adaptation_report.py           # POST /preview-pdf (only_chapter='customer_data')
├── services/
│   ├── customer_daily_service.py      # CRUD блоков, build_overlap_blocks
│   ├── customer_rose_service.py       # compute_rose
│   ├── customer_chart_renderer.py     # PNG 4 графиков + overlay UniTool
│   ├── rose_chart_renderer.py         # PNG роза + бар-чарт
│   └── adaptation_report_service.py   # collect_report_data + _enrich_period_with_customer_data
├── templates/
│   ├── customer_daily.html            # UI Two-Panel
│   ├── customer_upload.html           # Страница загрузки XLSX
│   ├── customer_popout.html           # Pop-out превью
│   └── latex/adaptation_report.tex    # LaTeX шаблон главы 2
└── static/js/
    └── cmp_segments.js                # Shared «Выбор участков для сравнения»
```

Backup до Two-Panel переделки: `.backup_before_twopanel/`.

---

## История версий

| Версия | Дата | Изменения |
|--------|------|-----------|
| 1.0 | 2026-05-15 | Начальная версия |
| 2.0 | 2026-05-17 | Структура главы, parts блоков, диагностика как часть блока (драфт) |
| 2.1 | 2026-05-17 | **Синхронизация ТЗ с реальной реализацией**: criteria_rose остаётся отдельным kind, UX-разделы (§9), снимок реализации (§12), удаление «Совместного периода» из overlap_html, миграция B1 → блок |
