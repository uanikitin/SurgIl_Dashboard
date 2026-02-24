# Проект интеграции расчёта дебита газа в Surgil Dashboard

**Дата:** 2026-02-16
**Статус:** Проект
**Автор:** Системный анализ на базе SMOD + Surgil Dashboard

---

## 1. Цель

Интегрировать расчёт дебита газа (из проекта SMOD) в существующий Surgil Dashboard так, чтобы:
- Данные читались из PostgreSQL (таблицы `pressure_raw`, `well_construction`)
- Не нарушалась работа уже запущенного приложения
- Модуль дебита можно было разрабатывать и тестировать независимо
- Расчёт дебита, потерь и статистика были доступны через API и UI

---

## 2. Существующая инфраструктура (что уже есть)

### 2.1. Таблицы в PostgreSQL (готовы к использованию)

```
pressure_raw          ← поминутные замеры давления (p_tube, p_line)
├── well_id           INT  → wells.id
├── measured_at       TIMESTAMP (UTC)
├── p_tube            FLOAT  (устьевое давление, кгс/см²)
├── p_line            FLOAT  (линейное давление, кгс/см²)
├── sensor_id_tube    INT
└── sensor_id_line    INT

well_construction     ← параметры скважин (импорт из Excel)
├── well_no           TEXT
├── choke_diam_mm     FLOAT  (диаметр штуцера, мм)
├── tubing_diam_mm    FLOAT
├── horizon           TEXT
└── data_as_of        DATE

wells                 ← справочник скважин
├── id                INT (PK)
├── number            INT
├── name              TEXT
└── current_status    TEXT

pressure_hourly       ← почасовые агрегаты (p_tube_avg, p_line_avg, ...)
pressure_latest       ← последний замер по каждой скважине
```

### 2.2. Существующие паттерны Dashboard

| Паттерн | Реализация |
|---------|------------|
| БД-подключение | `backend/db.py` → `SessionLocal`, `engine`, `get_db()` |
| Модели | `backend/models/*.py` → SQLAlchemy ORM, наследуют `Base` |
| Сервисы | `backend/services/*.py` → бизнес-логика (без HTTP) |
| Роутеры | `backend/routers/*.py` → FastAPI APIRouter |
| Шаблоны | `backend/templates/*.html` → Jinja2 |
| Миграции | `alembic/` → alembic |
| Настройки | `backend/settings.py` → Pydantic Settings + `.env` |

### 2.3. Что НЕ нужно делать (уже решено)

- Парсинг CSV / Excel → данные уже в `pressure_raw`
- Загрузка справочника скважин → таблица `wells` + `well_construction`
- Подключение к БД → `backend/db.py` уже настроен
- Фронтенд-инфраструктура → Chart.js + Jinja2 уже работают

---

## 3. Стратегия интеграции: изолированный модуль

### Принцип: «Подключай, а не переписывай»

```
backend/
├── services/
│   ├── pressure_aggregate_service.py    ← НЕ ТРОГАЕМ
│   ├── pressure_pipeline.py             ← НЕ ТРОГАЕМ
│   │
│   └── flow_rate/                       ← НОВЫЙ МОДУЛЬ
│       ├── __init__.py
│       ├── config.py                    # Константы расчёта
│       ├── calculator.py               # Формулы дебита + потерь
│       ├── cleaning.py                 # Предобработка данных
│       ├── downtime.py                 # Детекция простоев/продувок
│       ├── summary.py                  # Агрегированные показатели
│       └── data_access.py             # SQL-запросы к pressure_raw
│
├── models/
│   ├── flow_rate_result.py              ← НОВАЯ модель (ORM)
│   └── ...                              ← существующие НЕ ТРОГАЕМ
│
├── routers/
│   ├── flow_rate.py                     ← НОВЫЙ роутер (API)
│   └── ...                              ← существующие НЕ ТРОГАЕМ
│
├── templates/
│   ├── flow_rate.html                   ← НОВАЯ страница
│   └── ...                              ← существующие НЕ ТРОГАЕМ
│
└── static/js/
    ├── flow_rate_chart.js               ← НОВЫЙ график
    └── ...                              ← существующие НЕ ТРОГАЕМ
```

