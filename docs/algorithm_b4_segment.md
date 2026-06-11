# Алгоритм формирования блока B4 «Сегментный анализ»

> Документация: путь данных и сравнение с B2 (Baseline)

## Сравнение B2 vs B4

| Аспект | B2 Baseline | B4 Segment |
|--------|-------------|------------|
| Snapshot версия | `obs_baseline_v1` | `obs_segment_v1` |
| Сервис | `observation_baseline_service.py` | `observation_segment_service.py` |
| Цель | Статистики за период | Детекция изменений тренда |
| Сегменты | Нет | Да (N changepoints → N+1 сегментов) |
| Changepoints | Нет | Да (точки перелома) |
| Diagnostics | Нет | Да (verdict per segment/cp) |
| Smoothing | Нет (в B1) | Да (дополнительное) |
| Исключение простоев | Нет | Да (опционально) |
| Маска продувок | Нет | Да (±window_hours/2) |

---

## 1. ОБЩИЙ ИСТОЧНИК ДАННЫХ

**Оба сервиса используют одну точку входа:**

```python
# observation_data_service.py:106
load_observation_data(db, well_id, d_from, d_to, aggregation="daily")
```

```
pressure_raw (PostgreSQL)
    ↓
get_pressure_data() → DataFrame[p_tube, p_line]
    ↓
compute_full_flow() → очистка + расчёт Q + детект простоев/продувок
    ↓
load_observation_data() → агрегация (daily/hourly/...) + quality
    ↓
ObservationDataResult(our_df, our_raw_minute_df, our_meta, data_quality)
```

---

## 2. АЛГОРИТМ B4 SEGMENT

### Шаг 1: Загрузка данных (как B2)

```python
# observation_segment_service.py:168
obs = load_observation_data(
    db=db,
    well_id=well_id,
    d_from=d_from,
    d_to=d_to,
    aggregation=aggregation,  # daily/12h/6h/hourly
    smooth_minute=True,
    include_customer_overlay=False,
)
```

### Шаг 2: Дополнительное сглаживание (отличие от B2)

```python
# Скользящее среднее по Q
work_df = _apply_smoothing(agg_df, smoothing_window)
# smoothing_window: 10 (low) / 7 (medium) / 3 (high)
```

### Шаг 3: Исключение периодов простоя (отличие от B2)

```python
# Ставит q=NaN на строках из shutdown_clusters
work_df = _exclude_shutdown_periods(work_df, shutdown_clusters, ignore_flag)
```

**Логика shutdown_clusters:**
- `shutdown_min >= 300 мин/сут` (5 часов) → "проблемный" день
- ≥2 дней подряд → кластер простоя
- Соседние кластеры с зазором ≤2 дня объединяются

### Шаг 4: Маска продувок (отличие от B2)

```python
# Ставит q=NaN в окне ±window_hours/2 вокруг продувки
work_df = _apply_purge_mask(work_df, purge_events, window_hours)
# window_hours: по умолчанию 24 часа
```

### Шаг 5: Детекция Changepoints (ключевое отличие от B2)

```python
# observation_segment_service.py:530
cps = _detect_changepoints(work_df, min_change_pct, min_segment_days, aggregation)
```

**Алгоритм детекции:**

```
1. Интерполяция NaN для алгоритма
2. threshold = median(Q) × min_change_pct / 100
   └── min_change_pct: 20% (low) / 10% (medium) / 5% (high)

3. Базовая детекция по скачку среднего:
   for i in range(min_seg_pts, n - min_seg_pts):
       left  = Q[:i]
       right = Q[i:]
       mean_diff = |mean(right) - mean(left)|
       jump = |Q[i] - Q[i-1]|
       cost[i] = mean_diff + jump × 0.5

   if cost[i] >= threshold AND cost[i] == max(cost[окно]):
       → changepoint

4. Детекция коротких провалов:
   if drop > 25% за 1 день:
       → changepoint
       if recovery > 25% в течение 7 дней:
           → ещё changepoint

5. Слияние близких точек (< 5 дней):
   оставляем с большим cost

6. Финальный фильтр:
   |change_pct| >= min_change_pct / 3
```

