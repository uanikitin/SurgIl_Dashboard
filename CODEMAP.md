# CODEMAP — карта правок для Claude Code

> **Зачем этот файл.** Этот проект ломается от точечных правок, потому что у фич
> неявные инварианты и файлы, которые надо менять вместе. ПЕРЕД правкой любой
> фичи — найди её здесь и соблюди три блока: **Файлы вместе**, **Инварианты
> (нельзя нарушать)**, **Не трогать**. Если фичи здесь нет — сначала
> `find -iname '*<feature>*'` + `grep` по `templates/` и `static/`, память отстаёт от кода.
>
> Числа «↔ N» — сколько символов связывают два файла (из `graphify-out/graph.json`,
> модульный граф). Высокое N = «почти наверняка правятся вместе».

---

## 0. Глобальные правила (касаются всего)

- **Данные датчиков = как на странице скважины.** Для p_tube/p_line/ΔP/Q на
  ЛЮБОЙ странице используй `/api/pressure/chart/{well_id}` и
  `/api/flow-rate/calculate/{well_id}` — те же эндпоинты, что у `/well/{number}`.
  **Запрещено** писать свой aggregate, копирующий pipeline (`our_daily_data`,
  `_live_flow_daily` и т.п.) — каждый раз даёт баги.
- **False-zeros датчика SMOD-PT-60.** Датчик ~4% времени шлёт `0.0` вместо
  значения. ВСЕГДА `NULLIF(value, 0.0)` в SQL AVG/MIN/MAX; на чтении трактуй
  `0.0` как `None`. В шаблонах `{% if val is not none %}`, НИКОГДА `val or 0`.
- **ΔP считать построчно ДО агрегации.** Никогда не `AVG(p_tube)` и `AVG(p_line)`
  по отдельности с последующим вычитанием. Фильтр строки:
  `p_tube > p_line AND ΔP > 0.1 AND both NOT NULL`, потом агрегируй.
  На фронте: `Math.max(0, p_tube_avg - p_line_avg)`.
- **Таймзона.** Все таймстемпы приложения — Кунград (UTC+5). В БД хранится UTC.
  Наивный таймстемп с графика = Кунград → вычесть 5ч перед запросом в БД.
  Любая фича «клик по графику → backend → запрос» проверяется на TZ-сдвиг.
- **Миграции БД — только вручную.** НИКОГДА `alembic revision --autogenerate`
  (дропает таблицы). `alembic revision -m "..."` + SQL руками. Перед миграцией — бэкап.
- **Диагностические формулировки — осторожные.** В rule-based аналитике
  (сегменты, ПАВ, причины переломов) — только «возможная интерпретация / требует
  проверки по журналу работ». Запрещены категоричные causal-claims и поля
  `probable_cause` — вместо них `diagnostic_note` + `requires_log_check=true`.

---

## 0a. Слои давления и маски — signal processing (ОБЯЗАТЕЛЬНО)

**Зачем нужны маски.** Датчик не знает физического контекста — оператор знает.
Маска = ручная коррекция давления оператором, когда сырьё датчика не отражает
реальную добычу. Типовые случаи:
- **перекрыли линию**: p_line→0, на устье давление → формально большой ΔP, но
  дебит фактически **0** (оператор знает, что линия закрыта);
- **гидрат на штуцере**;
- **неисправность / пропуски данных** одного из манометров (длинные дыры);
- **забросы давления при продувках** — лучше исключить.

Маска — **авторитетный источник**: знание оператора > показание датчика.
Логикой системы маски **не ограничивать**.

**4 слоя давления:**

| Слой | Что | Кто потребляет |
|---|---|---|
| L0 raw | как прислал датчик (нули, спайки) | только аудит/диагностика |
| L1 cleaned | диапазон [0,85], нули→NaN, Hampel | промежуточный |
| **L2 corrected** | **L1 + применённые маски** | **ЕДИНЫЙ источник истины** |
| L3 smoothed | L2 + Савицкий-Голай | отображение/производные |

**Правила:**
1. Дебит, Наблюдение, адаптация, отчёт Заказчику — **всегда L2 (после масок)**.
   Наложенная маска ОБЯЗАНА отражаться в отчётах. `compute_full_flow` это уже
   делает (шаг 3, `load_active_masks(verified_only=False)` = все активные маски).
2. Raw (L0) **никогда не идёт в расчёт/отчёт** — только диагностический оверлей
   и экран редактирования масок.
3. **Дефолт каждого экрана = L2.** ⚠️ Известный рассинхрон: `/api/pressure/chart`
   имеет `apply_masks=false` по умолчанию → показывает L0/L1, расходится с
   Наблюдением/дебитом (на скв.128 разрыв до 16 атм, 94% точек). Дефолт должен
   быть L2; raw — явный тумблер-оверлей.
4. **Провенанс.** Реконструкция, меняющая операционное состояние (знак ΔP) или
   переводящая ΔP через порог 0.1, несёт смысл «реконструкция, не замер» — в
   отчёте регулятору такое значение должно быть отслеживаемо.
