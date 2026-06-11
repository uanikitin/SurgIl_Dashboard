# segment_analysis snapshot — контракт

Документ фиксирует контракт между:

- `segment_analysis_service.compute_segment_block(...)` — производит snapshot;
- хранилище `customer_report_block.data_snapshot` (JSONB) — единственное хранимое представление;
- HTML-renderer `customer_daily.html::_buildSegmentBlockHtml(...)` — преобразует snapshot в HTML-карточку;
- PDF-renderer (formatter `_format_segment_block` + `segment_chart_renderer.render_segment_q_chart` + LaTeX-подсекция в `adaptation_report.tex`) — преобразует snapshot в PDF.

Контракт зафиксирован после PB-V2 (HTML→PDF parity). До правки контракта необходимо явное согласование.

---

## 1. Назначение snapshot

`segment_analysis` snapshot — единственный источник правды для отображения сегментного анализа.

- HTML preview, popout и snapshot-modal используют **только** snapshot — повторного расчёта не делают.
- PDF-сборка использует **только** snapshot — повторного расчёта не делает (HTML→PDF parity).
- Графики в PDF (matplotlib) и HTML (Plotly) строятся **из тех же полей** snapshot.

Любое изменение `compute_segment_block(...)` обязано сохранять обратную совместимость существующих snapshot в БД либо изменить `schema_version` и предоставить миграцию рендереров.

---

## 2. Поля snapshot

### 2.1. Обязательные поля

| Поле | Тип | Источник | Назначение |
|---|---|---|---|
| `ok` | `bool` | service | `True` — snapshot валиден; `False` — рендереры выводят placeholder |
| `_v` | `str` | service | Текущее значение: `"segment_v1"`. Информационный маркер. |
| `well_number` | `str` | service | Номер скважины (для шапки) |
| `date_from`, `date_to` | `str` ISO `YYYY-MM-DD` | service | Период анализа |
| `days_count`, `n_points` | `int` | service | Длительность и количество суточных точек |
| `chart_data` | `dict` | service | Сырые данные для графика, см. §2.3 |
| `segments_extended` | `list[dict]` | service | Сегменты, см. §2.4 |
| `cp_marks` | `list[dict]` | service | Точки переломов (короткие метки), см. §2.5 |
| `shutdown_clusters` | `list[dict]` | service | Кластеры простоев (для подсветки графика и нижнего bar-chart) |
| `dual_summary` | `dict` | service | Сравнение Q общий ↔ Q рабочий, см. §2.7 |

### 2.2. Optional поля

| Поле | Тип | Назначение | Если отсутствует / пусто |
|---|---|---|---|
| `descriptions` | `list[str]` | Развёрнутые описания сегментов из алгоритма (§6.4) | Секция «Развёрнутые описания (из алгоритма)» не выводится |
| `cp_descriptions` | `list[str]` | Развёрнутые описания переломов (§6.5) | PDF: fallback на `cp_bullets`, построенные из `cp_marks` |
| `dual` | `dict` | Полный объект двойного анализа (модалка/legacy) | — |
| `include_pav_recommendation` | `bool` | Флаг наличия PAV-слоя | По умолчанию `False`. В PDF любое значение игнорируется (см. §6) |
| `pav` | `dict` | PAV-карточка (score / scenario / recommendation / signs_pro / signs_con) | В PDF игнорируется (см. §6) |
| `thresholds_used` | `dict` | Метаданные о применённых порогах | Информационное |
| `schema_version` | `int` | Версия структуры snapshot | Если отсутствует — считается `1` (см. §7) |

### 2.3. `chart_data`

```
chart_data = {
    "dates":        list[str],   # ISO YYYY-MM-DD, длина = n_points
    "q_total":      list[float], # Q общий, тыс.м³/сут
    "q_working":    list[float], # Q рабочий, тыс.м³/сут
    "shutdown_min": list[int],   # длительность простоя, мин/сут
}
```

Используется renderer-ом графика (PDF: `segment_chart_renderer.render_segment_q_chart`; HTML: `_renderSegmentBlockCharts`). Длины массивов должны совпадать.

### 2.4. `segments_extended[]`

Поля, используемые HTML и PDF:

