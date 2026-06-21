# HANDOFF — Текст сегментов: жизненный цикл и централизация (2026-06-19)

Chain: standalone-adaptation-ux → segment-descriptions
Продолжать на ЧИСТОМ контексте (эта сессия длинная).

## ЦЕЛЬ
Подробный текст сегментов (с «реакцией на вброс») должен попадать в HTML-отчёт
И в PDF во ВСЕХ главах (Наблюдение / Адаптация / Заказчик), а не только в живом
«Текстовом отчёте» на странице /segment-analysis.

## ЧТО УЖЕ СДЕЛАНО (СОХРАНЕНО на диск, проверено, НЕ закоммичено)
1. `backend/services/adaptation_report_service.py` (~5077): `_format_segment_block`
   теперь читает описания и из `snap.interpretation.descriptions` (формат v2),
   а не только из `snap.descriptions` (v1). Проверено на блоке 544: было 0 строк,
   стало 11. PDF-плита и LaTeX (descriptions_lines, секции ~704 и ~2230) уже умели
   рендерить — данные просто не доезжали.
2. `backend/templates/segment_result.html` (saveAnalysisAsBlock): передаёт
   `generateDescriptions` (богатый текст). Покрывает ТОЛЬКО standalone-страницу.
3. `backend/static/js/segment_blocks_widget.js` (_buildAnalysisSnapshot):
   использует generateDescriptions с fallback на заглушки.

Коммит (когда будет минута):
```bash
cd ~/Documents/PythonFiles/SurgIl_Dashboard
rm -f .git/index.lock .git/HEAD.lock .git/objects/maintenance.lock
git add backend/services/adaptation_report_service.py backend/templates/segment_result.html backend/static/js/segment_blocks_widget.js
git commit -m "feat(segment): подробные описания (реакция на вброс) в снимок/PDF (standalone path)"
```

## КОРЕНЬ ПРОБЛЕМЫ (диагностика жизненного цикла)
Описания сегментов генерируются в НЕСКОЛЬКИХ местах НЕсогласованно:
- Живой богатый текст (период + вбросы + реакция baseline→пик/лифт/устойчивость +
  тренд + статистика + сравнение) есть ТОЛЬКО в `segment_result.html`
  (generateTextDescriptionPlain / analyzeInjectionReactions / getSegmentInjections /
  getTrendInfo / generateInjectionAnalysisPlain).
- Глава НАБЛЮДЕНИЕ: виджет `observation_segment_widget.js` кладёт
  `interpretation.descriptions = data.descriptions` (с СЕРВЕРА).
- Standalone: `segment_blocks_widget.js` (без моего fix давал заглушки
  «Сегмент N: type, среднее X»).
- Адаптация (sub-section): `compute_segment_block` (сервер) → segData.descriptions.
- Итог: в реальных снимках (блоки 544, 547) `interpretation.descriptions` —
  КОРОТКИЕ ЗАГЛУШКИ, а не богатый текст.

Дополнительно НАЙДЕНЫ рассинхроны (вторичные баги UI, НЕ блокируют финальный отчёт):
- В `adaptation_wizard.html` есть СВОЙ реестр частей для `segment_analysis`
  (~2873): ключи `chart/table/metrics/description` — БЕЗ ключа `descriptions`,
  не совпадает с каноном в `chapter_render.js` (q_segment_chart/segments_table/
  descriptions/cp_descriptions). → галочки блока «не работают» (no-op для канон-рендера).
- Превью-карточка блока в `adaptation_wizard.html` (~3446) проверяет
  `snap.segments` (старый формат), а v2 хранит `segments_extended` →
  «Снимок данных пуст — пересохраните блок». Косметика карточки редактора;
  финальный отчёт рендерит `chapter_render.js` (канон) и описания бы показал.

## РЕКОМЕНДУЕМЫЙ ФИКС (централизация, server-side, без хрупкого UI)
Единый Python-генератор богатых описаний + вызов в READ-точках (покрывает старые и
новые блоки, без кеша браузера, без правок виджетов):

1. Новый helper, напр. `backend/services/segment_descriptions.py`:
   `build_rich_descriptions(snapshot) -> list[str]`.
   Вход из снимка: `segments_extended` (start_idx,end_idx,num,type,mean_value,
   std_value,min_value,max_value,slope,days), `chart_data.q_total` + `chart_data.dates`,
   `injections_table.events` ([{date,reagent,amount_kg,segment_num}]) и `by_segment`.
   Портировать 1-в-1 логику из segment_result.html (строки ~1934-2310, ~2529-2760):
   getSegmentInjections, analyzeInjectionReactions (baseline=среднее 3 точек до вброса,
   пик в окне 8 точек после, lift% , sustained), getTrendInfo (тип+интенсивность по
   slope*days/mean), generateInjectionAnalysisPlain, generateTextDescriptionPlain.
   ВАЛИДАЦИЯ: сравнить вывод Python с выводом JS на одном блоке (запустить JS-функции
   в браузере на /segment-analysis и сверить пословно).

