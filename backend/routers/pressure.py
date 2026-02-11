"""
/api/pressure/* — API endpoints для данных давлений.

Используется Chart.js на фронте для визуализации.
Данные берутся из pressure_hourly (PostgreSQL) для графиков
и pressure_latest для плиток на дашборде.
+ Админ-страница просмотра всех таблиц.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import text

from backend.db import engine as pg_engine
from backend.deps import get_current_user

router = APIRouter(prefix="/api/pressure", tags=["pressure"])
_templates = Jinja2Templates(directory="backend/templates")

# Часовой пояс Кунграда (Каракалпакстан) — UTC+5
KUNKRAD_OFFSET = timedelta(hours=5)

# Путь к локальному SQLite
_SQLITE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "pressure.db"


_sqlite_checked = False
_sqlite_ok = False


def _sqlite_available() -> bool:
    """True если локальный pressure.db содержит таблицу pressure_readings с данными."""
    global _sqlite_checked, _sqlite_ok
    if _sqlite_checked:
        return _sqlite_ok

    _sqlite_checked = True
    _sqlite_ok = False

    if not _SQLITE_PATH.exists():
        return False

    try:
        import sqlite3
        conn = sqlite3.connect(str(_SQLITE_PATH))
        row = conn.execute("SELECT 1 FROM pressure_readings LIMIT 1").fetchone()
        conn.close()
        _sqlite_ok = row is not None
    except Exception:
        pass

    return _sqlite_ok

# Путь к конфигу расписания
SCHEDULE_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "schedule_config.json"


@router.get("/hourly/{well_id}")
def get_pressure_hourly(
    well_id: int,
    days: int = Query(7, ge=1, le=365),
    start: Optional[str] = Query(None, description="ISO date: 2026-01-01"),
    end: Optional[str] = Query(None, description="ISO date: 2026-02-01"),
):
    """
    Часовые агрегаты давлений для одной скважины.
    Возвращает массив точек для Chart.js (time-series).

    ?days=7         — последние N дней (по умолчанию)
    ?start=&end=    — конкретный диапазон (приоритет над days)
    """
    if start and end:
        try:
            dt_start = datetime.fromisoformat(start)
            dt_end = datetime.fromisoformat(end)
        except ValueError:
            raise HTTPException(400, "Invalid date format. Use ISO: YYYY-MM-DD")
    else:
        dt_end = datetime.utcnow()
        dt_start = dt_end - timedelta(days=days)

    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT hour_start, p_tube_avg, p_tube_min, p_tube_max,
                       p_line_avg, p_line_min, p_line_max, reading_count, has_gaps
                FROM pressure_hourly
                WHERE well_id = :well_id
                    AND hour_start >= :start
                    AND hour_start <= :end
                ORDER BY hour_start
            """),
            {"well_id": well_id, "start": dt_start, "end": dt_end},
        ).fetchall()

    data = []
    for r in rows:
        # Время в базе UTC, конвертируем в Кунградское (+5ч) без timezone суффикса
        t_kungrad = (r[0] + KUNKRAD_OFFSET).strftime("%Y-%m-%dT%H:%M:%S") if r[0] else None
        data.append({
            "t": t_kungrad,
            "p_tube_avg": _r(r[1]),
            "p_tube_min": _r(r[2]),
            "p_tube_max": _r(r[3]),
            "p_line_avg": _r(r[4]),
            "p_line_min": _r(r[5]),
            "p_line_max": _r(r[6]),
            "count": r[7],
            "has_gaps": r[8],
        })

    return {"well_id": well_id, "points": data, "count": len(data), "tz": "UTC+5"}


