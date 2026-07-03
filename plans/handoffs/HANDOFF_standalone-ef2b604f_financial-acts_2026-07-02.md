# HANDOFF — Финансовые акты (Акт приёма-передачи выполненных работ)

**Chain:** standalone-ef2b604f · seq 1 · parent: none
**Date:** 2026-07-02
**Branch:** `fix/observation-chapter-2026-06-28` (⚠️ фича делалась НЕ в отдельной ветке; ничего НЕ закоммичено)
**Auto:** false

> Прочитать в начале следующей сессии: `CLAUDE.md` → `CODEMAP.md` → `docs/tz/TZ_financial_acts.md` → этот файл.
> Память: `memory/project_financial_acts.md` (полная хронология этапов).

---

## Goal

Добавить функционал **«Финансовые акты»** — ежемесячный двуязычный (RU/EN) «Акт приёма-передачи
выполненных работ» по Контракту № 2/24-09 от 24.09.2024. Данные берутся **из БД**, период = календарный
месяц. Интеграция как **подвкладка на странице «Акты» (`/documents`), только для админа**. Вывод —
**и `.docx` (по реальному шаблону клиента), и PDF**. Образец-шаблон:
`/Users/volodymyrnikitin/Downloads/Акт финансовый  январь_3 (3).docx`.

Фича **реализована и протестирована за одну сессию** (сквозняком через TestClient + генерация реальных
.docx/PDF). Есть 3 реальных акта в БД (Фев/Мар/Апр 2026, № 1/2/3).

---

## Where We Are (состояние — ГОТОВО и проверено)

Полностью рабочий вертикальный срез:
- Схема БД (3 миграции применены), сидинг (тип документа, классификация реагентов, дефолт-цены, библиотека подписантов).
- Сервис сборки акта из БД + расчёт + пропись RU/EN.
- Генерация `.docx` (docxtpl из шаблона клиента) + PDF (LibreOffice из того же .docx = паритет).
- Роутер (admin-гейт) + страница UI (форма, список, прайс, реагенты, подписанты, предпросмотр справа).
- Бизнес-правила согласованы с пользователем и внедрены.

**НЕ сделано:** git-коммит (всё untracked), живой тест под логином пользователя в браузере,
Этап 4 (ручная правка авто-строк перед выпуском).

---

## Три вида работ (алгоритм сборки — `_build_rows`)

| Работа | Источник | Кол-во | Стоимость |
|--------|----------|--------|-----------|
| **Адаптация** | `WellStatus(status='Адаптация')`, **завершённые в этом месяце** (`dt_end` в периоде, NOT NULL). Незавершённые (dt_end IS NULL) НЕ включаются. Привязка ПО МЕСЯЦУ ЗАВЕРШЕНИЯ (май→июнь = в июнь). | число завершённых операций | **фикс.цена × кол-во** |
| **Оптимизация** | `WellStatus(status='Оптимизация')`, клиппинг интервала по границам месяца | отработанные сутки `(clip_end−clip_start).days` | `(месячная цена / дней_в_месяце) × сутки`, суммирование по дням с округлением дневной цены; разбивка по интервалам `effective_from` |
| **Дозирование пенных** | `Event` где `reagent∈foam-группе` (`ReagentCatalog.act_group='foam'`) И `qty>0`, join по `cast(Event.well)==cast(Well.number)` (у Event нет well_id) | счёт событий | `цена × кол-во` |

**Период в строке** (`_fmt_range`): адаптация = интервал операции (может межмесячный «09.03-21.04.2026»),
оптимизация = отработанный span, дозирование = первый–последний вброс (min/max event_time).

**Расчёт:** НДС 12% построчно `ROUND_HALF_EVEN` (банковское); итоги = сумма колонок; сумма прописью
RU (сум/тийин, склонение тийин: 11-14→тийинов, 1→тийин, 2-4→тийна, else тийнов) + EN (UZS/tiyins).

**Номер акта:** сквозной глобальный (`max(meta.act_no)+1`), сохраняется при пересборке периода.
`doc_number = FA-{seq:03d}-{year}-{month:02d}`. Шапка «№ {act_no}».

**Подписанты v2:** независимые списки ШАПКА vs НИЗ, несколько на сторону (contractor/customer),
двуязычно (position_ru/en, name_ru/en). Библиотека `act_signatory`; выбор per-act → `meta.header_sigs`/`sign_sigs`.

---

## Files