| Поле | Тип | HTML | PDF |
|---|---|---|---|
| `num` | int | ✓ | ✓ |
| `start`, `end` | DD.MM.YYYY | ✓ | ✓ |
| `start_idx`, `end_idx` | int (exclusive end) | ✓ (тренды) | ✓ (тренды) |
| `days` | int | ✓ | ✓ |
| `type` | str | ✓ | ✓ |
| `color` | hex | ✓ | ✓ |
| `mean_q`, `mean_q_working` | float | ✓ | ✓ |
| `mean_dp`, `mean_shutdown`, `working_pct` | float | ✓ | ✓ |
| `mean_p_wellhead`, `mean_p_flowline`, `mean_choke_mm` | float | ✓ | ✓ rich §6.4 |
| `slope_total`, `intercept_total` | float | ✓ | ✓ |
| `slope_working`, `intercept_working` | float | ✓ | ✓ |
| `change_pct`, `change_q_working_pct`, `change_dp_pct` | float | ✓ | ✓ rich §6.4 |
| `change_shutdown`, `change_choke` | float | ✓ | ✓ rich §6.4 |
| `gradual_trend` (`"up"` / `"down"` / `None`) | str | ✓ | ✓ rich §6.4 |
| `gradual_drift_pct` | float | ✓ | ✓ rich §6.4 |
| `preshutdown_q`, `preshutdown_delta_pct`, `preshutdown_verdict` | float / str | ✓ | ✓ rich §6.4 |
| `cause` | str | ✓ | ✓ rich §6.4 (описание факта, §6.2) |

Все строковые поля могут содержать unicode, спецсимволы и `\n` — PDF formatter обязан экранировать перед LaTeX (см. `_safe_tex_full`).

### 2.5. `cp_marks[]`

```
{
    "idx":    int,                 # индекс в chart_data
    "date":   str ISO YYYY-MM-DD,
    "tag":    str,                 # короткая подпись над вертикалью
    "source": "confirmed" | "only_total" | "only_working",
}
```

PDF использует для:

- вертикалей переломов на графике (3 стиля по `source`);
- fallback-генерации `cp_bullets`, если `cp_descriptions` отсутствует.

### 2.6. `cp_descriptions[]` и `descriptions[]`

Списки строк. Каждая строка — независимый элемент (один пункт списка в HTML/PDF). Внутри могут быть `\n` (мягкий перенос строки), markdown-токены `**bold**`, unicode-стрелки и пр. **Renderers выводят содержимое как есть** — никакого парсинга markdown или замены unicode. Это поведение HTML, и PDF ему следует.

### 2.7. `dual_summary`

```
{
    "primary_cp_count": int,
    "working_cp_count": int,
    "common_count":     int,
    "common_dates":     list[str],
    "only_total_dates": list[str],
    "only_working_dates": list[str],
}
```

PDF выводит **всегда** — это часть инвариантов, не управляется чекбоксом (см. §5).

---

## 3. Поля, используемые PDF-рендером

Formatter `_format_segment_block` обращается только к:

- `ok`, `well_number`, `date_from`, `date_to`, `days_count`, `n_points`;
- `chart_data` (передаётся в `segment_chart_renderer.render_segment_q_chart`);
- `segments_extended[]` — все поля из §2.4;
- `cp_marks[]` (для fallback bullets);
- `cp_descriptions[]` (приоритет);
- `descriptions[]`;
- `dual_summary`;
- `schema_version` (мягкая проверка, см. §7).

Не используются: `pav`, `include_pav_recommendation`, `dual`, `thresholds_used`.

---

## 4. Поля, используемые HTML-рендером

`_buildSegmentBlockHtml(snap, block, idPrefix)` в [`customer_daily.html`](../../backend/templates/customer_daily.html) обращается к:

- `ok`, `date_from`, `date_to`, `days_count`, `n_points`, `thresholds_used.has_overrides`;
- `chart_data`, `segments_extended[]` (рендер графика через `_renderSegmentBlockCharts`);
- `cp_marks[]` (для вертикалей графика);
- `descriptions[]`, `cp_descriptions[]`;
- `dual_summary`;
- `include_pav_recommendation`, `pav` (только в HTML).

---

## 5. parts (чекбоксы блока)

Источник правды — HTML `PART_LABELS.segment_analysis` в [`customer_daily.html`](../../backend/templates/customer_daily.html). Сохраняются пользователем в `customer_report_block.params.parts`.

PDF использует те же ключи (за исключением PAV — см. §6). См. [`backend/constants/segment_parts.py`](../../backend/constants/segment_parts.py) — `SEGMENT_ANALYSIS_RENDER_PARTS`.

