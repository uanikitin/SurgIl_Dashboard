# Алгоритм формирования блока B2 «Анализ (датчики)»

> Документация: путь данных от LoRa-датчиков до отображения блока observation_baseline

## 1. ИСТОЧНИК ДАННЫХ (Data Source)

```
LoRa-датчики давления (hardware)
    ↓
SQLite файлы на сервере датчиков
    ↓
pressure_pipeline.py (импорт по расписанию)
    ↓
PostgreSQL таблица: pressure_raw
```

**Таблица `pressure_raw`:**
| Колонка | Тип | Описание |
|---------|-----|----------|
| well_id | int | ID скважины |
| measured_at | timestamp | Время замера (UTC) |
| p_tube | float | Давление устьевое (кгс/см²) |
| p_line | float | Давление шлейфовое (кгс/см²) |

---

## 2. ЗАПРОС ДАННЫХ (data_access.py)

```python
# flow_rate/data_access.py:20-35
get_pressure_data(well_id, start, end)
    →  SELECT measured_at, p_tube, p_line FROM pressure_raw
       WHERE well_id = :wid AND measured_at BETWEEN :start AND :end
    →  DataFrame[p_tube, p_line], index=measured_at (UTC)
```

---

## 3. ПАЙПЛАЙН РАСЧЁТА ДЕБИТА (full_pipeline.py)

```python
# flow_rate/full_pipeline.py:36
compute_full_flow(well_id, dt_start, dt_end, smooth=True)
```

**Шаги пайплайна:**
1. `get_pressure_data()` → сырые p_tube, p_line
2. `clean_pressure()` → убираем NaN, inf, выбросы
3. `false_zero_filter()` → фильтр ложных нулей (сбои датчика)
4. **UTC → Кунград** — сдвиг индекса +5h
5. `smooth_pressure()` (Savitzky-Golay) → сглаживание
6. `calculate_flow_rate()` → Q = f(ΔP, штуцер)
7. `detect_downtime()` → периоды простоя (ΔP < 0.1)
8. `detect_purge_cycles()` → события продувок
9. **Обнуление Q при простое** — flow_rate=0 для нерабочих минут
10. Возврат: `{df, choke_mm, downtime_periods, purge_cycles, summary}`

---

## 4. OBSERVATION DATA SERVICE (observation_data_service.py)

```python
# observation_data_service.py:106
load_observation_data(db, well_id, d_from, d_to, aggregation="daily")
```

**Логика:**
1. Конвертация дат Кунград → UTC
2. Вызов `compute_full_flow()` → поминутный df
3. Агрегация до запрошенного уровня (`_aggregate_to(df, "daily")`):
   - p_tube, p_line, dp: mean по **рабочим** строкам (ΔP-фильтр)
   - q: mean по **всем** строкам (включая 0 при простое)
   - shutdown_min: кол-во нерабочих минут
   - purge_flag: any() за окно
4. `compute_data_quality()` → quality flags:
   - coverage_pct (покрытие периода)
   - gap_count, max_gap_hours (гэпы)
   - suspicious_spikes_count (z-score > 3σ)
   - false_zero_pct (ложные нули)
5. Возврат: `ObservationDataResult(our_df, our_raw_minute_df, our_meta, data_quality)`

---

## 5. BASELINE SERVICE (observation_baseline_service.py)

```python
# observation_baseline_service.py:47
compute_baseline_preview(db, well_id, d_from, d_to, include_raw_chart=True)
```

**Формирование snapshot:**

```python
snapshot = {
    "_v": "obs_baseline_v1",
    "schema_version": "1.0",
    "computed_at": "2026-02-18T14:30:00Z",
    "block_status": "ok" | "no_data" | "insufficient_data",
    "period": {"from": "2026-02-02", "to": "2026-02-18"},

    # Layer 1: raw (если include_raw_chart=True)
    "raw": {
        "chart_payload": {
            "dates": ["2026-02-02", "2026-02-03", ...],
            "p_tube": [45.2, 44.8, ...],
            "p_line": [42.1, 41.9, ...],
            "dp": [3.1, 2.9, ...],
            "q": [125.4, 118.7, ...],
            "shutdown_hours": [0.5, 0.0, ...]
        }
    },

    # Layer 2: metrics
    "metrics": {
        "p_tube": {"mean": 45.1, "median": 45.0, "min": 43.2, "max": 47.8,
                   "std": 1.2, "cv": 2.7, "slope": -0.05, "direction": "falling"},
        "p_line": {...},
        "dp": {...},
        "q": {...},
        "downtime": {"total_hours": 12.5, "events_count": 3,
                     "max_event_hours": 8.0, "downtime_pct_of_period": 3.1},
        "purge_events_count": 5
    },

    # Layer 3: quality
    "quality": {
        "status": "ok",
        "flags": [],
        "metrics": {
            "coverage_pct": 98.5,
            "gap_count": 2,
            "max_gap_hours": 4.5,
            "suspicious_spikes_count": 0,
            "false_zero_pct": 0.3,
            "days_with_data": 17,
            "days_requested": 17
        }
    },

    # Layer 6: flags
    "flags": {
        "low_coverage": False,
        "significant_gap": False,
        "outlier_detected": False
    }
}
```