### Новые (все UNTRACKED — надо `git add`)
| Файл | Что |
|------|-----|
| `alembic/versions/fa1financial01_add_financial_acts.py` | contract_price, +колонки document_items, reagent_catalog.act_group/unit_cost, document_types.docx_template_name |
| `alembic/versions/fa2signatories01_add_act_signatory.py` | (устарела — role/name) → заменена fa3 |
| `alembic/versions/fa3signatories02_redesign_signatory.py` | redesign act_signatory: side/position_ru/en/name_ru/en |
| `backend/documents/services/financial_act.py` (323) | ядро: `_build_rows`, `build_financial_act`, пропись, цены, `_fmt_range`, `_clip` |
| `backend/models/act_signatory.py` | модель библиотеки подписантов (SIDE_CONTRACTOR/CUSTOMER, `as_dict()`) |
| `backend/routers/financial_acts.py` (227) | роуты (admin), создание/скачивание/прайс/реагенты/подписанты |
| `backend/templates/documents/financial_acts.html` (236) | страница: 2 колонки, форма, список, прайс, реагенты, библиотека подписантов, предпросмотр |
| `scripts/build_financial_act_template.py` (191) | превращает .docx клиента в docxtpl-шаблон (теги + циклы + шрифт + ширины) |
| `docs/tz/TZ_financial_acts.md` (324) | ТЗ (SPEC) |
| `backend/documents/templates/docx/financial_act_template.docx` | СГЕНЕРИРОВАННЫЙ шаблон (пересоздаётся build-скриптом) |

### Изменённые
| Файл | Что |
|------|-----|
| `backend/documents/models.py` | +DocumentType.docx_template_name; +8 колонок DocumentItem (well_number/work_group/unit/price_per_unit/amount/vat_amount/amount_with_vat/period_label); +import Numeric |
| `backend/documents/generator.py` | `_prepare_financial_act_context`, `_signatory_context`, `generate_docx` (docxtpl+fallback `_generate_docx_programmatic`), `generate_pdf_from_docx` (soffice), `_fin_fmt`, `_find_soffice` |
| `backend/models/reagent_catalog.py` | +act_group, +unit_cost (+import Numeric) |
| `backend/templates/documents/index.html` | кнопка «💰 Финансовые акты» (admin) в шапке |
| `backend/app.py` | регистрация financial_acts router **ДО** documents_pages |
| `requirements.txt` | +python-docx>=1.1, +num2words>=0.5.13 |

---

## Key Decisions (и почему)

1. **`Document.well_id = NULL`** для финакта (мульти-скважинный, в отличие от reagent_expense).
   Скважина построчно в `DocumentItem.well_number`. Из-за этого `numbering.build_doc_number` не годится →
   номер собирается в сервисе.
2. **PDF через LibreOffice** (`soffice --headless --convert-to pdf`), НЕ через LaTeX. Причина: `xelatex` НЕ
   установлен локально; конвертация из того же .docx = гарантированный PARITY .docx↔PDF. Путь soffice:
   `/Applications/LibreOffice.app/Contents/MacOS/soffice` (или `which soffice/libreoffice`).
3. **.docx = заполнение реального файла клиента** через docxtpl (не программная сборка). Программная
   сборка осталась как fallback `_generate_docx_programmatic` (если шаблона нет).
4. **Округление НДС** — `ROUND_HALF_EVEN` (банковское, выбор пользователя).
5. **Адаптация — по месяцу ЗАВЕРШЕНИЯ**, незавершённые исключаются, фикс.цена × число операций.
6. **Классификация реагентов** — расширение существующей `ReagentCatalog` (не новая таблица). Дефолт:
   Super Foam → inhibitor, остальное → foam; редактируемо.
7. **Цены** — таблица `contract_price` с `effective_from`; оптимизация хранит **месячную** цену.
   Значения из образца = реальные (185М адапт / 45.2М/мес опт / 468630 доз).
8. **Подписанты — независимые списки** шапка/низ, дефолт выбора = как в прошлом акте (`_last_used_ids`).

---

## What We Tried / Gotchas (дорого переоткрывать)

1. **Router order:** `/documents/{doc_id}` (documents_pages) перехватывал `/documents/financial-acts`
   (422, int-parsing "financial-acts"). Фикс: регистрировать financial_acts router **ДО** documents_pages в app.py.
2. **docxtpl `{%tr%}`/`{%p%}` УДАЛЯЕТ свою строку/абзац** (relocation regex в patch_xml). Значит:
   - Таблица: `{%tr for%}` и `{%tr endfor%}` в **ОТДЕЛЬНЫХ строках-обёртках** вокруг строки-данных (НЕ в одной).
   - Подписи: `{%p for s in sign_*%}` / … / `{%p endfor%}` в отдельных абзацах вокруг повторяемого блока.
   - Ошибка «unknown tag endfor» = for и endfor в одной строке.