| HTML key | HTML | PDF | Дефолт | Описание |
|---|---|---|---|---|
| `prefix_note` | ✓ | ✓ | `True` | Вступительный текст (`params.prefix_note`) |
| `pav_card` | ✓ | — | `True` | PAV-карточка. **Исключено из PDF** (§6) |
| `signs_table` | ✓ | — | `True` | Признаки за/против PAV. **Исключено из PDF** (§6) |
| `q_segment_chart` | ✓ | ✓ | `True` | График Q + downtime subplot |
| `segments_table` | ✓ | ✓ | `True` | Таблица сегментов (11 кол.) |
| `cp_descriptions` | ✓ | ✓ | `True` | События переломов |
| `descriptions` | ✓ | ✓ | `True` | Описание сегментов (§6.4 rich) + развёрнутые описания (`snap.descriptions[]`) |
| `description` | ✓ | ✓ | `True` | Пользовательский комментарий `block.comment` |
| `suffix_note` | ✓ | ✓ | `True` | Заключительный текст (`params.suffix_note`) |

### 5.1. Always-on (без чекбокса)

| Раздел | Источник данных |
|---|---|
| `dual_comparison` | `dual_summary` |

`dual_comparison` **не управляется чекбоксом**. В HTML рендерится автоматически при наличии `dual_summary`. PDF выводит безусловно — см. `SEGMENT_ANALYSIS_ALWAYS_ON_PARTS`.

---

## 6. Что НЕ рендерится в PDF (по scope PB-V2)

| Ключ | Причина |
|---|---|
| `pav_card` | PAV-слой не выводится в PDF. Отдельное согласование требуется для включения. |
| `signs_table` | См. выше. |

PAV-поля **могут присутствовать** в snapshot или в `block.title` (если пользователь сам так назвал блок) — это не считается нарушением. Запрещены только PAV-секции (карточка/балл/сценарий/обоснование).

См. `SEGMENT_ANALYSIS_PDF_EXCLUDED_PARTS`.

---

## 7. `schema_version` — мягкая проверка

В PDF formatter `_format_segment_block` выполняется мягкая проверка `schema_version`:

```
supported = 1
v = int(snapshot.get("schema_version") or 1)
if v > supported:
    log.warning(
        "segment snapshot schema_version=%s > supported=%s (block=%s) — "
        "продолжаю рендер на best-effort",
        v, supported, block_id,
    )
```

Правила:

- snapshot **без** `schema_version` → трактуется как `v = 1` (legacy compatibility).
- `v <= supported` → рендер штатный, без предупреждений.
- `v > supported` → рендер продолжается, выводится `log.warning`. **PDF не получает diagnostic-вставок в пользовательский текст.**
- Падать на новой версии **запрещено** — legacy snapshot и старые блоки в БД должны рендериться.

`schema_version` **не обязателен** в snapshot и не должен требоваться валидацией БД.

---

## 8. Legacy fallback

| Ситуация | Поведение |
|---|---|
| `descriptions == []` или отсутствует | Секция «Развёрнутые описания (из алгоритма)» в PDF не выводится. Rich-§6.4 описания строятся из `segments_extended` независимо. |
| `cp_descriptions == []` или отсутствует | PDF использует fallback: `cp_bullets`, построенные из `cp_marks[]` (короткие bullets с датой, тегом, source). |
| `cp_marks == []` и `cp_descriptions == []` | Секция «События переломов» / «Точки переломов» не выводится. |
| `shutdown_clusters == []` | Нижний bar-chart рисует все простои как одиночные (без подсветки кластеров на верхнем subplot). |
| Snapshot без `schema_version` | См. §7. |

---

## 9. Точки расширения

При добавлении новых разделов:

1. Добавить ключ в HTML `PART_LABELS.segment_analysis` и обработать в `_buildSegmentBlockHtml`.
2. Добавить ключ в `SEGMENT_ANALYSIS_RENDER_PARTS` ([`backend/constants/segment_parts.py`](../../backend/constants/segment_parts.py)).
3. Обработать в `_format_segment_block` и LaTeX-подсекции в `adaptation_report.tex`.
4. При несовместимом изменении полей snapshot — поднять `schema_version` в сервисе и предоставить миграцию рендереров.

При расхождении HTML↔PDF добавить failing self-check в `scripts/selfcheck_segment_parts.py`.

---

## 10. История

| Дата | Версия | Изменение |
|---|---|---|
| 2026-05-19 | 1 | Initial — после PB-V2 Final Hardening. Фиксация контракта, parts symmetry, legacy fallback, soft schema_version. |
