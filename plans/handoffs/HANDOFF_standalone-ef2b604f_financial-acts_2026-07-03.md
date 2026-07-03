# HANDOFF — Финансовые акты (seq 2)

**Chain:** standalone-ef2b604f · **seq 2** · parent: `HANDOFF_standalone-ef2b604f_financial-acts_2026-07-02.md`
**Date:** 2026-07-03
**Branch:** `fix/observation-chapter-2026-06-28` (⚠️ фича делалась НЕ в отдельной ветке; **НИЧЕГО не закоммичено**)
**Auto:** false

> Порядок чтения: `CLAUDE.md` → `CODEMAP.md` → `docs/tz/TZ_financial_acts.md` → **parent handoff (seq 1)** → этот файл.
> Полная хронология: `memory/project_financial_acts.md`. Seq 1 покрыл фундамент (схема, сервис, docx-шаблон,
> базовые бизнес-правила, подписанты, генерация). Этот seq 2 — крупные доработки поверх.

---

## Goal

«Финансовые акты» — ежемесячный двуязычный «Акт приёма-передачи выполненных работ» из данных БД,
подвкладка `/documents` (только админ), вывод `.docx` (по реальному шаблону клиента) + PDF.
Фича рабочая, протестирована, в БД 4 реальных акта (Фев/Мар/Апр/Май 2026, № 1–4).

---

## Since Last Handoff (что сделано ПОСЛЕ seq 1)

1. **🔥 Переделана логика возмещения реагентов (была НЕВЕРНА).** Возмещение — ТОЛЬКО за вбросы
   **в период оптимизации**; ДВЕ категории с разными ценами: «Дозирование пенных реагентов» (foam, 468 630)
   и «Дозирование ингибирующих реагентов» (inhibitor, **507 375** — из майского образца). Адаптация —
   без возмещения химии. Раньше: считались все пенные вбросы за месяц, ингибитор выбрасывался.
2. **Расширенные выводы:** «продолжить работы» (по умолч. ВСЕ, пункт 3.9) / «прекратить работы»
   (выбранные, пункт 3.17). `excluded_wells` в API, dual-list в UI.
3. **🔥 Редизайн формы создания** (была запутанной): чёткая последовательность
   1) Период 2) Преамбула (2 колонки Заказчик|Исполнитель, строки должность+ФИО, add/remove)
   3) Подписанты (то же) 4) Выводы (dual-list Продолжить↔Прекратить). Форма шлёт ПАРАЛЛЕЛЬНЫЕ массивы.
4. **🔥 FIX: IntegrityError duplicate doc_number.** `doc_number` теперь по ПЕРИОДУ (`FA-2026-05`),
   rebuild удаляет ВСЕ акты периода до вставки, `act_no` (№ в шапке) сохраняется/инкрементится глобально.
5. **Обработка ошибок:** create в try/except (реальная ошибка во флеше вместо generic-500),
   PDF-роут отдаёт читаемый текст, soffice с изолированным профилем (`-env:UserInstallation`).
6. **Визуал таблицы:** жирная линия перед каждой группой + перед «Всего»; vMerge названия по группам
   (центрировано); выравнивание ширин шапки↔тела.
7. **Период во всех строках** (`_fmt_range`): адаптация — интервал операции, оптимизация — отработанный
   span, дозирование — первый–последний вброс.

---

## Where We Are (текущее полное состояние)

Полностью рабочий вертикальный срез. В БД: **FA-2026-02..05** (act_no 1–4). Библиотека подписантов = 3
(Яцкив/Исраилов/Cho). Всё проверено сквозняком (TestClient) + генерация реальных .docx/PDF.

### Алгоритм сборки (`_build_rows`)
| Работа | Источник | Кол-во | Стоимость |
|--------|----------|--------|-----------|
| **Адаптация** | `WellStatus('Адаптация')`, завершённые в месяце (`dt_end` в периоде, NOT NULL). Незавершённые исключены. Привязка ПО МЕСЯЦУ ЗАВЕРШЕНИЯ | число завершённых операций | фикс.цена × кол-во |
| **Оптимизация** | `WellStatus('Оптимизация')`, клип по месяцу | сутки `(clip_end−clip_start).days` | `(месячная цена/дней_в_месяце)×сутки`, суммирование по дням |
| **Дозирование пенных** | `Event` reagent∈foam-группе, qty>0, **event_time в интервале оптимизации** этой скв | счёт вбросов | 468 630 × кол-во |
| **Дозирование ингибир.** | то же, reagent∈inhibitor-группе | счёт вбросов | 507 375 × кол-во |

Классификация реагента: `ReagentCatalog.act_group` (foam/inhibitor). Дефолт: Super Foam→inhibitor, остальное→foam.
Гейтинг по оптимизации: `event_time.date()` ∈ пересечению интервалов `WellStatus('Оптимизация')` с месяцем.