3. **Merged cells:** col0/col1 таблицы работ — объединённая ячейка; запись «» в col1 затирала соседний тег.
4. **Фрагментация runs** в .docx (напр. «01.01.2025» разбито на 4 run): точечная замена ненадёжна →
   абзацы переопределяются целиком (`set_para` = runs[0].text=..., остальные «»).
5. **`document.meta` НЕ `document.metadata`** — `metadata` зарезервировано SQLAlchemy (это латентный баг в
   существующем generator для reagent_expense, НЕ трогали). JSONB-колонка = атрибут `meta`.
6. **Тест ORM:** standalone-скрипт падал `KeyError 'Equipment'` — нужно `import backend.app` (или
   `backend.models.equipment`) чтобы загрузить полный реестр мапперов.
7. **httpx list-form в TestClient:** `data={'k':['1','2']}` (dict со списком), НЕ list-of-tuples (даёт 422
   «year missing»). Сам роут `list[int]=Form(default=[])` корректен.
8. **num2words:** не был установлен (поставлен). lang='ru' даёт число словами БЕЗ валюты; суффикс сум/тийин
   и склонение — самописные поверх.
9. **Alembic-дерево:** несколько heads + дубль-ревизия `a1b2c3d4e5f7` (в чужих файлах, PRE-EXISTING, не наш).
   Миграции fa1/fa2/fa3 линейно от `p2r4d6r8t0y2` (текущий DB head был). Применялись `alembic upgrade <rev>`.

---

## Evidence & Data (проверенные числа, апрель 2026)

- **Тестовый месяц — апрель 2026** (данные WellStatus/Event начинаются ОКТ 2025; янв-2025 из образца НЕ воспроизводим).
- Адаптация завершилась у 2 скв: **61** (09.03→21.04), **108** (04.04→17.04) → обе в акте, qty=1, 185М each.
- Оптимизация: скв 85 (22-30, 8 сут), 98 (01-30, 29 сут), 108 (17-30, 13 сут), 128 (01-30, 29 сут).
  Полный месяц апрель (30 дн) → worked=29 (= last_day − day1), daily=45200000.12/30.
- Дозирование: 7 скв (108/136/85/128/30/103/98), счёт вбросов пенных.
- **Скв.61 НЕ в дозировании** (вопрос пользователя, логика ВЕРНА): за апрель у неё только 3 события
  `event_type='equip'` (reagent=NULL, qty=NULL) — реагентных вбросов ноль.
- Реагенты в БД: Oil Foam, Super Foam(→inhibitor), 1251, SW-OF, Liquid Foam, Sand Stick, BT-10, TS, Chicko, Chico, CO2 (12 foam / 1 inhibitor).
- Итог апреля с текущей логикой ≈ 714,6 млн (5 адапт было при overlap-логике → стало 2 при completion-логике).
- Проверка формулы образца: 1 458 064,52 × 30 = 43 741 935,60 ✓; НДС 12% построчно сходится.
- Генерация: .docx ~23КБ (шаблон) / PDF ~165КБ. Все роуты 200 (TestClient, admin-гейт замокан).

---

## Data model (справка по колонкам)

- `contract_price`: id, work_type (adaptation|optimization|foam_dosing), well_id (nullable, приоритет над общей),
  price_per_unit, effective_from, contract_ref. Unique (work_type, well_id, effective_from).
- `document_items` +: well_number, work_group, unit, price_per_unit, amount, vat_amount, amount_with_vat, period_label.
- `reagent_catalog` +: act_group (foam|inhibitor), unit_cost.
- `act_signatory`: id, side (contractor|customer), position_ru, position_en, name_ru, name_en, created_at.
- `Document.meta` (JSONB): act_no, contract_ref, total_amount/vat/with_vat (+ *_words_ru/en), wells[],
  header_sigs[], sign_sigs[] (каждый = {side, position_ru, position_en, name_ru, name_en}).

---

## Open Questions

- **ОВ-8:** нужна ли отдельная сущность «Контракт» (номер/дата/реквизиты) или статичный текст? Сейчас — константа `CONTRACT_REF = "2/24-09 от 24.09.2024"`.
- Формат периода адаптации: сейчас сквозной межмесячный интервал («09.03-21.04.2026»). Пользователь может захотеть только внутри месяца — уточнить при желании.
- Кеш PDF предпросмотра: сейчас каждый выбор акта регенерит PDF через LibreOffice (~2с). Можно кешировать.

---

## Where We're Going (следующие этапы)

