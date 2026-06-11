> ⚠️ **DEPRECATED DRAFT — НЕ ИСПОЛЬЗОВАТЬ КАК IMPLEMENTATION SOURCE**
>
> Этот документ конфликтует с [`RFC_observation_snapshot.md`](../../RFC_observation_snapshot.md) и существующим observation backend (`backend/services/observation_*.py`, 141 тест PASS).
>
> **Источник правды:** `RFC_observation_snapshot.md` + текущие `backend/services/observation_*`.
>
> Документ оставлен для архива (черновик 2026-05-19, сессия Этапа 1 без сверки existing backend). Принятое решение: **Вариант A — реинтегрировать existing backend** (см. реконсилиационный отчёт).

---

# observation block — контракт

Документ фиксирует контракт **блоков главы «Наблюдение»**. Применяется к kinds:

- `observation_period_analysis`
- `observation_segment`

(Дополнительные kinds — отдельным согласованием.)

Связан с [segment_snapshot_contract.md](segment_snapshot_contract.md) и TZ Этапа 1.

---

## 1. Назначение

Блок — атомарная единица главы «Наблюдение»:

- хранится в `customer_report_block.data_snapshot` (JSONB);
- имеет фиксированный набор полей в `params`;
- источник правды для UI preview / popout / chapter render / future PDF;
- **не пересчитывается** на стороне renderer — рассчитывается один раз сервисом, дальше только сериализуется.

Принцип `block-driven + snapshot-driven + renderer-neutral`.

---

## 2. Жизненный цикл блока

```
[ConstructorPanel]
   │
   │ user указывает скважину, период, kind, опции
   ▼
[/api/observation/block/preview]    ← НЕ пишет в БД
   │
   │ возвращает {ok, snapshot, status}
   ▼
[ActiveBlockWorkspace]               ← rendering из snapshot
   │
   │ user проверяет, корректирует params, повторяет preview
   ▼
[/api/observation/block/save]        ← INSERT в customer_report_block
   │
   ▼
[BlockSidebar.list]
   │
   │ select / edit / reorder / toggle in_report / delete
   ▼
[/api/observation/block/update]      ← повторный preview + UPDATE snapshot
[/api/observation/block/reorder]
[/api/observation/block/toggle]
[/api/observation/block/delete]
```

Block lifecycle status (в snapshot.status):

| status | Смысл |
|---|---|
| `ok` | snapshot валиден, готов к рендеру |
| `partial` | данные посчитаны, но не для всех полей (например нет p_flowline) — renderer показывает доступное, отсутствующие части заменяются placeholder |
| `error` | расчёт упал; `error` поле содержит читаемое сообщение; renderer не рисует, выводит status |
| `legacy` | snapshot из старого формата (без `_v`/`schema_version`) — рендерится best-effort, помечается warning-баджем |

`status` пишет сервис, **не renderer**.

---

## 3. Контракт полей блока (БД-строка)

`customer_report_block`:

| Поле | Источник | Назначение |
|---|---|---|
| `id` | DB | PK |
| `well_id` | UI | FK → wells.id |
| `kind` | UI | `observation_period_analysis` или `observation_segment` |
| `title` | сервис | автогенерируется при preview, может быть отредактирован UI |
| `params` | UI | см. §4 |
| `data_snapshot` | сервис | см. §5 |
| `comment` | UI | пользовательский комментарий блока (отдельно от автогенерации) |
| `in_report` | UI | bool — попадёт ли в финальную главу |
| `sort_order` | UI | int — позиция в главе (для reorder) |
| `created_at`, `updated_at` | DB | timestamp |

---

## 4. `params` (UI-управляемая часть)

`params` — **только пользовательские входы**, ничего вычислимого из данных:

### 4.1. Общие поля (все observation_* kinds)

```jsonc
{
  "source":       "observation",        // обязательное: chapter discriminator
  "chapter":      "observation",        // дубль для удобства фильтра в API/SQL
  "well_number":  "85",                 // для сохранения identity при reorder
  "date_from":    "2026-01-01",         // ISO
  "date_to":      "2026-03-31",
  "prefix_note":  "",                   // оператор: вступительный текст
  "suffix_note":  "",                   // оператор: заключительный текст
  "parts":        { ... }               // renderer-toggles (см. §4.4)
}
```

