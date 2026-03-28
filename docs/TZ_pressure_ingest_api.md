# ТЗ: API endpoint POST /api/pressure/ingest

## Цель

Убрать зависимость обновления данных давлений от MacBook.
Raspberry Pi сам отправляет CSV на Render каждые 5 минут.

## Текущая схема (проблемная)

```
Pi (CoDeSys) → HTTP :2224 → [MacBook + ZeroTier] → SQLite → PostgreSQL (Render)
                                    ^
                         Выключили — данные встали
```

## Новая схема

```
Pi (CoDeSys, cron) → curl POST → Render /api/pressure/ingest → PostgreSQL
```

Ноль промежуточных устройств. MacBook не нужен.

---

## 1. Endpoint

**Метод:** `POST /api/pressure/ingest`

**Расположение:** `backend/routers/pressure.py`

**Принимает:** multipart/form-data с одним CSV файлом

**Авторизация:** заголовок `X-Job-Secret` (тот же механизм что в `jobs_api.py`)

### Запрос

```bash
curl -s -X POST https://<app>.onrender.com/api/pressure/ingest \
  -H "X-Job-Secret: <JOB_API_SECRET>" \
  -F "file=@/path/to/26.03.2026.1_arc.csv"
```

### Ответ (200 OK)

```json
{
  "status": "ok",
  "filename": "26.03.2026.1_arc.csv",
  "rows_imported": 42,
  "affected_wells": [3, 7],
  "duration_sec": 1.2
}
```

### Ошибки

- `401` — неверный или отсутствующий `X-Job-Secret`
- `400` — имя файла не соответствует формату `DD.MM.YYYY.{group}_arc.csv`
- `422` — CSV не читается (битый файл, неправильный формат)

---

## 2. Что endpoint делает внутри

Пошагово:

### Шаг А. Авторизация
Проверить `X-Job-Secret == settings.JOB_API_SECRET`. Переиспользовать `verify_job_secret()` из `jobs_api.py`.

### Шаг Б. Валидация имени файла
Имя файла (`file.filename`) должно соответствовать regex `^\d{1,2}\.\d{1,2}\.\d{4}\.\d+_arc\.csv$`.

### Шаг В. Сохранить CSV во временный файл
Записать содержимое в `/tmp/{filename}`. Удалить после обработки.

### Шаг Г. Импорт CSV → SQLite (pressure.db)
Вызвать существующую функцию:
```python
from backend.services.pressure_import_csv import (
    import_csv_file, _load_sensor_cache, _load_installation_cache
)
from backend.db_pressure import PressureSessionLocal, init_pressure_db

init_pressure_db()
sensor_cache = _load_sensor_cache()
installation_cache = _load_installation_cache()
db = PressureSessionLocal()
result = import_csv_file(tmp_path, db, sensor_cache, installation_cache)
```

### Шаг Д. Агрегация → PostgreSQL
Если `result["affected_wells"]` не пусто:
```python
from backend.services.pressure_aggregate_service import (
    aggregate_to_hourly, sync_raw_to_pg, update_latest
)
aggregate_to_hourly(since=result["first_ts"], well_ids=affected)
sync_raw_to_pg(since=result["first_ts"], well_ids=affected)
update_latest(well_ids=affected)
```

### Шаг Е. Очистка
Удалить временный файл. Закрыть SQLite-сессию.

---

## 3. Что переиспользуется из существующего кода

| Функция | Файл | Без изменений? |
|---|---|---|
| `import_csv_file()` | `pressure_import_csv.py` | Да |
| `_load_sensor_cache()` | `pressure_import_csv.py` | Да |
| `_load_installation_cache()` | `pressure_import_csv.py` | Да |
| `aggregate_to_hourly()` | `pressure_aggregate_service.py` | Да |
| `sync_raw_to_pg()` | `pressure_aggregate_service.py` | Да |
| `update_latest()` | `pressure_aggregate_service.py` | Да |
| `verify_job_secret()` | `jobs_api.py` | Да |
| `init_pressure_db()` | `db_pressure.py` | Да |
| `CsvImportLog` | `models/csv_import_log.py` | Да |

**Новый код:** только сам endpoint (~60-80 строк).

---

## 4. Инкрементальность (защита от дублей)

Уже решено в существующем коде, три уровня:

1. **CsvImportLog** — файл с тем же sha256 пропускается. Если файл дописался — обрабатываются только новые строки (`tail_offset`).
2. **`INSERT OR IGNORE`** в SQLite — дубли по `(well_id, measured_at)` игнорируются.
3. **`ON CONFLICT DO UPDATE`** в PostgreSQL — upsert, безопасная перезапись.

Можно отправлять один и тот же файл сколько угодно раз — данные не задублируются.

---

## 5. SQLite на Render

`pressure.db` хранится в `/data/pressure.db` (или `/tmp`). На Render файловая система ephemeral — при рестарте SQLite теряется.

**Это НЕ проблема:**
- SQLite — промежуточный буфер, данные сразу уходят в PostgreSQL
- При следующем запросе `init_pressure_db()` создаст пустую БД
- `CsvImportLog` будет пуст → файл обработается заново → `INSERT OR IGNORE` в SQLite + `ON CONFLICT` в PostgreSQL не создадут дублей

Нужно: добавить env-переменную `PRESSURE_DB_PATH` в `db_pressure.py` чтобы путь был настраиваем (по умолчанию `/tmp/pressure.db` на Render).

---

## 6. Потенциальные проблемы

| Проблема | Решение |
|---|---|
| Два curl одновременно — SQLite locked | Pi отправляет файлы последовательно (цикл, не параллельно) + `threading.Lock()` в endpoint |
| Render free tier засыпает | curl с `--max-time 60` — первый запрос разбудит сервер |
| Pi нет интернета | curl молча фейлит, следующий cron попробует снова |
| Файл пустой или битый | `pd.read_csv()` кинет исключение → endpoint вернёт 422 |

---

## 7. Порядок реализации

1. Добавить `PRESSURE_DB_PATH` в `backend/settings.py` + использовать в `db_pressure.py`
2. Написать endpoint `POST /api/pressure/ingest` в `backend/routers/pressure.py`
3. Протестировать локально (curl с реальным CSV)
4. Задеплоить на Render
5. Настроить crontab на Pi (см. отдельную инструкцию)
6. Выключить MacBook-пайплайн (`schedule_config.json` → `enabled: false`)
7. Подождать 24 часа, проверить данные на дашборде
