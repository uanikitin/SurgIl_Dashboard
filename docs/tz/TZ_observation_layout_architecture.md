# TZ — Observation Layout Architecture (Этап 1, MVP) [DEPRECATED DRAFT]

> ⚠️ **DEPRECATED DRAFT — НЕ ИСПОЛЬЗОВАТЬ КАК IMPLEMENTATION SOURCE**
>
> Этот документ конфликтует с [`RFC_observation_snapshot.md`](../../RFC_observation_snapshot.md) и существующим observation backend (`backend/services/observation_*.py`, 141 тест PASS).
>
> **Источник правды:** `RFC_observation_snapshot.md` + текущие `backend/services/observation_*`.
>
> Документ оставлен для архива (черновик 2026-05-19, сессия Этапа 1 без сверки existing backend). Принятое решение: **Вариант A — реинтегрировать existing backend** (см. реконсилиационный отчёт).
>
> Часть идей этого документа может быть переиспользована для **frontend Step 3** (этапы E1+E2 RFC), но архитектура и контракты должны браться **только из RFC**.

---

# TZ — Observation Layout Architecture (Этап 1, MVP) — ORIGINAL DRAFT

**Цель этапа:** создать самостоятельную страницу `/observation` с **block-driven workflow** для главы «Наблюдение». Оператор может создать preview блока, сохранить snapshot, управлять списком блоков и подготовить основу для будущего HTML/PDF rendering.

**Архитектурный принцип:** `snapshot → block → preview-renderer → chapter-renderer → PDF-renderer`. Wizard НЕ управляет логикой — он только организует UX вокруг snapshot/block lifecycle.

**Связанные документы:**
- [docs/contracts/observation_block_contract.md](../contracts/observation_block_contract.md) — контракт блока (поля, params, snapshot, status).
- [docs/contracts/segment_snapshot_contract.md](../contracts/segment_snapshot_contract.md) — snapshot контракт для segment kind (переиспользуется).

---

## 0. Scope / Out of scope

### IN SCOPE Этапа 1

- Новая страница `/observation` + шаблон + JS.
- Левая панель: chapter control sidebar (well/period/add-block/list/reorder/toggle).
- Правая панель: active block workspace (preview/edit-params/save/update/delete).
- Block lifecycle: preview / save / list / select / edit / update / delete / reorder / toggle in_report.
- Backend kinds: `observation_period_analysis`, `observation_segment`.
- Единый preview-endpoint с dispatch внутри.
- API: 7 endpoints (см. §6).
- Storage: переиспользуется `customer_report_block`, фильтр по `params.source = "observation"`.
- Renderer-neutral: ни один renderer не читает БД и не вызывает сервисы аналитики.

### OUT OF SCOPE Этапа 1

- ❌ PDF generation для главы Observation.
- ❌ Final chapter HTML render (только preview отдельного блока).
- ❌ Popout окно.
- ❌ BroadcastChannel / multi-window sync.
- ❌ Rose / criteria block.
- ❌ Сравнение / comparison block.
- ❌ Baseline B1/B2 creation (только read-only ссылка из observation, если оператор хочет привязать).
- ❌ Global settings / per-user preferences.
- ❌ LaTeX integration.
- ❌ Schema bump для customer_report_block.
- ❌ Drag-reorder через jQuery UI / heavyweight libs — используем native HTML5 `draggable`.
- ❌ Real-time collaboration.

---

## 1. Файловая структура (новые файлы)

```
backend/
├── routers/
│   └── observation.py                    # NEW — 7 endpoints
├── services/
│   ├── observation/
│   │   ├── __init__.py                   # NEW
│   │   ├── registry.py                   # NEW — kind → service mapping
│   │   ├── observation_period_service.py # NEW — compute_observation_period_block(...)
│   │   └── observation_segment_service.py# NEW — wrapper над compute_segment_block(..) с tag params.source=observation
│   └── (existing) segment_analysis_service.py  # переиспользуется, не меняется
├── templates/
│   └── observation.html                   # NEW — minimal skeleton (см. §3)
├── static/
│   ├── js/
│   │   └── observation.js                 # NEW — main JS for /observation
│   └── css/
│       └── observation.css                # NEW — page-specific styles
├── schemas/
│   └── observation.py                     # NEW — Pydantic models for API
└── app.py                                 # PATCH — register router + add /observation route
```

