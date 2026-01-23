## Карта роутов

### Условные обозначения

- **Тип ответа**:  
  - `HTML (template)` – возвращает HTML через Jinja2 (`TemplateResponse`); указывается шаблон.  
  - `JSON` – обычный JSON / `JSONResponse` / pydantic‑модель.  
  - `Redirect` – `RedirectResponse` / navigation‑redirect.  
  - `File` – скачивание файла / поток (`StreamingResponse`).

---

### Роуты в `backend/app.py`

| Метод | URL                                 | Файл / функция                            | Тип ответа                   | Шаблон / примечание                            |
|-------|-------------------------------------|-------------------------------------------|------------------------------|-----------------------------------------------|
| POST  | `/admin/reagents/add`              | `app.py::admin_reagents_add_supply`       | Redirect / JSON errors       | Обработка формы добавления прихода реагента   |
| GET   | `/`                                 | `app.py::root`                            | Redirect                     | Редирект на `/visual`                         |
| GET   | `/login`                            | `app.py::login_page`                      | HTML (template)              | `login.html` via `auth_base.html`            |
| GET   | `/register`                         | `app.py::register_get`                    | HTML (template)              | `register.html` via `auth_base.html`         |
| POST  | `/register`                         | `app.py::register_post`                   | HTML / Redirect              | Регистрация пользователя                      |
| POST  | `/login`                            | `app.py::login_submit`                    | Redirect / HTML (ошибки)     | Логин, запись сессии, логирование входов     |
| GET   | `/logout`                           | `app.py::logout`                          | Redirect                     | Очистка сессии, запись logout‑события        |
| GET   | `/visual`                           | `app.py::visual_page`                     | HTML (template)              | `visual.html` (глобальный таймлайн)          |
| GET   | `/admin/users`                      | `app.py::admin_users_page`                | HTML (template)              | `admin_panel.html`                            |
| GET   | `/admin/logins`                     | `app.py::admin_logins_page`               | HTML (template)              | `admin_logins.html` с Chart.js                |
| GET   | `/admin/reagents`                   | `app.py::admin_reagents_page`             | HTML (template)              | `admin_reagents.html` + `reagents.js`        |
| GET   | `/admin/reagents/inventory`         | `app.py::admin_reagents_inventory_page`   | HTML (template)              | `admin_reagents_inventory.html`              |
| POST  | `/admin/users/{user_id}/toggle-admin`   | `app.py::admin_toggle_admin`          | Redirect / JSON errors       | Управление правами администратора            |
| POST  | `/admin/users/{user_id}/toggle-active`  | `app.py::admin_toggle_active`         | Redirect                     | Вкл/выкл пользователя                         |
| POST  | `/admin/users/{user_id}/delete`         | `app.py::admin_delete_user`           | Redirect                     | Soft‑delete пользователя                      |
| POST  | `/admin/users/{user_id}/toggle-reagents`| `app.py::admin_toggle_reagents_access`| Redirect                     | Доступ к модулю реагентов                     |
| GET   | `/well/number/{well_number}`        | `app.py::redirect_from_number_to_id`      | Redirect / 404               | Поиск скважины по номеру → редирект          |
| GET   | `/well/{well_identifier}`           | `app.py::well_page`                       | HTML (template)              | `well.html` (страница скважины + график)     |
| POST  | `/well/{well_id}/status`            | `app.py::set_well_status`                 | Redirect / HTML (встроено)   | Создание статуса скважины                    |
| POST  | `/well/{well_id}/status/{status_id}/edit`   | `app.py::edit_well_status`         | Redirect / HTML              | Редактирование статуса                        |
| POST  | `/well/{well_id}/status/{status_id}/delete` | `app.py::delete_well_status`       | Redirect                     | Удаление статуса                              |
| POST  | `/well/{well_id}/update`            | `app.py::update_well`                     | Redirect                     | Обновление координат и параметров скважины   |
| POST  | `/well/{well_id}/notes/save`        | `app.py::save_well_note`                  | Redirect                     | Создание/редактирование заметок              |
| POST  | `/well/{well_id}/notes/{note_id}/delete` | `app.py::delete_well_note`            | Redirect                     | Удаление заметки                              |
| POST  | `/well/{well_id}/equipment/add`     | `app.py::add_well_equipment`              | Redirect                     | Привязка оборудования к скважине             |
| POST  | `/well/{well_id}/equipment/{eq_id}/edit`  | `app.py::edit_well_equipment`       | Redirect                     | Редактирование записи оборудования           |
| POST  | `/well/{well_id}/equipment/{eq_id}/delete`| `app.py::delete_well_equipment`     | Redirect                     | Удаление записи                               |
| POST  | `/well/{well_id}/channel/add`       | `app.py::add_well_channel`                | Redirect                     | Добавление каналов                            |
| POST  | `/well/{well_id}/channel/{channel_id}/edit` | `app.py::edit_well_channel`       | Redirect                     | Редактирование канала                         |
| POST  | `/well/{well_id}/channel/{channel_id}/delete`|`app.py::delete_well_channel`    | Redirect                     | Удаление канала                               |
| GET   | `/api/well/{well_id}/events`        | `app.py::well_events_api`                 | JSON                         | Список событий для графика                    |
| GET   | `/api/well/{well_id}/events.csv`    | `app.py::well_events_csv`                 | File (CSV)                   | Выгрузка событий в CSV                        |
| GET   | `/api/well/{well_id}/events.xlsx`   | `app.py::well_events_xlsx`                | File (XLSX)                  | Выгрузка событий в Excel                      |
| GET   | `/admin/reagents/import`            | `app.py::admin_reagents_import_page`      | HTML (template)              | `admin_reagents_import.html` (шаблон в проекте) |
| POST  | `/admin/reagents/import`            | `app.py::admin_reagents_import`           | HTML / Redirect / JSON error | Импорт реестра реагентов из Excel            |
| POST  | `/admin/reagents/inventory/add`     | `app.py::admin_reagents_inventory_add`    | Redirect / JSON              | Добавление строки инвентаризации             |
| GET   | `/debug/well/{well_id}/equipment`   | `app.py::debug_well_equipment`            | HTML/JSON debug              | Отладочная страница по оборудованию скважины |

