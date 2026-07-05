# HANDOFF — Финансовые акты (seq 3)

**Chain:** standalone-ef2b604f · **seq 3** · parent: `HANDOFF_standalone-ef2b604f_financial-acts_2026-07-03.md` (seq 2)
**Date:** 2026-07-03
**Branch:** `feat/financial-acts` · **закоммичено** до `d60156f`
**Auto:** false

> Порядок чтения: `CLAUDE.md` → `CODEMAP.md` → `docs/tz/TZ_financial_acts.md` → seq 1 → seq 2 → этот файл.
> Полная хронология: `memory/project_financial_acts.md`.

---

## Goal

«Финансовые акты» — ежемесячный «Акт приёма-передачи выполненных работ» из данных БД (подвкладка
`/documents`, только админ, .docx+PDF) + **Счёт-фактура** из акта + **ревизия по скважинам** + учёт/статусы.
Рабочее, протестировано. Ветка `feat/financial-acts` (коммиты `0b0aa64` → `d60156f`), в main НЕ влита.

---

## Since Last Handoff (что сделано ПОСЛЕ seq 2)

1. **🔥 Ревизия черновика «Скважины и этапы»** (панель справа, грузится по AJAX `GET /{id}/catalog` — работает
   для ЛЮБОГО акта, не зависит от meta). Два блока:
   - **Оптимизация — продолжить/прекратить**: только опт-скважины ПЕРИОДА (не все). Чекбокс «прекратить» + пункты 3.9/3.17.
   - **Адаптация — эффективность**: Оплатить адаптацию (185М) / Неэффективна (→ оптимизация время + реагенты дозирование, 2 независимые галочки) / Исключить.
   - Пересборка `POST /{id}/rebuild`. Форма создания упрощена (период+преамбула+подписанты, dual-list убран).
2. **Валидация адаптации**: < 1 дня (dt_end≤dt_start) = ошибка → исключается + ⚠; < 5 дней = ⚠ (ADAPT_MIN_DAYS/WARN_DAYS).
3. **Реагенты гейтятся ПО-СКВАЖИННО**: оплаченная адаптация→не считаются; неэфф.адаптация(галочка)→за период адаптации; оптимизация→за период оптимизации.
4. **🔥 Счёт-фактура (`financial_invoice`)**: формируется ИЗ акта (`create_invoice_from_act`) — копирует строки+meta →
   **сумма идентична акту**. Свой шаблон (таблица работ переиспользована из шаблона акта), «Счёт-фактура № N-c». Одна СФ на акт (кнопка «→ СФ»).
5. **Реестр актов+СФ + статусы**: таблица Тип/Номер/Период/Создан/Сумма/Статус. Статусы Черновик→Отправлен→Принят (кнопки, meta.sent_at/accepted_at).
6. **Fix**: `generate_docx` брал шаблон по хардкоду → теперь по `doc_type.docx_template_name` (иначе СФ рендерилась как акт).
7. **Fix**: IntegrityError doc_number (seq 2 хвост) — номер по периоду `FA-YYYY-MM`, rebuild удаляет ВСЕ акты периода.

---

## Where We Are (текущее состояние)

В БД: акты `FA-2026-02..05` + СФ создаются кнопкой. Всё проверено сквозняком (TestClient) + реальные docx/PDF.

### Алгоритм биллинга (`_build_rows(db, year, month, decisions)` → rows, catalog, warnings)
| Работа | Источник | Кол-во | Сумма |
|--------|----------|--------|-------|
| Адаптация | WellStatus('Адаптация') завершённая в месяце (dt_end в периоде); валидация ≥1дн | завершённых операций | фикс.цена × кол-во (если mode=adaptation) |
| Оптимизация | WellStatus('Оптимизация') клип по месяцу; + неэфф.адаптация (время) | сутки | (мес.цена/дней)×сутки |
| Дозирование пенных | Event foam-группы за релевантный период скважины | вбросов | 468 630 × кол-во |
| Дозирование ингибир. | Event inhibitor-группы за релевантный период | вбросов | 507 375 × кол-во |

`decisions` = {well: {mode: adaptation|ineffective|exclude, reagents: bool, time: bool}}. Каталог+warnings в meta и через `get_well_catalog`.

### Счёт-фактура
`create_invoice_from_act(db, act_id)`: doc_number=`СФ-YYYY-MM`, meta.invoice_no=`{seq}-c`, parent_id=act, копия items.
Rebuild по периоду. Рендер = `_prepare_financial_act_context` (тот же) в шаблон `financial_invoice_template.docx`.

---

## Files (все в коммитах на feat/financial-acts)