5. Методы масок: `median_1d/3d`, `interpolate(_noise)`, `bridge_median`,
   `seasonal_reconstruct`, `delta_reconstruct` (manual ΔP), `exclude`. Случай
   «линия перекрыта → дебит 0» = реконструкция ΔP ниже 0.1 или `exclude`.
6. Реконструкция требует чистых опорных точек вокруг окна; на длинных дырах без
   опор `seasonal_reconstruct` вырождается в интерполяцию → низкое доверие.
7. **Заполнение пропусков (L1) регулируемо.** `clean_pressure(df, max_fill_min=20)`
   ([flow_rate/cleaning.py](backend/services/flow_rate/cleaning.py)): короткий
   пропуск датчика (≤ порога, значение физически было) заполняется интерполяцией;
   дыра длиннее порога остаётся NaN — не фабрикуем. Порог проброшен через
   `compute_full_flow(max_fill_min=)` → эндпоинт `/api/flow-rate/calculate`
   (`max_fill_min` Query, default 20, 0=без лимита) → UI: страница скважины,
   панель коэффициентов дебита, поле «Заполнение пропусков, мин» (`#flow-fill-max`
   в `well.html`, проброс в `flow_rate_chart.js`). При смене дефолта/логики менять
   все 4 точки. Защищено `test_flow_regression` (golden на 5 скважинах).

См. также §0 (false-zeros, ΔP-фильтр, TZ) и §B обработки сигнала ниже по разделам.

---

## 1. Pressure pipeline (импорт → агрегация → API)

**Файлы вместе:**
- `backend/services/pressure_pipeline.py` — оркестратор
- `backend/services/pressure_import_csv.py` — импорт сырья (cp1251, `;`, `,`-десятичная)
- `backend/services/pressure_aggregate_service.py` — почасовая агрегация
- `backend/services/pressure_filter_service.py` — спайки/выбросы
- `backend/services/pressure_mask_service.py` — маски (v2)
- `backend/routers/pressure.py` — API + графики
- `scripts/run_pressure_update.py` + `scripts/schedule_config.json` — планировщик (launchd, день 07–22 = 5 мин, ночь = 30 мин)

**Инварианты:** §0 false-zeros, ΔP-фильтр, TZ. `pressure_raw` **никогда не меняется**.
Маски v2 (`both`/`zero_flow`/`delta_noise`) — миграция может быть НЕ применена на БД;
INSERT таких типов падает на v1-constraints (см. память `project_v2_migration_not_applied`).

**Не трогать:** raw-таблицы, формулу `_now_db() = utcnow() + 5h`.

---

## 2. Flow Rate Analysis (анализ дебита)

**Файлы вместе:**
- `backend/services/flow_rate/` — весь модуль: `data_access → cleaning →
  calculator → purge_detector → downtime → summary → full_pipeline`
- `backend/services/flow_rate/scenario_service.py` ↔ `routers/flow_analysis.py` (↔19)
- `backend/services/flow_rate/config.py` ↔ `purge_detector.py` (↔29) — менять вместе
- `backend/models/flow_analysis.py` — FlowScenario / FlowCorrection / FlowResult
- `backend/services/flow_rate/chart_renderer.py` + `report_service.py` +
  `backend/templates/latex/flow_analysis_report.tex` — PDF

**Инварианты:**
- `pressure_raw` НЕ модифицируется — коррекции применяются in-memory во время расчёта.
- `calculate_flow_rate` возвращает `Q=0` и для простоя, и для маскированных строк
  (визуально неразличимо). На ЛЮБОМ графике Q/cumulative: после расчёта выставить
  `Q=NaN` там, где строка не прошла полный idle-фильтр (см. `feedback_flow_rate_zero_clamp`).
- Все графики и расчёты — на данных ПОСЛЕ маски; нулей/провалов там быть не должно.

---

## 3. Отчёты: Daily / Adaptation / Customer (связанная тройка)

> Сильнейшая связка проекта: `adaptation_report_service ↔ daily_report_service` (↔48),
> `adaptation_report_service ↔ observation_chapter_renderer` (↔22). Правка одного
> отчёта почти всегда задевает соседний — проверяй все три.

**Файлы вместе:**
- `backend/services/daily_report_service.py` + `routers/daily_report.py`
- `backend/services/adaptation_report_service.py` + `routers/adaptation_report.py`
  (↔31) + `templates/latex/adaptation_report.tex` + `templates/adaptation_wizard.html`
- `backend/services/customer_daily_service.py` + `routers/customer_daily.py`
- Главы из блоков рендерятся через `observation_chapter_renderer.py` (см. §4)

**Не трогать без явного запроса:**
- ⛔ **`backend/templates/customer_daily.html`** — PROTECTED, стабильная страница
  «Заказчик». Любые изменения только по прямому указанию пользователя.

---

## 4. Observation / Chapter Framework (главы из блоков)

> Единственная подсистема с тестами (7 файлов, ~186 тестов). Соблюдай слоистость —
> именно её нарушение всё ломало раньше (правила R1–R5).

