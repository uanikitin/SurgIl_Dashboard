# Сравнение блоков главы «Наблюдение»

> Обзор всех типов блоков observation и их различий

## Типы блоков

| Код | Kind | Название | Сервис | Snapshot |
|-----|------|----------|--------|----------|
| B2 | `observation_baseline` | Анализ (датчики) | `observation_baseline_service.py` | `obs_baseline_v1` |
| B3 | `observation_period` | Анализ периода | `observation_period_service.py` | `obs_period_v1` |
| B4 | `observation_segment` | Сегментный анализ | `observation_segment_service.py` | `obs_segment_v1` |

---

## Общая архитектура

```
┌─────────────────────────────────────────────────────────────────┐
│                    ОБЩИЙ ИСТОЧНИК ДАННЫХ                        │
├─────────────────────────────────────────────────────────────────┤
│  LoRa-датчики → SQLite → pressure_pipeline → pressure_raw (PG) │
│                              ↓                                  │
│            flow_rate/full_pipeline.py (compute_full_flow)       │
│                              ↓                                  │
│            observation_data_service.py (load_observation_data)  │
│                              ↓                                  │
│               ObservationDataResult (our_df + quality)          │
└─────────────────────────────────────────────────────────────────┘
                               ↓
         ┌─────────────────────┼─────────────────────┐
         ↓                     ↓                     ↓
   ┌───────────┐         ┌───────────┐         ┌───────────┐
   │    B2     │         │    B3     │         │    B4     │
   │ Baseline  │         │  Period   │         │ Segment   │
   │           │         │ + Overlay │         │ + CPs     │
   └───────────┘         └───────────┘         └───────────┘
```

---

## Сравнительная таблица

| Аспект | B2 Baseline | B3 Period | B4 Segment |
|--------|-------------|-----------|------------|
| **Цель** | Статистики периода | Сравнение с УзКорГаз | Детекция изменений тренда |
| **Сегменты** | ❌ | ❌ | ✅ N+1 сегментов |
| **Changepoints** | ❌ | ❌ | ✅ |
| **Overlay УзКорГаз** | ❌ | ✅ | ❌ |
| **Comparisons layer** | ❌ | ✅ | ❌ |
| **Metrics layer** | ✅ (p_tube, q, dp...) | ✅ (diff_pct, RMSE...) | ❌ (per-segment) |
| **Diagnostics** | ❌ | ✅ (deviation verdict) | ✅ (per-segment/cp) |
| **Доп. обработка** | ❌ | ❌ | ✅ smoothing + exclusions |
| **Настройки пользователя** | ❌ | ❌ | ✅ sensitivity presets |

---

## Структура snapshot по типам

### B2: obs_baseline_v1

```python
{
    "_v": "obs_baseline_v1",
    "block_status": "ok",
    "period": {...},
    "raw": {"chart_payload": {...}},      # Layer 1
    "metrics": {                           # Layer 2 ★
        "p_tube": {"mean", "median", "slope", "direction"},
        "q": {...},
        "downtime": {...},
        "purge_events_count": 5
    },
    "quality": {...},                      # Layer 3
    "flags": {...}                         # Layer 6
}
```

### B3: obs_period_v1

```python
{
    "_v": "obs_period_v1",
    "block_status": "ok",
    "period": {...},
    "raw": {"chart_payload": {...}},
    "metrics": {...},
    "quality": {...},
    "comparisons": {                       # Layer 4 ★
        "p_tube": {"our_mean", "customer_mean", "diff_abs", "diff_pct"},
        "q": {...},
        "correlation_daily": 0.85,
        "rmse_q": 12.5
    },
    "diagnostics": [                       # Layer 5 ★
        {"target": "q", "verdict": "significant_deviation", ...}
    ],
    "flags": {...}
}
```

### B4: obs_segment_v1

```python
{
    "_v": "obs_segment_v1",
    "block_status": "ok",
    "period": {...},
    "raw": {"chart_payload": {...}},
    "quality": {...},
    "flags": {...},
    "thresholds_used": {                   # ★ Настройки детекции
        "sensitivity": "medium",
        "min_change_pct": 10.0,
        ...
    },
    "segments": [                          # ★ N+1 сегментов
        {"num": 1, "direction": "falling", "slope_q_per_day": -2.5, ...}
    ],
    "changepoints": [                      # ★ Точки перелома
        {"idx": 9, "date": "2026-02-10", "magnitude_pct": -15.3, ...}
    ],
    "shutdown_clusters": [...],            # ★ Кластеры простоев
    "diagnostics": [                       # ★ Per-segment + overall
        {"target": "segment", "verdict": "falling", ...},
        {"target": "overall", "verdict": "falling", ...}
    ]
}
```

---

## Когда использовать какой блок

| Задача | Рекомендуемый блок |
|--------|-------------------|
| Общая статистика давления/дебита за период | B2 Baseline |
| Сравнение наших данных с данными заказчика | B3 Period |
| Поиск точек изменения тренда | B4 Segment |
| Анализ влияния продувок/простоев | B4 Segment |
| Валидация данных LoRa vs УзКорГаз | B3 Period |
| Базовый отчёт для заказчика | B2 + B3 вместе |

---

## Файлы сервисов

```
backend/services/
├── observation_data_service.py      # B1: общая загрузка данных
├── observation_baseline_service.py  # B2: baseline statistics
├── observation_period_service.py    # B3: period + overlay
├── observation_segment_service.py   # B4: segmentation + changepoints
├── observation_chapter_renderer.py  # HTML/LaTeX рендер всех типов
└── observation_chart_renderer.py    # matplotlib графики для PDF
```

---

## Связи между сервисами

```
observation_data_service.py (B1)
    │
    ├──→ observation_baseline_service.py (B2)
    │         └── compute_baseline_preview()
    │
    ├──→ observation_period_service.py (B3)
    │         └── compute_period_preview()
    │
    └──→ observation_segment_service.py (B4)
              └── compute_segment_preview()
```

---

## Quality Layer (общий для всех)

Все три типа блоков используют одинаковую структуру quality:

```python
"quality": {
    "status": "ok" | "sparse" | "gap" | "suspicious" | "no_data",
    "flags": ["low_coverage", "significant_gap", "outlier_detected", ...],
    "metrics": {
        "coverage_pct": 98.5,
        "gap_count": 2,
        "max_gap_hours": 4.5,
        "suspicious_spikes_count": 0,
        "false_zero_pct": 0.3,
        "days_with_data": 17,
        "days_requested": 17
    }
}
```

---

## Документация по алгоритмам

- [B2 Baseline](algorithm_b2_baseline.md) — подробный алгоритм
- [B4 Segment](algorithm_b4_segment.md) — подробный алгоритм + сравнение с B2