**Ключевой принцип:** все изменения — это ДОБАВЛЕНИЕ новых файлов. Единственное изменение в существующих файлах — одна строка в `app.py` для подключения нового роутера.

---

## 4. Новые таблицы БД

### 4.1. flow_rate_results — результаты расчёта дебита

```sql
-- alembic migration
CREATE TABLE flow_rate_results (
    id              SERIAL PRIMARY KEY,
    well_id         INTEGER NOT NULL REFERENCES wells(id),

    -- Период расчёта
    period_start    TIMESTAMPTZ NOT NULL,
    period_end      TIMESTAMPTZ NOT NULL,
    period_label    TEXT,              -- 'base', '1 period', 'adaptive', ...

    -- Дебит
    median_flow_rate      REAL,        -- тыс. м³/сут
    mean_flow_rate        REAL,        -- тыс. м³/сут
    q1_flow_rate          REAL,        -- 25-й процентиль
    q3_flow_rate          REAL,        -- 75-й процентиль
    cumulative_flow       REAL,        -- тыс. м³
    actual_avg_flow       REAL,        -- Q_cum / T_obs, тыс. м³/сут

    -- Простои
    downtime_minutes      INTEGER,
    downtime_hours        REAL,
    downtime_days         REAL,

    -- Потери при продувках
    purge_time_hours      REAL,
    purge_loss_total      REAL,        -- тыс. м³
    purge_loss_daily_avg  REAL,        -- тыс. м³/сут

    -- Потери при простоях
    downtime_loss         REAL,        -- тыс. м³
    downtime_loss_daily   REAL,        -- тыс. м³/сут

    -- Итоговые показатели
    total_loss_daily      REAL,        -- суммарные потери, тыс. м³/сут
    loss_coefficient_pct  REAL,        -- коэфф. потерь, %
    effective_flow_rate   REAL,        -- эффективный дебит, тыс. м³/сут
    forecast_gain_tppav   REAL,        -- прогноз прироста ТППАВ, тыс. м³/сут

    -- Параметры расчёта (для воспроизводимости)
    choke_mm              REAL,
    multiplier            REAL DEFAULT 4.1,
    observation_days      REAL,

    -- Служебные
    calculated_at         TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(well_id, period_start, period_end)
);

CREATE INDEX ix_flow_rate_well ON flow_rate_results(well_id);
CREATE INDEX ix_flow_rate_period ON flow_rate_results(period_start, period_end);
```

### 4.2. flow_rate_downtime_periods — периоды простоев

```sql
CREATE TABLE flow_rate_downtime_periods (
    id              SERIAL PRIMARY KEY,
    result_id       INTEGER NOT NULL REFERENCES flow_rate_results(id) ON DELETE CASCADE,
    well_id         INTEGER NOT NULL REFERENCES wells(id),
    period_start    TIMESTAMPTZ NOT NULL,
    period_end      TIMESTAMPTZ NOT NULL,
    duration_min    REAL,
    is_blowout      BOOLEAN DEFAULT FALSE,   -- TRUE если >30 мин
    purge_loss      REAL                      -- тыс. м³ за этот период
);

CREATE INDEX ix_downtime_well ON flow_rate_downtime_periods(well_id);
```

---

## 5. Модули — детальное описание

### 5.1. `config.py` — Константы расчёта