**Правило:** `params.source = "observation"` — единственный признак, по которому новая глава фильтрует блоки. Это позволяет переиспользовать `customer_report_block` без миграций.

### 4.2. observation_period_analysis

Дополнительные поля:

```jsonc
{
  "metric_set": "default",              // resv. для будущих "extended" наборов
}
```

### 4.3. observation_segment

Дополнительные поля:

```jsonc
{
  "include_pav":          false,        // analysis param, не render toggle
  "promote_only_working": false         // R1 experimental opt-in, default OFF
                                        // (см. segment_snapshot_contract.md §7)
}
```

### 4.4. `params.parts` — renderer toggles

**ВАЖНО (R1 lesson):** `parts` — **только видимость секций при рендере**. Никаких analysis-параметров.

Для observation_period_analysis:
```jsonc
{
  "prefix_note":     true,
  "key_metrics":     true,    // плитки
  "pressures_chart": true,
  "dp_chart":        true,
  "flow_dt_chart":   true,
  "describe_table":  true,
  "description":     true,    // user comment
  "suffix_note":     true
}
```

Для observation_segment:
```jsonc
{
  "prefix_note":      true,
  "q_segment_chart":  true,
  "segments_table":   true,
  "descriptions":     true,
  "cp_descriptions":  true,
  "description":      true,
  "suffix_note":      true
}
```

(Совпадает с `SEGMENT_ANALYSIS_RENDER_PARTS` для совместимости с existing renderer.)

---

## 5. `data_snapshot` (сервис-вычисляемая часть)

### 5.1. Общая структура (все observation_* kinds)

```jsonc
{
  "ok":            true,                // или false при ошибке
  "_v":            "observation_v1",
  "schema_version": 1,
  "kind":          "observation_period_analysis",
  "status":        "ok" | "partial" | "error" | "legacy",
  "error":         null,                // при status=error
  "warnings":      [],                  // optional, человеко-читаемые
  "well_number":   "85",
  "date_from":     "2026-01-01",
  "date_to":       "2026-03-31",
  "days_count":    90,
  "n_points":      88,
  "computed_at":   "2026-05-19T13:14:15Z",
  "thresholds_used": { "has_overrides": false, "updated_at": null },
  ...
  // далее — kind-specific блоки
}
```

### 5.2. observation_period_analysis snapshot

```jsonc
{
  ...common,
  "kind": "observation_period_analysis",

  // плитки
  "key_metrics": {
    "q_total":   { "avg": 45.6, "median": 46.0, "min": 36.7, "max": 49.6 },
    "q_working": { "avg": 41.8, "median": 42.7, "min": 26.9, "max": 49.8 },
    "p_wellhead":{ "avg": 22.8, "median": 22.9 },
    "p_flowline":{ "avg": 16.5, "median": 16.4 },
    "dp":        { "avg":  6.3, "median":  6.5 },
    "shutdown":  { "total_min": 1115, "days_with": 19 }
  },

  // chart-data — для plotly/matplotlib (renderer-neutral)
  "chart_data": {
    "dates":       ["2026-01-01", ...],
    "p_wellhead":  [23.1, ...],
    "p_flowline":  [16.8, ...],
    "p_annular":   null,                // optional
    "p_static":    null,                // optional
    "dp":          [6.3, ...],
    "q_total":     [46.4, ...],
    "q_working":   [47.0, ...],
    "shutdown_min":[0, ...]
  },

  // короткое инженерное описание (autogen, без diagnostic claims)
  "describe_table_rows": [
    { "label": "Период",           "value": "2026-01-01 — 2026-03-31 (90 сут)" },
    { "label": "Q общий, ср.",     "value": "45.6 тыс.м³/сут" },
    ...
  ]
}
```

### 5.3. observation_segment snapshot

Полностью совместим с `segment_v1` ([segment_snapshot_contract.md](segment_snapshot_contract.md)):