### Шаг 6: Построение сегментов

```python
# N changepoints → N+1 сегментов
segments = _build_segments_from_changepoints(agg_df, cps, aggregation)
segments = _compute_segment_trends(segments, agg_df, aggregation)
```

**Для каждого сегмента:**
```python
segment = {
    "num": 1,
    "start_date": "2026-02-02",
    "end_date": "2026-02-10",
    "duration_days": 9,
    "mean_q": 125.4,
    "mean_dp": 3.2,
    "mean_p_tube": 45.1,
    "mean_p_line": 41.9,
    "slope_q_per_day": -2.5,      # нормализован per-day
    "direction": "falling"        # rising/falling/stable
}
```

**Логика direction:**
```python
_SLOPE_STABLE_THRESHOLD_Q = 50.0  # тыс.м³/сут / день

if |slope| < 50:
    direction = "stable"
elif slope > 0:
    direction = "rising"
else:
    direction = "falling"
```

### Шаг 7: Обогащение Changepoints

```python
changepoint = {
    "idx": 42,                     # индекс в DataFrame
    "date": "2026-02-10",
    "magnitude_pct": -15.3,        # изменение Q в %
    "confidence": "medium"         # high/medium/low
}
```

**Логика confidence:**
```python
if |pct| >= 2 × threshold:    confidence = "high"
elif |pct| >= threshold:      confidence = "medium"
else:                         confidence = "low"
```

### Шаг 8: Diagnostics (отличие от B2)

```python
diagnostics = [
    # Per-segment trend
    {
        "target": "segment",
        "context": "trend_1",
        "verdict": "falling",
        "magnitude": {"slope_q_per_day": -2.5},
        "requires_log_check": True
    },
    # Per-changepoint
    {
        "target": "changepoint",
        "context": "cp_42",
        "verdict": "detected",
        "magnitude": {"pct": -15.3},
        "flags": {"shutdown_related": False, "purge_related": True},
        "requires_log_check": True
    },
    # Overall trend
    {
        "target": "overall",
        "context": "combined",
        "verdict": "falling",  # weighted by duration_days
        "requires_log_check": True
    }
]
```

---

## 3. СТРУКТУРА SNAPSHOT B4

```python
snapshot = {
    "_v": "obs_segment_v1",
    "schema_version": "1.0",
    "computed_at": "2026-02-18T14:30:00Z",
    "block_status": "ok",
    "period": {"from": "2026-02-02", "to": "2026-02-18"},

    # Layer 1: raw (если include_raw_chart=True)
    "raw": {
        "chart_payload": {
            "dates": ["2026-02-02T00:00:00", ...],  # унифицировано с baseline
            "q": [125.4, 118.7, ...],
            "p_tube": [45.2, 44.8, ...],
            "p_line": [42.1, 41.9, ...],
            "dp": [3.1, 2.9, ...],
            "shutdown_min": [30, 0, ...]
        }
    },

    # Layer: quality (как в B2)
    "quality": {
        "status": "ok",
        "flags": [],
        "metrics": {...}
    },

    # Layer: flags (как в B2)
    "flags": {
        "low_coverage": False,
        "significant_gap": False,
        "outlier_detected": False
    },

    # ╔════════════════════════════════════════════╗
    # ║  УНИКАЛЬНЫЕ СЛОИ B4 (нет в B2)             ║
    # ╚════════════════════════════════════════════╝

    # Параметры детекции
    "thresholds_used": {
        "aggregation": "daily",
        "sensitivity": "medium",
        "min_segment_days": 7,
        "min_change_pct": 10.0,
        "smoothing_window": 7,
        "ignore_shutdown_days": True,
        "ignore_purge_window_hours": 24,
        "has_user_overrides": False
    },

    # Сегменты
    "segments": [
        {
            "num": 1,
            "start_date": "2026-02-02",
            "end_date": "2026-02-10",
            "duration_days": 9,
            "mean_q": 125.4,
            "slope_q_per_day": -2.5,
            "direction": "falling"
        },
        {...}
    ],

    # Точки перелома
    "changepoints": [
        {"idx": 9, "date": "2026-02-10", "magnitude_pct": -15.3, "confidence": "medium"}
    ],

    # Кластеры простоев
    "shutdown_clusters": [
        {"start_date": "2026-02-05", "end_date": "2026-02-06", "total_minutes": 1800}
    ],

    # Диагностика
    "diagnostics": [...]
}
```