```python
"""
Константы для расчёта дебита газа.
Вынесены из кода для параметризации и прозрачности.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class FlowRateConfig:
    """Параметры формулы истечения газа через штуцер."""
    C1: float = 2.919           # 4.17 * 0.7 — коэфф. расхода
    C2: float = 4.654           # нормирующий диаметр штуцера, мм
    C3: float = 286.95          # удельная газовая постоянная, Дж/(кг·К)
    multiplier: float = 4.1     # калибровочный множитель
    critical_ratio: float = 0.5 # порог критического истечения


@dataclass(frozen=True)
class PurgeLossConfig:
    """Параметры расчёта потерь при продувках."""
    choke_diameter_m: float = 0.03       # диаметр штуцера продувки, м
    gas_constant: float = 8314           # Дж/(кмоль·К)
    molar_mass: float = 18.0             # кг/кмоль (уточнить по PVT!)
    discharge_coeff: float = 0.9         # коэфф. расхода
    standard_temp_K: float = 273.15      # стандартная температура, К
    atm_pressure_MPa: float = 0.101325   # атмосферное давление, МПа
    p_unit_to_MPa: float = 0.0980665     # коэфф. пересчёта кгс/см² → МПа


@dataclass(frozen=True)
class DowntimeConfig:
    """Параметры детекции простоев."""
    min_blowout_minutes: int = 30   # минимальная длительность продувки


# Экземпляры по умолчанию
DEFAULT_FLOW = FlowRateConfig()
DEFAULT_PURGE = PurgeLossConfig()
DEFAULT_DOWNTIME = DowntimeConfig()
```

### 5.2. `data_access.py` — Чтение из PostgreSQL

```python
"""
Чтение данных давления из PostgreSQL для расчёта дебита.
Использует существующий engine из backend.db — без создания нового подключения.
"""
import pandas as pd
from sqlalchemy import text
from backend.db import engine as pg_engine


def get_pressure_data(
    well_id: int,
    start: str,
    end: str,
) -> pd.DataFrame:
    """
    Читает поминутные данные давления из pressure_raw.

    Returns:
        DataFrame с колонками: [p_tube, p_line], индекс = measured_at
    """
    query = text("""
        SELECT measured_at, p_tube, p_line
        FROM pressure_raw
        WHERE well_id = :well_id
          AND measured_at BETWEEN :start AND :end
          AND (p_tube IS NOT NULL OR p_line IS NOT NULL)
        ORDER BY measured_at
    """)
    with pg_engine.connect() as conn:
        df = pd.read_sql(query, conn, params={
            "well_id": well_id, "start": start, "end": end,
        }, parse_dates=["measured_at"], index_col="measured_at")
    return df


def get_choke_mm(well_id: int) -> float | None:
    """
    Читает диаметр штуцера из well_construction.
    Берёт самую свежую запись (по data_as_of).
    """
    query = text("""
        SELECT wc.choke_diam_mm
        FROM well_construction wc
        JOIN wells w ON w.number::text = wc.well_no
        WHERE w.id = :well_id
          AND wc.choke_diam_mm IS NOT NULL
        ORDER BY wc.data_as_of DESC
        LIMIT 1
    """)
    with pg_engine.connect() as conn:
        row = conn.execute(query, {"well_id": well_id}).fetchone()
    return float(row[0]) if row else None


def get_well_number(well_id: int) -> int | None:
    """Получить номер скважины по id."""
    query = text("SELECT number FROM wells WHERE id = :well_id")
    with pg_engine.connect() as conn:
        row = conn.execute(query, {"well_id": well_id}).fetchone()
    return int(row[0]) if row else None
```

### 5.3. `calculator.py` — Расчёт дебита (ядро)