**НЕ создаём:** новые таблицы, миграции, новые контракты snapshot.

---

## 2. Backend архитектура

### 2.1. registry.py

Единая точка dispatch для всех observation kinds:

```python
# backend/services/observation/registry.py
KIND_SERVICES = {
    "observation_period_analysis": observation_period_service.compute_block,
    "observation_segment":          observation_segment_service.compute_block,
}

def compute(kind, db, well, d_from, d_to, params):
    service = KIND_SERVICES.get(kind)
    if service is None:
        return {"ok": False, "status": "error",
                "error": f"Неизвестный kind: {kind}"}
    return service(db, well, d_from, d_to, params)
```

Новый kind добавляется в одну строку. Это и есть архитектурное лекарство от adaptation_wizard-style роста.

### 2.2. Сервисы

Каждый сервис реализует **одну функцию**:

```python
def compute_block(db, well: str, d_from: date, d_to: date,
                   params: dict) -> dict:
    """Возвращает snapshot формата observation_v1 (см. контракт §5).
    Не пишет в БД. Не вызывает router. Не знает о UI.
    """
    ...
    return {
        "ok": True, "_v": "observation_v1", "schema_version": 1,
        "kind": "observation_period_analysis",
        "status": "ok",  # или "partial" / "error"
        "well_number": well, "date_from": d_from.isoformat(), ...
        ...
    }
```

Сервис **только**:
- читает БД (well_daily + сопутствующее);
- вызывает analytics-функции;
- собирает snapshot формата из контракта;
- возвращает dict.

Сервис **не**:
- пишет в `customer_report_block`;
- логирует action history;
- знает про текущего пользователя/сессию;
- знает про другие блоки.

### 2.3. observation_segment_service — обёртка

```python
def compute_block(db, well, d_from, d_to, params):
    from backend.services.segment_analysis_service import compute_segment_block
    snap = compute_segment_block(
        db, well, d_from, d_to,
        include_pav=bool(params.get("include_pav", False)),
        promote_only_working=bool(params.get("promote_only_working", False)),
    )
    # Tag для observation chapter:
    snap["kind"] = "observation_segment"
    snap["_v"] = "observation_v1"  # обёртка-контракт, segment_v1 внутри
    return snap
```

Это и есть **adapter уровня сервиса** (см. §4 контракта).

### 2.4. Адаптер для existing renderer

Existing PDF formatter `_format_segment_block` принимает блок с `kind=segment_analysis`. На уровне `compute_block` мы отдаём `kind=observation_segment`, но при последующем рендере нужно либо:

**Вариант A (рекомендуется):** существующий renderer обрабатывает любой kind, оканчивающийся на `_segment` через нормализацию:

```python
if b.get("kind") in ("segment_analysis", "observation_segment"):
    return _format_segment_block(b, ...)
```

**Вариант B:** новый формат собственного renderer для `observation_*`. Не делаем в Этапе 1.

Решение: Вариант A — одна строка в коде PDF formatter (но саму реализацию делаем не в Этапе 1, а когда дойдём до PDF главы Observation).

---

## 3. Frontend архитектура

### 3.1. observation.html — минимальный скелет

Только структура (контейнеры, ссылки на JS/CSS). НЕТ inline-логики, НЕТ embedded JS бизнес-функций.

### 3.2. observation.js — модульная организация

Один файл, но разделён на чёткие секции:

