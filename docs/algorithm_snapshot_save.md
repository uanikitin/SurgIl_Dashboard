# Алгоритм сохранения блоков Observation

> Документация: порядок создания и сохранения snapshot, какие данные попадают

## 1. ПОТОК СОЗДАНИЯ БЛОКА

```
┌─────────────────────────────────────────────────────────────────┐
│                      UI (observation_ui.js)                     │
├─────────────────────────────────────────────────────────────────┤
│  1. Пользователь выбирает период на timeline                    │
│  2. Нажимает "Предпросмотр"                                     │
│  3. Вызывается POST /api/observation/preview/segment            │
│     с include_raw_chart: true                                   │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│                   Backend (observation.py)                       │
├─────────────────────────────────────────────────────────────────┤
│  4. preview_segment() вызывает compute_segment_preview()        │
│  5. Возвращает {ok: true, snapshot: {...}}                      │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│                      UI (observation_ui.js)                     │
├─────────────────────────────────────────────────────────────────┤
│  6. state.currentPreview = resp.snapshot                        │
│  7. Показывает preview в правой панели                          │
│  8. Пользователь нажимает "Сохранить блок"                      │
│  9. POST /api/observation/blocks                                │
│     { well_id, kind, title, params, data_snapshot: snapshot }   │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│                    Backend (observation.py)                      │
├─────────────────────────────────────────────────────────────────┤
│  10. create_block() валидирует snapshot                         │
│  11. INSERT INTO customer_report_block                          │
│      (well_id, kind, title, params, data_snapshot, ...)         │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. ЧТО ПОПАДАЕТ В data_snapshot

### observation_baseline (obs_baseline_v1)

```javascript
{
  "_v": "obs_baseline_v1",
  "schema_version": "1.0",
  "computed_at": "2026-02-18T14:30:00Z",
  "block_status": "ok",
  "period": {"from": "2026-02-02", "to": "2026-02-18"},

  // ★ RAW LAYER — данные для графиков
  "raw": {
    "chart_payload": {
      "dates": ["2026-02-02", "2026-02-03", ...],    // ← КЛЮЧ: dates
      "p_tube": [45.2, 44.8, ...],
      "p_line": [42.1, 41.9, ...],
      "dp": [3.1, 2.9, ...],
      "q": [125.4, 118.7, ...],
      "shutdown_hours": [0.5, 0.0, ...]
    }
  },

  "metrics": {...},
  "quality": {...},
  "flags": {...}
}
```

### observation_segment (obs_segment_v1)

```javascript
{
  "_v": "obs_segment_v1",
  "schema_version": "1.0",
  "computed_at": "2026-02-18T14:30:00Z",
  "block_status": "ok",
  "period": {"from": "2026-02-02", "to": "2026-02-18"},

  // ★ RAW LAYER — данные для графиков
  "raw": {
    "chart_payload": {
      "dates": ["2026-02-02T00:00:00", ...],        // ← КЛЮЧ: dates (унифицирован с baseline)
      "q": [125.4, 118.7, ...],
      "p_tube": [45.2, 44.8, ...],
      "p_line": [42.1, 41.9, ...],
      "dp": [3.1, 2.9, ...],
      "shutdown_min": [30, 0, ...]
    }
  },

  "quality": {...},
  "flags": {...},
  "thresholds_used": {...},
  "segments": [...],
  "changepoints": [...],
  "shutdown_clusters": [...],
  "diagnostics": [...]
}
```

---

## 3. ✅ ИСПРАВЛЕНО: УНИФИКАЦИЯ КЛЮЧЕЙ

### Была проблема

| Источник | Ключ (было) | Ключ (стало) |
|----------|-------------|--------------|
| baseline_service | `dates` | `dates` |
| segment_service | `timestamps` | `dates` ✅ |
| chapter_render.js | ожидает `dates` | `dates` |

### Примененное исправление

**observation_segment_service.py** — изменён ключ с `timestamps` на `dates`:

```python
return {
    "dates":        timestamps,  # ✅ Унифицировано с baseline
    "q":            _col_to_list("q"),
    ...
}
```

**observation_chapter_renderer.py** и **segment_chart_renderer.py** — добавлена обратная совместимость со старыми блоками:

```python
# Поддержка обоих ключей: dates (новый) и timestamps (старые блоки)
time_values = chart_payload.get("dates") or chart_payload.get("timestamps")
```

### Результат

- Новые блоки сохраняются с ключом `dates`
- Старые блоки с `timestamps` продолжают работать
- Графики корректно рендерятся в HTML и PDF

---

## 5. ПОТОК РЕНДЕРА (после исправления)

```
┌─────────────────────────────────────────────────────────────────┐
│                  chapter_render.js                               │
├─────────────────────────────────────────────────────────────────┤
│  1. _renderObservationBlockCharts(block)                        │
│  2. kind === 'observation_segment' && on('chart_segments')      │
│  3. const rawChart = snap.raw?.chart_payload || {}              │
│  4. const dates = rawChart.dates || []     ← ЕСТЬ ДАННЫЕ ✅     │
│  5. if (dates.length && rawChart.q) {      ← TRUE               │
│  6.   ... рендер графика ВЫПОЛНЯЕТСЯ ✅                         │
│  7. }                                                            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. КОНТРОЛЬНЫЙ СПИСОК ДЛЯ ОТЛАДКИ

Чтобы проверить почему график не рендерится:

1. **Проверить snapshot в БД:**
```sql
SELECT id, kind, data_snapshot->'raw'->'chart_payload'
FROM customer_report_block
WHERE id = <block_id>;
```

2. **Проверить ключ:**
   - Если есть `timestamps` но нет `dates` → проблема в сервисе
   - Если `chart_payload` пуст → `include_raw_chart=false` при preview

3. **Проверить console.log в браузере:**
```javascript
// В chapter_render.js добавить:
console.log('rawChart:', rawChart);
console.log('dates:', dates);
```

---

## 7. СВЯЗАННЫЕ ФАЙЛЫ

| Файл | Роль |
|------|------|
| `backend/static/js/observation_ui.js` | UI создания блока |
| `backend/routers/observation.py` | API endpoints |
| `backend/services/observation_segment_service.py` | Создание snapshot segment |
| `backend/services/observation_baseline_service.py` | Создание snapshot baseline |
| `backend/static/js/chapter_render.js` | Рендер HTML графиков |
| `backend/services/observation_chapter_renderer.py` | Рендер LaTeX/PDF |