```python
"""
Векторизованный расчёт дебита газа и потерь при продувках.
Работает с numpy-массивами — ~100x быстрее чем df.apply().
"""
import numpy as np
import pandas as pd

from .config import (
    FlowRateConfig, PurgeLossConfig, DEFAULT_FLOW, DEFAULT_PURGE,
)


def calculate_flow_rate(
    df: pd.DataFrame,
    choke_mm: float,
    cfg: FlowRateConfig = DEFAULT_FLOW,
) -> pd.DataFrame:
    """
    Расчёт мгновенного дебита по каждой строке.

    Ожидает колонки: p_tube, p_line
    Добавляет колонку: flow_rate (тыс. м³/сут)
    """
    wh = df["p_tube"].values.astype(float)
    lp = df["p_line"].values.astype(float)

    # Относительный перепад (защита от деления на 0)
    safe_wh = np.where(wh > 0, wh, 1.0)
    r = np.where(wh > lp, (wh - lp) / safe_wh, 0.0)

    choke_ratio_sq = (choke_mm / cfg.C2) ** 2

    # Докритический режим (r < 0.5)
    q_sub = (
        cfg.C1 * choke_ratio_sq * wh
        * (1 - r / 1.5)
        * np.sqrt(np.maximum(r / cfg.C3, 0.0))
    )

    # Критический режим (r >= 0.5)
    q_crit = (
        0.667 * cfg.C1 * choke_ratio_sq * wh
        * np.sqrt(0.5 / cfg.C3)
    )

    q_base = np.where(r < cfg.critical_ratio, q_sub, q_crit)
    q = np.where(wh > lp, q_base * cfg.multiplier, 0.0)
    q = np.maximum(q, 0.0)

    df = df.copy()
    df["flow_rate"] = q
    return df


def calculate_cumulative(df: pd.DataFrame) -> pd.DataFrame:
    """
    Накопленный дебит методом трапеций.
    Добавляет колонку: cumulative_flow (тыс. м³)
    """
    dt = 1.0 / 1440.0  # 1 минута в сутках
    q = df["flow_rate"].values
    q_prev = np.roll(q, 1)
    q_prev[0] = 0.0
    df = df.copy()
    df["cumulative_flow"] = np.cumsum((q_prev + q) * dt / 2.0)
    return df


def calculate_purge_loss(
    df: pd.DataFrame,
    cfg: PurgeLossConfig = DEFAULT_PURGE,
) -> pd.DataFrame:
    """
    Расчёт потерь газа при продувках (стравливание в атмосферу).
    Добавляет колонки: purge_loss_per_min, purge_flag
    """
    wh = df["p_tube"].values.astype(float)
    lp = df["p_line"].values.astype(float)

    # Пересчёт кгс/см² → МПа
    wh_mpa = wh * cfg.p_unit_to_MPa

    # Флаг продувки: P_wh < P_line
    is_purge = wh < lp

    # Перепад относительно атмосферы (Па)
    delta_p = np.maximum((wh_mpa - cfg.atm_pressure_MPa) * 1e6, 0.0)

    # Площадь сечения штуцера
    area = np.pi * (cfg.choke_diameter_m / 2) ** 2

    # Плотность газа при устьевых условиях (кг/м³)
    rho = np.maximum(
        (wh_mpa * 1e6) * cfg.molar_mass / (cfg.gas_constant * cfg.standard_temp_K),
        1e-6,  # защита от деления на 0
    )

    # Объёмный расход (м³/с) → тыс. м³/мин
    q = cfg.discharge_coeff * area * np.sqrt(2.0 * delta_p / rho)
    loss_per_min = q * 60.0 / 1000.0

    # Обнуляем если нет продувки
    loss_per_min = np.where(is_purge & (delta_p > 0), loss_per_min, 0.0)

    df = df.copy()
    df["purge_flag"] = is_purge.astype(int)
    df["purge_loss_per_min"] = loss_per_min
    df["cumulative_purge_loss"] = np.cumsum(loss_per_min * is_purge)
    return df
```

### 5.4. `cleaning.py` — Предобработка

```python
"""
Предобработка данных давления перед расчётом дебита.
Повторяет логику DataCleaner из SMOD, но адаптировано под структуру Dashboard.
"""
import numpy as np
import pandas as pd
from scipy.signal import savgol_filter


def clean_pressure(df: pd.DataFrame) -> pd.DataFrame:
    """
    Этапы:
    1. Приведение к float
    2. Отрицательные → NaN
    3. Нули → NaN
    4. Forward fill → Backward fill
    """
    df = df.copy()
    for col in ["p_tube", "p_line"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df.loc[df[col] < 0, col] = np.nan
            df.loc[df[col] == 0, col] = np.nan
    df = df.ffill().bfill()
    return df


def smooth_pressure(
    df: pd.DataFrame,
    window: int = 17,
    polyorder: int = 3,
    passes: int = 2,
) -> pd.DataFrame:
    """
    Фильтр Савицкого-Голая (двойной проход по умолчанию).
    Сохраняет оригиналы в p_tube_raw, p_line_raw.
    """
    df = df.copy()
    for col in ["p_tube", "p_line"]:
        if col not in df.columns:
            continue
        df[f"{col}_raw"] = df[col].values.copy()

        series = df[col].values.astype(float)
        n = len(series[~np.isnan(series)])
        if n < polyorder + 2:
            continue

        w = min(window, n)
        if w % 2 == 0:
            w -= 1
        if w < polyorder + 2:
            continue

        # Интерполируем NaN перед фильтрацией
        s = pd.Series(series).interpolate(method="nearest").values

        for _ in range(passes):
            s = savgol_filter(s, window_length=w, polyorder=polyorder)
            s = np.clip(s, 0, None)

        df[col] = s
    return df
```