### Расчёт
НДС 12% построчно `ROUND_HALF_EVEN`; итоги = сумма колонок; сумма прописью RU (сум/тийин, склонение тийин)
+ EN (UZS/tiyins). `doc_number=FA-{year}-{month:02d}`, `act_no` (№ шапки) — глобальный сквозной.

### Подписанты (v2, независимые шапка/низ)
Библиотека `act_signatory` (side contractor|customer, position_ru/en, name_ru/en). Per-act выбор в
`meta.header_sigs`/`sign_sigs`. Форма: 2-колоночные строки должность+ФИО (RU), EN подтягивается из
справочника по совпадению (side,position_ru,name_ru); новые значения сохраняются. Дефолт строк = из
последнего акта, иначе из образца (`_DEFAULT_SIGS`).

---

## Files

### Новые (UNTRACKED — надо `git add`)
- `alembic/versions/fa1financial01_add_financial_acts.py` — contract_price, +колонки document_items, reagent_catalog.act_group/unit_cost, document_types.docx_template_name
- `alembic/versions/fa2signatories01_add_act_signatory.py` — (устарела) role/name
- `alembic/versions/fa3signatories02_redesign_signatory.py` — act_signatory: side/position_ru/en/name_ru/en
- `backend/documents/services/financial_act.py` (356) — `_build_rows`, `build_financial_act`, пропись, `_fmt_range`, `_clip`, `_DEFAULT_SIGS`
- `backend/models/act_signatory.py` — модель библиотеки (SIDE_CONTRACTOR/CUSTOMER, as_dict())
- `backend/routers/financial_acts.py` (289) — роуты (admin): create (параллельные массивы), download docx/pdf, прайс, реагенты, signatory add/delete
- `backend/templates/documents/financial_acts.html` (304) — форма-мастер, список, прайс, реагенты, библиотека, предпросмотр справа
- `scripts/build_financial_act_template.py` (211) — .docx клиента → docxtpl-шаблон (теги, циклы, шрифт, ширины, выравнивание шапки)
- `docs/tz/TZ_financial_acts.md` — ТЗ
- `backend/documents/templates/docx/financial_act_template.docx` — сгенерированный шаблон (пересоздаётся build-скриптом)

### Изменённые (M)
- `backend/documents/models.py` — +DocumentType.docx_template_name; +8 колонок DocumentItem; +Numeric
- `backend/documents/generator.py` (618) — `_prepare_financial_act_context`, `_signatory_context`, `_decision_context`,
  `generate_docx` (docxtpl+fallback), `generate_pdf_from_docx` (soffice изолир. профиль), **`_style_work_table`** (визуал)
- `backend/models/reagent_catalog.py` — +act_group, +unit_cost
- `backend/templates/documents/index.html` — кнопка «💰 Финансовые акты» (admin)
- `backend/app.py` — регистрация financial_acts router ДО documents_pages
- `requirements.txt` — +python-docx, +num2words

---

## Key Decisions

- `Document.well_id=NULL` (мульти-скв); скважина построчно в `DocumentItem.well_number`.
- **doc_number по периоду** (`FA-YYYY-MM`), не по seq — чтобы rebuild не давал коллизию unique.
- **PDF через LibreOffice** (не LaTeX — xelatex не установлен), из того же .docx = PARITY. Изолированный профиль обязателен.
- **.docx = заполнение файла клиента** (docxtpl); программная сборка = fallback.
- Возмещение реагентов только в оптимизации; 2 категории, разные цены; НДС half-even.
- Визуал таблицы — **постобработкой** rendered .docx (`_style_work_table`), не в статик-шаблоне (группы динамические).

---

## What We Tried / Gotchas (дорого переоткрывать)

1. **Router order:** financial_acts ДО documents_pages (иначе `/documents/{doc_id}` ловит `/documents/financial-acts` → 422).
2. **docxtpl `{%tr%}`/`{%p%}` УДАЛЯЕТ свою строку/абзац** → for/endfor в ОТДЕЛЬНЫХ строках-обёртках вокруг повторяемого. «unknown tag endfor» = for и endfor в одной строке.
3. **`document.meta` НЕ `document.metadata`** (metadata зарезервировано SQLAlchemy).
4. **httpx list-form в тестах:** `data={'k':['1','2']}`, НЕ list-of-tuples (иначе 422 «year missing»).
5. **Тест ORM:** `import backend.app` для полного реестра мапперов (иначе KeyError 'Equipment').
6. **soffice single-instance:** headless падает, если LibreOffice открыт / параллельные конвертации → `-env:UserInstallation=file://<уник>`.
7. **IntegrityError doc_number:** rebuild-delete должен удалять ВСЕ акты периода + номер по периоду (см. Key Decisions).
8. **Шапка↔тело таблицы:** «№» в шапке был grid0, в теле — grid0+1(sp2) → границы не совпадали. Фикс: шапке «№» тоже sp2.
9. **Merged cell col0/col1** в строке данных таблицы — запись в col1 затирала тег col0.
10. **Commit-в-цикле:** rebuild нескольких актов в одной сессии с commit между итерациями ловил lazy-load ошибку (`well_equipment`) — собирать по одному / свежая сессия.