> Примечание: в `app.py` также есть значительный объём вспомогательных функций по каталогу реагентов (`_resolve_reagent_from_form`, `_get_or_create_catalog_item` и др.) – это бизнес‑логика, встроенная в уровень роутов.

---

### Роуты API (`backend/api`)

#### `backend/api/wells.py`

| Метод | URL                | Функция               | Тип ответа | Описание                                 |
|-------|--------------------|-----------------------|-----------|------------------------------------------|
| GET   | `/api/wells`       | `list_wells`          | JSON      | Список всех скважин, сортировка по имени |
| GET   | `/api/wells/{id}`  | `get_well`            | JSON      | Одна скважина по ID, 404 если не найдена |

#### `backend/api/reagents.py`

| Метод | URL                    | Функция               | Тип ответа | Описание                                 |
|-------|------------------------|-----------------------|-----------|------------------------------------------|
| GET   | `/api/reagents`        | `api_list_reagents`   | JSON (pydantic list) | Список приходов реагентов         |
| POST  | `/api/reagents`        | `api_create_reagent`  | JSON (created)       | Создание прихода реагента        |
| GET   | `/api/reagents/balance`| `api_reagents_balance`| JSON (pydantic list) | Баланс реагентов по имени        |

---

### Роуты управления оборудованием

#### `backend/routers/equipment_management.py`

| Метод | URL                                   | Функция                    | Тип ответа         | Шаблон / назначение                       |
|-------|----------------------------------------|----------------------------|--------------------|-------------------------------------------|
| GET   | `/equipment/add`                      | `equipment_add_page`       | HTML (template)    | `equipment_add.html` – форма добавления   |
| POST  | `/api/equipment/create`               | `create_equipment`         | Redirect           | После создания → `/equipment/view/{id}`   |
| GET   | `/equipment`                          | `equipment_list_page`      | HTML (template)    | `equipment_list.html` – список + фильтры  |
| GET   | `/equipment/view/{equipment_id}`      | `equipment_detail_page`    | HTML (template)    | `equipment_detail.html`–детали и история  |
| POST  | `/api/equipment/{equipment_id}/move`  | `move_equipment`           | JSON               | Установка/демонтаж на скважину           |
| POST  | `/api/equipment/{equipment_id}/update_status` | `update_equipment_status`| JSON         | Обновление статуса/локации               |
| POST  | `/api/equipment/{equipment_id}/add_maintenance` | `add_maintenance`   | JSON         | Добавление записи обслуживания           |
| DELETE| `/api/equipment/{equipment_id}`        | `delete_equipment`         | JSON               | Soft‑delete (проверка активной установки) |
| GET   | `/equipment/import`                   | `equipment_import_page`    | HTML (template)    | `equipment_import.html`                   |
| GET   | `/api/equipment/template`             | `download_template`        | File (XLSX)        | Шаблон Excel для импорта                  |
| POST  | `/api/equipment/import/preview`       | `preview_import`           | JSON               | Валидация Excel перед импортом           |
| POST  | `/api/equipment/import/execute`       | `execute_import`           | JSON               | Фактический импорт из проверенных данных  |
| POST  | `/api/equipment/{equipment_id}/update`| `update_equipment`         | JSON               | Редактирование оборудования               |
| GET   | `/api/installation/{installation_id}` | `get_installation_record`  | JSON               | Получение записи установки                |
| POST  | `/api/installation/{installation_id}/update` | `update_installation_record` | JSON       | Обновление записи установки              |
| DELETE| `/api/installation/{installation_id}/delete` | `delete_installation_record` | JSON      | Удаление записи установки (если не активна) |