### 5.5. `downtime.py` — Детекция простоев

```python
"""
Детекция периодов простоя и продувок.
"""
import pandas as pd
from .config import DowntimeConfig, DEFAULT_DOWNTIME


def detect_downtime_periods(
    df: pd.DataFrame,
    cfg: DowntimeConfig = DEFAULT_DOWNTIME,
) -> pd.DataFrame:
    """
    Группировка непрерывных периодов, где p_tube < p_line.

    Returns:
        DataFrame: start, end, duration_min, is_blowout
    """
    mask = df["p_tube"] < df["p_line"]

    periods = []
    start = None

    for i in range(len(df)):
        if mask.iloc[i] and start is None:
            start = df.index[i]
        elif not mask.iloc[i] and start is not None:
            end = df.index[i]
            dur = (end - start).total_seconds() / 60
            periods.append({
                "start": start,
                "end": end,
                "duration_min": dur,
                "is_blowout": dur > cfg.min_blowout_minutes,
            })
            start = None

    # Если последний период не закрыт
    if start is not None:
        end = df.index[-1]
        dur = (end - start).total_seconds() / 60
        periods.append({
            "start": start,
            "end": end,
            "duration_min": dur,
            "is_blowout": dur > cfg.min_blowout_minutes,
        })

    result = pd.DataFrame(periods)

    if not result.empty:
        result["duration_hours"] = result["duration_min"] / 60
        result["interval_hours"] = (
            result["start"].diff().dt.total_seconds() / 3600
        )

    return result
```

### 5.6. `summary.py` — Сводные показатели

```python
"""
Формирование итоговых показателей эффективности скважины.
"""
import pandas as pd
from scipy.signal import medfilt


def build_summary(
    df: pd.DataFrame,
    downtime_periods: pd.DataFrame,
    well_id: int,
    choke_mm: float,
) -> dict:
    """
    Расчёт всех итоговых показателей для одного периода.

    Ожидает в df колонки: flow_rate, cumulative_flow,
    purge_loss_per_min, purge_flag, p_tube, p_line
    """
    # Медианный фильтр для статистики (убрать выбросы)
    filtered = medfilt(df["flow_rate"].values, kernel_size=5)

    T_obs = (df.index.max() - df.index.min()).total_seconds() / 86400

    median_flow = float(pd.Series(filtered).median())
    mean_flow = float(pd.Series(filtered).mean())
    q1_flow = float(pd.Series(filtered).quantile(0.25))
    q3_flow = float(pd.Series(filtered).quantile(0.75))
    cum_flow = float(df["cumulative_flow"].iloc[-1])
    actual_avg = cum_flow / T_obs if T_obs > 0 else 0

    # Простои
    dt_min = int(df["purge_flag"].sum())
    dt_hours = dt_min / 60
    dt_days = dt_hours / 24

    # Потери при простоях
    lost_production = dt_days * median_flow

    # Потери при продувках
    purge_total = float(df["cumulative_purge_loss"].iloc[-1])
    purge_daily = float(df["purge_loss_per_min"].sum()) * 1440 / len(df) if len(df) > 0 else 0

    # Потери при простоях в сутки
    downtime_loss_daily = lost_production / T_obs if T_obs > 0 else 0

    # Суммарные потери
    total_loss_daily = downtime_loss_daily + purge_daily

    # Коэффициент потерь
    loss_pct = (lost_production + purge_total) / cum_flow * 100 if cum_flow > 0 else 0

    # Эффективный дебит
    effective_flow = actual_avg - purge_daily

    # Прогноз ТППАВ
    forecast_gain = q3_flow - median_flow

    # Давления (медианы)
    median_p_tube = float(df["p_tube"].median())
    median_p_line = float(df["p_line"].median())
    median_dp = max(median_p_tube - median_p_line, 0)

    return {
        "well_id": well_id,
        "observation_days": round(T_obs, 2),
        "choke_mm": choke_mm,
        "median_flow_rate": round(median_flow, 3),
        "mean_flow_rate": round(mean_flow, 3),
        "q1_flow_rate": round(q1_flow, 3),
        "q3_flow_rate": round(q3_flow, 3),
        "cumulative_flow": round(cum_flow, 3),
        "actual_avg_flow": round(actual_avg, 3),
        "downtime_minutes": dt_min,
        "downtime_hours": round(dt_hours, 2),
        "downtime_days": round(dt_days, 2),
        "purge_time_hours": round(dt_hours, 2),
        "purge_loss_total": round(purge_total, 4),
        "purge_loss_daily_avg": round(purge_daily, 4),
        "downtime_loss": round(lost_production, 3),
        "downtime_loss_daily": round(downtime_loss_daily, 4),
        "total_loss_daily": round(total_loss_daily, 4),
        "loss_coefficient_pct": round(loss_pct, 2),
        "effective_flow_rate": round(effective_flow, 3),
        "forecast_gain_tppav": round(forecast_gain, 3),
        "median_p_tube": round(median_p_tube, 2),
        "median_p_line": round(median_p_line, 2),
        "median_dp": round(median_dp, 2),
        "blowout_count": int(downtime_periods["is_blowout"].sum()) if not downtime_periods.empty else 0,
        "total_periods": len(downtime_periods),
    }
```