---

## 6. СОХРАНЕНИЕ В БД (customer_daily_service.py)

```python
# POST /api/customer-daily/blocks
{
    "well_id": 123,
    "kind": "observation_baseline",
    "title": "B2 · 2026-02-02 — 2026-02-18 (17 сут.) 📊 Анализ (датчики)",
    "params": {"period": {"from": "2026-02-02", "to": "2026-02-18"}},
    "data_snapshot": snapshot,
    "in_report": true
}
```

**Таблица `customer_report_block`:**
| Колонка | Значение |
|---------|----------|
| id | 338 |
| well_id | 123 |
| kind | "observation_baseline" |
| title | "B2 · 2026-02-02 — 2026-02-18..." |
| params | `{period: {...}}` |
| data_snapshot | (JSON snapshot выше) |
| in_report | true |

---

## 7. ОТОБРАЖЕНИЕ В UI (customer_daily.html)

**HTML-превью:**
```javascript
// customer_daily.html:4697
_buildSegmentBlockHtml(snap, block, idPrefix)
    → hasData = snap.block_status === "ok"
    → _renderSegmentBlockCharts() → Plotly.js графики
```

**Рендер графиков:**
```javascript
// Данные из snapshot.raw.chart_payload
const dates = snap.raw.chart_payload.dates;
const q = snap.raw.chart_payload.q;
const dp = snap.raw.chart_payload.dp;

Plotly.newPlot(chartDiv, [{x: dates, y: q, name: 'Q'}], layout);
```

---

## 8. РЕНДЕР ДЛЯ PDF (observation_chapter_renderer.py)

```python
# observation_chapter_renderer.py:984
render_baseline(snapshot, ctx) → RenderResult(html, latex, figures)
```

**LaTeX-вывод:**
1. Таблица метрик (p_tube, p_line, dp, q)
2. PNG-график через `render_baseline_chart()` (matplotlib)
3. Quality flags как предупреждения

---

## СХЕМА ПОТОКА ДАННЫХ (ASCII)

```
┌─────────────────┐
│  LoRa-датчики   │  ← Hardware
│  (p_tube,p_line)│
└────────┬────────┘
         ↓
┌─────────────────┐
│  SQLite files   │  ← Локальное хранилище датчиков
└────────┬────────┘
         ↓
┌─────────────────┐
│ pressure_       │  ← Импорт по расписанию (schedule_config.json)
│ pipeline.py     │     День: каждые 5 мин, Ночь: каждые 30 мин
└────────┬────────┘
         ↓
┌─────────────────┐
│ pressure_raw    │  ← PostgreSQL: сырые поминутные замеры
│ (well_id,       │
│  measured_at,   │
│  p_tube, p_line)│
└────────┬────────┘
         ↓
┌─────────────────┐
│ get_pressure_   │  ← SQL SELECT за период
│ data()          │
└────────┬────────┘
         ↓
┌─────────────────┐
│ compute_full_   │  ← Пайплайн: очистка → сглаживание →
│ flow()          │     расчёт Q → детект простоев/продувок
└────────┬────────┘
         ↓
┌─────────────────┐
│ load_observation│  ← Агрегация (daily) + quality flags
│ _data()         │
└────────┬────────┘
         ↓
┌─────────────────┐
│ compute_baseline│  ← Формирование snapshot obs_baseline_v1
│ _preview()      │
└────────┬────────┘
         ↓
┌─────────────────┐
│ customer_report │  ← PostgreSQL: сохранённый блок
│ _block          │
└────────┬────────┘
         ↓
    ┌────┴────┐
    ↓         ↓
┌───────┐ ┌───────┐
│ HTML  │ │ PDF   │
│Plotly │ │LaTeX  │
│graphs │ │render │
└───────┘ └───────┘
```

---

## Связанные файлы

| Файл | Назначение |
|------|------------|
| `backend/services/pressure_pipeline.py` | Импорт из SQLite в PostgreSQL |
| `backend/services/flow_rate/data_access.py` | SQL-запросы к pressure_raw |
| `backend/services/flow_rate/full_pipeline.py` | Полный расчёт дебита |
| `backend/services/observation_data_service.py` | Загрузка и агрегация данных |
| `backend/services/observation_baseline_service.py` | Формирование snapshot B2 |
| `backend/services/customer_daily_service.py` | CRUD блоков отчёта |
| `backend/templates/customer_daily.html` | UI отображение блоков |
| `backend/services/observation_chapter_renderer.py` | HTML/LaTeX рендер |
| `backend/services/observation_chart_renderer.py` | matplotlib графики для PDF |