@router.get("/chart/{well_id}")
def get_pressure_chart(
    well_id: int,
    days: int = Query(7, ge=1, le=365),
    interval: int = Query(15, description="Interval in minutes: 5, 10, 15, 30, 60"),
    # ── Параметры фильтрации сигнала ──
    filter_zeros: bool = Query(False, description="Убрать 0.0 как ложные нули"),
    filter_spikes: bool = Query(False, description="Hampel-фильтр для спайков"),
    fill_mode: str = Query("none", description="Заполнение пропусков: none|ffill|interpolate"),
    max_gap: int = Query(10, ge=1, le=60, description="Макс. пропуск для заполнения (мин)"),
):
    """
    Агрегированные давления с настраиваемым интервалом.

    Локально: данные из SQLite (pressure_readings) — агрегация на лету.
    На сервере (Render): fallback на PostgreSQL (pressure_hourly, 60 мин).

    ?days=7&interval=15  — последние 7 дней, интервал 15 минут

    Фильтрация (опционально, только при наличии SQLite):
    ?filter_zeros=true   — убрать 0.0
    ?filter_spikes=true  — Hampel-фильтр для спайков
    ?fill_mode=ffill     — заполнить пропуски (ffill/interpolate)
    ?max_gap=10          — макс. пропуск для заполнения (мин)
    """
    # Валидация интервала
    allowed_intervals = {1, 2, 5, 10, 15, 30, 60}
    if interval not in allowed_intervals:
        raise HTTPException(400, f"interval must be one of: {sorted(allowed_intervals)}")

    # Валидация fill_mode
    allowed_fill_modes = {"none", "ffill", "interpolate"}
    if fill_mode not in allowed_fill_modes:
        raise HTTPException(400, f"fill_mode must be one of: {sorted(allowed_fill_modes)}")

    # Проверяем наличие локальной SQLite с реальными данными
    if not _sqlite_available():
        return _chart_from_pg(well_id, days, interval)

    # SQLite доступна — используем локальные данные (быстрее, поддержка фильтров)
    try:
        return _chart_from_sqlite(
            well_id, days, interval,
            filter_zeros=filter_zeros,
            filter_spikes=filter_spikes,
            fill_mode=fill_mode,
            max_gap=max_gap,
        )
    except Exception:
        # Любая ошибка SQLite → fallback на PostgreSQL
        return _chart_from_pg(well_id, days, interval)