**Ядро:** `backend/documents/services/financial_act.py` (`_build_rows` decision-aware, `build_financial_act`,
`create_invoice_from_act`, `get_well_catalog`, `_adaptation_by_well`, `_opt_by_well`, валидация, пропись).
`backend/documents/generator.py` (`generate_docx` выбор шаблона, `_prepare_financial_act_context` +invoice_no,
`_decision_context`, `_signatory_context`, `_style_work_table`, `generate_pdf_from_docx` soffice изолир.профиль).
`backend/documents/models.py` (+колонки DocumentItem/DocumentType), `backend/models/act_signatory.py`, `backend/models/reagent_catalog.py`.
**Роутер:** `backend/routers/financial_acts.py` (page тянет оба типа; create, rebuild, `/catalog`, `/invoice`,
`/status`, download docx/pdf, prices, reagents, signatory add/delete). Регистрация в app.py ДО documents_pages.
**UI:** `backend/templates/documents/financial_acts.html` (форма-мастер, реестр актов+СФ, панель ревизии AJAX, предпросмотр).
**Шаблоны:** `scripts/build_financial_act_template.py`, `scripts/build_financial_invoice_template.py`;
`backend/documents/templates/docx/financial_act_template.docx` + `financial_invoice_template.docx`.
**Миграции:** `alembic/versions/fa1financial01`, `fa2signatories01`, `fa3signatories02`. **ТЗ:** `docs/tz/TZ_financial_acts.md`.

---

## Key Decisions

- СФ ИЗ акта (единый источник → идентичная сумма); свой шаблон, тот же контекст.
- `doc_number` по периоду (`FA-YYYY-MM`/`СФ-YYYY-MM`), rebuild удаляет всё за период → нет коллизий.
- Ревизия = draft→правка→rebuild; каталог на лету (для любого акта).
- Реагенты по-скважинно (по режиму). Валидация адаптации ≥1дн/⚠5дн.
- PDF через LibreOffice (изолир.профиль), .docx через docxtpl. Статусы через meta (без миграции CheckConstraint).

## Gotchas (дорого переоткрывать)

1. `generate_docx` — шаблон по `doc_type.docx_template_name` (был хардкод → СФ рендерилась как акт).
2. docxtpl `{%tr%}`/`{%p%}` удаляет свою строку/абзац → for/endfor в отдельных обёртках.
3. Router financial_acts ДО documents_pages (иначе `/documents/{doc_id}` ловит `/financial-acts` → 422).
4. `document.meta` НЕ `document.metadata` (зарезервировано SQLAlchemy).
5. httpx list-form в тестах: `data={'k':['1','2']}`; тест ORM: `import backend.app` (реестр мапперов).
6. soffice изолир.профиль `-env:UserInstallation` (иначе падает при открытом LibreOffice).
7. rebuild: delete ВСЕ за период до insert (номер по периоду).
8. Панель ревизии только для актов (faPreview 3-й арг type); каталог по AJAX (не эмбеддинг — работает для старых актов).

## Evidence (проверено)

- Данные WellStatus/Event с ОКТ 2025. Май 2026: адаптация 30/103, оптимизация 30/85/98/108/128, дозирование пенных+ингибитор.
- Цены: адаптация 185 000 000; оптимизация 45 200 000,12/мес; foam 468 630; inhibitor 507 375 (507375×11=5 581 125 ✓).
- Акт FA-2026-05 = СФ-2026-05 по сумме: 789 446 302,84 ✓. Валидация: скв98 (0дн) исключена+⚠.
- Скв30 «неэффективна» → 185М убрано, +оптимизация 29сут +реагенты в дозирование. .docx/PDF генерируются.

## Open Questions / Tech debt

- **Сиды не в миграциях**: DocumentType (financial_act/financial_invoice), contract_price, reagent act_group,
  act_signatory — засеяны прямым SQL/скриптами, НЕ миграцией. Свежая БД их не получит → нужна seed-миграция.
- Неэфф.адаптация «время»: биллингуется ПОЛНЫЙ период адаптации (может включать прошлый месяц) — уточнить: клипать по месяцу?
- ОВ-8: сущность «Контракт» vs статичный текст. Кеш PDF-предпросмотра.
- СФ при пересборке акта не обновляется автоматически (кнопка «→ СФ» пересоздаёт вручную).

## Next Action

1. **Влить feat/financial-acts в main** (когда готово) ИЛИ продолжить доработки.
2. **Seed-миграция** для типов документов/цен/групп реагентов/подписантов (сейчас только в рантайм-БД).
3. Живой тест под логином: `/documents` → «💰 Финансовые акты» → создать акт → ревизия → «→ СФ» → статусы.

**Локально:** сервер `uvicorn backend.app:app --reload --reload-dir backend` :8000. БД Render Postgres (frankfurt) `telegram_events_db`.
Пересборка шаблонов: `.venv/bin/python scripts/build_financial_act_template.py` затем `..._invoice_template.py`.
