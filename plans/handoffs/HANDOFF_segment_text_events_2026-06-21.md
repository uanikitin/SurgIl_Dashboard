# HANDOFF — Текст сегментов + события + паритет PDF (2026-06-21)

Продолжать на чистом контексте. Эта сессия очень длинная.

## ЧТО СДЕЛАНО (на диске; нужно ЗАКОММИТИТЬ — см. ниже)

### 1. Текст описания сегментов — фактическая модель (готово, проверено)
Файл `backend/services/segment_descriptions.py` — единый серверный генератор.
Формат описания сегмента (блоками, через \n):
- `Сегмент N (Тип)`
- `Перелом: ВРЕМЯ.` (или «начало периода»)
- `Тренд: <текущий> после <род. падеж предыдущего>, <интенсивность>, ±% за период.`
- `Длительность периода: N ч.`
- `События у перелома:` вброс(ы)/события ДО (лагом, окно 24 ч, кластер ≤1 ч) и
  сразу ПОСЛЕ (≤1 ч — точным временем).
- `Изменение дебита Q: A → B (±ед, ±%), темп ±/ч.` (A,B = медиана 5 точек у границ внутри сегмента)
- `Перепад давления ΔP: A → B (±кгс/см², ±%) — значимо.` (если |Δ|≥0.3 и есть ряд dp)
- `В период осуществлено вбросов: N — «реагент» ВРЕМЯ; …`
- `Другие события в период: <суть> ВРЕМЯ; …` (продувки/оборуд./прочее)
- `Метрики: Q ср.; σ; диапазон; P_шл; P_уст; ΔP ср.; простой; раб.%`
- `Сравнение с предыдущим периодом: среднее изменение дебита Q ±%; ΔP ±; раб.; характер: X → Y.`

Убрано: «реакция на вброс»/баллы ПАВ, «смена режима», «возможна погрешность оператора»,
«5 точек у границ» (в тексте), одиночный «На переломе — спад» (давал противоречие).

### 2. События из БД (готово)
`fetch_ops_events(db, well, d_from, d_to)` тянет из таблицы `events`
(event_type IN purge/equip/other; pressure исключён; reagent идёт отдельно через
injections_table). Метки — через `daily_report_service._smart_event_label`
(гидраты→«Гидратообразование», «Прод. штуцера», «Снятие/Установка оборудования»,
иначе текст). Продувка собирается в ЦИКЛ: новый цикл с фазы `start` или разрыв >12 ч;
полная = есть start+press+stop, иначе «Продувка неполная (фазы)».
Подтягивается на ЧТЕНИИ (старые блоки не надо пересохранять). Если сессия БД не
передана (PDF) — `fetch_ops_events` открывает свою через `backend.db.SessionLocal`.

### 3. Идентичность HTML и PDF по тексту (готово)
Единый генератор используется:
- HTML-превью глав: enrich в `routers/customer_daily.py` (api_list_blocks) и
  `routers/observation.py` (list_blocks) — `enrich_block_descriptions(b, db=db)`.
- Живая страница `/segment-analysis`: кнопка «Сформировать» → POST
  `/api/customer-daily/segment-analysis/describe` (новый эндпоинт) →
  `build_rich_descriptions`. (Клиент: `segment_result.html` generateFullReport — async,
  fallback на локальный текст; перенос \n→<br>.)
- PDF: `adaptation_report_service._format_segment_block` вызывает
  `enrich_snapshot_descriptions(snap)` и читает `interpretation.descriptions`
  (фолбэк-чтение из interpretation добавлено ранее). LaTeX уже рендерит descriptions_lines.

### 4. Ряд ΔP в снимок (готово)
`routers/segment_demo.py` теперь кладёт в `chart_data.secondary` поточечные ряды
(dp/p_tube/p_line/downtime) → виджет нормализует в `chart_data.dp` → ΔP «с→до» в
новых сохранённых блоках/PDF. Старые блоки — ΔP «с→до» появится после пересохранения
(события и Q «с→до» работают и без пересохранения).

## ФАЙЛЫ К КОММИТУ
backend/services/segment_descriptions.py (новый/переписан)
backend/services/adaptation_report_service.py
backend/routers/customer_daily.py
backend/routers/observation.py
backend/routers/segment_demo.py
backend/templates/segment_result.html
backend/static/js/segment_blocks_widget.js
plans/ (хендоффы, карты)

```bash
cd ~/Documents/PythonFiles/SurgIl_Dashboard
rm -f .git/index.lock .git/HEAD.lock .git/objects/maintenance.lock
git add backend/services/segment_descriptions.py backend/services/adaptation_report_service.py \
        backend/routers/customer_daily.py backend/routers/observation.py \
        backend/routers/segment_demo.py backend/templates/segment_result.html \
        backend/static/js/segment_blocks_widget.js plans/
git commit -m "feat(segment): фактический текст сегментов + события из БД (HTML/PDF идентичны), ряд ΔP в снимок"
```

## НАСТРАИВАЕМЫЕ ПАРАМЕТРЫ (в начале segment_descriptions.py)
- PRE_CP_LOOKBACK_H = 24 (окно поиска вброса до перелома)
- CLUSTER_GAP_H = 1 (кластер вбросов «пачкой»)
- POST_CP_WINDOW_H = 1 (вброс сразу после перелома → точное время)
- порог «значимо» ΔP = 0.3 (в _describe_segment)
- продувка: цикл — новый с 'start' или разрыв >12 ч (в _purge_cycles)

## СЛЕДУЮЩИЙ ШАГ — паритет вида PDF↔HTML (НЕ начат)
См. отдельный план `plans/handoffs/HANDOFF_pdf_html_parity_2026-06-21.md`:
- метрики в PDF сделать ПЛИТКАМИ (макрос `\kmTile` уже есть в adaptation_report.tex —
  вынести в преамбулу, применить к «Рабочие параметры», «Карточки метрик» R/R★/B2/B1,
  «Сводка B2→R»);
- графики matplotlib донастроить под Plotly (цвета/сетка/маркеры);
- ОБЯЗАТЕЛЬНО проверять компиляцией PDF (preview-pdf) маленькими шагами + бэкап .tex.

## ИНВАРИАНТЫ
- customer_daily.html — PROTECTED, не трогать.
- Перед правками .tex — бэкап; компиляция PDF после каждого шага.
- Граф знаний (graphify-out/) устарел — пересобрать `graphify update .` когда устаканится.
- Диагностировать по реальному снимку/ответу, не «как должно быть».