def _chart_from_sqlite(
    well_id: int, days: int, interval: int,
    filter_zeros: bool, filter_spikes: bool, fill_mode: str, max_gap: int,
) -> dict:
    """График из локального SQLite (быстро, поддержка фильтров)."""
    import sqlite3
    import math

    conn = sqlite3.connect(str(_SQLITE_PATH))
    filters_active = any([filter_zeros, filter_spikes, fill_mode != "none"])
    filter_stats = None

    try:
        if filters_active:
            raw_query = """
                SELECT measured_at, p_tube, p_line
                FROM pressure_readings
                WHERE well_id = ?
                  AND measured_at >= datetime('now', ?)
                ORDER BY measured_at
            """
            raw_rows = conn.execute(raw_query, (well_id, f"-{days} days")).fetchall()

            if not raw_rows:
                return {
                    "well_id": well_id,
                    "interval_min": interval,
                    "points": [],
                    "count": 0,
                    "tz": "UTC+5",
                }

            from backend.services.pressure_filter_service import (
                filter_pressure_pair,
                aggregate_filtered,
            )

            filtered = filter_pressure_pair(
                p_tube=[r[1] for r in raw_rows],
                p_line=[r[2] for r in raw_rows],
                timestamps=[r[0] for r in raw_rows],
                filter_zeros=filter_zeros,
                filter_spikes=filter_spikes,
                fill_mode=fill_mode,
                max_gap_min=max_gap,
            )
            filter_stats = filtered["stats"]

            aggregated = aggregate_filtered(
                p_tube=filtered["p_tube"],
                p_line=filtered["p_line"],
                timestamps=filtered["timestamps"],
                interval_min=interval,
            )

            data = []
            for point in aggregated:
                try:
                    dt_utc = datetime.fromisoformat(point["t"])
                    dt_kungrad = dt_utc + KUNKRAD_OFFSET
                    point["t"] = dt_kungrad.strftime("%Y-%m-%dT%H:%M:%S")
                except (ValueError, TypeError):
                    continue
                data.append(point)

        else:
            if interval == 60:
                bucket_expr = "strftime('%Y-%m-%d %H:00:00', measured_at)"
            else:
                bucket_expr = (
                    "strftime('%Y-%m-%d %H:', measured_at) || "
                    f"printf('%02d', (CAST(strftime('%M', measured_at) AS INTEGER) / {interval}) * {interval}) || ':00'"
                )

            query = f"""
                SELECT
                    {bucket_expr} as bucket,
                    AVG(p_tube) as p_tube_avg,
                    MIN(p_tube) as p_tube_min,
                    MAX(p_tube) as p_tube_max,
                    AVG(p_line) as p_line_avg,
                    MIN(p_line) as p_line_min,
                    MAX(p_line) as p_line_max,
                    COUNT(*) as cnt
                FROM pressure_readings
                WHERE well_id = ?
                  AND measured_at >= datetime('now', ?)
                  AND (p_tube IS NOT NULL OR p_line IS NOT NULL)
                GROUP BY bucket
                ORDER BY bucket
            """

            rows = conn.execute(query, (well_id, f"-{days} days")).fetchall()

            def _safe(v):
                if v is None:
                    return None
                f = float(v)
                if math.isnan(f) or math.isinf(f):
                    return None
                return round(f, 2)

            data = []
            for r in rows:
                if not r[0]:
                    continue
                try:
                    dt_utc = datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S")
                    dt_kungrad = dt_utc + KUNKRAD_OFFSET
                    t_kungrad = dt_kungrad.strftime("%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    continue
                data.append({
                    "t": t_kungrad,
                    "p_tube_avg": _safe(r[1]),
                    "p_tube_min": _safe(r[2]),
                    "p_tube_max": _safe(r[3]),
                    "p_line_avg": _safe(r[4]),
                    "p_line_min": _safe(r[5]),
                    "p_line_max": _safe(r[6]),
                    "count": r[7],
                })
    finally:
        conn.close()

    result = {
        "well_id": well_id,
        "interval_min": interval,
        "points": data,
        "count": len(data),
        "tz": "UTC+5",
    }

    if filter_stats:
        result["filter_stats"] = filter_stats

    return result


def _chart_from_pg(well_id: int, days: int, interval: int = 5) -> dict:
    """
    График из PostgreSQL pressure_raw.
    Используется на Render (где нет локального SQLite).
    Поддерживает произвольный интервал агрегации.

    Если таблица pressure_raw не существует (миграция ещё не прошла),
    падает в fallback на pressure_hourly.
    """
    import math
    from sqlalchemy.exc import ProgrammingError

    dt_end = datetime.utcnow()
    dt_start = dt_end - timedelta(days=days)
    interval_sec = interval * 60

    try:
        with pg_engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT
                        to_timestamp(
                            floor(extract(epoch FROM measured_at) / :isec) * :isec
                        ) AS bucket,
                        AVG(p_tube) AS p_tube_avg,
                        MIN(p_tube) AS p_tube_min,
                        MAX(p_tube) AS p_tube_max,
                        AVG(p_line) AS p_line_avg,
                        MIN(p_line) AS p_line_min,
                        MAX(p_line) AS p_line_max,
                        COUNT(*) AS cnt
                    FROM pressure_raw
                    WHERE well_id = :well_id
                        AND measured_at >= :start
                        AND measured_at <= :end
                        AND (p_tube IS NOT NULL OR p_line IS NOT NULL)
                    GROUP BY bucket
                    ORDER BY bucket
                """),
                {
                    "well_id": well_id,
                    "start": dt_start,
                    "end": dt_end,
                    "isec": interval_sec,
                },
            ).fetchall()
    except ProgrammingError:
        # pressure_raw ещё не создана — fallback на hourly
        return _chart_from_hourly(well_id, days)

    def _safe(v):
        if v is None:
            return None
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, 2)

    data = []
    for r in rows:
        t_kungrad = (r[0] + KUNKRAD_OFFSET).strftime("%Y-%m-%dT%H:%M:%S") if r[0] else None
        data.append({
            "t": t_kungrad,
            "p_tube_avg": _safe(r[1]),
            "p_tube_min": _safe(r[2]),
            "p_tube_max": _safe(r[3]),
            "p_line_avg": _safe(r[4]),
            "p_line_min": _safe(r[5]),
            "p_line_max": _safe(r[6]),
            "count": r[7],
        })

    return {
        "well_id": well_id,
        "interval_min": interval,
        "points": data,
        "count": len(data),
        "tz": "UTC+5",
        "source": "raw_pg",
    }


def _chart_from_hourly(well_id: int, days: int) -> dict:
    """
    Fallback на pressure_hourly (если pressure_raw ещё не создана).
    """
    dt_end = datetime.utcnow()
    dt_start = dt_end - timedelta(days=days)

    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT hour_start, p_tube_avg, p_tube_min, p_tube_max,
                       p_line_avg, p_line_min, p_line_max, reading_count
                FROM pressure_hourly
                WHERE well_id = :well_id
                    AND hour_start >= :start
                    AND hour_start <= :end
                ORDER BY hour_start
            """),
            {"well_id": well_id, "start": dt_start, "end": dt_end},
        ).fetchall()

    data = []
    for r in rows:
        t_k = (r[0] + KUNKRAD_OFFSET).strftime("%Y-%m-%dT%H:%M:%S") if r[0] else None
        data.append({
            "t": t_k,
            "p_tube_avg": _r(r[1]),
            "p_tube_min": _r(r[2]),
            "p_tube_max": _r(r[3]),
            "p_line_avg": _r(r[4]),
            "p_line_min": _r(r[5]),
            "p_line_max": _r(r[6]),
            "count": r[7],
        })

    return {
        "well_id": well_id,
        "interval_min": 60,
        "points": data,
        "count": len(data),
        "tz": "UTC+5",
        "source": "hourly",
    }


