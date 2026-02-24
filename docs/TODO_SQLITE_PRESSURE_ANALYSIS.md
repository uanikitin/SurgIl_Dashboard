# TODO: Разобраться с импортом давлений из SQLite Tracing

**Статус:** Отложено
**Приоритет:** Средний
**Дата анализа:** 2026-02-09

---

## Проблема

CSV и SQLite Tracing показывают **разные значения трубного давления** для одних и тех же каналов.

### Пример расхождения (31.01.2026 10:30:10 UTC+5)

| Канал | CSV Ptr | SQLite Val | Совпадает? |
|-------|---------|------------|------------|
| 1 | 18.0 | Val31=17.90 | ✓ |
| 3 | **0.0** | Val4=**19.33** | ✗ |
| 4 | **3.9** | Val6=**11.66** | ✗ |
| 5 | 33.7 | Val8=33.68 | ✓ |

**Линейное давление (Pshl) совпадает**, трубное (Ptr) — нет.

---

## Структура SQLite Tracing

**Файлы:** `data/lora_sqlite/Trend1.sqlite` — `Trend6.sqlite`

**Таблицы:**
- `TblTrendData` — данные (TS + Val1-Val32)
- `TblTrendConfiguration` — маппинг ColId → имя переменной PLC

**Маппинг колонок (из TblTrendConfiguration):**

```
ColId=1  → Settings.Complex_parametr[1].w_Value_Pshl
ColId=2  → Settings.Complex_parametr[2].w_Value_Prt
ColId=3  → Settings.Complex_parametr[2].w_Value_Pshl
ColId=4  → Settings.Complex_parametr[3].w_Value_Prt
ColId=5  → Settings.Complex_parametr[3].w_Value_Pshl
ColId=6  → Settings.Complex_parametr[4].w_Value_Prt
ColId=7  → Settings.Complex_parametr[4].w_Value_Pshl
ColId=8  → Settings.Complex_parametr[5].w_Value_Prt
ColId=9  → Settings.Complex_parametr[5].w_Value_Pshl
ColId=31 → Settings.Complex_parametr[1].w_Value_Prt
```

---

## Гипотезы

1. **Complex_parametr[N]** в PLC не соответствует каналу N в CSV
2. CSV и SQLite пишут данные с разных датчиков трубного давления
3. Ошибка в конфигурации Tracing на PLC

---

## Что нужно сделать

1. [ ] Уточнить у PLC-инженера: какой датчик записывает `Complex_parametr[4].w_Value_Prt`?
2. [ ] Сравнить конфигурацию CSV-архива и Tracing в CODESYS
3. [ ] Найти правильный маппинг Val → канал CSV
4. [ ] После выяснения — исправить `WELL_COLUMN_MAP` в `pressure_import_sqlite.py`

---

## Текущее решение

**Импорт SQLite отключён.** Используем только CSV как приоритетный источник.

Файл: `backend/services/pressure_pipeline.py` — шаг импорта SQLite пропускается.

---

## Файлы для анализа

- `backend/services/pressure_import_sqlite.py` — импорт SQLite (код сохранён)
- `data/lora_sqlite/Trend1.sqlite` — пример файла для анализа
- SQL запрос для проверки:
  ```sql
  SELECT datetime(TS/1000000, 'unixepoch') as time_utc, Val1, Val6, Val7
  FROM TblTrendData
  WHERE TS BETWEEN 1738304400000000 AND 1738304420000000;
  ```