---

## 6. API — Новый роутер

### `backend/routers/flow_rate.py`

```python
"""
/api/flow-rate/* — API расчёта и выдачи дебита газа.

Полностью изолирован от остальных роутеров.
Подключается в app.py одной строкой:
    app.include_router(flow_rate_router)
"""
from fastapi import APIRouter, Query, HTTPException
from datetime import datetime, timedelta
from typing import Optional

router = APIRouter(prefix="/api/flow-rate", tags=["flow-rate"])


@router.get("/calculate/{well_id}")
def calculate_flow_rate(
    well_id: int,
    start: Optional[str] = Query(None, description="ISO: 2025-01-01"),
    end: Optional[str] = Query(None, description="ISO: 2025-02-01"),
    days: int = Query(30, ge=1, le=365),
    smooth: bool = Query(True, description="Применять фильтр Савицкого-Голая"),
    multiplier: float = Query(4.1, description="Калибровочный множитель"),
):
    """
    Полный расчёт дебита для скважины за период.
    Возвращает: summary + временной ряд flow_rate для графика.
    """
    from backend.services.flow_rate.data_access import (
        get_pressure_data, get_choke_mm,
    )
    from backend.services.flow_rate.cleaning import clean_pressure, smooth_pressure
    from backend.services.flow_rate.calculator import (
        calculate_flow_rate as calc_flow,
        calculate_cumulative,
        calculate_purge_loss,
    )
    from backend.services.flow_rate.downtime import detect_downtime_periods
    from backend.services.flow_rate.summary import build_summary
    from backend.services.flow_rate.config import FlowRateConfig

    # Период
    if start and end:
        dt_start, dt_end = start, end
    else:
        dt_end = datetime.utcnow()
        dt_start = dt_end - timedelta(days=days)

    # Данные
    df = get_pressure_data(well_id, str(dt_start), str(dt_end))
    if df.empty:
        raise HTTPException(404, f"Нет данных давления для well_id={well_id}")

    choke = get_choke_mm(well_id)
    if choke is None:
        raise HTTPException(404, f"Штуцер не найден для well_id={well_id}")

    # Предобработка
    df = clean_pressure(df)
    if smooth:
        df = smooth_pressure(df)

    # Расчёт
    cfg = FlowRateConfig(multiplier=multiplier)
    df = calc_flow(df, choke, cfg)
    df = calculate_cumulative(df)
    df = calculate_purge_loss(df)

    # Простои
    periods = detect_downtime_periods(df)

    # Сводка
    summary = build_summary(df, periods, well_id, choke)

    # Временной ряд (прореженный для графика, макс 2000 точек)
    step = max(1, len(df) // 2000)
    chart_df = df.iloc[::step]

    return {
        "summary": summary,
        "chart": {
            "timestamps": chart_df.index.strftime("%Y-%m-%dT%H:%M:%S").tolist(),
            "flow_rate": chart_df["flow_rate"].round(3).tolist(),
            "cumulative_flow": chart_df["cumulative_flow"].round(3).tolist(),
            "p_tube": chart_df["p_tube"].round(2).tolist(),
            "p_line": chart_df["p_line"].round(2).tolist(),
        },
        "downtime_periods": periods.to_dict(orient="records") if not periods.empty else [],
    }


@router.get("/summary/{well_id}")
def get_summary_only(
    well_id: int,
    start: Optional[str] = None,
    end: Optional[str] = None,
    days: int = Query(30, ge=1, le=365),
):
    """Только сводные показатели (без графика). Быстрый endpoint."""
    result = calculate_flow_rate(well_id, start, end, days)
    return result["summary"]


@router.get("/wells")
def list_wells_with_flow_rate(
    days: int = Query(7, ge=1, le=90),
):
    """
    Список всех скважин с кратким расчётом дебита за последние N дней.
    Для таблицы на странице дашборда.
    """
    from sqlalchemy import text
    from backend.db import engine as pg_engine

    with pg_engine.connect() as conn:
        wells = conn.execute(text("""
            SELECT w.id, w.number, w.name, w.current_status
            FROM wells w
            ORDER BY w.number
        """)).fetchall()

    results = []
    for w in wells:
        try:
            result = calculate_flow_rate(
                w[0], days=days, smooth=True, multiplier=4.1,
            )
            results.append({
                "well_id": w[0],
                "well_number": w[1],
                "well_name": w[2],
                "status": w[3],
                **result["summary"],
            })
        except HTTPException:
            results.append({
                "well_id": w[0],
                "well_number": w[1],
                "well_name": w[2],
                "status": w[3],
                "error": "Нет данных",
            })

    return results
```