#### `backend/routers/equipment_admin.py`

| Метод | URL                                     | Функция                 | Тип ответа         | Шаблон / назначение                       |
|-------|------------------------------------------|-------------------------|--------------------|-------------------------------------------|
| GET   | `/admin/equipment`                      | `equipment_admin_panel` | HTML (template)    | `equipment_admin.html` – диагностика      |
| GET   | `/admin/equipment/{equipment_id}/raw`   | `equipment_raw_data`    | JSON               | Полные данные по оборудованию             |
| POST  | `/admin/equipment/test-move`            | `test_move`             | JSON               | Тест установки/снятия (диагностика)       |
| POST  | `/admin/equipment/test-status`          | `test_status`           | JSON               | Тест смены статуса (диагностика)          |

#### `backend/routers/equipment_documents.py`

| Метод | URL                                     | Функция                       | Тип ответа      | Шаблон / назначение                         |
|-------|------------------------------------------|-------------------------------|-----------------|---------------------------------------------|
| GET   | `/api/equipment/pressure`               | `get_pressure_api`            | JSON            | «Умный» поиск давлений по скважине/дате    |
| GET   | `/documents/equipment/new`              | `equipment_doc_new`           | HTML (template) | `documents/equipment_new.html`             |
| POST  | `/documents/equipment/create`           | `equipment_doc_create`        | Redirect        | Создание документа → просмотр              |
| GET   | `/documents/equipment/{doc_id}`         | `equipment_doc_detail`        | HTML (template) | `documents/equipment_detail.html`          |
| POST  | `/documents/equipment/{doc_id}/generate-pdf` | `equipment_doc_generate_pdf` | Redirect/File | Генерация PDF через LaTeX                  |
| POST  | `/documents/equipment/{doc_id}/sign`    | `equipment_doc_sign`          | Redirect        | Подписание документа                        |
| POST  | `/documents/equipment/{doc_id}/change-status` | `equipment_doc_change_status`| Redirect   | Канбан‑переходы статусов документа         |
| GET   | `/documents/equipment/{doc_id}/edit`    | `equipment_doc_edit`          | Redirect        | Редирект обратно на форму создания         |
| POST  | `/documents/equipment/{doc_id}/delete`  | `equipment_doc_delete`        | Redirect        | Удаление документа и откат статусов equip  |
| POST  | `/api/equipment/add`                    | `add_equipment_api`           | JSON            | Быстрое создание оборудования по JSON      |

#### `backend/routers/well_equipment_integration.py`

> В этом файле присутствуют **дубли** маршрутов из `equipment_management.py` (см. подробности в `DUPLICATES_AND_DEAD_CODE.md`).

| Метод | URL                                   | Функция                         | Тип ответа | Назначение                                 |
|-------|----------------------------------------|---------------------------------|-----------|--------------------------------------------|
| POST  | `/api/wells/{well_id}/install-equipment` (x2) | `install_equipment_*`   | JSON      | Дублирующее API установки оборудования     |
| GET   | `/api/wells/{well_id}/equipment`      | `get_well_equipment`            | JSON      | Список оборудования на скважине            |
| GET   | `/api/equipment/available`            | `get_available_equipment`       | JSON      | Список доступного оборудования             |
| GET   | `/equipment/add`                      | `equipment_add_page` (дубль)    | HTML      | Дублирует `equipment_management`           |
| POST  | `/api/equipment/create`               | `create_equipment` (дубль)      | Redirect   | Дублирует создание оборудования            |
| GET   | `/equipment`                          | `equipment_list_page` (дубль)   | HTML      | Дублирует список оборудования              |
| GET   | `/equipment/{equipment_id}`           | `equipment_detail_page` (дубль) | HTML      | Дублирует детальную страницу               |
| POST  | `/api/equipment/{equipment_id}/move`  | `move_equipment` (дубль)        | JSON      | Дублирует API перемещения                  |
| POST  | `/api/equipment/{equipment_id}/update_status` | `update_equipment_status` (дубль) | JSON | Дублирует смену статуса                    |
| POST  | `/api/equipment/{equipment_id}/add_maintenance` | `add_maintenance` (дубль) | JSON | Дублирует добавление обслуживания          |
| DELETE| `/api/equipment/{equipment_id}`        | `delete_equipment` (дубль)      | JSON      | Дублирует удаление                         |
| GET   | `/api/maintenance/{maintenance_id}`   | `get_maintenance`               | JSON      | Чтение записи обслуживания                 |
| POST  | `/api/maintenance/{maintenance_id}/update` | `update_maintenance`       | JSON      | Обновление обслуживания                    |
| DELETE| `/api/maintenance/{maintenance_id}/delete` | `delete_maintenance`      | JSON      | Удаление обслуживания                      |