@router.get("/raw_nearby/{well_id}")
def get_pressure_raw_nearby(
    well_id: int,
    t: str = Query(..., description="Время точки клика, ISO формат, UTC+5"),
    n: int = Query(5, ge=1, le=20, description="Количество строк до и после"),
):
    """
    Возвращает ±N сырых строк из pressure_readings вокруг указанного момента.
    Время на входе — UTC+5 (от графика), конвертируется в UTC для запроса.

    Ответ: { well_id, center, rows: [{measured_at, p_tube, p_line}, ...] }
    Время в ответе — UTC+5.

    Требует локальный SQLite. На Render возвращает пустой массив.
    """
    import sqlite3

    # Парсим входное время (UTC+5) и конвертируем в UTC
    try:
        dt_local = datetime.fromisoformat(t)
    except ValueError:
        raise HTTPException(400, f"Invalid datetime format: {t}")

    dt_utc = dt_local - KUNKRAD_OFFSET
    center_utc_str = dt_utc.strftime("%Y-%m-%d %H:%M:%S")

    if not _sqlite_available():
        return {"well_id": well_id, "center": t, "rows": [], "source": "no_sqlite"}

    conn = sqlite3.connect(str(_SQLITE_PATH))

    try:
        # N+1 строк до (включая центральную) + N строк после
        query = """
            SELECT measured_at, p_tube, p_line FROM (
                SELECT measured_at, p_tube, p_line
                FROM pressure_readings
                WHERE well_id = ? AND measured_at <= ?
                ORDER BY measured_at DESC
                LIMIT ?
            )
            UNION ALL
            SELECT measured_at, p_tube, p_line FROM (
                SELECT measured_at, p_tube, p_line
                FROM pressure_readings
                WHERE well_id = ? AND measured_at > ?
                ORDER BY measured_at ASC
                LIMIT ?
            )
            ORDER BY measured_at
        """
        rows = conn.execute(query, (
            well_id, center_utc_str, n + 1,
            well_id, center_utc_str, n,
        )).fetchall()
    finally:
        conn.close()

    # Конвертируем UTC → UTC+5 для ответа
    result_rows = []
    for r in rows:
        try:
            dt = datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            # Может быть формат с микросекундами
            try:
                dt = datetime.strptime(r[0][:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
        t_local = dt + KUNKRAD_OFFSET
        result_rows.append({
            "measured_at": t_local.strftime("%Y-%m-%d %H:%M:%S"),
            "p_tube": round(float(r[1]), 3) if r[1] is not None else None,
            "p_line": round(float(r[2]), 3) if r[2] is not None else None,
        })

    return {
        "well_id": well_id,
        "center": t,
        "rows": result_rows,
    }


@router.get("/latest")
def get_pressure_latest():
    """
    Последние давления по всем скважинам.
    Используется для плиток на дашборде.
    """
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT pl.well_id, w.name, w.number,
                       pl.p_tube, pl.p_line, pl.measured_at, pl.updated_at
                FROM pressure_latest pl
                JOIN wells w ON w.id = pl.well_id
                ORDER BY pl.well_id
            """)
        ).fetchall()

    data = []
    for r in rows:
        data.append({
            "well_id": r[0],
            "well_name": r[1],
            "well_number": r[2],
            "p_tube": _r(r[3]),
            "p_line": _r(r[4]),
            "measured_at": r[5].isoformat() if r[5] else None,
            "updated_at": r[6].isoformat() if r[6] else None,
        })

    return {"wells": data}


@router.get("/latest/{well_id}")
def get_pressure_latest_well(well_id: int):
    """Последние давления для одной скважины."""
    with pg_engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT pl.well_id, pl.p_tube, pl.p_line, pl.measured_at
                FROM pressure_latest pl
                WHERE pl.well_id = :well_id
            """),
            {"well_id": well_id},
        ).fetchone()

    if not row:
        return {"well_id": well_id, "p_tube": None, "p_line": None, "measured_at": None}

    return {
        "well_id": row[0],
        "p_tube": _r(row[1]),
        "p_line": _r(row[2]),
        "measured_at": row[3].isoformat() if row[3] else None,
    }