### Подключение в `app.py` (ЕДИНСТВЕННОЕ изменение в существующем коде)

```python
# Добавить в секцию import:
from backend.routers.flow_rate import router as flow_rate_router

# Добавить после остальных include_router:
app.include_router(flow_rate_router)
```

---

## 7. Блок-схема интеграции

```
┌─────────────────────────────────────────────────────────────┐
│                СУЩЕСТВУЮЩИЙ DASHBOARD                        │
│                                                              │
│  CSV → pressure.db → pressure_raw (PG) → pressure_hourly    │
│         ↑ pipeline уже работает, НЕ ТРОГАЕМ                │
└──────────────────────────┬──────────────────────────────────┘
                           │
                    pressure_raw (PG)
                    well_construction (PG)
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│               НОВЫЙ МОДУЛЬ flow_rate/                        │
│                                                              │
│  ┌──────────────┐   ┌────────────┐   ┌───────────────┐      │
│  │ data_access   │──→│  cleaning   │──→│  calculator    │     │
│  │ (SQL из PG)   │   │ (Savgol)   │   │ (numpy vec.)  │     │
│  └──────────────┘   └────────────┘   └───────┬───────┘      │
│                                               │              │
│                                    ┌──────────┴──────┐       │
│                                    │                 │       │
│                              ┌─────▼─────┐    ┌─────▼─────┐ │
│                              │  downtime   │    │  summary   │ │
│                              │ (периоды)  │    │ (сводка)  │ │
│                              └────────────┘    └───────────┘ │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│               API + UI                                       │
│                                                              │
│  GET /api/flow-rate/calculate/{well_id}?start=&end=          │
│  GET /api/flow-rate/summary/{well_id}                        │
│  GET /api/flow-rate/wells?days=7                             │
│                                                              │
│  flow_rate.html → flow_rate_chart.js (Chart.js)             │
└─────────────────────────────────────────────────────────────┘
```

---