---

## Evidence & Data (май 2026, проверено)

- Тестовые данные WellStatus/Event начинаются ОКТ 2025 (янв-2025 из образца НЕ воспроизводим).
- Май: адаптация завершилась у скв.30/103 (07.04→06.05); оптимизация скв.30/85/98/108/128;
  дозирование пенных 128/30/85/98/108; ингибитор скв.98(1)/128(4).
- Цены (реальные, из образцов): адаптация 185 000 000/скв-оп; оптимизация 45 200 000,12/мес (÷дни);
  foam 468 630/вброс; **inhibitor 507 375/вброс**. Проверка: 507 375×11=5 581 125 ✓.
- Скв.61 НЕ в дозировании (вопрос был): за апрель у неё только события `equip` (reagent=NULL) — логика верна.
- Генерация: .docx ~22–23 КБ / PDF ~160–163 КБ. Все роуты 200 (TestClient, admin замокан).
- Визуал таблицы: 4 группы с thick-top (sz=18) + vMerge+vAlign center; шапка sp2 = тело.

---

## Data model (справка)

- `contract_price`: work_type (adaptation|optimization|foam_dosing|inhibitor_dosing), well_id nullable (приоритет над общей), price_per_unit, effective_from, contract_ref.
- `document_items` +: well_number, work_group, unit, price_per_unit, amount, vat_amount, amount_with_vat, period_label.
- `reagent_catalog` +: act_group (foam|inhibitor), unit_cost.
- `act_signatory`: side, position_ru/en, name_ru/en.
- `Document.meta`: act_no, contract_ref, total_* (+*_words_ru/en), wells[], continue_wells[], stop_wells[], continue_clause, stop_clause, header_sigs[], sign_sigs[].

---

## Open Questions

- **ОВ-8:** отдельная сущность «Контракт» (реквизиты) или статичный текст `CONTRACT_REF="2/24-09 от 24.09.2024"`?
- Формат периода адаптации: сейчас сквозной межмесячный («07.04-06.05.2026»). Возможно захотят иначе.
- Кеш PDF предпросмотра (каждый выбор акта = регенерация soffice ~2–5с).

---

## Where We're Going / Next Action

1. **Git-коммит** — вся фича UNTRACKED на чужой ветке. `git checkout -b feat/financial-acts` → `git add` (см. Files) → commit. **Не коммитить в observation-ветку.** — ЭТО СЛЕДУЮЩЕЕ ДЕЙСТВИЕ.
2. **Живой тест под логином** — `/documents` → «💰 Финансовые акты» → сформировать акт, скачать .docx/PDF, оценить визуал таблицы глазами (жирные линии, merge, ширины).
3. **Этап 4** — правка авто-собранных строк перед выпуском (кол-во/цена/скважина вручную).
4. (опц.) кеш PDF, сущность «Контракт».

**Проверка локально:** сервер уже запущен (`uvicorn backend.app:app --reload --reload-dir backend`, порт 8000, PID был 54019).
Пересборка docx-шаблона после правок: `.venv/bin/python scripts/build_financial_act_template.py`.
Быстрый тест: `import backend.app; from backend.documents.services.financial_act import build_financial_act`.
БД: Render Postgres (frankfurt), `telegram_events_db`.

## Приложение: карта функций
- `financial_act.py`: `build_financial_act(db,year,month,created_by_name,header_sigs,sign_sigs,excluded_wells,continue_clause,stop_clause)`,
  `_build_rows`, `_price_for`, `_clip`, `_fmt_range`, `amount_in_words_ru/en`, `_tiyin_word`, `_money`(HALF_EVEN).
- `generator.py`: `generate_docx` (docxtpl→`_style_work_table`), `generate_pdf_from_docx` (soffice), `_prepare_financial_act_context`,
  `_signatory_context`, `_decision_context`, `_style_work_table` (thick borders + vMerge), `_fin_fmt`, `_find_soffice`.
- `financial_acts.py`: GET page, POST create (hc_/hi_/sc_/si_ параллельные массивы + stop_wells), download.docx/pdf, prices, reagents, signatory add/delete; хелперы `_sig_defaults`, `_sig_used`, `_optimization_wells`, `_signatory_library`.