@router.get("/stats/{well_id}")
def get_pressure_stats(well_id: int, days: int = Query(30, ge=1, le=365)):
    """Статистика давлений за период (мин/макс/среднее)."""
    dt_start = datetime.utcnow() - timedelta(days=days)

    with pg_engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT
                    AVG(p_tube_avg) as tube_avg,
                    MIN(p_tube_min) as tube_min,
                    MAX(p_tube_max) as tube_max,
                    AVG(p_line_avg) as line_avg,
                    MIN(p_line_min) as line_min,
                    MAX(p_line_max) as line_max,
                    SUM(reading_count) as total_readings,
                    COUNT(*) as total_hours
                FROM pressure_hourly
                WHERE well_id = :well_id AND hour_start >= :start
            """),
            {"well_id": well_id, "start": dt_start},
        ).fetchone()

    if not row or row[7] == 0:
        return {"well_id": well_id, "days": days, "data": None}

    return {
        "well_id": well_id,
        "days": days,
        "data": {
            "p_tube": {"avg": _r(row[0]), "min": _r(row[1]), "max": _r(row[2])},
            "p_line": {"avg": _r(row[3]), "min": _r(row[4]), "max": _r(row[5])},
            "total_readings": row[6],
            "total_hours": row[7],
        },
    }


def _r(val, decimals=2):
    import math
    if val is None:
        return None
    f = float(val)
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, decimals)


# ═══════════════════════════════════════════════════════════
# Управление расписанием автообновления
# ═══════════════════════════════════════════════════════════


class ScheduleUpdate(BaseModel):
    enabled: Optional[bool] = None
    day_start: Optional[str] = None    # "07:00"
    day_end: Optional[str] = None      # "22:00"
    day_interval: Optional[int] = None  # минуты
    night_interval: Optional[int] = None  # минуты


def _read_schedule() -> dict:
    """Читает schedule_config.json."""
    if not SCHEDULE_CONFIG_PATH.exists():
        return {
            "enabled": False,
            "day": {"start": "07:00", "end": "22:00", "interval_min": 5},
            "night": {"interval_min": 30},
            "last_run": None,
        }
    with open(SCHEDULE_CONFIG_PATH) as f:
        return json.load(f)


def _write_schedule(config: dict) -> None:
    """Сохраняет schedule_config.json."""
    SCHEDULE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SCHEDULE_CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


@router.get("/schedule")
def get_schedule(current_user: str = Depends(get_current_user)):
    """Текущее расписание автообновления."""
    config = _read_schedule()
    # Добавляем статус launchd
    config["launchd_plist"] = str(
        Path.home() / "Library/LaunchAgents/com.surgil.pressure-update.plist"
    )
    return config


@router.post("/schedule")
def update_schedule(
    body: ScheduleUpdate,
    current_user: str = Depends(get_current_user),
):
    """
    Обновить расписание автообновления.
    Можно менять отдельные поля (partial update).
    """
    config = _read_schedule()

    if body.enabled is not None:
        config["enabled"] = body.enabled
    if body.day_start is not None:
        # Валидация формата HH:MM
        try:
            datetime.strptime(body.day_start, "%H:%M")
        except ValueError:
            raise HTTPException(400, "day_start must be HH:MM format")
        config["day"]["start"] = body.day_start
    if body.day_end is not None:
        try:
            datetime.strptime(body.day_end, "%H:%M")
        except ValueError:
            raise HTTPException(400, "day_end must be HH:MM format")
        config["day"]["end"] = body.day_end
    if body.day_interval is not None:
        if body.day_interval < 1 or body.day_interval > 120:
            raise HTTPException(400, "day_interval must be 1-120 minutes")
        config["day"]["interval_min"] = body.day_interval
    if body.night_interval is not None:
        if body.night_interval < 1 or body.night_interval > 120:
            raise HTTPException(400, "night_interval must be 1-120 minutes")
        config["night"]["interval_min"] = body.night_interval

    _write_schedule(config)
    return {"status": "ok", "config": config}


# ═══════════════════════════════════════════════════════════
# Ручное обновление (refresh)
# ═══════════════════════════════════════════════════════════

_refresh_running = False


@router.post("/refresh")
def pressure_refresh(
    skip_sync: bool = Query(False, description="Пропустить скачивание с Pi"),
    current_user: str = Depends(get_current_user),
):
    """
    Запустить пайплайн обновления давлений.
    POST /api/pressure/refresh?skip_sync=false
    """
    global _refresh_running
    if _refresh_running:
        return {"status": "already_running", "message": "Обновление уже запущено"}

    _refresh_running = True
    try:
        from backend.services.pressure_pipeline import run_pipeline
        result = run_pipeline(skip_sync=skip_sync)
        return {"status": "ok", "result": result}
    finally:
        _refresh_running = False


@router.get("/refresh/status")
def pressure_refresh_status(current_user: str = Depends(get_current_user)):
    """Проверить, запущено ли обновление."""
    return {"running": _refresh_running}


# ═══════════════════════════════════════════════════════════
# Админ-страница: просмотр всех таблиц давлений
# ═══════════════════════════════════════════════════════════

@router.get("/admin/page", response_class=HTMLResponse)
def pressure_admin_page(request: Request, current_user: str = Depends(get_current_user)):
    """HTML-страница для просмотра таблиц давлений."""
    return _templates.TemplateResponse("pressure_admin.html", {"request": request})


@router.get("/admin/overview")
def admin_overview(current_user: str = Depends(get_current_user)):
    """Обзор: статистика + все скважины с давлениями."""
    from backend.db_pressure import PressureSessionLocal, init_pressure_db
    init_pressure_db()

    # Из локального SQLite (если есть)
    if _sqlite_available():
        sqlite_db = PressureSessionLocal()
        try:
            total_readings = sqlite_db.execute(
                text("SELECT COUNT(*) FROM pressure_readings")
            ).scalar() or 0

            csv_count = sqlite_db.execute(
                text("SELECT COUNT(*) FROM csv_import_log WHERE status = 'imported'")
            ).scalar() or 0
        finally:
            sqlite_db.close()
    else:
        total_readings = 0
        csv_count = 0

    # Из PostgreSQL
    with pg_engine.connect() as conn:
        total_hourly = conn.execute(
            text("SELECT COUNT(*) FROM pressure_hourly")
        ).scalar() or 0

        active_sensors = conn.execute(
            text("""
                SELECT COUNT(DISTINCT ls.id)
                FROM lora_sensors ls
                JOIN equipment e ON e.serial_number = ls.serial_number
                JOIN equipment_installation ei ON ei.equipment_id = e.id AND ei.removed_at IS NULL
            """)
        ).scalar() or 0

        rows = conn.execute(
            text("""
                SELECT pl.well_id, w.name, w.number,
                       pl.p_tube, pl.p_line, pl.measured_at,
                       sub.csv_group, sub.csv_channel
                FROM pressure_latest pl
                JOIN wells w ON w.id = pl.well_id
                LEFT JOIN LATERAL (
                    SELECT DISTINCT ls.csv_group, ls.csv_channel
                    FROM equipment_installation ei
                    JOIN equipment e ON e.id = ei.equipment_id
                    JOIN lora_sensors ls ON ls.serial_number = e.serial_number
                    WHERE ei.well_id = pl.well_id AND ei.removed_at IS NULL
                    LIMIT 1
                ) sub ON true
                ORDER BY w.number
            """)
        ).fetchall()

    wells = []
    for r in rows:
        ch_label = f"Гр{r[6]}/К{r[7]}" if r[6] and r[7] else None
        wells.append({
            "well_id": r[0],
            "well_name": r[1],
            "well_number": r[2],
            "p_tube": _r(r[3]),
            "p_line": _r(r[4]),
            "measured_at": r[5].isoformat() if r[5] else None,
            "channel": ch_label,
        })

    return {
        "total_wells": len(wells),
        "total_readings": total_readings,
        "total_hourly": total_hourly,
        "csv_files_imported": csv_count,
        "active_sensors": active_sensors,
        "wells": wells,
    }


@router.get("/admin/channels")
def admin_channels(current_user: str = Depends(get_current_user)):
    """Привязка датчиков к скважинам через equipment_installation."""
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT ei.id, ls.csv_group, ls.csv_channel, ls.csv_column,
                       ls.serial_number, ei.well_id, w.name, w.number,
                       ei.installed_at, ei.removed_at, ei.notes
                FROM equipment_installation ei
                JOIN equipment e ON e.id = ei.equipment_id
                JOIN lora_sensors ls ON ls.serial_number = e.serial_number
                JOIN wells w ON w.id = ei.well_id
                ORDER BY ls.csv_group, ls.csv_channel, ei.installed_at
            """)
        ).fetchall()

    return [
        {
            "id": r[0],
            "csv_group": r[1],
            "csv_channel": r[2],
            "csv_column": r[3],
            "serial_number": r[4],
            "well_id": r[5],
            "well_name": r[6],
            "well_number": r[7],
            "installed_at": r[8].isoformat() if r[8] else None,
            "removed_at": r[9].isoformat() if r[9] else None,
            "note": r[10],
        }
        for r in rows
    ]


