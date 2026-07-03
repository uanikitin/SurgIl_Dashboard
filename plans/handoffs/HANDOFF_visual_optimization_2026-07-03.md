# Оптимизация страницы /visual — приоритетная загрузка скважин

**Date:** 2026-07-03
**Status:** PHASE 1 COMPLETE
**Commit:** `4420a52`
**Epic:** Dashboard Performance
**Chain:** `visual-perf-001` seq `1`

---

## Reference Documents

- `CLAUDE.md` — project conventions
- `plans/handoffs/HANDOFF_standalone-de8b5890_step7-redesign_2026-07-02.md` — предыдущая работа

## The Goal

Ускорить загрузку страницы `/visual` с 4-5 секунд до ~1-1.5 секунд путём:
1. Удаления `sync_wells_from_events()` из каждого запроса страницы
2. Загрузки только приоритетных скважин (Наблюдение/Оптимизация/Адаптация — 9 шт.)
3. Lazy-load остальных скважин по запросу

## Where We Are

### Предыдущая сессия — исправления Step 7 Wizard

**Коммиты:**
| Hash | Message |
|------|---------|
| `f8197a0` | fix(wizard): Step 7 — три критических фикса (скорость preview, disabled cards, period reports) |
| `1468125` | fix(wizard): Step 7 — загрузка реальных блоков из БД для счётчиков глав |

**Изменения в `backend/templates/adaptation_wizard.html`:**
- Добавлена функция `wz7LoadChapterBlockCounts()` — загружает блоки из БД и обновляет счётчики карточек глав
- Фикс: карточки показывали 0/0 даже когда блоки существовали в БД

### Текущая проблема — медленная загрузка /visual

**Профилирование:**
```
sync_wells_from_events()     ~2000ms   ← ГЛАВНЫЙ BOTTLENECK
Статусы скважин             ~50ms
События за 30 дней          ~180ms
Текущие давления            ~60ms
Отрисовка 37 тайлов         ~800ms
─────────────────────────────────────
ИТОГО                       ~4-5 секунд
```

**Статистика скважин по статусам:**
- Наблюдение: 3 скважины
- Оптимизация: 5 скважин
- Адаптация: 1 скважина
- **Итого приоритетных: 9 скважин** (из 37)

## What We Tried

1. Профилирование отдельных SQL-запросов — все быстрые (<200ms)
2. Анализ `sync_wells_from_events()` — выполняется на КАЖДЫЙ запрос страницы, занимает ~2 сек
3. TestClient — подтвердил что страница рендерится (200, 211KB), но медленно

## Plan

### Phase 1 — Quick Wins (текущая задача)

1. **Удалить `sync_wells_from_events()` из `/visual`**
   - Перенести в `/api/admin/sync-wells` для ручного/scheduled вызова
   - Экономия: ~2 секунды

2. **Фильтр по статусу**
   - Загружать только скважины со статусами: Наблюдение, Оптимизация, Адаптация
   - 9 скважин вместо 37 = ~4x меньше данных

**Ожидаемый результат:** 4-5 сек → 1-1.5 сек

### Phase 2 — Lazy Loading (следующий этап)

1. Кнопка «Показать все скважины» для загрузки остальных 28
2. AJAX-подгрузка без перезагрузки страницы
3. Индикатор загрузки

### Phase 3 — Архитектура (будущее)

1. SPA-подход с виртуальным скроллом
2. WebSocket для real-time обновлений
3. Предвычисленные тайлы в Redis

## Files to Modify

### Phase 1

| File | Change |
|------|--------|
| `backend/app.py` | Удалить `sync_wells_from_events()` из `visual_page()`, добавить фильтр по статусу |
| `backend/routers/admin.py` или новый endpoint | Перенести sync в отдельный endpoint |

## Evidence & Data

### Профилирование запросов (из предыдущей сессии)

```sql
-- Статусы скважин
SELECT id, name, status FROM well WHERE active = true;
-- Время: ~50ms, 37 строк

-- События за 30 дней
SELECT well_id, COUNT(*) FROM event
WHERE created_at > now() - interval '30 days'
GROUP BY well_id;
-- Время: ~180ms
```

## Quick Start for Next Session

```bash
# Reference docs
cat CLAUDE.md
cat plans/handoffs/HANDOFF_visual_optimization_2026-07-03.md

# Key files
backend/app.py                           # visual_page() function ~line 901
backend/services/well_sync_service.py    # sync_wells_from_events()

# Current state
git log --oneline -5

# Next action
# 1. Удалить вызов sync_wells_from_events() из visual_page()
# 2. Добавить фильтр status IN ('Наблюдение', 'Оптимизация', 'Адаптация')
# 3. Тест: curl -w "@curl-format.txt" http://127.0.0.1:8000/visual
```

## Risks & Blockers

- Синхронизация скважин из событий всё ещё нужна — но не на каждый запрос страницы
- Возможно понадобится scheduled job для периодической синхронизации

## Open Questions

- Как часто запускать `sync_wells_from_events()`? Раз в час? При создании события?
- Нужен ли UI для ручного запуска синхронизации?