**Файлы вместе (сервис ↔ его тест — менять парой):**
- `observation_chapter_renderer.py` ↔ `tests/test_observation_chapter_renderer.py` (↔109)
- `observation_segment_service.py` ↔ `tests/test_observation_segment_service.py` (↔46)
- `observation_snapshot_service.py` ↔ `tests/test_observation_snapshot_service.py` (↔44)
- `observation_period_service.py` ↔ `tests/test_observation_period_service.py` (↔37)
- `observation_data_service.py` ↔ `tests/test_observation_data_service.py` (↔22)
- `observation_chart_renderer.py`, `observation_baseline_service.py`,
  `routers/observation.py` (+ `test_observation_router.py`)
- UI: `static/js/observation_ui.js`, `observation_segment_widget.js`, секция в
  `adaptation_wizard.html`

**Инварианты (R1–R5):**
- **R1** Слой аналитики ≠ слой рендера — без перекрёстных вызовов.
- **R2** Renderer всегда snapshot-only: НЕ ходит в БД, не зовёт сервисы.
- **R3** Новый renderer = 3 типа тестов: mutation + parity + broken-snapshot.
- **R4** Chart renderer: Agg backend + DejaVu Sans + детерминированные имена файлов.
- **R5** Preview endpoint: `skip_figures=True` по умолчанию (редактор);
  `False` только для PDF.
- При изменении логики Score/метрик — **править backend И UI одновременно** (см. §5 паттерн).

**Правило процесса:** меняешь сервис → запусти его тест
(`pytest backend/tests/test_observation_*.py`). Тесты ДОЛЖНЫ проходить до и после.

---

## 5. Reagent Effectiveness (ИРВ + Score) — двойная правка

**Файлы вместе (Score-логика живёт в ДВУХ местах — менять синхронно):**
- backend: `services/reagent_effectiveness_service.py` (`_SCORE_WEIGHTS`,
  `_compute_extended_metrics`) ↔ `routers/reagent_analysis.py` (↔18)
- UI: `static/js/reagent_analysis.js` (hover-подсказки метрик) +
  `templates/reagent_analysis.html` (блок `<details>` «как считается?»)

**Инвариант:** изменил веса/пороги/формулу Score → обнови ОБА места (backend +
оба UI-куска). Иначе таблица и подсказки разойдутся с расчётом.

---

## 6. Equipment (размазано по 5 роутерам — высокий риск рассинхрона)

> `models/equipment.py` тянут 5 роутеров: `equipment_management` (↔29),
> `equipment_documents` (↔22), `equipment_admin` (↔16), `well_equipment_integration`
> (↔16), `admin_map` (↔16). Литерального дублирования путей нет, но поведение и
> модель общие — правка модели/статуса задевает все пять.

**Файлы вместе:**
- `backend/models/equipment.py` (+ `app.py` ↔22 — статусы/интеграция)
- `backend/routers/equipment_management.py` (17 маршрутов — основной)
- `equipment_documents.py`, `equipment_admin.py`, `well_equipment_integration.py`, `admin_map.py`
- `backend/config/` — типы оборудования, реестр статусов

**Правило:** меняешь модель/enum статусов оборудования → пройди grep по всем пяти
роутерам и обнови согласованно.

---

## 7. Documents (генерация документов)

**Файлы вместе:**
- `backend/documents/models.py` — ядро, тянется отовсюду: `service.py` (↔42),
  `routers/equipment_documents.py` (↔22), `routers/documents_pages.py` (↔20),
  `services/auto_create.py` (↔15), `models_notifications.py`
- `backend/documents/numbering.py` — автонумерация по типу/скважине/периоду
- `backend/documents/services/` — reagent_expense, auto_create, notifications
- Уведомления: `documents/services/notification_service.py` (Telegram/email)

**Поток:** форма → service → LaTeX-шаблон (Jinja2) → XeLaTeX → PDF в `backend/generated/pdf/`.

---

## 8. Состояние тестов (важно для «не ломать»)

- ✅ Покрыто: **только observation** (`backend/tests/test_observation_*.py`).
- ❌ Без тестов: pressure, flow-rate, отчёты (daily/adaptation/customer), reagent,
  equipment, documents. **Здесь регрессии всплывают поздно — в UI/PDF.**
- Запуск: `pytest backend/tests/ -q` (pytest 9.0.3 в requirements).
- При правке непокрытой критичной фичи — сначала добавь smoke-тест, потом меняй.

---

## 9. Чек-лист перед коммитом правки

1. Нашёл фичу в этой карте? Если нет — `find` + `grep` по templates/static.
2. Тронул ВСЕ файлы из блока «Файлы вместе»?
3. Не нарушил §0 (датчики/ΔP/TZ) и инварианты фичи?
4. Не залез в «Не трогать» (особенно `customer_daily.html`)?
5. Если есть тест на этот сервис — `pytest` зелёный до и после?
6. Каждая изменённая строка трассируется к запросу пользователя?