---

## 4. SENSITIVITY PRESETS

| Preset | min_change_pct | min_segment_days | smoothing_window |
|--------|---------------|------------------|------------------|
| low | 20% | 10 дней | 10 |
| medium | 10% | 7 дней | 7 |
| high | 5% | 3 дня | 3 |
| custom | user-defined | user-defined | user-defined |

---

## 5. СХЕМА ПОТОКА ДАННЫХ B4

```
┌─────────────────┐
│ pressure_raw    │  ← PostgreSQL (общий источник)
└────────┬────────┘
         ↓
┌─────────────────┐
│ compute_full_   │  ← Пайплайн (как B2)
│ flow()          │
└────────┬────────┘
         ↓
┌─────────────────┐
│ load_observation│  ← Агрегация + quality (как B2)
│ _data()         │
└────────┬────────┘
         ↓
┌─────────────────┐
│ _apply_         │  ← ★ ДОПОЛНИТЕЛЬНОЕ сглаживание
│ smoothing()     │
└────────┬────────┘
         ↓
┌─────────────────┐
│ _exclude_       │  ← ★ Исключение простоев
│ shutdown_       │
│ periods()       │
└────────┬────────┘
         ↓
┌─────────────────┐
│ _apply_         │  ← ★ Маска продувок
│ purge_mask()    │
└────────┬────────┘
         ↓
┌─────────────────┐
│ _detect_        │  ← ★ КЛЮЧЕВОЕ: детекция changepoints
│ changepoints()  │
└────────┬────────┘
         ↓
┌─────────────────┐
│ _build_segments │  ← ★ N changepoints → N+1 сегментов
│ _from_          │
│ changepoints()  │
└────────┬────────┘
         ↓
┌─────────────────┐
│ _compute_       │  ← ★ Тренды per-segment
│ segment_trends()│
└────────┬────────┘
         ↓
┌─────────────────┐
│ _build_         │  ← ★ Diagnostics (verdict per item)
│ diagnostics()   │
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
│+ сегм.│ │render │
└───────┘ └───────┘
```

---

## 6. СВЯЗАННЫЕ ФАЙЛЫ

| Файл | Назначение |
|------|------------|
| `backend/services/observation_segment_service.py` | Главный сервис B4 |
| `backend/services/observation_data_service.py` | Загрузка данных (общий B1) |
| `backend/services/observation_baseline_service.py` | Сервис B2 (сравнение) |
| `backend/services/flow_rate/full_pipeline.py` | Расчёт дебита |
| `backend/services/observation_chapter_renderer.py` | HTML/LaTeX рендер |
| `backend/templates/customer_daily.html` | UI отображение |

---

## 7. КЛЮЧЕВЫЕ ОТЛИЧИЯ B4 ОТ B2

1. **Сегментация**: B2 — один период, B4 — N+1 сегментов
2. **Changepoints**: B4 детектирует точки перелома тренда
3. **Дополнительная обработка**: B4 применяет smoothing + исключение простоев + маска продувок
4. **Diagnostics**: B4 имеет verdict per segment/changepoint/overall
5. **Настройки**: B4 имеет sensitivity presets и thresholds_used
6. **Flags в changepoints**: B4 маркирует shutdown_related/purge_related