```jsonc
{
  ...common,
  "kind": "observation_segment",

  // ВСЁ из segment_v1:
  "chart_data":        { dates, q_total, q_working, shutdown_min },
  "segments_extended": [...],
  "cp_marks":          [...],
  "shutdown_clusters": [...],
  "dual_summary":      { ...,
                         r1_promote_enabled, r1_promoted_count,
                         r1_near_confirmed_count, r1_final_boundary_count },
  "descriptions":      [...],
  "cp_descriptions":   [...],
  "include_pav_recommendation": false,
  "pav":               null
}
```

**Адаптер `kind`:** для саженных блоков renderer обрабатывает `observation_segment` идентично `segment_analysis` — UI/PDF код не меняется.

---

## 6. Жёсткие правила (acceptance invariants)

| # | Правило |
|---|---|
| 1 | **Renderer-neutral**: renderer обращается ТОЛЬКО к `data_snapshot`. Запрещено: чтение БД, вызов сервисов аналитики, доступ к `pressure_raw`/`flow_rate_calculate`. |
| 2 | **Snapshot immutability**: сохранённый snapshot НЕ мутируется при чтении/рендере/preview. Любое изменение = новый snapshot, новая запись/UPDATE. |
| 3 | **No silent recompute**: запрос `/list` или `/get` не пересчитывает блок. Только `/preview` и `/update` запускают сервис. |
| 4 | **Default OFF для экспериментальных опций**: `promote_only_working`, `include_pav` — default False, передаются явно. |
| 5 | **params ≠ parts**: analysis-параметры (`promote_only_working`, `include_pav`, `metric_set`) хранятся в `params.*`, **НЕ** в `params.parts`. `parts` — только видимость секций. |
| 6 | **Status-aware rendering**: renderer обязан читать `snapshot.status` и `snapshot.ok`. При `error` — placeholder, без попытки рисовать. При `partial` — показывает доступное + индикатор. При `legacy` — рисует best-effort + warning badge. |
| 7 | **Source of truth — snapshot**: при attach в БД, любые computed-флаги (например `params.promote_only_working`) берутся из `snapshot.dual_summary.r1_promote_enabled`, а не из UI checkbox state (R1 lesson). |
| 8 | **No schema bump в Этапе 1**: `schema_version=1` остаётся. Любое изменение структуры — отдельным контрактом. |
| 9 | **No global settings**: все параметры — на уровне блока. Никаких per-user / per-well defaults на этом этапе. |
| 10 | **Tag-based discriminator**: блоки главы наблюдения отличаются от блоков customer_data только через `params.source = "observation"`. Никаких новых таблиц. |

---

## 7. Failure modes и status codes

### 7.1. status = `ok`
Полный успешный snapshot. UI рендерит без warnings.

### 7.2. status = `partial`
Сервис посчитал часть полей (например, `key_metrics` есть, но `p_annular=null` потому что нет колонки). Renderer показывает доступные секции, для отсутствующих — em-dash. `warnings` содержит причины.

### 7.3. status = `error`
Расчёт упал (нет данных в well_daily, пустой период, неизвестная скважина). `ok=false`, `error: "Нет данных..."`. UI показывает:
```
⚠ Блок не рассчитан: <error>
```
**Не блокирует список блоков**, не пишется в БД при save (validation).

### 7.4. status = `legacy`
Снимок без `_v` / `schema_version` (загружен из старой БД). UI рендерит через legacy-fallback (как PB-V2), показывает badge «legacy snapshot».

---

## 8. Расширение

Новый kind `observation_<X>`:

1. Создать `observation_<X>_service.py` со своей `compute_<X>_block(...)`.
2. Зарегистрировать в `observation/registry.py` (см. TZ Этап 1 §3).
3. Добавить `kind`-specific поля в snapshot.
4. Дополнить `params.parts` (если нужны renderer-toggles).
5. Дополнить этот контракт §4-§5.
6. Bump `_v` в `observation_v2`, если несовместимое изменение схемы (НЕ в Этапе 1).

---

## 9. История

| Дата | Версия | Изменение |
|---|---|---|
| 2026-05-19 | 1 | Initial — Этап 1 Observation Layout Architecture. Контракт фиксируется до начала кода. |