2. Вызвать helper в READ-точках для kind='segment_analysis', если
   `interpretation.descriptions` пустые/заглушки (детект заглушки: строка матчит
   r'^Сегмент \d+: \w+, среднее'):
   - GET `/api/observation/blocks` (observation.py) — для HTML-превью Наблюдения.
   - GET `/api/customer-daily/blocks` — для HTML-превью Заказчика/Адаптации.
   - `adaptation_report_service._format_segment_block` — для PDF (уже читает
     interpretation.descriptions; добавить enrich перед _build_descriptions_lines).
   (Альтернатива: enrich на SAVE в POST/PUT обоих endpoint'ов + один backfill-скрипт
   по существующим блокам. Тогда read-точки не трогаем.)

3. (Опционально, UI) Починить вторичные баги: реестр частей segment_analysis в
   adaptation_wizard.html привести к канону (добавить descriptions/cp_descriptions),
   превью-карточку — проверять segments_extended. customer_daily.html — PROTECTED.

## КАК ПРОВЕРЯТЬ (localhost:8000)
- Standalone: /segment-analysis?well_id=17&date_from=2026-02-02&date_to=2026-02-17&series=flow_rate&sensitivity=5
- БД блоков: PostgreSQL (DATABASE_URL в .env). Таблица customer_report_block,
  data_snapshot JSONB. Примеры v2: блоки 544 (adaptation), 547 (observation).
- Проверять и HTML-превью главы, и сгенерированный PDF.

## ИНВАРИАНТЫ
- Не трогать customer_daily.html (PROTECTED).
- Сразу коммитить рабочую точку; диагностировать по реальному снимку, не по «как должно быть».
- Описания — снимок фиксируется при сохранении; для старых блоков нужен backfill или пересохранение.


## РЕАЛИЗОВАНО (2026-06-19, эта сессия) — server-side read-time
Выбран READ-TIME подход (без записи в БД, без save-hook, без бэкфилла,
самовосстановление для старых и новых блоков, не зависит от кеша браузера).

Файлы:
- НОВЫЙ `backend/services/segment_descriptions.py` — порт генератора из
  segment_result.html: build_rich_descriptions(snapshot),
  enrich_snapshot_descriptions(snap) [in place, только если заглушки],
  enrich_block_descriptions(block), is_stub_descriptions().
- `backend/routers/customer_daily.py` api_list_blocks — enrich каждого блока.
- `backend/routers/observation.py` list_blocks — enrich каждого item.
- `backend/services/adaptation_report_service.py` _format_segment_block —
  enrich snap + чтение descriptions из interpretation.descriptions (v2).

Проверено live:
- GET /api/observation/blocks?well_id=17 → блок 547: 12 описаний, есть
  «Реакция на вброс … (+71.0% от baseline)».
- _format_segment_block на 547/545/540 → descriptions_lines богатые (11–13),
  с реакцией. py_compile всех файлов OK. Приложение (--reload) поднялось.

Валидация порта: «+71.0% от baseline» совпало с независимым живым JS-прогоном
(та же скважина) — подтверждает логику реакции; формулировки совпадают со
скринами пользователя. Полный авто-diff JS↔Python не делал (ограничение
переноса больших массивов из браузера) — при желании добить отдельно.

ОСТАЛОСЬ (вторичное, НЕ блокирует текст в отчёте/PDF):
- adaptation_wizard.html: реестр частей segment_analysis (~2873) и превью-карточка
  (~3446, проверка snap.segments вместо segments_extended) — рассинхрон с каноном.
  Галочки блока no-op + карточка показывает «Снимок данных пуст». Косметика
  редактора; финальный HTML/PDF уже корректны.


## СТАТУС: ГОТОВО ✅ (подтверждено пользователем «все работает», 2026-06-19)
Подробный текст сегментов (с реакцией на вброс) выводится в HTML-отчёт и PDF
во всех главах через серверный read-time генератор. Граф знаний (graphify-out/)
пересобран командой `graphify update` после правок.

Файлы к коммиту (итог):
- backend/services/segment_descriptions.py (новый)
- backend/services/adaptation_report_service.py
- backend/routers/customer_daily.py
- backend/routers/observation.py
- backend/templates/segment_result.html
- backend/static/js/segment_blocks_widget.js


## ИЗМЕНЕНИЕ ЛОГИКИ ТЕКСТА (2026-06-19, v2) — фактическая модель
По решению: сегментный анализ = ОПИСАНИЕ работы скважины (факты), без оценки
«реакции на вброс» (эффективность реагента — отдельный блок, позже).

`backend/services/segment_descriptions.py` переписан. Состав описания сегмента:
точка перелома (начало); вброс(ы) ДО точки перелома с лагом в окне
PRE_CP_LOOKBACK_H=24 ч; «сдвиг на переломе» (рост/спад, нейтральный факт);
длительность; тренд (тип + интенсивность); число вбросов внутри сегмента;
метрики (Q, σ, диапазон, P_шл, P_уст, ΔP, простой, раб%); сравнение с
предыдущим (среднее %, ΔP, раб%, смена характера).
Параметр окна: PRE_CP_LOOKBACK_H в начале модуля.
Старые функции реакции (_analyze_injection_reactions и т.п.) удалены.
Read-time enrich и is_stub обновлены (маркер нового формата — «Длительность:»).
Проверено: GET /api/observation/blocks и _format_segment_block(547/545/540) →
новый формат, без «Реакция», % в LaTeX ок.

NB: граф (graphify-out/) после этой правки снова устарел — пересобрать
`graphify update .` когда логика текста стабилизируется.