---

### Роуты документов (`backend/routers/documents_pages.py`)

| Метод | URL                                     | Функция                    | Тип ответа         | Шаблон / назначение                         |
|-------|------------------------------------------|----------------------------|--------------------|---------------------------------------------|
| GET   | `/documents`                            | `documents_index`          | HTML (template)    | `documents/index.html` – канбан + список    |
| GET   | `/documents/{doc_id}`                   | `document_detail`          | HTML (template)    | `documents/detail.html`                     |
| POST  | `/documents/{doc_id}/update`            | `document_update`          | Redirect           | Обновление заметок и `meta`                 |
| POST  | `/documents/{doc_id}/items/add`         | `document_item_add`        | Redirect           | Добавление строки документа                 |
| POST  | `/documents/items/{item_id}/delete`     | `document_item_delete`     | Redirect           | Удаление строки документа                   |
| GET   | `/documents/reagent-expense/new`        | `reagent_expense_new`      | HTML (template)    | `documents/reagent_expense_new.html`        |
| POST  | `/documents/reagent-expense/create`     | `reagent_expense_create`   | Redirect           | Массовое создание актов расхода             |
| POST  | `/documents/create`                     | `documents_create`         | Redirect           | Создание произвольного документа            |
| POST  | `/documents/{doc_id}/delete`            | `documents_delete`         | Redirect           | Безопасное удаление (черновики)             |
| POST  | `/documents/{doc_id}/soft-delete`       | `documents_soft_delete`    | Redirect           | Мягкое удаление документа                   |
| POST  | `/documents/{doc_id}/reagent-expense/refill` | `reagent_expense_refill`| Redirect       | Перезаполнение строк расхода по событиям    |
| POST  | `/documents/{doc_id}/generate-pdf`      | `document_generate_pdf`    | Redirect/File      | Генерация PDF акта расхода                  |
| POST  | `/documents/{doc_id}/sign`              | `document_sign`            | Redirect           | Подписание документа                         |
| POST  | `/documents/{doc_id}/mark-sent`         | `document_mark_sent`       | Redirect           | Пометить как «отправлен»                    |
| POST  | `/documents/{doc_id}/archive`           | `document_archive`         | Redirect           | Перевести документ в архив                  |
| POST  | `/documents/items/{item_id}/update`     | `document_item_update`     | Redirect           | Редактирование строки документа             |

---

### Роуты актов приёма/передачи скважин (`backend/routers/documents_well_handover.py`)

| Метод | URL                                     | Функция                          | Тип ответа         | Шаблон / назначение                         |
|-------|------------------------------------------|----------------------------------|--------------------|---------------------------------------------|
| GET   | `/documents/well-handover/new`          | `well_handover_new`             | HTML (template)    | `documents/well_handover_new.html`          |
| POST  | `/documents/well-handover/create`       | `well_handover_create`          | Redirect           | Создание акта приёма/передачи               |
| GET   | `/documents/well-handover/{doc_id}`     | `well_handover_detail`          | HTML (template)    | `documents/well_handover_detail.html`       |
| POST  | `/documents/well-handover/{doc_id}/update` | `well_handover_update`       | Redirect           | Обновление полей акта                       |
| POST  | `/documents/well-handover/{doc_id}/rebuild-from-events` | `well_handover_rebuild_from_events` | Redirect | Пересборка давлений по событиям         |
| POST  | `/documents/well-handover/{doc_id}/generate-pdf` | `well_handover_generate_pdf` | Redirect/File      | Генерация PDF акта                          |

---

### Привязка шаблонов и CSS/JS по страницам

- **`auth_base.html`**:  
  - Страницы: `login.html`, `register.html`.  
  - CSS: `static/css/auth.css`.  
  - Без тяжёлых JS, только базовый layout.

- **`base.html`** (почти все остальные страницы):  
  - Страницы: `visual.html`, `well.html`, `admin_panel.html`, `admin_logins.html`, `admin_reagents.html`, `admin_reagents_inventory.html`, `equipment_*.html`, все `documents/*.html`, `admin_unlock.html`.  
  - CSS: `static/css/style.css`, `static/css/tables.css`.  
  - JS по страницам:
    - `visual.html` – Chart.js CDN + `static/js/visual_timeline.js`.  
    - `well.html` – Chart.js CDN + `static/js/well_events_chart.js`.  
    - `admin_reagents.html` – Chart.js CDN + `static/js/reagents.js`.  
    - `admin_logins.html` – Chart.js CDN, inline‑скрипт построения графиков.  