## 8. Граница изменений

### Файлы, которые ИЗМЕНЯЮТСЯ (минимально):

| Файл | Изменение |
|------|-----------|
| `backend/app.py` | +2 строки: import + include_router |

### Файлы, которые СОЗДАЮТСЯ:

| Файл | Назначение |
|------|-----------|
| `backend/services/flow_rate/__init__.py` | Пакет |
| `backend/services/flow_rate/config.py` | Константы |
| `backend/services/flow_rate/data_access.py` | SQL-запросы |
| `backend/services/flow_rate/calculator.py` | Формулы дебита |
| `backend/services/flow_rate/cleaning.py` | Предобработка |
| `backend/services/flow_rate/downtime.py` | Детекция простоев |
| `backend/services/flow_rate/summary.py` | Сводные показатели |
| `backend/routers/flow_rate.py` | API endpoints |
| `backend/models/flow_rate_result.py` | ORM модель (опционально) |
| `backend/templates/flow_rate.html` | UI страница |
| `backend/static/js/flow_rate_chart.js` | Графики |
| `alembic/versions/xxx_add_flow_rate_tables.py` | Миграция |

### Файлы, которые НЕ ТРОГАЕМ:

- `backend/services/pressure_*.py` — весь pipeline давлений
- `backend/routers/pressure.py` — API давлений
- `backend/models/pressure_*.py` — модели давлений
- `backend/templates/well.html`, `visual.html` — существующие страницы
- Всё остальное

---

## 9. План реализации (поэтапный)

### Фаза 1: Ядро расчёта (без UI)
1. Создать `backend/services/flow_rate/` с 6 модулями
2. Написать unit-тесты для `calculator.py`
3. Проверить расчёт на реальных данных из pressure_raw

### Фаза 2: API
4. Создать `backend/routers/flow_rate.py`
5. Добавить 2 строки в `app.py`
6. Проверить API через curl / Swagger UI (`/docs`)

### Фаза 3: Хранение результатов
7. Alembic миграция для `flow_rate_results`
8. Endpoint для сохранения расчёта в БД

### Фаза 4: UI
9. Страница `flow_rate.html` с графиками
10. JavaScript `flow_rate_chart.js`
11. Ссылка в навигации

### Фаза 5: Автоматизация
12. Cron-задача для периодического пересчёта
13. Интеграция с `jobs_api.py`

---

## 10. Ключевые отличия от SMOD

| Аспект | SMOD (текущий) | Dashboard (новый) |
|--------|---------------|-------------------|
| Источник данных | CSV файл + Excel | PostgreSQL (pressure_raw + well_construction) |
| Интерактивность | input() в консоли | REST API + web UI |
| Расчёт | df.apply() (медленно) | numpy vectorized (~100x) |
| Калькуляторы | 3 дублирующих класса | 1 модуль `calculator.py` |
| Константы | зашиты в код | `config.py` (dataclass) |
| Размерности | смешаны кгс/см² и МПа | явный пересчёт через `p_unit_to_MPa` |
| Код | ~2900 строк монолит | ~6 модулей по ~80-120 строк |
| Тестируемость | нет тестов | изолированные модули → pytest |
| Визуализация | Plotly (HTML файлы) | Chart.js (встроен в Dashboard) |

---

## 11. Как запустить разработку не ломая Dashboard

```bash
# 1. Создать feature-ветку
cd /Users/volodymyrnikitin/Documents/PythonFiles/SurgIl_Dashboard
git checkout -b feature/flow-rate

# 2. Создать структуру модуля
mkdir -p backend/services/flow_rate
touch backend/services/flow_rate/__init__.py

# 3. Разрабатывать модули по одному, тестируя через Python REPL:
python -c "
from backend.services.flow_rate.data_access import get_pressure_data
df = get_pressure_data(well_id=1, start='2025-01-01', end='2025-02-01')
print(df.head())
print(df.shape)
"

# 4. Когда ядро готово — добавить роутер и проверить:
# GET http://localhost:8000/api/flow-rate/calculate/1?days=30

# 5. Dashboard продолжает работать на main ветке
# Merge только после полного тестирования
```