@router.get("/admin/latest")
def admin_latest(current_user: str = Depends(get_current_user)):
    """Содержимое pressure_latest."""
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT pl.well_id, w.name, pl.p_tube, pl.p_line,
                       pl.measured_at, pl.updated_at
                FROM pressure_latest pl
                JOIN wells w ON w.id = pl.well_id
                ORDER BY pl.well_id
            """)
        ).fetchall()

    return [
        {
            "well_id": r[0],
            "well_name": r[1],
            "p_tube": _r(r[2]),
            "p_line": _r(r[3]),
            "measured_at": r[4].isoformat() if r[4] else None,
            "updated_at": r[5].isoformat() if r[5] else None,
        }
        for r in rows
    ]


@router.get("/admin/readings")
def admin_readings(
    well_id: Optional[int] = Query(None),
    source: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=5000),
    current_user: str = Depends(get_current_user),
):
    """Сырые записи из pressure.db (локальный SQLite). На Render — пусто."""
    if not _sqlite_available():
        return {"total": 0, "rows": [], "source": "no_sqlite"}

    from backend.db_pressure import PressureSessionLocal, init_pressure_db
    init_pressure_db()

    conditions = []
    params = {}
    if well_id:
        conditions.append("well_id = :well_id")
        params["well_id"] = well_id
    if source:
        conditions.append("source = :source")
        params["source"] = source

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sqlite_db = PressureSessionLocal()
    try:
        total = sqlite_db.execute(
            text(f"SELECT COUNT(*) FROM pressure_readings {where}"),
            params,
        ).scalar() or 0

        rows = sqlite_db.execute(
            text(
                f"SELECT id, well_id, channel, measured_at, p_tube, p_line, "
                f"source, source_file FROM pressure_readings {where} "
                f"ORDER BY measured_at DESC LIMIT :lim"
            ),
            {**params, "lim": limit},
        ).fetchall()
    finally:
        sqlite_db.close()

    return {
        "total": total,
        "rows": [
            {
                "id": r[0],
                "well_id": r[1],
                "channel": r[2],
                "measured_at": r[3],
                "p_tube": _r(r[4]),
                "p_line": _r(r[5]),
                "source": r[6],
                "source_file": r[7],
            }
            for r in rows
        ],
    }


@router.get("/admin/csv_log")
def admin_csv_log(current_user: str = Depends(get_current_user)):
    """Журнал импорта CSV. На Render — пусто."""
    if not _sqlite_available():
        return []

    from backend.db_pressure import PressureSessionLocal, init_pressure_db
    init_pressure_db()

    sqlite_db = PressureSessionLocal()
    try:
        rows = sqlite_db.execute(
            text(
                "SELECT filename, status, rows_imported, rows_skipped, "
                "file_sha256, imported_at FROM csv_import_log "
                "ORDER BY imported_at DESC"
            )
        ).fetchall()
    finally:
        sqlite_db.close()

    return [
        {
            "filename": r[0],
            "status": r[1],
            "rows_imported": r[2],
            "rows_skipped": r[3],
            "file_sha256": r[4],
            "imported_at": r[5],
        }
        for r in rows
    ]


@router.get("/admin/sensors")
def admin_sensors(current_user: str = Depends(get_current_user)):
    """Все LoRa-датчики с привязкой через equipment_installation."""
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT ls.id, ls.serial_number, ls.csv_group, ls.csv_channel,
                       ls.csv_column, ls.label, ls.note,
                       ei.well_id, w.name AS well_name, w.number AS well_number
                FROM lora_sensors ls
                LEFT JOIN equipment e ON e.serial_number = ls.serial_number
                LEFT JOIN equipment_installation ei
                    ON ei.equipment_id = e.id AND ei.removed_at IS NULL
                LEFT JOIN wells w ON w.id = ei.well_id
                ORDER BY ls.csv_group, ls.csv_channel, ls.csv_column
            """)
        ).fetchall()

    return [
        {
            "id": r[0],
            "serial_number": r[1],
            "csv_group": r[2],
            "csv_channel": r[3],
            "csv_column": r[4],
            "position": "tube" if r[4] == "Ptr" else "line",
            "position_ru": "устье" if r[4] == "Ptr" else "шлейф",
            "label": r[5],
            "note": r[6],
            "well_id": r[7],
            "well_name": r[8],
            "well_number": r[9],
        }
        for r in rows
    ]