1. **Git-коммит** — вся фича untracked. Создать ветку (напр. `feat/financial-acts`), `git add` новые файлы +
   изменённые, коммит. **Не коммитить в observation-ветку.**
2. **Живой тест под логином** — зайти админом `/documents` → «💰 Финансовые акты», сформировать акт,
   открыть .docx/PDF, оценить шрифт 10pt/ширины/подписантов/предпросмотр глазами.
3. **Этап 4** — правка авто-собранных строк перед выпуском (кол-во/цена/скважина вручную), паттерн
   rebuild-from-events как у well_handover.
4. (опц.) кеш PDF, сущность «Контракт», полировка UI.

---

## Next Action

**Закоммитить фичу** (сейчас всё untracked на чужой ветке) ИЛИ по запросу пользователя — Этап 4 (правка строк).
Начать с: `git checkout -b feat/financial-acts` затем `git add` всех файлов из раздела Files.

**Как проверить локально:** `uvicorn backend.app:app --reload` → войти админом → `/documents/financial-acts`.
Пересборка docx-шаблона после правок: `.venv/bin/python scripts/build_financial_act_template.py`.
Быстрый тест логики: `import backend.app; from backend.documents.services.financial_act import build_financial_act`.

---

## Приложение A. Карта ключевых функций

`backend/documents/services/financial_act.py`:
- `build_financial_act(db, year, month, created_by_name=None, header_sigs=None, sign_sigs=None) -> Document`
  — публичное API. Rebuild: удаляет прежний draft периода (сохраняя act_no), пересобирает, коммитит вызывающий.
- `_build_rows(db, year, month) -> list[dict]` — сборка 3 групп. Порядок: adaptation → optimization → foam_dosing.
- `_price_for(db, work_type, well_id, on_date)` — цена на дату (приоритет well-specific над NULL).
- `_clip(status, p_start, p_end)` — пересечение интервала статуса с месяцем.
- `_fmt_range(d1, d2)` — «22-30.04.2026» / «09.03-21.04.2026» / полные даты.
- `amount_in_words_ru/en(Decimal)`, `_tiyin_word(n)`, `_money(x)` (HALF_EVEN), `_period(year,month)`.
- Константы: `VAT_RATE=0.12`, `CONTRACT_REF`, `_DEFAULT_SIGS` (3 подписанта образца).

`backend/documents/generator.py` (класс DocumentGenerator):
- `generate_docx(doc)` → docxtpl из `templates/docx/financial_act_template.docx` (иначе `_generate_docx_programmatic`).
- `generate_pdf_from_docx(doc)` → `_find_soffice()` + convert-to pdf; ставит `doc.pdf_filename`.
- `_prepare_financial_act_context(doc)` — ЕДИНЫЙ источник для .docx и (потенциально) LaTeX. Читает `doc.meta`.
- `_signatory_context(m)` — header_contractor/customer(_en) строки + sign_contractor/customer списки.
- `_fin_fmt(x)` — «1 234 567,89».

`backend/routers/financial_acts.py` (все роуты `/documents/financial-acts...`, admin):
- GET `` (страница), POST `/create`, GET `/{id}/download.docx`, `/{id}/download.pdf`, POST `/{id}/delete`.
- POST `/prices/add`, `/prices/{id}/delete`; POST `/reagents/{id}/update`.
- POST `/signatory/add`, `/signatory/{id}/delete`. Хелперы `_signatory_library`, `_last_used_ids`, `_sigs_from_ids`.

## Приложение B. Рецепты (сидинг/пересборка)

```bash
# пересобрать docx-шаблон из исходного файла клиента
.venv/bin/python scripts/build_financial_act_template.py

# применить миграции финакта (если чистая БД)
.venv/bin/alembic upgrade fa3signatories02

# сидинг (см. историю сессии): DocumentType 'financial_act'; reagent_catalog.act_group
#   (Super Foam→inhibitor, остальное→foam); contract_price (adaptation 185000000,
#   optimization 45200000.12/мес, foam_dosing 468630, effective_from 2025-01-01);
#   act_signatory библиотека (Яцкив/Исраилов/Cho, двуязычно).
```

**Классификация реагентов:** дефолт вычисляется по имени (`Super Foam`/`SF` → inhibitor, иначе foam),
редактируется на странице (секция «Подписанты/Реагенты»). Дозирование учитывает только `act_group='foam'`.

**Инвариант:** любое изменение таблицы/текста акта делать в ЕДИНОМ контексте
(`_prepare_financial_act_context`) — .docx и PDF идентичны (PDF = конвертация .docx).