```
[STATE]           глобальное состояние страницы (selected well, active block, blocks list)
[API]             функции-обёртки над fetch — одна на endpoint
[SIDEBAR]         render левой панели (list, add, drag-reorder, toggle)
[WORKSPACE]       render правой панели (active block preview)
[BLOCK-PREVIEW]   renderer per kind — НЕ читает БД, только snapshot
  ├ period_analysis_preview(snap, container)
  └ observation_segment_preview(snap, container) — переиспользует _buildSegmentBlockHtml + _renderSegmentBlockCharts
[EVENTS]          подписки на UI events
[INIT]            на DOMContentLoaded
```

**Правило (renderer-neutral в JS):** функции из `[BLOCK-PREVIEW]` принимают аргументами `(snap, container)` — никаких глобальных переменных, никаких `fetch()` вызовов внутри.

### 3.3. observation.css — minimal

Стили для двухпанельной сетки + drag handle + status badges. Без новой дизайн-системы.

---

## 4. Структура страницы / Wireframe

### 4.1. Общий layout

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Header (минимальный): «Глава: Наблюдение · скв.{N}»     [link к /well] │
├──────────────────────────────────┬──────────────────────────────────────┤
│                                  │                                      │
│  ◀ Sidebar (левая, ~320px)       │  Workspace (правая, остальная)       │
│                                  │                                      │
│  ┌────────────────────────────┐ │  ┌────────────────────────────────┐   │
│  │ Well: [ скв.85 ▼ ]         │ │  │  Active block: «Период 1»      │   │
│  │ Period: [01.01 — 31.03]    │ │  │  kind: observation_period_anal.│   │
│  │                            │ │  │  status: ● ok                  │   │
│  │ [+ Добавить блок ▼]        │ │  ├────────────────────────────────┤   │
│  │   ├ period_analysis        │ │  │                                │   │
│  │   └ segment                │ │  │   ── Block params  ──          │   │
│  └────────────────────────────┘ │  │   prefix_note   [_____________]│   │
│                                  │  │   date_from     [2026-01-01]   │   │
│  ── Blocks (3, sorted) ──        │  │   date_to       [2026-03-31]   │   │
│  ┌────────────────────────────┐ │  │   ⚗ promote_only_working [ ]   │   │
│  │ ⠿ #1 ● Период 1            │ │  │   ☑ include_pav         [ ]    │   │
│  │   period_analysis · 90д    │ │  │                                │   │
│  │   [edit] [☑ in_report]     │ │  │   [▶ Preview] [💾 Save] [🗑]  │   │
│  ├────────────────────────────┤ │  │                                │   │
│  │ ⠿ #2 ● Сегментный анализ   │ │  │   ── Live preview ──           │   │
│  │   observation_segment · 88 │ │  │                                │   │
│  │   [edit] [☑ in_report]     │ │  │   [graph]                      │   │
│  ├────────────────────────────┤ │  │   [table]                      │   │
│  │ ⠿ #3 ◌ Период 2 (draft)    │ │  │   [descriptions]               │   │
│  │   period_analysis · 30д    │ │  │                                │   │
│  │   [edit] [☐ in_report]     │ │  │                                │   │
│  └────────────────────────────┘ │  │                                │   │
│                                  │  │                                │   │
│  Footer (controls):              │  │                                │   │
│   [⟳ Refresh list] [→ Open well] │  │                                │   │
└──────────────────────────────────┴──────────────────────────────────────┘
```

### 4.2. Detail wireframes

#### 4.2.1. Block card в sidebar

```
┌────────────────────────────────────────────────────┐
│ [⠿ drag] [#1] [● ok] Период 1                      │
│   kind: observation_period_analysis                │
│   2026-01-01 — 2026-03-31  (90 сут)               │
│   ┌──────────────┐ ┌─────┐ ┌────────────────────┐ │
│   │ ☑ in_report  │ │edit │ │🗑 (confirm)        │ │
│   └──────────────┘ └─────┘ └────────────────────┘ │
└────────────────────────────────────────────────────┘
```

Status indicators:
- `●` green — `ok`
- `◐` yellow — `partial`
- `◌` grey — `draft` (created but never previewed)
- `⚠` red — `error`
- `◊` purple — `legacy`

#### 4.2.2. Workspace для observation_segment

```
┌─────────────────────────────────────────────────────────────┐
│ Active: «Сегментный анализ скв.85»                          │
│ kind: observation_segment · status: ● ok                    │
├─────────────────────────────────────────────────────────────┤
│ ── Block params ──                                          │
│ prefix_note  [Реальный комментарий...                    ]  │
│ suffix_note  [                                            ] │
│ comment      [User comment                               ]  │
│ ⚗ promote_only_working [✓]   ☑ include_pav [ ]              │
│                                                             │
│ [▶ Preview]  [💾 Save]  [🔄 Update]  [🗑 Delete]            │
│                                                             │
│ ── Parts (renderer-toggles) ──                              │
│ ☑ prefix_note  ☑ q_segment_chart  ☑ segments_table          │
│ ☑ descriptions ☑ cp_descriptions  ☑ description             │
│ ☑ suffix_note                                               │
│                                                             │
│ ── Live preview (renderer from snapshot) ──                 │
│ [Plotly chart Q + downtime subplot]                         │
│ [Segments table 11 cols]                                    │
│ [Описание сегментов]                                        │
│ [События переломов]                                         │
│ [⇄ Сравнение Q общий ↔ Q рабочий  ⚗ R1: promoted=4]         │
└─────────────────────────────────────────────────────────────┘
```

#### 4.2.3. Empty state

Если на скв. нет ни одного observation-блока:

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│                  🔍  Нет блоков для главы                   │
│                                                             │
│      Выберите тип блока в левой панели, укажите период      │
│      и нажмите Preview — это создаст черновик.              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### 4.2.4. Error state в workspace

```
┌─────────────────────────────────────────────────────────────┐
│ Active: «Период 1»  · status: ⚠ error                       │
├─────────────────────────────────────────────────────────────┤
│  ⚠ Блок не рассчитан                                        │
│                                                             │
│  Нет данных в well_daily для скв.85 за период               │
│  2026-05-01 — 2026-05-15.                                   │
│                                                             │
│  Расширьте период или выберите другую скважину.             │
│  [Изменить период]                                          │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. State management (frontend)

Глобальное состояние страницы — **минималистичное**:

```js
const STATE = {
  wellId:        null,         // selected well
  wellNumber:    null,
  dateFrom:      null,
  dateTo:        null,
  blocks:        [],           // массив всех блоков для well, отсортированный по sort_order
  activeBlockId: null,         // блок, открытый в workspace; null = empty state
  activeSnapshot: null,        // snapshot активного блока (если есть preview)
  isDirty:       false,        // есть несохранённые изменения в workspace
};
```

**Правила:**
- Все изменения STATE проходят через явный action-функцию (например `setActiveBlock(id)`), не через прямую мутацию из event listeners.
- STATE — single source of truth для UI. Sidebar и workspace render-функции принимают STATE как аргумент.
- При navigation away — confirm если `isDirty`.

---

## 6. API contract (7 endpoints)

Все под префиксом `/api/observation/`.

### 6.1. POST `/api/observation/block/preview`

**Назначение:** рассчитать snapshot для (kind, well, period, params). НЕ пишет в БД.

**Request:**
```jsonc
{
  "kind": "observation_period_analysis" | "observation_segment",
  "well_id": 14,
  "period_from": "2026-01-01",
  "period_to":   "2026-03-31",
  "params": {
    "prefix_note": "",
    "suffix_note": "",
    "metric_set":  "default",          // for period_analysis
    "include_pav":          false,     // for segment
    "promote_only_working": false      // for segment, R1 experimental
  }
}
```

**Response (success):**
```jsonc
{
  "ok": true,
  "snapshot": { ...observation_v1 dict... },
  "status":   "ok"
}
```

**Response (compute error — НЕ HTTP 500):**
```jsonc
{
  "ok": false,
  "snapshot": null,
  "status": "error",
  "error":  "Нет данных в well_daily для скв.85 за период ..."
}
```

**HTTP 422** — только при validation (invalid kind, missing required fields).

### 6.2. POST `/api/observation/block/save`

**Назначение:** сохранить новый блок в БД на основе уже-посчитанного snapshot.

**Request:**
```jsonc
{
  "well_id": 14,
  "kind":    "observation_period_analysis",
  "title":   "Период наблюдения 01.01–31.03",   // optional, autogen if missing
  "comment": "",
  "params":  { ... },                            // полный, как при preview
  "data_snapshot": { ... },                      // ответ предыдущего /preview
  "in_report": true,
  "sort_order": null                             // null = append в конец
}
```

**Response:**
```jsonc
{
  "ok": true,
  "id": 142,
  "sort_order": 5
}
```

**Server-side validation:**
- `params.source` обязательно `"observation"` (router выставит сам, если отсутствует).
- `kind` ∈ известных kind.
- `data_snapshot.ok == true` — нельзя сохранять error snapshots.

### 6.3. GET `/api/observation/blocks?well_id=14`

**Назначение:** список блоков observation-главы для скважины. Без `data_snapshot` (heavy), только meta.

**Response:**
```jsonc
{
  "ok": true,
  "well_id": 14,
  "blocks": [
    {
      "id": 142,
      "kind": "observation_period_analysis",
      "title": "...",
      "comment": "",
      "in_report": true,
      "sort_order": 1,
      "status": "ok",
      "params_summary": {
        "date_from": "2026-01-01",
        "date_to":   "2026-03-31",
        "promote_only_working": false,
        "include_pav": false
      },
      "updated_at": "2026-05-19T13:14:15Z"
    },
    ...
  ]
}
```

### 6.4. GET `/api/observation/block/{id}`

**Назначение:** полный блок с `data_snapshot`. Используется при select-в-sidebar.

**Response:**
```jsonc
{
  "ok": true,
  "block": {
    "id": 142,
    "well_id": 14,
    "kind": "observation_period_analysis",
    "title": "...",
    "params": { ... },
    "data_snapshot": { ... full ... },
    "comment": "",
    "in_report": true,
    "sort_order": 1,
    "created_at": "...",
    "updated_at": "..."
  }
}
```

**НЕ пересчитывает snapshot.**

### 6.5. PUT `/api/observation/block/{id}`

**Назначение:** обновить блок (params + новый snapshot). Делает явный recompute через preview.

**Request:**
```jsonc
{
  "params":  { ... обновлённые ... },
  "comment": "...",                          // optional
  "title":   "...",                          // optional
  "data_snapshot": { ... }                   // ответ /preview с новыми params
}
```

**Response:**
```jsonc
{
  "ok": true,
  "id": 142,
  "updated_at": "..."
}
```

### 6.6. POST `/api/observation/block/reorder`

**Назначение:** изменить порядок блоков. Batch operation.

**Request:**
```jsonc
{
  "well_id": 14,
  "order": [142, 145, 143]      // новый порядок id
}
```

**Response:**
```jsonc
{ "ok": true }
```

### 6.7. PATCH `/api/observation/block/{id}/toggle`

**Назначение:** переключить `in_report` без других изменений.

**Request:**
```jsonc
{ "in_report": true }
```

**Response:**
```jsonc
{ "ok": true, "id": 142, "in_report": true }
```

### 6.8. DELETE `/api/observation/block/{id}`

**Назначение:** удалить блок.

**Response:**
```jsonc
{ "ok": true, "id": 142 }
```

(7 endpoints в счёте — preview + save + list + get + update + reorder + toggle + delete = технически 8; toggle является PATCH-вариантом update, формально один endpoint в API contract; в реализации можно совместить с update — обсудим в Этапе 1.)

---

## 7. Pydantic schemas

`backend/schemas/observation.py`:

```python
class BlockPreviewRequest(BaseModel):
    kind: Literal["observation_period_analysis", "observation_segment"]
    well_id: int
    period_from: date
    period_to: date
    params: dict[str, Any] = Field(default_factory=dict)

class BlockSaveRequest(BaseModel):
    well_id: int
    kind: Literal["observation_period_analysis", "observation_segment"]
    title: str | None = None
    comment: str = ""
    params: dict[str, Any]
    data_snapshot: dict[str, Any]
    in_report: bool = True
    sort_order: int | None = None

class BlockUpdateRequest(BaseModel):
    params: dict[str, Any]
    comment: str | None = None
    title: str | None = None
    data_snapshot: dict[str, Any]

class BlockReorderRequest(BaseModel):
    well_id: int
    order: list[int]

class BlockToggleRequest(BaseModel):
    in_report: bool
```

`params` намеренно `dict[str, Any]` (не вложенная Pydantic модель), потому что разные kinds имеют разные allowed fields — валидация per-kind делается в сервисе.

---

## 8. Test Plan Этапа 1

### Раздел A — Backend smoke (без UI)

| # | Тест | Ожидание |
|---|---|---|
| A1 | POST /preview kind=observation_period_analysis для реальной скв.85, период 01.01–31.03 | `ok=True, status="ok", snapshot._v="observation_v1", snapshot.kind="observation_period_analysis"`, есть `key_metrics`, `chart_data`, `describe_table_rows` |
| A2 | POST /preview kind=observation_segment для скв.85, promote_only_working=False | Snapshot совместим с `segment_v1` (segments_extended, cp_marks, dual_summary), `dual_summary.r1_promote_enabled=False` |
| A3 | POST /preview kind=observation_segment с promote_only_working=True | `dual_summary.r1_promote_enabled=True`, `r1_promoted_count > 0` |
| A4 | POST /preview с пустым периодом (нет данных well_daily) | HTTP 200, `ok=False, status="error", error="..."` (НЕ 500) |
| A5 | POST /preview с invalid kind | HTTP 422 (Pydantic Literal валидация) |
| A6 | POST /save с `data_snapshot.ok=false` | HTTP 400 — нельзя сохранять error |
| A7 | POST /save → запись в `customer_report_block`, `params.source="observation"`, sort_order auto-assign | Запись создана, id вернулся |
| A8 | GET /blocks?well_id=14 | Только observation-блоки (фильтр `params->>'source' = 'observation'`), без data_snapshot |
| A9 | GET /block/{id} | Полный блок с snapshot, БЕЗ recompute |
| A10 | PUT /block/{id} с новыми params + новым snapshot | UPDATE прошёл, updated_at обновился |
| A11 | POST /reorder с order=[c,a,b] | sort_order у трёх блоков перерасставлен |
| A12 | PATCH /block/{id}/toggle in_report=false | Поле обновилось, snapshot не пересчитан |
| A13 | DELETE /block/{id} | Запись удалена; повторный GET → 404 |

### Раздел B — Frontend (manual + automation)

| # | Тест | Ожидание |
|---|---|---|
| B1 | Открыть /observation, выбрать скв.85, указать период | Sidebar load с блоками well 14, или empty state |
| B2 | Кликнуть «+ Добавить блок» → period_analysis → ввести период → Preview | Workspace показывает live snapshot, status badge `ok`, плитки + графики |
| B3 | Save | Блок появляется в sidebar, sort_order=N+1 |
| B4 | Выбрать блок в sidebar | Workspace грузит блок через /block/{id}, рендерит из snapshot (без вызовов аналитики в network log) |
| B5 | Изменить prefix_note → workspace flag isDirty | Появляется индикатор «несохранённые изменения» |
| B6 | Update | PUT /block/{id}, snapshot обновляется |
| B7 | Drag-reorder | POST /reorder, порядок сохраняется при reload страницы |
| B8 | Toggle in_report | PATCH /toggle |
| B9 | Delete | confirm dialog → DELETE → блок исчезает из sidebar |
| B10 | observation_segment блок с promote_only_working=True | Виден R1 badge в preview, в saved params хранится `promote_only_working=true` |
| B11 | Открыть error-блок (например период без данных) | Workspace показывает error state, save кнопка disabled |
| B12 | Renderer-neutral check: Network tab при select блока | Только GET /block/{id} и static asset requests, нет аналитических endpoints |

### Раздел C — Renderer-neutral acceptance (жёсткие критерии)

| # | Правило | Verification |
|---|---|---|
| C1 | JS render-функции не делают fetch() аналитических endpoints | grep по observation.js: внутри `[BLOCK-PREVIEW]` секции — только функции из `[API]` для GET блока, никаких прямых `/api/customer-daily/segment-analysis/preview` или прочее |
| C2 | Сервисы не пишут в БД | grep по `observation_period_service.py` / `observation_segment_service.py` — нет `db.add`, `db.commit`, `db.execute(INSERT/UPDATE/DELETE)`. Только SELECT для чтения well_daily. |
| C3 | Renderer обрабатывает status корректно | manual: snapshot с `status="error"` → renderer не падает, показывает placeholder. snapshot с `status="partial"` → показывает доступные секции, em-dash для отсутствующих. |
| C4 | Existing renderer не сломан | После добавления Observation: запустить `python scripts/selfcheck_segment_parts.py` — должен пройти. Открыть /customer-daily — должен работать без изменений. |
| C5 | Saved snapshot immutability | После 10 чтений блока: `data_snapshot` и `updated_at` идентичны исходным |

### Раздел D — Regression

| # | Тест | Ожидание |
|---|---|---|
| D1 | Запустить `selfcheck_segment_parts.py` | ALL OK |
| D2 | /customer-daily загружается без ошибок | OK |
| D3 | /adaptation-wizard загружается без ошибок | OK |
| D4 | Saved блок 77 (скв.128, customer_data) рендерится в PDF без изменений | OK |
| D5 | Любой existing блок segment_analysis (НЕ observation) — preview и attach работают | OK |

---

## 9. Acceptance criteria Этапа 1

| # | Критерий | Verification |
|---|---|---|
| 1 | Страница `/observation` отдаётся как самостоятельная (не extends customer_daily/adaptation_wizard) | Manual: открыть URL, view source |
| 2 | Все 7 endpoints работают согласно §6 | Tests раздел A |
| 3 | Frontend lifecycle: preview → save → list → select → edit → update → delete → reorder → toggle | Tests раздел B |
| 4 | observation_period_analysis и observation_segment оба работают как kinds | A1, A2, B2, B10 |
| 5 | observation_segment переиспользует существующий segment renderer без копирования HTML | grep в observation.js → вызовы `_buildSegmentBlockHtml`/`_renderSegmentBlockCharts` из customer_daily.js (либо вынесенные в shared lib) |
| 6 | params.source="observation" применяется ко всем блокам главы | A7 |
| 7 | Renderer-neutral: ни один renderer не вызывает analytics | C1, C2, C3 |
| 8 | Saved snapshot immutable | C5 |
| 9 | params ≠ parts разделение соблюдено | grep в Pydantic + manual: `promote_only_working` в `params.*`, не в `params.parts` |
| 10 | Все experimental опции default OFF | A2 (promote default False), schema check |
| 11 | Existing функционал не сломан | Раздел D |
| 12 | Нет schema bump customer_report_block | Migrations не менялись |
| 13 | adaptation_wizard и customer_daily не расширены | git diff показывает изменения только в новых файлах + точечный patch app.py для подключения роутера |

---

## 10. Этапы реализации (рекомендуемый порядок после СОГЛАСОВАНО)

1. **Этап 1.1 — Backend skeleton:**
   - `observation_period_service.py` (compute_block для period_analysis с базовыми key_metrics + chart_data + describe_table)
   - `observation_segment_service.py` (wrapper)
   - `registry.py`
   - `schemas/observation.py`
   - `routers/observation.py` (7 endpoints с TODO внутри)
   - Регистрация в `app.py`
   - Тесты A1–A13

2. **Этап 1.2 — Frontend skeleton:**
   - `observation.html` (структура)
   - `observation.css` (grid layout)
   - `observation.js` STATE + API + INIT
   - Sidebar render + workspace empty state
   - Тест B1

3. **Этап 1.3 — Block lifecycle:**
   - Add block flow (preview + workspace)
   - Save / Update / Delete
   - Тесты B2-B9

4. **Этап 1.4 — observation_segment integration:**
   - Reuse `_buildSegmentBlockHtml` / `_renderSegmentBlockCharts` (вынести в shared `segment_renderer.js` если нужно)
   - Тест B10

5. **Этап 1.5 — Reorder + toggle + error states:**
   - Drag-reorder через HTML5 native
   - in_report toggle
   - Error state UI
   - Тесты B7, B8, B11

6. **Этап 1.6 — Renderer-neutral + regression:**
   - Verification раздел C
   - Раздел D

Каждый суб-этап — отдельное СОГЛАСОВАНО, чтобы не повторить adaptation_wizard.

---

## 11. Жёсткие архитектурные правила (повтор)

| # | Правило |
|---|---|
| 1 | НЕ расширять `adaptation_wizard.html` / `customer_daily.html` |
| 2 | НЕ делать PDF на Этапе 1 |
| 3 | НЕ делать rose, comparison, baseline на Этапе 1 |
| 4 | НЕ пересчитывать snapshot при render |
| 5 | НЕ мутировать saved snapshot |
| 6 | Все экспериментальные опции — explicit opt-in, default OFF |
| 7 | params (analysis) хранятся ОТДЕЛЬНО от params.parts (rendering) |
| 8 | Source of truth для сохранённого блока — `data_snapshot` |
| 9 | Renderer reuse там, где возможно, без копирования гигантского HTML |
| 10 | Никаких новых таблиц / миграций |
| 11 | snapshot.status — read-only для renderer, write-only для service |
| 12 | Каждый kind = одна функция compute_block в одном файле сервиса |
| 13 | UI не имеет права угадывать состояние из params — всегда читает status/ok из snapshot |

---

## 12. Открытые вопросы (требуется решение перед кодом)

| # | Вопрос | Варианты | Рекомендация |
|---|---|---|---|
| Q1 | Где хранить `params_summary` для list view (§6.3)? | (a) генерировать на лету из `params`+`snapshot`; (b) materialized field | **(a)** на лету — нет нужды в денормализации |
| Q2 | Куда вынести `_buildSegmentBlockHtml` для reuse? | (a) оставить в customer_daily.js + import via `<script src=...>`; (b) выделить `static/js/lib/segment_renderer.js` | **(b)** выделить в отдельный файл, оба места подключают — снизит риск drift |
| Q3 | Какой PK route к /observation? | `/observation?well={number}` ИЛИ `/observation/{well_id}` | `/observation?well={number}` — консистентно с `/well/{number}` существующим, well_number в URL |
| Q4 | Что делать с draft-блоками (preview без save)? | (a) теряются при reload — STATE only; (b) сохранять как draft с in_report=false | **(a)** теряются — простое поведение; preview быстрый |
| Q5 | Обработка одновременного редактирования двумя operators? | (a) last-write-wins; (b) version field + 409 | **(a)** lww — Этап 1 не имеет collaboration scope |
| Q6 | Где well selector? | (a) на /observation странице; (b) глобальный header выбор скважины | **(a)** локальный selector в sidebar; глобальный header не делаем |
| Q7 | Минимальная скважина без well_daily — что показать? | error при /preview; sidebar блокирует выбор? | error при /preview (как existing), sidebar не блокирует — оператор может выбрать скв., увидит error при попытке preview |

---

## 13. История версий TZ

| Дата | Версия | Изменение |
|---|---|---|
| 2026-05-19 | 1 | Initial draft. Подготовлен до начала кода в рамках Этапа 1. Все 13 acceptance criteria, 7 endpoints, ASCII wireframe, Test Plan, 7 open questions. |
