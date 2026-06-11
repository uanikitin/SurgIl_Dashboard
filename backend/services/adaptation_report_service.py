"""
Сервис генерации отчёта об адаптации скважины.

Отчёт сравнивает два этапа работы скважины:
  - Наблюдение (должен быть закрыт)
  - Адаптация (может быть открыт — тогда берётся date.today())

Этап 1 (текущий): сбор данных, валидация, расчёт дельт, автовыводы.
Этапы 2-4: роутер, HTML-форма, LaTeX-шаблон, графики.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models.wells import Well
from backend.services.daily_report_service import (
    KUNGRAD_OFFSET,
    TEMP_DIR,
    _load_masked_hourly,
    _compute_month_flow_from_raw,
    _ensure_dirs,
    _robust_trend,
)

log = logging.getLogger(__name__)


STATUS_OBSERVATION = "Наблюдение"
STATUS_ADAPTATION = "Адаптация"

# Дефолтная длительность этапа адаптации при автодетекте (от первого вброса)
DEFAULT_ADAPT_DURATION_DAYS = 10


# ═══════════════════════════════════════════════════════════════════
# Обогащение событий данными из БД
# ═══════════════════════════════════════════════════════════════════

def _enrich_events_from_db(
    db: Session,
    events: list[dict],
    well_number: str,
) -> list[dict]:
    """Обогатить события данными из таблицы events.

    Для событий из snapshot, которые не содержат p_tube, p_line, reagent, qty,
    description — подтягиваем их из БД по времени события.

    Args:
        db: SQLAlchemy session
        events: список событий (из snapshot.adaptation.events_for_chart)
        well_number: номер скважины (строка)

    Returns:
        Обогащённый список событий (мутирует in-place и возвращает)
    """
    if not events:
        return events

    # Собираем все времена событий для одного запроса
    event_times = []
    for ev in events:
        t = ev.get("t") or ev.get("ts") or ev.get("event_time")
        if t:
            # Время в snapshot — в Kungrad-local (ISO string)
            event_times.append(str(t)[:19])  # YYYY-MM-DDTHH:MM:SS

    if not event_times:
        return events

    # Загружаем все события из БД для этой скважины и времён
    try:
        rows = db.execute(text("""
            SELECT
                TO_CHAR(event_time, 'YYYY-MM-DD"T"HH24:MI') as t,
                event_type, reagent, qty, description, p_tube, p_line
            FROM events
            WHERE well = :wno
              AND event_type IN ('purge', 'reagent', 'equip', 'other')
        """), {"wno": well_number}).fetchall()

        # Индексируем по времени (первые 16 символов: YYYY-MM-DDTHH:MM)
        db_events = {}
        for r in rows:
            key = str(r[0])[:16] if r[0] else None
            if key:
                db_events[key] = {
                    "event_type": r[1],
                    "reagent": r[2],
                    "qty": float(r[3]) if r[3] else None,
                    "description": r[4],
                    "p_tube": float(r[5]) if r[5] else None,
                    "p_line": float(r[6]) if r[6] else None,
                }

        # Обогащаем события
        for ev in events:
            t = ev.get("t") or ev.get("ts") or ev.get("event_time")
            if not t:
                continue
            key = str(t)[:16]
            if key in db_events:
                db_ev = db_events[key]
                # Добавляем только отсутствующие поля
                if ev.get("reagent") is None or ev.get("reagent") == "":
                    ev["reagent"] = db_ev.get("reagent") or ""
                if ev.get("qty") is None:
                    ev["qty"] = db_ev.get("qty")
                    ev["amount"] = db_ev.get("qty")
                if ev.get("description") is None or ev.get("description") == "":
                    ev["description"] = db_ev.get("description") or ""
                if ev.get("p_tube") is None:
                    ev["p_tube"] = db_ev.get("p_tube")
                if ev.get("p_line") is None:
                    ev["p_line"] = db_ev.get("p_line")
                # Обновляем label если пустой
                if not ev.get("label"):
                    ev["label"] = ev.get("reagent") or ev.get("description") or ""
    except Exception as e:
        log.warning("_enrich_events_from_db failed: %s", e)

    return events


# ═══════════════════════════════════════════════════════════════════
# Нормализация дат/времени
# ═══════════════════════════════════════════════════════════════════

def _to_dt(value, end_of_day: bool = False) -> datetime:
    """Привести date/datetime к datetime.

    - datetime → возвращаем как есть
    - date → combine с 00:00:00 (начало) или 23:59:59 (конец)
    - str (ISO) → парсим
    """
    if isinstance(value, datetime):
        # Убираем tzinfo если есть (работаем с naive Kungrad-local)
        if value.tzinfo is not None:
            value = value.replace(tzinfo=None)
        return value
    if isinstance(value, date):
        t = time(23, 59, 59) if end_of_day else time(0, 0)
        return datetime.combine(value, t)
    if isinstance(value, str):
        # Поддерживаем ISO: 'YYYY-MM-DD' или 'YYYY-MM-DDTHH:MM' или 'YYYY-MM-DDTHH:MM:SS'
        s = value.strip()
        if "T" in s or " " in s:
            return datetime.fromisoformat(s.replace(" ", "T"))
        d = date.fromisoformat(s)
        t = time(23, 59, 59) if end_of_day else time(0, 0)
        return datetime.combine(d, t)
    raise TypeError(f"Unsupported datetime value: {value!r}")


def _duration_hours(dt_from: datetime, dt_to: datetime) -> float:
    """Длительность в часах (может быть дробной)."""
    return max((dt_to - dt_from).total_seconds() / 3600.0, 0)


def _duration_days_str(dt_from: datetime, dt_to: datetime) -> str:
    """Человеко-читаемая длительность: '3 сут.' или '1 сут. 12 ч'."""
    hours = _duration_hours(dt_from, dt_to)
    days = int(hours // 24)
    rem_h = int(round(hours - days * 24))
    if rem_h == 24:
        days += 1
        rem_h = 0
    if days == 0:
        return f"{rem_h} ч"
    if rem_h == 0:
        return f"{days} сут."
    return f"{days} сут. {rem_h} ч"


# ═══════════════════════════════════════════════════════════════════
# Автодетект этапов по журналу событий
# ═══════════════════════════════════════════════════════════════════

def suggest_stages_from_events(
    db: Session, well_id: int,
    default_adapt_days: int = DEFAULT_ADAPT_DURATION_DAYS,
) -> dict[str, Any]:
    """Предложить даты этапов по событиям:

    - Этап наблюдения: от первого события установки оборудования
      (event_type='equip') до дня перед первым вбросом реагента.
    - Этап адаптации: от первого вброса реагента, длительностью
      default_adapt_days суток (но не позже сегодняшнего дня).

    Возвращает dict с ключами obs_from/obs_to/adapt_from/adapt_to (date | None)
    и rationale (краткое текстовое обоснование каждого источника).
    """
    well_no_row = db.execute(text(
        "SELECT number FROM wells WHERE id = :wid"
    ), {"wid": well_id}).fetchone()
    if not well_no_row:
        return {"ok": False, "error": f"Скважина id={well_id} не найдена"}
    well_no = str(well_no_row[0])

    # Первое событие оборудования (установка манометра и т.п.)
    equip_row = db.execute(text("""
        SELECT MIN(event_time) FROM events
        WHERE well = :wno AND event_type = 'equip'
    """), {"wno": well_no}).fetchone()
    equip_first = equip_row[0] if equip_row else None

    # Первый вброс реагента
    reagent_row = db.execute(text("""
        SELECT MIN(event_time) FROM events
        WHERE well = :wno AND event_type = 'reagent'
    """), {"wno": well_no}).fetchone()
    reagent_first = reagent_row[0] if reagent_row else None

    # Последнее событие (любое) — граница этапа
    last_row = db.execute(text("""
        SELECT MAX(event_time) FROM events
        WHERE well = :wno
    """), {"wno": well_no}).fetchone()
    last_event = last_row[0] if last_row else None

    rationale: dict[str, str] = {}
    result = {
        "obs_from": None, "obs_to": None,
        "adapt_from": None, "adapt_to": None,
        "rationale": rationale,
        "default_adapt_days": default_adapt_days,
    }

    if reagent_first:
        # Адаптация: от первого вброса + default_adapt_days (но не позже сейчас)
        now = datetime.now()
        adapt_from = reagent_first
        adapt_to_default = adapt_from + timedelta(days=default_adapt_days)
        adapt_to = min(adapt_to_default, now)
        # Если есть события после (вбросы продолжаются) — можно до последнего
        if last_event and last_event > adapt_from and last_event <= now:
            adapt_to = max(adapt_to, last_event)
        result["adapt_from"] = adapt_from
        result["adapt_to"] = adapt_to
        rationale["adapt_from"] = (
            f"первый вброс реагента: {adapt_from.strftime('%d.%m.%Y %H:%M')}"
        )
        rationale["adapt_to"] = (
            f"по умолчанию +{default_adapt_days} сут. или до последнего события "
            f"({adapt_to.strftime('%d.%m.%Y %H:%M')})"
        )

        # Наблюдение: от первого equip-события до момента первого вброса
        if equip_first and equip_first < reagent_first:
            result["obs_from"] = equip_first
            result["obs_to"] = reagent_first  # конец наблюдения = начало адаптации
            rationale["obs_from"] = (
                f"первое событие установки оборудования: "
                f"{equip_first.strftime('%d.%m.%Y %H:%M')}"
            )
            rationale["obs_to"] = (
                f"время первого вброса реагента: "
                f"{result['obs_to'].strftime('%d.%m.%Y %H:%M')}"
            )
        elif equip_first:
            # Оборудование установлено одновременно с вбросом — окно 1 час
            result["obs_from"] = equip_first
            result["obs_to"] = equip_first + timedelta(hours=1)
            rationale["obs_from"] = (
                f"установка оборудования и вброс почти одновременно "
                f"({equip_first.strftime('%d.%m.%Y %H:%M')}) — минимальное окно"
            )
    elif equip_first:
        # Вбросов нет — только наблюдение (от установки до сейчас)
        result["obs_from"] = equip_first
        result["obs_to"] = last_event or datetime.now()
        rationale["obs_from"] = (
            f"первое событие установки: "
            f"{equip_first.strftime('%d.%m.%Y %H:%M')}"
        )
        rationale["obs_to"] = "последнее событие или текущий момент"

    return result


# ═══════════════════════════════════════════════════════════════════
# Валидация этапов
# ═══════════════════════════════════════════════════════════════════

@dataclass
class StageValidation:
    """Результат валидации этапов скважины перед созданием отчёта."""
    ok: bool
    error: str | None = None       # блокирующая ошибка
    warnings: list[str] = None     # предупреждения (не блокирующие)
    obs_from: datetime | None = None
    obs_to: datetime | None = None
    adapt_from: datetime | None = None
    adapt_to: datetime | None = None
    obs_note: str | None = None
    adapt_note: str | None = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


def validate_stages(db: Session, well_id: int) -> StageValidation:
    """Проверить корректность этапов наблюдения/адаптации.

    Правила:
    1. Нет записи "Наблюдение" → блокировать.
    2. Есть "Наблюдение" без dt_end и нет "Адаптация" → блокировать.
    3. Есть "Наблюдение" без dt_end и есть "Адаптация" →
       закрыть наблюдение датой начала адаптации + warning.
    4. Даты перекрываются (adapt_from < obs_to) →
       укоротить obs_to = adapt_from - 1 день + warning.
    5. "Адаптация" без dt_end → взять date.today() + warning.
    """
    # Последние по dt_start записи обоих статусов.
    # Конвертируем TIMESTAMPTZ в naive Kungrad-local (UTC+5).
    obs_row = db.execute(text("""
        SELECT (dt_start AT TIME ZONE 'Asia/Tashkent')::timestamp,
               (dt_end   AT TIME ZONE 'Asia/Tashkent')::timestamp,
               note
        FROM well_status
        WHERE well_id = :wid AND status = :st
        ORDER BY dt_start DESC
        LIMIT 1
    """), {"wid": well_id, "st": STATUS_OBSERVATION}).fetchone()

    adapt_row = db.execute(text("""
        SELECT (dt_start AT TIME ZONE 'Asia/Tashkent')::timestamp,
               (dt_end   AT TIME ZONE 'Asia/Tashkent')::timestamp,
               note
        FROM well_status
        WHERE well_id = :wid AND status = :st
        ORDER BY dt_start DESC
        LIMIT 1
    """), {"wid": well_id, "st": STATUS_ADAPTATION}).fetchone()

    # Правило 1: нет наблюдения
    if not obs_row:
        return StageValidation(
            ok=False,
            error="У скважины нет этапа наблюдения в журнале статусов. Отчёт невозможен.",
        )

    # obs_from/obs_to/adapt_from/adapt_to — теперь datetime (или None)
    obs_from, obs_to, obs_note = obs_row[0], obs_row[1], obs_row[2]

    # Санация: dt_end < dt_start — очевидная ошибка в БД, трактуем как 1 сутки
    obs_reversed_warning: str | None = None
    if obs_to is not None and obs_to < obs_from:
        obs_reversed_warning = (
            f"В журнале статусов конец наблюдения "
            f"({obs_to.strftime('%d.%m.%Y %H:%M')}) раньше начала "
            f"({obs_from.strftime('%d.%m.%Y %H:%M')}). Используется 1 сутки."
        )
        obs_to = obs_from + timedelta(days=1)

    # Правила 2-3: наблюдение не закрыто
    if obs_to is None:
        if not adapt_row:
            return StageValidation(
                ok=False,
                error=(
                    "Этап наблюдения не закрыт и отсутствует этап адаптации. "
                    "Отчёт об адаптации не имеет смысла."
                ),
            )
        # Правило 3: автоматически закрыть наблюдение началом адаптации
        result = StageValidation(ok=True, obs_from=obs_from, obs_note=obs_note)
        if obs_reversed_warning:
            result.warnings.append(obs_reversed_warning)
        result.warnings.append(
            f"Этап наблюдения автоматически закрыт по началу адаптации "
            f"({adapt_row[0].strftime('%d.%m.%Y %H:%M')})."
        )
        # Гарантируем минимум 1 сутки (если adapt_from слишком рано)
        min_obs_to = obs_from + timedelta(hours=1)
        obs_to = max(adapt_row[0], min_obs_to)
    else:
        result = StageValidation(ok=True, obs_from=obs_from, obs_note=obs_note)
        if obs_reversed_warning:
            result.warnings.append(obs_reversed_warning)

    # Блок 2: адаптации нет → блокировать
    if not adapt_row:
        return StageValidation(
            ok=False,
            error="Этап адаптации отсутствует. Отчёт об адаптации невозможен.",
        )

    adapt_from, adapt_to, adapt_note = adapt_row[0], adapt_row[1], adapt_row[2]

    # Санация некорректных дат адаптации (dt_end < dt_start)
    if adapt_to is not None and adapt_to < adapt_from:
        result.warnings.append(
            f"В журнале статусов конец адаптации "
            f"({adapt_to.strftime('%d.%m.%Y %H:%M')}) раньше начала "
            f"({adapt_from.strftime('%d.%m.%Y %H:%M')}). Используется 1 сутки."
        )
        adapt_to = adapt_from + timedelta(days=1)

    # Правило 5: адаптация не закрыта → берём сегодня 23:59:59
    if adapt_to is None:
        adapt_to = datetime.combine(date.today(), time(23, 59, 59))
        result.warnings.append(
            f"Этап адаптации не закрыт, используется текущая дата "
            f"({adapt_to.strftime('%d.%m.%Y')}) как конец периода."
        )

    # Правило 4: перекрытие (adapt_from <= obs_to)
    if adapt_from <= obs_to:
        new_obs_to = adapt_from  # граница наблюдения = начало адаптации
        if new_obs_to <= obs_from:
            # Гарантируем минимум 1 час наблюдения
            new_obs_to = obs_from + timedelta(hours=1)
            result.warnings.append(
                f"Этапы начинаются почти одновременно — наблюдение "
                f"сведено к минимальному окну."
            )
        else:
            result.warnings.append(
                f"Периоды перекрывались. Этап наблюдения укорочен до "
                f"{new_obs_to.strftime('%d.%m.%Y %H:%M')} (приоритет адаптации)."
            )
        obs_to = new_obs_to

    result.obs_to = obs_to
    result.adapt_from = adapt_from
    result.adapt_to = adapt_to
    result.adapt_note = adapt_note
    return result


# ═══════════════════════════════════════════════════════════════════
# Сбор статистики этапа (raw числа)
# ═══════════════════════════════════════════════════════════════════

def collect_stage_stats(
    db: Session, well: Well, choke_mm: float | None,
    date_from: date | datetime, date_to: date | datetime,
    render_charts: bool = False, chart_tag: str = "",
    highlight_windows: list[dict] | None = None,
    dp_threshold: float = 0.1,
) -> dict[str, Any]:
    """Собрать статистику скважины за период (наблюдение или адаптация).

    Использует pressure_raw → clean_pressure → verified masks → hourly.
    Дебит считается через _compute_month_flow_from_raw (pipeline scenario_service).

    Args:
        date_from/date_to: date или datetime (Kungrad-local). Если date — берутся
            границы суток 00:00 / 23:59:59.
        render_charts: если True, рендерит PNG-графики и кладёт пути в результат.
        chart_tag: суффикс для имени PNG (например, "obs" или "adapt").

    Возвращает dict с числовыми значениями (raw). None означает «нет данных».
    """
    from backend.services.flow_rate.data_access import get_pressure_data
    from backend.services.flow_rate.cleaning import clean_pressure
    from backend.services.flow_rate.calculator import (
        calculate_flow_rate, calculate_cumulative,
    )

    # Нормализация: всегда работаем с datetime (Kungrad-local naive)
    dt_from = _to_dt(date_from, end_of_day=False)
    dt_to = _to_dt(date_to, end_of_day=True)

    duration_h = _duration_hours(dt_from, dt_to)
    # Для отображения: целое число суток, округление вверх если есть остаток
    duration_days = max(1, int((duration_h + 23.9999) // 24))
    duration_label = _duration_days_str(dt_from, dt_to)

    utc_start = dt_from - KUNGRAD_OFFSET
    utc_end = dt_to - KUNGRAD_OFFSET

    # 1. Почасовые давления (маски уже применены)
    hourly = _load_masked_hourly(well.id, utc_start, utc_end)

    p_tube_median = None
    p_line_median = None
    dp_median = None
    p_tube_avg = None
    p_line_avg = None
    dp_avg = None
    working_hours = 0
    hours_with_data = 0
    utilization_pct = None
    dp_trend = None
    p_tube_trend = None
    p_line_trend = None
    q_trend = None

    if not hourly.empty:
        pt_all = hourly["p_tube"].dropna()
        pl_all = hourly["p_line"].dropna()
        dp_all = (hourly["p_tube"] - hourly["p_line"]).dropna()

        if len(pt_all) > 0:
            p_tube_median = float(pt_all.median())
        if len(pl_all) > 0:
            p_line_median = float(pl_all.median())
        if len(dp_all) > 0:
            dp_median = float(dp_all.median())

        hours_with_data = len(hourly)
        working_mask = (
            hourly["p_tube"].notna()
            & hourly["p_line"].notna()
            & ((hourly["p_tube"] - hourly["p_line"]) > 0.1)
        )
        working_hours = int(working_mask.sum())
        downtime_hours = max(hours_with_data - working_hours, 0)
        utilization_pct = (
            working_hours / hours_with_data * 100.0 if hours_with_data > 0 else None
        )

        # Тренды считаются ТОЛЬКО на working-hours (p_tube > p_line, ΔP > 0.1).
        # Закрытая скважина / аномальные пики при закрытии исключаются —
        # иначе линия тренда тянется по верхушкам.
        # Метод: Theil-Sen (медианный slope) + Mann-Kendall (значимость) + 95% CI.
        working = hourly[working_mask]
        if len(working) >= 4:
            t0 = working.index.min()
            hours_x = (
                (working.index - t0).total_seconds() / 3600.0
            ).tolist()
            dp_trend = _robust_trend(
                (working["p_tube"] - working["p_line"]).tolist(), hours_x,
            )
            p_tube_trend = _robust_trend(working["p_tube"].tolist(), hours_x)
            p_line_trend = _robust_trend(working["p_line"].tolist(), hours_x)
    else:
        downtime_hours = 0

    # 2. Дебит через ЕДИНЫЙ pipeline страницы скважины
    # (compute_full_flow → clean → verified-маски → smooth → calculate_flow_rate
    #  → purge → cumulative → build_summary). Точки ПОСЛЕ масок, без двойного
    #  усреднения и без фильтра «median > 0», который выкидывал простойные дни.
    # Все агрегаты (median, mean, cumulative, КИВ, downtime, медианы P) берутся
    # из summary — они идентичны тому, что показывает /well/{number} и /api/flow-rate/calculate.
    flow_median = None         # median по точкам после масок
    flow_avg = None            # mean по точкам после масок
    flow_cumulative = None     # накопленный дебит за период
    actual_avg_flow = None     # cum / observation_days — среднесуточный календарный
    working_days = 0
    q_max = None
    q_min = None
    q_max_time = None   # datetime (Kungrad-local) пика

    df_flow = None   # DataFrame с колонкой flow_rate для графика

    # Список интервалов простоя — для рисования красных зон на фронте.
    # Объект формата [{start: "ISO", end: "ISO", duration_min: float}].
    # Заполняется из full_pipeline → detect_downtime_periods.
    downtime_periods_list: list[dict[str, Any]] = []

    if choke_mm and choke_mm > 0:
        try:
            from backend.services.flow_rate.full_pipeline import compute_full_flow
            full = compute_full_flow(
                well_id=well.id,
                dt_start=utc_start,
                dt_end=utc_end,
                smooth=True,
                dp_threshold=dp_threshold,
            )
            summary_flow = full["summary"]
            # Список простоев (Кунградское время — индекс df уже в Кунграде)
            dt_periods_df = full.get("downtime_periods")
            if dt_periods_df is not None and not dt_periods_df.empty:
                for _, row in dt_periods_df.iterrows():
                    downtime_periods_list.append({
                        "start": row["start"].isoformat(timespec="minutes"),
                        "end":   row["end"].isoformat(timespec="minutes"),
                        "duration_min": float(row["duration_min"]),
                    })
            flow_median     = summary_flow.get("median_flow_rate")
            flow_avg        = summary_flow.get("mean_flow_rate")
            flow_cumulative = summary_flow.get("cumulative_flow")
            actual_avg_flow = summary_flow.get("actual_avg_flow")

            # Простой и КИВ — берём ФАКТИЧЕСКИЙ простой через detect_downtime_periods
            # (поле downtime_total_hours), а не "downtime_hours" (это продувки = purge_flag).
            # КИВ и working_hours пересчитываем от него.
            dt_total = summary_flow.get("downtime_total_hours")
            if dt_total is not None and duration_h and duration_h > 0:
                downtime_hours  = float(dt_total)
                working_hours   = max(0.0, duration_h - downtime_hours)
                utilization_pct = working_hours / duration_h * 100.0
            else:
                # Fallback на часовой mask из hourly (как раньше)
                ut_pct = summary_flow.get("utilization_pct")
                if ut_pct is not None:
                    utilization_pct = ut_pct

            # Медианы И средние давлений (по точкам после масок).
            p_t_med = summary_flow.get("median_p_tube")
            p_l_med = summary_flow.get("median_p_line")
            dp_med  = summary_flow.get("median_dp")
            if p_t_med is not None: p_tube_median = float(p_t_med)
            if p_l_med is not None: p_line_median = float(p_l_med)
            if dp_med  is not None: dp_median     = float(dp_med)
            p_t_avg = summary_flow.get("mean_p_tube")
            p_l_avg = summary_flow.get("mean_p_line")
            dp_av   = summary_flow.get("mean_dp")
            if p_t_avg is not None: p_tube_avg = float(p_t_avg)
            if p_l_avg is not None: p_line_avg = float(p_l_avg)
            if dp_av   is not None: dp_avg     = float(dp_av)
        except ValueError as e:
            log.warning(
                "collect_stage_stats: full_pipeline no data for %s (%s..%s): %s",
                well.number, date_from, date_to, e,
            )
        except Exception:
            log.exception(
                "collect_stage_stats: full_pipeline failed for well %s (%s..%s)",
                well.number, date_from, date_to,
            )

        # Часовой df_flow для chart_data, q_max/min, q_trend — оставляем как есть.
        # На фронте графики используют часовое разрешение из соображений
        # производительности и консистентности с hourly-pressure.
        if not hourly.empty:
            df_flow = _compute_hourly_flow(hourly, choke_mm, dp_threshold=dp_threshold)
            if df_flow is not None and not df_flow.empty:
                q_series = df_flow["flow_rate"].dropna()
                q_working = q_series[q_series > 0]
                if len(q_working) > 0:
                    q_max = float(q_working.max())
                    q_min = float(q_working.min())
                    q_max_ts_utc = q_working.idxmax()
                    q_max_time = q_max_ts_utc + KUNGRAD_OFFSET
                if len(q_working) >= 4:
                    t0 = q_working.index.min()
                    hours_x = (
                        (q_working.index - t0).total_seconds() / 3600.0
                    ).tolist()
                    q_trend = _robust_trend(q_working.tolist(), hours_x)

    # 3. События за период (продувки, вбросы реагентов)
    local_start = dt_from
    local_end = dt_to

    # Один полный цикл продувки = 3 события (start/press/stop). Считаем
    # уникальные циклы по числу маркеров 'start' (а для легаси-данных без
    # phase — каждое событие как один цикл).
    event_counts = db.execute(text("""
        SELECT
            COUNT(*) FILTER (
                WHERE event_type = 'purge'
                  AND (purge_phase = 'start' OR purge_phase IS NULL)
            ) AS purges,
            COUNT(*) FILTER (WHERE event_type = 'reagent') AS reagents,
            COALESCE(SUM(qty) FILTER (WHERE event_type = 'reagent'), 0) AS reagent_qty
        FROM events
        WHERE well = :wno
          AND event_time >= :start AND event_time <= :end
    """), {
        "wno": str(well.number), "start": local_start, "end": local_end,
    }).fetchone()

    purge_count = int(event_counts[0] or 0) if event_counts else 0
    reagent_count = int(event_counts[1] or 0) if event_counts else 0
    reagent_qty = float(event_counts[2] or 0.0) if event_counts else 0.0

    # Разбивка реагентов по типам
    reagent_rows = db.execute(text("""
        SELECT reagent, COUNT(*), COALESCE(SUM(qty), 0)
        FROM events
        WHERE well = :wno AND event_type = 'reagent'
          AND event_time >= :start AND event_time <= :end
          AND reagent IS NOT NULL
        GROUP BY reagent ORDER BY reagent
    """), {
        "wno": str(well.number), "start": local_start, "end": local_end,
    }).fetchall()

    reagent_stats = [
        {"name": r[0], "count": int(r[1] or 0), "total_qty": float(r[2] or 0.0)}
        for r in reagent_rows
    ]

    # Потери (простой × медианный дебит)
    downtime_loss = None
    if flow_median and downtime_hours > 0:
        downtime_loss = float(flow_median) * (downtime_hours / 24.0)

    # Маркеры событий для графиков (все значимые типы) — полные данные
    # Добавлены p_tube, p_line из самого события (давления на момент фиксации)
    event_markers: list[dict] = []
    marker_rows = db.execute(text("""
        SELECT event_time, event_type, reagent, qty, description, p_tube, p_line
        FROM events
        WHERE well = :wno
          AND event_type IN ('purge', 'reagent', 'equip', 'other')
          AND event_time >= :start AND event_time <= :end
        ORDER BY event_time
    """), {
        "wno": str(well.number), "start": local_start, "end": local_end,
    }).fetchall()
    for r in marker_rows:
        if r[0]:
            # events хранятся в Kungrad-локальном времени → переводим в UTC
            event_markers.append({
                "time_utc": r[0] - KUNGRAD_OFFSET,
                "event_type": r[1],
                "reagent": r[2],
                "qty": float(r[3]) if r[3] else None,
                "description": r[4],
                "p_tube": float(r[5]) if r[5] else None,
                "p_line": float(r[6]) if r[6] else None,
            })

    # 4. Графики
    pressure_chart_path = None
    dp_chart_path = None
    flow_chart_path = None

    combined_chart_path = None

    # События в Kungrad-local (для отображения на frontend-графике и в таблице)
    # Давления берём из самого события (p_tube, p_line записаны при фиксации)
    events_for_chart: list[dict] = []
    for m in event_markers:
        ts_local = m["time_utc"] + KUNGRAD_OFFSET
        # Давления из события (уже есть в event_markers)
        p_tube_val = round(m["p_tube"], 1) if m.get("p_tube") is not None else None
        p_line_val = round(m["p_line"], 1) if m.get("p_line") is not None else None

        events_for_chart.append({
            "t": ts_local.isoformat(timespec="minutes"),
            "type": m["event_type"],
            "label": m.get("reagent") or m.get("description") or "",
            "reagent": m.get("reagent") or "",
            "qty": m.get("qty"),
            "amount": m.get("qty"),
            "description": m.get("description") or "",
            "p_tube": p_tube_val,
            "p_line": p_line_val,
        })

    # Компактные часовые данные для интерактивных графиков (frontend)
    chart_data = None
    if not hourly.empty:
        # Переводим индекс в Kungrad-local для отображения
        h_local = hourly.copy()
        h_local.index = h_local.index + KUNGRAD_OFFSET
        chart_data = []
        for ts, row in h_local.iterrows():
            pt = row.get("p_tube")
            pl = row.get("p_line")
            q = None
            if df_flow is not None and not df_flow.empty:
                utc_ts = ts - KUNGRAD_OFFSET
                if utc_ts in df_flow.index:
                    q_val = df_flow.loc[utc_ts, "flow_rate"]
                    if pd.notna(q_val):
                        q = float(q_val)
            chart_data.append({
                "t": ts.isoformat(timespec="minutes"),
                "p_tube": float(pt) if pd.notna(pt) else None,
                "p_line": float(pl) if pd.notna(pl) else None,
                "dp": (
                    float(pt) - float(pl)
                    if pd.notna(pt) and pd.notna(pl) and pt > pl else None
                ),
                "q": q,
            })

    if render_charts and not hourly.empty:
        try:
            pressure_chart_path = _render_stage_pressure_chart(
                hourly, well.number, chart_tag,
                event_markers=event_markers,
            )
            dp_chart_path = _render_stage_dp_chart(
                hourly, well.number, chart_tag, dp_trend,
                event_markers=event_markers,
            )
            if df_flow is not None and not df_flow.empty:
                flow_chart_path = _render_stage_flow_chart(
                    df_flow, well.number, chart_tag,
                    event_markers=event_markers,
                )
            # Комбинированный график на полную страницу
            combined_chart_path = _render_stage_combined_chart(
                hourly, df_flow, well.number, chart_tag,
                event_markers=event_markers,
                dp_trend=dp_trend, q_trend=q_trend,
                highlight_windows=highlight_windows,
            )
        except Exception:
            log.exception("Chart rendering failed for %s/%s", well.number, chart_tag)

    return {
        "date_from": dt_from,
        "date_to": dt_to,
        "duration_days": duration_days,
        "duration_hours": round(duration_h, 2),
        "duration_label": duration_label,
        # Давления (raw float или None) — медианы и средние по точкам после масок
        "p_tube_median": p_tube_median,
        "p_line_median": p_line_median,
        "dp_median": dp_median,
        "p_tube_avg": p_tube_avg,
        "p_line_avg": p_line_avg,
        "dp_avg": dp_avg,
        "dp_trend": dp_trend,
        "p_tube_trend": p_tube_trend,
        "p_line_trend": p_line_trend,
        "q_trend": q_trend,
        # Дебит (raw float или None) — все по точкам после масок
        "flow_median": flow_median,
        "flow_avg": flow_avg,
        "flow_cumulative": flow_cumulative,
        # cum / observation_days — среднесуточный фактический по календарю
        "actual_avg_flow": actual_avg_flow,
        "q_min": q_min,
        "q_max": q_max,
        "q_max_time": q_max_time,
        # Рабочее время — единый источник, через downtime_periods_list
        "hours_with_data": hours_with_data,
        "working_hours": working_hours,
        "downtime_hours": downtime_hours,
        "utilization_pct": utilization_pct,
        "working_days": working_days,
        "dp_threshold": dp_threshold,
        "downtime_periods_list": downtime_periods_list,
        # Потери
        "downtime_loss": downtime_loss,
        # События
        "purge_count": purge_count,
        "reagent_count": reagent_count,
        "reagent_qty": reagent_qty,
        "reagent_stats": reagent_stats,
        # Графики
        "pressure_chart_path": pressure_chart_path,
        "dp_chart_path": dp_chart_path,
        "flow_chart_path": flow_chart_path,
        "combined_chart_path": combined_chart_path,
        # Данные для интерактивных графиков (frontend)
        "chart_data": chart_data,
        "events_for_chart": events_for_chart,
    }


# ═══════════════════════════════════════════════════════════════════
# Рендеринг графиков для многодневных периодов
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
# Детектор оптимального режима (подпериод внутри этапа адаптации)
# ═══════════════════════════════════════════════════════════════════

# Максимум CV (residual, после удаления тренда) для "стабильного" окна.
# Окна с CV выше отбрасываются как слишком шумные.
OPTIMAL_MAX_CV = 0.05


def _window_metrics(
    hourly: pd.DataFrame, df_flow: pd.DataFrame | None,
    event_markers: list[dict] | None,
    w_from: datetime, w_to: datetime,
) -> dict | None:
    """Рассчитать 5 метрик для одного окна.

    Индексы hourly/df_flow — UTC. w_from/w_to — Kungrad-local naive.
    """
    utc_from = w_from - KUNGRAD_OFFSET
    utc_to = w_to - KUNGRAD_OFFSET

    # Срез hourly
    h = hourly.loc[utc_from:utc_to] if not hourly.empty else hourly
    if h.empty or len(h) < 3:
        return None

    # Покрытие окна данными: < 50% → отбраковать
    window_hours_total = (w_to - w_from).total_seconds() / 3600.0
    data_coverage = len(h) / max(window_hours_total, 1)
    if data_coverage < 0.5:
        return None

    # Срез Q
    if df_flow is not None and not df_flow.empty and "flow_rate" in df_flow.columns:
        f = df_flow.loc[utc_from:utc_to]
        q_vals = f["flow_rate"].dropna()
        q_working = q_vals[q_vals > 0]
    else:
        q_working = pd.Series([], dtype=float)

    if len(q_working) < 3:
        return None

    q_mean = float(q_working.mean())
    q_median = float(q_working.median())

    # CV считаем от остатков после удаления линейного тренда:
    # монотонный рост Q — это НЕ нестабильность, а позитивная динамика.
    # Обычный std ошибочно штрафует такой рост. Residual std измеряет
    # только "шум" вокруг тренда.
    t0 = q_working.index.min()
    t_h = np.array([(t - t0).total_seconds() / 3600.0 for t in q_working.index])
    v = q_working.values
    try:
        coef = np.polyfit(t_h, v, 1)
        residuals = v - np.polyval(coef, t_h)
        residual_std = float(np.std(residuals))
    except (np.linalg.LinAlgError, ValueError):
        residual_std = float(q_working.std(ddof=0))
    cv_q = residual_std / q_mean if q_mean > 0 else float("inf")

    # КИВ в окне: часов где ΔP > 0.1 / всего часов в окне
    working_mask = (
        h["p_tube"].notna()
        & h["p_line"].notna()
        & ((h["p_tube"] - h["p_line"]) > 0.1)
    )
    utilization = float(working_mask.sum()) / max(len(h), 1) * 100.0

    # Частота продувок (на сутки) в окне
    purge_count = sum(
        1 for ev in (event_markers or [])
        if ev.get("event_type") == "purge"
        and utc_from <= ev["time_utc"] <= utc_to
    )
    window_days = window_hours_total / 24.0
    purge_freq = purge_count / max(window_days, 0.1)  # в сутки

    # Тренд Q (линейная регрессия)
    q_trend_abs = 0.0
    if len(q_working) >= 3:
        t0 = q_working.index.min()
        t_h = np.array([(t - t0).total_seconds() / 3600.0 for t in q_working.index])
        v = q_working.values
        try:
            coef = np.polyfit(t_h, v, 1)
            q_trend_abs = abs(float(coef[0]) * 24.0)  # тыс.м³/сут за сутки
        except (np.linalg.LinAlgError, ValueError):
            pass

    return {
        "start": w_from,
        "end": w_to,
        "q_mean": q_mean,
        "q_median": q_median,
        "cv_q": cv_q,
        "utilization_pct": utilization,
        "purge_freq_per_day": purge_freq,
        "q_trend_abs": q_trend_abs,
        "data_points": len(h),
    }


def _percentile_rank(value: float, all_values: list[float], higher_is_better: bool = True) -> float:
    """Ранг значения среди всех (0..1). Если higher_is_better=False, инвертируем."""
    if not all_values or len(all_values) < 2:
        return 0.5
    arr = np.array(all_values)
    rank = float((arr < value).sum() + 0.5 * (arr == value).sum()) / len(arr)
    return rank if higher_is_better else (1.0 - rank)


def _score_candidates(cands: list[dict]) -> list[dict]:
    """Ранжировать окна по максимальному Q_median.

    Логика простая: оптимум = окно с наивысшим медианным дебитом.
    Окна с CV > OPTIMAL_MAX_CV (резкие колебания) отбрасываются.
    КИВ должен быть ≥ 70% (иначе окно содержит слишком много простоев).
    """
    if not cands:
        return []

    # Фильтрация по CV и КИВ (явные отсечки, а не мягкий штраф)
    viable = [
        c for c in cands
        if (c.get("cv_q") is not None and c["cv_q"] <= OPTIMAL_MAX_CV)
        and (c.get("utilization_pct") is not None and c["utilization_pct"] >= 70)
    ]
    # Если всё отсеклось — возвращаемся к исходному (с маяком в rationale)
    source = viable if viable else cands

    # Назначим псевдо-score = нормализованный Q_median (0..1), чтобы UI продолжил
    # отображать "Score", но на самом деле это просто ранг по Q.
    qs = [c["q_median"] for c in source]
    q_min = min(qs)
    q_max = max(qs)
    q_span = (q_max - q_min) or 1.0
    for c in source:
        c["score"] = (c["q_median"] - q_min) / q_span
        # Заполняем rank-поля на всякий случай (используются в UI/PDF)
        c["rank_q"] = c["score"]
        c["rank_stability"] = 1.0 - min(1.0, c.get("cv_q", 0) / OPTIMAL_MAX_CV)
        c["rank_utilization"] = (c.get("utilization_pct") or 0) / 100.0
        c["rank_purge"] = 1.0 if (c.get("purge_freq_per_day") or 0) == 0 else 0.0

    # Сортировка по убыванию Q_median
    source.sort(key=lambda x: x["q_median"], reverse=True)
    return source


def detect_optimal_windows(
    db: Session, well: Well, choke_mm: float | None,
    dt_from: datetime, dt_to: datetime,
    window_hours: int = 72, top_n: int = 3, step_hours: int = 6,
) -> list[dict]:
    """Найти top-N оптимальных подпериодов внутри [dt_from, dt_to].

    Алгоритм:
    1. Скользящее окно шириной window_hours, шаг step_hours.
    2. Для каждого окна — 5 метрик: Q_median, CV, КИВ, продувок/сут, |тренд Q|.
    3. Перцентиль-нормализация по всем кандидатам.
    4. Composite Score с весами OPTIMAL_WEIGHTS.
    5. Возврат top-N по убыванию Score.

    Если весь период короче window_hours — окно = весь период (один кандидат).
    """
    if not choke_mm or choke_mm <= 0:
        return []

    utc_start = dt_from - KUNGRAD_OFFSET
    utc_end = dt_to - KUNGRAD_OFFSET

    hourly = _load_masked_hourly(well.id, utc_start, utc_end)
    if hourly.empty:
        return []

    df_flow = _compute_hourly_flow(hourly, choke_mm) if not hourly.empty else None

    # Маркеры событий (нужны для частоты продувок). Один полный цикл
    # продувки = 3 события (start/press/stop) → берём только маркер 'start'
    # как точку отсчёта цикла; для легаси-данных без phase — каждое.
    local_start = dt_from
    local_end = dt_to
    rows = db.execute(text("""
        SELECT event_time, event_type FROM events
        WHERE well = :wno AND event_type = 'purge'
          AND (purge_phase = 'start' OR purge_phase IS NULL)
          AND event_time >= :start AND event_time <= :end
    """), {
        "wno": str(well.number), "start": local_start, "end": local_end,
    }).fetchall()
    event_markers = [
        {"time_utc": r[0] - KUNGRAD_OFFSET, "event_type": r[1]}
        for r in rows if r[0]
    ]

    # Если период короче окна — берём весь период
    total_hours = (dt_to - dt_from).total_seconds() / 3600.0
    if total_hours < window_hours:
        m = _window_metrics(hourly, df_flow, event_markers, dt_from, dt_to)
        if m:
            m["score"] = 1.0
            m["rank_q"] = m["rank_stability"] = m["rank_utilization"] = 1.0
            m["rank_purge"] = m["rank_trend"] = 1.0
            return [m]
        return []

    # Скользящее окно
    candidates: list[dict] = []
    t = dt_from
    while t + timedelta(hours=window_hours) <= dt_to + timedelta(minutes=1):
        w_from = t
        w_to = t + timedelta(hours=window_hours)
        m = _window_metrics(hourly, df_flow, event_markers, w_from, w_to)
        if m is not None:
            candidates.append(m)
        t += timedelta(hours=step_hours)

    if not candidates:
        return []

    # Отсечка: Q_median кандидата не ниже 50% от медианы всего периода
    global_q_median = float(
        df_flow["flow_rate"][df_flow["flow_rate"] > 0].median()
        if df_flow is not None and not df_flow.empty
        else 0
    )
    if global_q_median > 0:
        threshold = global_q_median * 0.5
        candidates = [c for c in candidates if c["q_median"] >= threshold]

    if not candidates:
        return []

    scored = _score_candidates(candidates)
    return scored[:top_n]


# ═══════════════════════════════════════════════════════════════════
# Помесячная статистика за всю историю скважины
# ═══════════════════════════════════════════════════════════════════

def compute_monthly_stats(
    db: Session, well_id: int,
    months_back: int = 24,
) -> list[dict]:
    """Посчитать помесячную статистику скважины.

    Для каждого месяца:
    - Q mean / median / min / max / std
    - ΔP mean / median
    - Тренд Q за месяц (slope + direction + R²)
    - Кол-во событий (продувки, вбросы реагента)
    - Авто-описание

    Загружает всю историю давлений (с масками), считает Q по часам,
    группирует по году-месяцу, строит статистику.
    """
    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        return []

    # Штуцер (берём последний — для старых периодов может быть неточно,
    # но pipeline flow_rate использует ту же формулу везде)
    row = db.execute(text("""
        SELECT choke_diam_mm FROM well_construction
        WHERE well_no = :wno
        ORDER BY data_as_of DESC NULLS LAST LIMIT 1
    """), {"wno": str(well.number)}).fetchone()
    choke_mm = float(row[0]) if row and row[0] else None
    if not choke_mm or choke_mm <= 0:
        return []

    # Временной диапазон: последние N месяцев
    today = datetime.now()
    period_start_local = (
        today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        - timedelta(days=months_back * 31)
    )
    # Округлим до начала месяца
    period_start_local = period_start_local.replace(day=1)
    utc_start = period_start_local - KUNGRAD_OFFSET
    utc_end = today - KUNGRAD_OFFSET

    hourly = _load_masked_hourly(well_id, utc_start, utc_end)
    if hourly.empty:
        return []

    df_flow = _compute_hourly_flow(hourly, choke_mm)
    # Переведём индекс в Kungrad-local для группировки по календарным месяцам
    df_flow_local = df_flow.copy()
    df_flow_local.index = df_flow_local.index + KUNGRAD_OFFSET
    df_flow_local["year_month"] = df_flow_local.index.strftime("%Y-%m")
    df_flow_local["dp"] = (hourly["p_tube"] - hourly["p_line"]).clip(lower=0).values

    # События помесячно — одним запросом. Один цикл продувки = 3 события
    # (start/press/stop) → считаем только маркер 'start' как 1 цикл
    # (для легаси-данных без phase — каждое событие как 1 цикл).
    events_rows = db.execute(text("""
        SELECT
            to_char(event_time, 'YYYY-MM') AS ym,
            event_type,
            COUNT(*) FILTER (
                WHERE event_type <> 'purge'
                   OR purge_phase = 'start'
                   OR purge_phase IS NULL
            ) AS cnt
        FROM events
        WHERE well = :wno
          AND event_time >= :start
          AND event_time <= :end
          AND event_type IN ('purge', 'reagent')
        GROUP BY ym, event_type
    """), {
        "wno": str(well.number),
        "start": period_start_local,
        "end": today,
    }).fetchall()

    events_by_month: dict[str, dict] = {}
    for r in events_rows:
        events_by_month.setdefault(r[0], {})[r[1]] = int(r[2] or 0)

    # Агрегируем по месяцам
    result: list[dict] = []
    for ym, group in df_flow_local.groupby("year_month"):
        q_series = group["flow_rate"].dropna()
        q_working = q_series[q_series > 0]

        if len(q_working) == 0:
            continue

        dp_series = group["dp"].dropna()
        dp_series = dp_series[dp_series > 0]

        # Тренд Q за месяц — robust (Theil-Sen + Mann-Kendall) по рабочим часам
        trend = None
        if len(q_working) >= 12:  # минимум 12 часов для осмысленного тренда
            t0 = q_working.index.min()
            hours_x = [
                (t - t0).total_seconds() / 3600.0 for t in q_working.index
            ]
            trend = _robust_trend(q_working.tolist(), hours_x)

        # События
        ev = events_by_month.get(ym, {})
        purges = ev.get("purge", 0)
        reagents = ev.get("reagent", 0)

        # Дни с данными
        unique_days = len(group.index.normalize().unique())

        # Авто-описание
        parts = []
        q_med_val = float(q_working.median())
        parts.append(f"Q мед. {q_med_val:.1f} тыс.м³/сут")
        if trend:
            slope = trend.get("slope_per_day") or 0
            if trend.get("direction") == "up" and abs(slope) > 0.1:
                parts.append(f"↑ рост {slope:+.2f}/сут")
            elif trend.get("direction") == "down" and abs(slope) > 0.1:
                parts.append(f"↓ снижение {slope:+.2f}/сут")
            else:
                parts.append("→ стабильно")
        if reagents > 0:
            parts.append(f"{reagents} вбросов реагента")
        if purges > 0:
            parts.append(f"{purges} продувок")

        result.append({
            "year_month": ym,
            "days": unique_days,
            "hours_count": int(len(group)),
            "working_hours": int(len(q_working)),
            "q_mean": float(q_working.mean()),
            "q_median": q_med_val,
            "q_min": float(q_working.min()),
            "q_max": float(q_working.max()),
            "q_std": float(q_working.std(ddof=0)) if len(q_working) > 1 else 0.0,
            "dp_mean": float(dp_series.mean()) if len(dp_series) > 0 else None,
            "dp_median": float(dp_series.median()) if len(dp_series) > 0 else None,
            "q_trend": trend,
            "purge_count": purges,
            "reagent_count": reagents,
            "description": ". ".join(parts) + ".",
        })

    return result


def _compute_hourly_flow(
    hourly: pd.DataFrame, choke_mm: float, dp_threshold: float = 0.1,
) -> pd.DataFrame:
    """Посчитать мгновенный дебит Q для каждой часовой точки.

    Использует ту же формулу, что scenario_service (критический/докритический режим).

    ВАЖНО: Q = 0 при простое (dp < dp_threshold), согласовано с full_pipeline step 11a
    и красными зонами на графике.
    """
    from backend.services.flow_rate.config import DEFAULT_FLOW as cfg

    choke_sq = (choke_mm / cfg.C2) ** 2
    q_values = []
    for _, row in hourly.iterrows():
        pt, pl = row["p_tube"], row["p_line"]
        # NaN для отсутствующих данных
        if pd.isna(pt) or pd.isna(pl) or pt <= 0:
            q_values.append(np.nan)
            continue
        pt, pl = float(pt), float(pl)
        dp = pt - pl
        # Простой: ΔP < порога — Q = 0 (согласовано с красными зонами)
        if dp < dp_threshold:
            q_values.append(0.0)
            continue
        ratio = dp / pt
        if ratio < cfg.critical_ratio:
            q = cfg.C1 * choke_sq * pt * (1.0 - ratio / 1.5) * (ratio / cfg.C3) ** 0.5
        else:
            q = 0.667 * cfg.C1 * choke_sq * pt * (0.5 / cfg.C3) ** 0.5
        q_values.append(max(q * cfg.multiplier, 0.0))

    df = hourly.copy()
    df["flow_rate"] = q_values
    return df


def _overlay_event_markers(ax, event_markers: list[dict] | None):
    """Нанести маркеры событий:
    - Вбросы реагента → треугольники-стрелки сверху оси (НЕ перекрывают кривую)
    - Продувки → фиолетовые пунктирные линии
    - Прочее → серые тонкие линии
    """
    if not event_markers:
        return
    purge_drawn = False
    other_drawn = False

    reagent_x: list = []

    for ev in event_markers:
        ev_local = ev["time_utc"] + KUNGRAD_OFFSET
        et = ev["event_type"]

        if et == "purge":
            ax.axvline(
                ev_local, color="#9c27b0", alpha=0.7, linewidth=1.0,
                linestyle="--",
                label="Продувка" if not purge_drawn else None,
            )
            purge_drawn = True
        elif et == "reagent":
            reagent_x.append(ev_local)
        else:
            # Прочие события (оборудование, гидрат, прочее) — тонкая линия
            ax.axvline(
                ev_local, color="#757575", alpha=0.4, linewidth=0.6,
                linestyle="-",
                label="Прочее" if not other_drawn else None,
            )
            other_drawn = True

    # Треугольники реагента над верхней границей оси (clip_on=False).
    # x в data-координатах, y в axes-координатах (1.0 = верх оси).
    # НЕ пересекаются с кривой и не путаются с значениями Q.
    if reagent_x:
        ax.scatter(
            reagent_x, [1.02] * len(reagent_x),
            marker="v", s=45, color="#2e7d32",
            edgecolor="white", linewidth=0.6,
            transform=ax.get_xaxis_transform(),
            clip_on=False, zorder=10,
            label="Вброс реагента",
        )


def _render_stage_pressure_chart(
    hourly: pd.DataFrame, well_number, chart_tag: str,
    event_markers: list[dict] | None = None,
) -> str | None:
    """График P трубное / P линейное за период этапа."""
    if hourly.empty or len(hourly) < 2:
        return None

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        return None

    _ensure_dirs()

    df_local = hourly.copy()
    df_local.index = df_local.index + KUNGRAD_OFFSET

    fig, ax = plt.subplots(figsize=(7.0, 2.5), dpi=150)

    ax.plot(df_local.index, df_local["p_tube"], color="#1f77b4",
            linewidth=0.8, label="P труб")
    ax.plot(df_local.index, df_local["p_line"], color="#d62728",
            linewidth=0.8, label="P линия")

    _overlay_event_markers(ax, event_markers)

    ax.set_ylabel("кгс/см²", fontsize=8)
    ax.set_xlabel("")
    ax.legend(fontsize=7, loc="upper right")
    ax.tick_params(labelsize=7)

    # Формат даты: если период > 2 дней → даты, иначе часы
    duration = (df_local.index.max() - df_local.index.min()).total_seconds() / 3600.0
    if duration > 48:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))

    ax.grid(True, alpha=0.3, linewidth=0.5)
    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout(pad=0.5)

    chart_path = TEMP_DIR.resolve() / f"adapt_p_{well_number}_{chart_tag}.png"
    fig.savefig(str(chart_path), bbox_inches="tight")
    plt.close(fig)
    return str(chart_path)


def _render_stage_dp_chart(
    hourly: pd.DataFrame, well_number, chart_tag: str,
    dp_trend: dict | None = None,
    event_markers: list[dict] | None = None,
) -> str | None:
    """График ΔP за период с линией тренда."""
    if hourly.empty or len(hourly) < 2:
        return None

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        return None

    _ensure_dirs()

    df_local = hourly.copy()
    df_local.index = df_local.index + KUNGRAD_OFFSET
    dp = (df_local["p_tube"] - df_local["p_line"]).clip(lower=0)

    fig, ax = plt.subplots(figsize=(7.0, 2.5), dpi=150)

    ax.plot(df_local.index, dp, color="#ef6c00", linewidth=0.8, label="ΔP")
    ax.fill_between(df_local.index, dp, alpha=0.15, color="#ef6c00")

    # Линия тренда (Theil-Sen) + полоса 95% CI (Mann-Kendall значимость в подписи)
    if dp_trend and dp_trend.get("slope_per_day") is not None:
        slope_per_h = dp_trend["slope_per_day"] / 24.0
        t0 = df_local.index.min()
        t_hours = np.array([(t - t0).total_seconds() / 3600.0 for t in df_local.index])
        dp_mean = float(dp.mean())
        # центрируем линию по среднему ΔP
        trend_line = slope_per_h * (t_hours - t_hours.mean()) + dp_mean

        # 95% CI по slope: рисуем полосу low/high slope, относительно того же центра
        low_per_h = (dp_trend.get("slope_low_per_day") or
                     dp_trend["slope_per_day"]) / 24.0
        high_per_h = (dp_trend.get("slope_high_per_day") or
                      dp_trend["slope_per_day"]) / 24.0
        trend_low = low_per_h * (t_hours - t_hours.mean()) + dp_mean
        trend_high = high_per_h * (t_hours - t_hours.mean()) + dp_mean
        ax.fill_between(df_local.index, trend_low, trend_high,
                        color="#333", alpha=0.10, linewidth=0,
                        label="95% CI тренда")

        sig_mark = " ✓" if dp_trend.get("mk_significant") else ""
        p_str = f", p={dp_trend.get('mk_p', 1):.3f}{sig_mark}"
        ax.plot(df_local.index, trend_line, color="#333", linewidth=1.0,
                linestyle="--",
                label=(f"Тренд ({dp_trend.get('direction', 'flat')}, "
                       f"{dp_trend.get('slope_per_day', 0):+.3f}/сут{p_str})"))

    _overlay_event_markers(ax, event_markers)

    ax.set_ylabel("ΔP, кгс/см²", fontsize=8)
    ax.set_xlabel("")
    ax.legend(fontsize=7, loc="upper right")
    ax.tick_params(labelsize=7)

    duration = (df_local.index.max() - df_local.index.min()).total_seconds() / 3600.0
    if duration > 48:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))

    ax.grid(True, alpha=0.3, linewidth=0.5)
    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout(pad=0.5)

    chart_path = TEMP_DIR.resolve() / f"adapt_dp_{well_number}_{chart_tag}.png"
    fig.savefig(str(chart_path), bbox_inches="tight")
    plt.close(fig)
    return str(chart_path)


def _render_stage_flow_chart(
    df_flow: pd.DataFrame, well_number, chart_tag: str,
    event_markers: list[dict] | None = None,
) -> str | None:
    """График мгновенного дебита Q(t) за период."""
    if df_flow.empty or "flow_rate" not in df_flow.columns or len(df_flow) < 2:
        return None

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        return None

    _ensure_dirs()

    df_local = df_flow.copy()
    df_local.index = df_local.index + KUNGRAD_OFFSET

    fig, ax = plt.subplots(figsize=(7.0, 2.5), dpi=150)

    ax.plot(df_local.index, df_local["flow_rate"], color="#2e7d32",
            linewidth=0.8, label="Q, тыс.м³/сут")
    ax.fill_between(df_local.index, df_local["flow_rate"], alpha=0.15, color="#2e7d32")

    _overlay_event_markers(ax, event_markers)

    ax.set_ylabel("тыс. м³/сут", fontsize=8)
    ax.set_xlabel("")
    ax.legend(fontsize=7, loc="upper right")
    ax.tick_params(labelsize=7)

    duration = (df_local.index.max() - df_local.index.min()).total_seconds() / 3600.0
    if duration > 48:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))

    ax.grid(True, alpha=0.3, linewidth=0.5)
    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout(pad=0.5)

    chart_path = TEMP_DIR.resolve() / f"adapt_q_{well_number}_{chart_tag}.png"
    fig.savefig(str(chart_path), bbox_inches="tight")
    plt.close(fig)
    return str(chart_path)


def _render_stage_combined_chart(
    hourly: pd.DataFrame, df_flow: pd.DataFrame | None,
    well_number, chart_tag: str,
    event_markers: list[dict] | None = None,
    dp_trend: dict | None = None,
    q_trend: dict | None = None,
    highlight_windows: list[dict] | None = None,
) -> str | None:
    """Комбинированный график на полную страницу A4: P, ΔP, Q в столбец.

    Используется как «разворот» на отдельной странице PDF для каждого этапа.
    """
    if hourly.empty or len(hourly) < 2:
        return None

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        return None

    _ensure_dirs()

    df_local = hourly.copy()
    df_local.index = df_local.index + KUNGRAD_OFFSET
    dp = (df_local["p_tube"] - df_local["p_line"]).clip(lower=0)

    # 3 subplot-а, общая x-ось, размер под A4 portrait
    fig, axes = plt.subplots(
        3, 1, figsize=(7.5, 9.5), dpi=150, sharex=True,
        gridspec_kw={"hspace": 0.18},
    )
    ax_p, ax_dp, ax_q = axes

    # ── 1. Давления ──
    ax_p.plot(df_local.index, df_local["p_tube"], color="#1f77b4",
              linewidth=0.9, label="P труб")
    ax_p.plot(df_local.index, df_local["p_line"], color="#d62728",
              linewidth=0.9, label="P линия")
    ax_p.set_ylabel("P, кгс/см²", fontsize=9)
    ax_p.legend(fontsize=8, loc="upper right")
    ax_p.tick_params(labelsize=8)
    ax_p.grid(True, alpha=0.3, linewidth=0.5)
    ax_p.set_title(
        f"Давления трубное и линейное",
        fontsize=10, loc="left", pad=4,
    )

    # ── 2. ΔP + тренд (Theil-Sen + 95% CI + MK p-value) ──
    ax_dp.plot(df_local.index, dp, color="#ef6c00", linewidth=0.9, label="ΔP")
    ax_dp.fill_between(df_local.index, dp, alpha=0.15, color="#ef6c00")
    if dp_trend and dp_trend.get("slope_per_day") is not None:
        slope_per_h = dp_trend["slope_per_day"] / 24.0
        t0 = df_local.index.min()
        t_hours = np.array([(t - t0).total_seconds() / 3600.0 for t in df_local.index])
        dp_mean = float(dp.mean())
        trend_line = slope_per_h * (t_hours - t_hours.mean()) + dp_mean

        low_per_h = (dp_trend.get("slope_low_per_day") or
                     dp_trend["slope_per_day"]) / 24.0
        high_per_h = (dp_trend.get("slope_high_per_day") or
                      dp_trend["slope_per_day"]) / 24.0
        trend_low = low_per_h * (t_hours - t_hours.mean()) + dp_mean
        trend_high = high_per_h * (t_hours - t_hours.mean()) + dp_mean
        ax_dp.fill_between(df_local.index, trend_low, trend_high,
                           color="#333", alpha=0.10, linewidth=0,
                           label="95% CI тренда")

        sig_mark = " ✓" if dp_trend.get("mk_significant") else ""
        p_str = f", p={dp_trend.get('mk_p', 1):.3f}{sig_mark}"
        ax_dp.plot(
            df_local.index, trend_line, color="#333", linewidth=1.0, linestyle="--",
            label=(f"Тренд ({dp_trend.get('slope_per_day', 0):+.3f}/сут{p_str})"),
        )
    ax_dp.set_ylabel("ΔP, кгс/см²", fontsize=9)
    ax_dp.legend(fontsize=8, loc="upper right")
    ax_dp.tick_params(labelsize=8)
    ax_dp.grid(True, alpha=0.3, linewidth=0.5)
    ax_dp.set_title("Перепад давления ΔP", fontsize=10, loc="left", pad=4)

    # ── 3. Q + тренд ──
    if df_flow is not None and not df_flow.empty and "flow_rate" in df_flow.columns:
        df_q = df_flow.copy()
        df_q.index = df_q.index + KUNGRAD_OFFSET
        ax_q.plot(df_q.index, df_q["flow_rate"], color="#2e7d32",
                  linewidth=0.9, label="Q")
        ax_q.fill_between(df_q.index, df_q["flow_rate"], alpha=0.15, color="#2e7d32")
        if q_trend and q_trend.get("slope_per_day") is not None:
            slope_per_h = q_trend["slope_per_day"] / 24.0
            t0 = df_q.index.min()
            t_hours = np.array(
                [(t - t0).total_seconds() / 3600.0 for t in df_q.index]
            )
            q_working = df_q["flow_rate"][df_q["flow_rate"] > 0]
            q_mean = float(q_working.mean()) if len(q_working) > 0 else 0.0
            trend_line = slope_per_h * (t_hours - t_hours.mean()) + q_mean

            low_per_h = (q_trend.get("slope_low_per_day") or
                         q_trend["slope_per_day"]) / 24.0
            high_per_h = (q_trend.get("slope_high_per_day") or
                          q_trend["slope_per_day"]) / 24.0
            trend_low = low_per_h * (t_hours - t_hours.mean()) + q_mean
            trend_high = high_per_h * (t_hours - t_hours.mean()) + q_mean
            ax_q.fill_between(df_q.index, trend_low, trend_high,
                              color="#333", alpha=0.10, linewidth=0,
                              label="95% CI тренда")

            sig_mark = " ✓" if q_trend.get("mk_significant") else ""
            p_str = f", p={q_trend.get('mk_p', 1):.3f}{sig_mark}"
            ax_q.plot(
                df_q.index, trend_line, color="#333", linewidth=1.0, linestyle="--",
                label=(f"Тренд ({q_trend.get('slope_per_day', 0):+.2f}/сут{p_str})"),
            )
        ax_q.legend(fontsize=8, loc="upper right")
    else:
        ax_q.text(
            0.5, 0.5, "Нет данных дебита",
            ha="center", va="center", transform=ax_q.transAxes,
            fontsize=9, color="#888",
        )
    ax_q.set_ylabel("Q, тыс.м³/сут", fontsize=9)
    ax_q.tick_params(labelsize=8)
    ax_q.grid(True, alpha=0.3, linewidth=0.5)
    ax_q.set_title("Мгновенный дебит Q(t)", fontsize=10, loc="left", pad=4)

    # Маркеры событий на всех трёх графиках
    for ax in (ax_p, ax_dp, ax_q):
        _overlay_event_markers(ax, event_markers)

    # Подсветка top-N окон кандидатов (для этапа адаптации)
    if highlight_windows:
        colors = ["#66bb6a", "#ffa726", "#42a5f5"]  # 1=зелёный, 2=оранж, 3=синий
        for idx, w in enumerate(highlight_windows[:3]):
            start = w.get("start")
            end = w.get("end")
            if not start or not end:
                continue
            # start/end — naive Kungrad. Отрисуем в Kungrad-оси (как и данные).
            color = colors[idx] if idx < len(colors) else "#9e9e9e"
            label = f"Окно #{idx + 1}" if idx < 3 else None
            for ax in (ax_p, ax_dp, ax_q):
                ax.axvspan(start, end, color=color, alpha=0.12,
                           label=label if ax is ax_q else None)

    # Формат даты на нижней оси
    duration_h = (
        df_local.index.max() - df_local.index.min()
    ).total_seconds() / 3600.0
    if duration_h > 48:
        ax_q.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    else:
        ax_q.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
    for label in ax_q.get_xticklabels():
        label.set_rotation(30)
        label.set_ha("right")

    fig.tight_layout(pad=0.8)

    chart_path = TEMP_DIR.resolve() / f"adapt_combined_{well_number}_{chart_tag}.png"
    fig.savefig(str(chart_path), bbox_inches="tight")
    plt.close(fig)
    return str(chart_path)


# ═══════════════════════════════════════════════════════════════════
# Сравнение этапов (дельты)
# ═══════════════════════════════════════════════════════════════════

def _delta(adapt_val: float | None, obs_val: float | None) -> float | None:
    """Абсолютная дельта: adaptation − observation. None если любое None."""
    if adapt_val is None or obs_val is None:
        return None
    return adapt_val - obs_val


def _delta_pct(adapt_val: float | None, obs_val: float | None) -> float | None:
    """Процентное изменение: (adapt − obs) / obs × 100. None если obs пусто/0."""
    if adapt_val is None or obs_val is None or obs_val == 0:
        return None
    return (adapt_val - obs_val) / abs(obs_val) * 100.0


def build_comparison(obs: dict, adapt: dict) -> dict[str, Any]:
    """Посчитать дельты между этапами."""
    return {
        # Давления
        "delta_p_tube": _delta(adapt["p_tube_median"], obs["p_tube_median"]),
        "delta_p_line": _delta(adapt["p_line_median"], obs["p_line_median"]),
        "delta_dp": _delta(adapt["dp_median"], obs["dp_median"]),
        "delta_p_tube_pct": _delta_pct(adapt["p_tube_median"], obs["p_tube_median"]),
        "delta_p_line_pct": _delta_pct(adapt["p_line_median"], obs["p_line_median"]),
        "delta_dp_pct": _delta_pct(adapt["dp_median"], obs["dp_median"]),
        # Дебит
        "delta_flow_median": _delta(adapt["flow_median"], obs["flow_median"]),
        "delta_flow_avg": _delta(adapt["flow_avg"], obs["flow_avg"]),
        "delta_flow_median_pct": _delta_pct(adapt["flow_median"], obs["flow_median"]),
        "delta_flow_avg_pct": _delta_pct(adapt["flow_avg"], obs["flow_avg"]),
        # Утилизация
        "delta_utilization_pct": _delta(
            adapt["utilization_pct"], obs["utilization_pct"],
        ),
        # События
        "delta_purge_count": adapt["purge_count"] - obs["purge_count"],
        "delta_reagent_count": adapt["reagent_count"] - obs["reagent_count"],
    }


# ═══════════════════════════════════════════════════════════════════
# Автовыводы
# ═══════════════════════════════════════════════════════════════════

def _trend_phrase(trend: dict | None, unit: str, per: str = "сут") -> str:
    """Короткая фраза для тренда: ↑Рост 0.12 кгс/см²/сут (R²=0.83)."""
    if not trend or trend.get("slope_per_day") is None:
        return "недостаточно данных"
    slope = trend.get("slope_per_day") or 0.0
    direction = trend.get("direction", "flat")
    r2 = trend.get("r_squared", 0)
    if direction == "up":
        arrow, word = "↑", "рост"
    elif direction == "down":
        arrow, word = "↓", "снижение"
    else:
        arrow, word = "→", "стабильно"
    return f"{arrow} {word}, {abs(slope):.3f} {unit}/{per} (R²={r2:.2f})"


def generate_stage_narrative(
    stage: dict, stage_name: str, *, with_reagent_interp: bool = True,
) -> str:
    """Текстовое описание этапа: период, работа, характеристики, тренды.

    with_reagent_interp=False: не подмешивать качественную интерпретацию
    через реагент (для этапа наблюдения, где работ ещё нет).
    """
    lines: list[str] = []

    # Период и длительность
    lines.append(
        f"Этап {stage_name} продолжался {stage['duration_days']} сут. "
        f"(с {stage['date_from'].strftime('%d.%m.%Y') if hasattr(stage['date_from'], 'strftime') else stage['date_from']} "
        f"по {stage['date_to'].strftime('%d.%m.%Y') if hasattr(stage['date_to'], 'strftime') else stage['date_to']})."
    )

    # Рабочее время
    wh = stage.get("working_hours") or 0
    hwd = stage.get("hours_with_data") or 0
    kiv = stage.get("utilization_pct")
    dwh = stage.get("downtime_hours") or 0
    if hwd > 0:
        parts = [f"Скважина работала {wh} ч из {hwd} ч наблюдения"]
        if kiv is not None:
            parts.append(f"коэффициент использования времени (КИВ) составил {kiv:.1f}%")
        if dwh > 0:
            parts.append(f"зафиксировано {dwh} ч простоев")
        lines.append(". ".join(parts) + ".")
    else:
        lines.append("За этап отсутствуют данные давления для анализа времени работы.")

    # Давления
    pt, pl, dp = stage.get("p_tube_median"), stage.get("p_line_median"), stage.get("dp_median")
    if pt is not None:
        parts = []
        parts.append(f"Медианное трубное давление составило {pt:.2f} кгс/см²")
        if pl is not None:
            parts.append(f"линейное {pl:.2f} кгс/см²")
        if dp is not None:
            parts.append(f"перепад ΔP = {dp:.2f} кгс/см²")
        lines.append(", ".join(parts) + ".")

    # Дебит
    qm = stage.get("flow_median")
    qa = stage.get("flow_avg")
    cum = stage.get("flow_cumulative")
    qmax = stage.get("q_max")
    qmin = stage.get("q_min")
    qmax_t = stage.get("q_max_time")
    if qm is not None:
        parts = [f"Медианный дебит {qm:.2f} тыс.м³/сут"]
        if qa is not None:
            parts.append(f"средний {qa:.2f} тыс.м³/сут")
        if cum is not None:
            parts.append(f"накопленная добыча {cum:.1f} тыс.м³")
        lines.append(", ".join(parts) + ".")
        # Амплитуда Q
        if qmax is not None and qmin is not None:
            range_line = f"Диапазон Q: от {qmin:.2f} до {qmax:.2f} тыс.м³/сут"
            if qmax_t is not None and hasattr(qmax_t, 'strftime'):
                range_line += f" (пик {qmax_t.strftime('%d.%m.%Y %H:%M')})"
            lines.append(range_line + ".")
    elif qm is None and hwd > 0:
        lines.append("Дебит не рассчитан (недостаточно рабочих часов или отсутствует штуцер).")

    # События
    pc = stage.get("purge_count") or 0
    rc = stage.get("reagent_count") or 0
    rq = stage.get("reagent_qty") or 0
    ev_parts = []
    if pc > 0:
        ev_parts.append(f"{pc} продувок")
    if rc > 0:
        if rq > 0:
            ev_parts.append(f"{rc} вбросов реагента (суммарно {rq:.1f})")
        else:
            ev_parts.append(f"{rc} вбросов реагента")
    if ev_parts:
        lines.append("За этап зафиксировано: " + ", ".join(ev_parts) + ".")

    # Тренды
    trend_lines = []
    if stage.get("p_tube_trend"):
        trend_lines.append(
            f"• Тренд P трубного: {_trend_phrase(stage['p_tube_trend'], 'кгс/см²')}"
        )
    if stage.get("p_line_trend"):
        trend_lines.append(
            f"• Тренд P линейного: {_trend_phrase(stage['p_line_trend'], 'кгс/см²')}"
        )
    if stage.get("dp_trend"):
        trend_lines.append(
            f"• Тренд ΔP: {_trend_phrase(stage['dp_trend'], 'кгс/см²')}"
        )
    if stage.get("q_trend"):
        trend_lines.append(
            f"• Тренд Q: {_trend_phrase(stage['q_trend'], 'тыс.м³')}"
        )
    if trend_lines:
        lines.append("Динамика за этап:")
        lines.extend(trend_lines)

    # Интерпретация трендов (только если разрешено вызывающим)
    if with_reagent_interp:
        interp = _interpret_trends(stage)
        if interp:
            lines.append("Анализ трендов: " + interp)

    return "\n".join(lines)


def _trend_is_significant(tr: dict | None) -> bool:
    """Считаем тренд значимым по Mann-Kendall (новый robust метод).

    Для обратной совместимости с трендами, где ещё не сохранён mk_p,
    используем r_squared > 0.3 (старый критерий).
    """
    if not tr:
        return False
    if "mk_significant" in tr:
        return bool(tr["mk_significant"])
    return (tr.get("r_squared") or 0) > 0.3


def _interpret_trends(stage: dict) -> str:
    """Качественная интерпретация трендов (с правильной физической привязкой).

    Правила:
      * рост Q  → эффективность реагента, удаление воды из ствола.
      * рост ΔP → реагент вспенивает флюид — положительный эффект.
      * рост P_tube без роста Q → возможное вспенивание (набор давления).
      * плато Q + плато ΔP → реагент не контактирует с водой / требуется другой.
      * снижение Q → падение эффективности, накопление жидкости или истощение.
    """
    parts = []
    dp_tr = stage.get("dp_trend")
    q_tr = stage.get("q_trend")
    pt_tr = stage.get("p_tube_trend")

    q_up = q_tr and q_tr.get("direction") == "up" and _trend_is_significant(q_tr)
    q_dn = q_tr and q_tr.get("direction") == "down" and _trend_is_significant(q_tr)
    q_flat = q_tr and not (q_up or q_dn)

    dp_up = dp_tr and dp_tr.get("direction") == "up" and _trend_is_significant(dp_tr)
    dp_dn = dp_tr and dp_tr.get("direction") == "down" and _trend_is_significant(dp_tr)
    dp_flat = dp_tr and not (dp_up or dp_dn)

    if q_up:
        parts.append(
            "рост дебита указывает на эффективность реагента и удаление "
            "воды из ствола"
        )
    elif q_dn:
        parts.append(
            "снижение дебита — возможное накопление жидкости, падение "
            "контакта реагента с водой или истощение"
        )

    if dp_up:
        parts.append(
            "рост ΔP — реагент вспенивает флюид, облегчая вынос воды "
            "(положительный эффект)"
        )
    elif dp_dn:
        parts.append(
            "снижение ΔP может быть связано с накоплением жидкости "
            "в стволе или снижением притока"
        )

    if pt_tr and pt_tr.get("direction") == "down" and _trend_is_significant(pt_tr):
        parts.append(
            "снижение P трубного — очистка ствола от жидкости под "
            "действием реагента"
        )
    elif pt_tr and pt_tr.get("direction") == "up" and _trend_is_significant(pt_tr) and not q_up:
        parts.append(
            "рост P трубного без увеличения дебита — возможное вспенивание "
            "без прорыва"
        )

    # Плато: нет значимых трендов ни по Q, ни по ΔP
    if q_flat and dp_flat:
        parts.append(
            "отсутствует значимая динамика по Q и ΔP — возможно, реагент "
            "не контактирует с водой; рассмотреть другой состав"
        )

    return "; ".join(parts) + "." if parts else ""


def generate_conclusions(
    obs: dict, adapt: dict, comparison: dict,
    optimal: dict | None = None,
) -> list[str]:
    """Сформировать текстовые выводы по правилам.

    Длительности этапов (наблюдение/адаптация/оптимум) берутся из реальных
    статистик; сравнительные показатели (Q, ΔP, простои, продувки) считаются
    по переданному `comparison` — может быть как adapt↔obs, так и optimum↔obs.
    """
    out: list[str] = []

    # Длительности: наблюдение, адаптация, (опционально) оптимум.
    # Раньше здесь была строка "адаптации: {adapt['duration_days']} сут.", но
    # при передаче optimal_regime на месте adapt длительность подменялась на
    # оптимум. Теперь явный триплет.
    def _dur_str(stage: dict) -> str:
        # Человеко-читаемое "7 сут. 17 ч" предпочтительнее округлённого int
        return stage.get("duration_label") or f"{stage.get('duration_days', '—')} сут."
    dur_parts = [
        f"наблюдения: {_dur_str(obs)}",
        f"адаптации: {_dur_str(adapt)}",
    ]
    if optimal is not None:
        dur_parts.append(f"оптимума: {_dur_str(optimal)}")
    out.append("Продолжительность этапа " + ", ".join(dur_parts))

    # Дебит — интерпретируем как показатель эффективности реагента
    dq = comparison["delta_flow_median"]
    dq_pct = comparison["delta_flow_median_pct"]
    if dq is not None and dq_pct is not None:
        if dq_pct > 10:
            out.append(
                f"Медианный дебит вырос на {dq:+.2f} тыс.м³/сут ({dq_pct:+.1f}%) "
                f"по сравнению с этапом наблюдения — положительный эффект "
                f"реагента (вынос воды из ствола)."
            )
        elif dq_pct < -10:
            out.append(
                f"Медианный дебит снизился на {abs(dq):.2f} тыс.м³/сут "
                f"({dq_pct:+.1f}%) по сравнению с этапом наблюдения — "
                f"возможное накопление жидкости или недостаточный контакт "
                f"реагента с водой."
            )
        else:
            out.append(
                f"Дебит стабилен: изменение в пределах погрешности "
                f"({dq_pct:+.1f}%) — если ожидался прирост, реагент "
                f"вероятно не контактирует с водой."
            )

    # Перепад давления — физически связан со вспениванием флюида реагентом
    ddp = comparison["delta_dp"]
    ddp_pct = comparison["delta_dp_pct"]
    if ddp is not None:
        if ddp > 0.5:
            pct_str = f" ({ddp_pct:+.1f}\\%)" if ddp_pct is not None else ""  # \\% для LaTeX
            out.append(
                f"Перепад давления ΔP увеличился на {ddp:+.2f} кгс/см²{pct_str} — "
                f"реагент вспенивает флюид, облегчая вынос воды "
                f"(положительный признак эффективности)."
            )
        elif ddp < -0.5:
            out.append(
                f"Перепад давления ΔP снизился на {abs(ddp):.2f} кгс/см² — "
                f"возможное накопление жидкости в стволе или снижение притока."
            )
        else:
            out.append(
                f"ΔP практически не изменился ({ddp:+.2f} кгс/см²) — "
                f"реагент не оказывает выраженного вспенивающего эффекта."
            )

    # КИВ (коэффициент использования времени) — показатель стабильности
    du = comparison["delta_utilization_pct"]
    if du is not None:
        if du > 5:
            out.append(
                f"КИВ вырос на {du:+.1f} п.п. — скважина стала работать "
                f"стабильнее, меньше простаивает."
            )
        elif du < -5:
            out.append(
                f"КИВ снизился на {abs(du):.1f} п.п. — увеличилась доля "
                f"нерабочего времени."
            )

    # Простои — отдельный важный показатель
    obs_down = obs.get("downtime_hours") or 0
    adapt_down = adapt.get("downtime_hours") or 0
    if obs_down > 0 and adapt_down is not None:
        if obs_down >= 4 or adapt_down >= 4:
            if adapt_down < obs_down * 0.5 and obs_down > 0:
                out.append(
                    f"Время простоев сократилось с {obs_down:.1f} ч (наблюдение) "
                    f"до {adapt_down:.1f} ч (адаптация) — значимое улучшение."
                )
            elif adapt_down > obs_down * 1.5:
                out.append(
                    f"Время простоев выросло с {obs_down:.1f} ч до "
                    f"{adapt_down:.1f} ч — негативный признак."
                )

    # Продувки — чем меньше, тем лучше для скважины и реагента
    dpurge = comparison["delta_purge_count"]
    if dpurge < 0:
        out.append(
            f"Количество продувок уменьшилось на {abs(dpurge)} — "
            f"положительный признак эффективности реагента."
        )
    elif dpurge > 0:
        out.append(
            f"Количество продувок увеличилось на {dpurge} — "
            f"возможно, текущий режим реагента недостаточен."
        )

    # Реагенты — факт применения
    if adapt["reagent_count"] > 0 and obs["reagent_count"] == 0:
        out.append(
            f"На этапе адаптации применялись реагенты ({adapt['reagent_count']} "
            f"вбросов, расход {adapt['reagent_qty']:.1f})."
        )

    return out


# ═══════════════════════════════════════════════════════════════════
# Технические данные скважины (§1 отчёта): конструкция, перфорация,
# карта расположения относительно соседей
# ═══════════════════════════════════════════════════════════════════

def _collect_well_tech_data(
    db: Session, well: Well, *, render_map: bool = True,
    neighbor_limit: int = 12,
) -> dict:
    """Собирает данные для §1 «Технические данные» PDF-отчёта.

    Возвращает dict:
      - construction:  параметры конструкции (или None если нет записи)
      - perforation:   список интервалов перфорации (top, bottom, idx)
      - neighbors:     список dict {number, name, lat, lon, distance_km}
      - map_chart_path: абсолютный путь к PNG карты (или None если нет coords)
    """
    out: dict[str, Any] = {
        "construction": None,
        "perforation": [],
        "neighbors": [],
        "map_chart_path": None,
    }

    # ── 1. well_construction (последняя запись по data_as_of) ──
    try:
        row = db.execute(text("""
            SELECT id, prod_casing_diam_mm, prod_casing_depth_m,
                   current_bottomhole_m, horizon, tubing_diam_mm,
                   tubing_shoe_depth_m, packer_depth_m, adapter_depth_m,
                   pattern_stuck_depth_m, choke_diam_mm, data_as_of
            FROM well_construction
            WHERE TRIM(well_no) = :wno
            ORDER BY data_as_of DESC NULLS LAST, id DESC
            LIMIT 1
        """), {"wno": str(well.number).strip()}).fetchone()
    except Exception:
        log.warning("well_construction lookup failed for well %s", well.id)
        row = None

    construction_id: int | None = None
    if row:
        construction_id = row[0]
        out["construction"] = {
            "prod_casing_diam_mm":  _safe_num(row[1]),
            "prod_casing_depth_m":  _safe_num(row[2]),
            "current_bottomhole_m": _safe_num(row[3]),
            "horizon":              row[4] or None,
            "tubing_diam_mm":       _safe_num(row[5]),
            "tubing_shoe_depth_m":  _safe_num(row[6]),
            "packer_depth_m":       _safe_num(row[7]),
            "adapter_depth_m":      _safe_num(row[8]),
            "pattern_stuck_depth_m": _safe_num(row[9]),
            "choke_diam_mm":        _safe_num(row[10]),
            "data_as_of":           row[11].isoformat() if row[11] else None,
        }

    # ── 2. Интервалы перфорации ──
    if construction_id:
        try:
            rows = db.execute(text("""
                SELECT interval_index, top_depth_m, bottom_depth_m
                FROM well_perforation_interval
                WHERE well_construction_id = :cid
                ORDER BY interval_index, top_depth_m
            """), {"cid": construction_id}).fetchall()
            out["perforation"] = [
                {
                    "interval_index": r[0],
                    "top_depth_m": _safe_num(r[1]),
                    "bottom_depth_m": _safe_num(r[2]),
                }
                for r in rows
            ]
        except Exception:
            log.warning("perforation lookup failed for well %s", well.id)

    # ── 3. Соседи: все скважины с координатами, кроме текущей ──
    if well.lat is not None and well.lon is not None:
        try:
            from math import asin, cos, radians, sin, sqrt

            def _haversine_km(la1, lo1, la2, lo2):
                R = 6371.0
                la1, lo1, la2, lo2 = map(radians, (la1, lo1, la2, lo2))
                dlat = la2 - la1
                dlon = lo2 - lo1
                a = sin(dlat / 2) ** 2 + cos(la1) * cos(la2) * sin(dlon / 2) ** 2
                return 2 * R * asin(sqrt(a))

            others = (
                db.query(Well)
                .filter(Well.id != well.id,
                        Well.lat.isnot(None), Well.lon.isnot(None))
                .all()
            )
            with_dist = [
                {
                    "id": w.id,
                    "number": str(w.number),
                    "name": w.name,
                    "lat": float(w.lat),
                    "lon": float(w.lon),
                    "distance_km": _haversine_km(
                        float(well.lat), float(well.lon),
                        float(w.lat), float(w.lon),
                    ),
                }
                for w in others
            ]
            with_dist.sort(key=lambda x: x["distance_km"])
            out["neighbors"] = with_dist[:neighbor_limit]
        except Exception:
            log.warning("neighbors lookup failed for well %s", well.id)

    # ── 4. Рендер карты ──
    if render_map and well.lat is not None and well.lon is not None:
        try:
            from backend.services.well_map_renderer import render_well_map_png
            _ensure_dirs()
            map_path = TEMP_DIR / f"well_map_{well.id}.png"
            rendered = render_well_map_png(
                active={
                    "number": str(well.number),
                    "name": well.name,
                    "lat": float(well.lat),
                    "lon": float(well.lon),
                },
                neighbors=out["neighbors"],
                output_path=map_path,
            )
            if rendered:
                # xelatex запускается с cwd=TEMP_DIR.resolve() — путь должен
                # быть абсолютным, иначе \includegraphics не найдёт файл.
                out["map_chart_path"] = str(Path(rendered).resolve())
        except Exception:
            log.exception("well map render failed for well %s", well.id)

    return out


def _safe_num(v) -> float | None:
    """SQL-numeric → float | None (NaN/Inf тоже → None)."""
    if v is None:
        return None
    try:
        f = float(v)
        if np.isnan(f) or np.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _build_chart_captions(analysis: dict) -> dict[str, str]:
    """Короткие динамические подписи к 4 графикам §2 PDF-отчёта.

    Возвращает dict {pressures, dp, flow_dt, monthly} → строка-предложение
    с ключевыми числами (медианы, диапазоны, простой). Текст plain, без
    LaTeX-команд: дополнительное экранирование происходит в роутере.
    """
    out: dict[str, str] = {}

    # Pressures
    pieces: list[str] = []
    if analysis.get("p_wellhead_median") is not None:
        pieces.append(f"P устья мед. {analysis['p_wellhead_median']:.1f}")
    if analysis.get("p_flowline_median") is not None:
        pieces.append(f"P шлейфа мед. {analysis['p_flowline_median']:.1f}")
    if pieces:
        out["pressures"] = (
            "Динамика устьевого, затрубного, линейного и статического давлений "
            "по суткам. " + ", ".join(pieces) + " кгс/см²."
        )

    # ΔP
    if analysis.get("dp_median") is not None:
        dp_med = analysis["dp_median"]; dp_avg = analysis.get("dp_avg")
        avg_part = (f", ср. {dp_avg:.2f}" if dp_avg is not None else "")
        out["dp"] = (
            f"Перепад давления по суткам (устье минус линия). "
            f"Мед. {dp_med:.2f}{avg_part} кгс/см². "
            f"Рост перепада — признак улучшения работы скважины "
            f"(рост дебита, более стабильный режим)."
        )

    # Flow + downtime (с помесячными пунктирными линиями тренда Q)
    pieces = []
    if analysis.get("q_total_avg") is not None:
        pieces.append(f"среднее Q общий {analysis['q_total_avg']:.1f}")
    if analysis.get("q_working_avg") is not None:
        pieces.append(f"среднее Q рабочий {analysis['q_working_avg']:.1f}")
    sd_total = analysis.get("shutdown_min_total") or 0
    sd_days = analysis.get("shutdown_days_count") or 0
    if pieces:
        cap = (
            "Линиями — Q общий и рабочий (левая ось), барами — простой "
            "в мин/сут (правая ось). Пунктир — линия линейной регрессии "
            "(тренд) ПО КАЖДОМУ КАЛЕНДАРНОМУ МЕСЯЦУ отдельно; число рядом с линией — "
            "наклон, тыс.м³/сут за день. " + ", ".join(pieces) + " тыс.м³/сут."
        )
        if sd_total > 0:
            cap += (f" Простой суммарно {sd_total:.0f} мин "
                    f"(~{sd_total/60:.1f} ч за {sd_days} сут.).")
        else:
            cap += " Простоев за период не зафиксировано."
        # Помесячные тренды Q общ. — короткая сводка (если months есть)
        months = analysis.get("months") or []
        per_month = []
        for m in months:
            slope = m.get("trend_q_total")
            label = m.get("month_label") or m.get("month")
            if slope is not None and label:
                per_month.append(f"{label} {slope:+.2f}")
        if per_month:
            cap += " Помесячный наклон Q общ.: " + "; ".join(per_month) + "."
        out["flow_dt"] = cap

    # Monthly bars
    months = analysis.get("months") or []
    if months:
        out["monthly"] = (
            f"Помесячные значения Q общ. и Q раб. (среднее и медиана) — "
            f"{len(months)} мес. в выборке. Медиана устойчивее к выбросам "
            f"и простоям, поэтому даёт более «спокойную» оценку режима, "
            f"чем среднее."
        )

    return out


def _build_observation_intro(
    db: Session, well_id: int, obs_from: datetime, obs_to: datetime,
) -> dict[str, Any]:
    """Сводка для §3.1: дата приёмки, список датчиков, шлюз дозирования.

    Источники:
      - equipment_installation JOIN equipment JOIN lora_sensors
        (датчики, активные на момент этапа наблюдения).
      - Шлюз дозирования — статичный текст «Шлюз устьевой ШУ 50-36-550».

    Возвращает dict (для прокидывания в LaTeX-шаблон):
      {
        "sensors": [{serial, label, position, position_label,
                     installed_at, installed_at_fmt}, ...],
        "earliest_install_dt": datetime | None,
        "earliest_install_fmt": "DD.MM.YYYY" | None,
        "acceptance_date_fmt": "DD.MM.YYYY",  # дата приёмки
        "gateway_text": "...",  # описание шлюза
        "intro_text": "...",   # готовая фраза целиком
      }
    """
    obs_from_dt = obs_from
    obs_to_dt = obs_to
    sensors: list[dict[str, Any]] = []
    try:
        rows = db.execute(text("""
            SELECT ls.serial_number, ls.label, ls.csv_column,
                   ei.installed_at, ei.removed_at
            FROM equipment_installation ei
            JOIN equipment e ON e.id = ei.equipment_id
            JOIN lora_sensors ls ON ls.serial_number = e.serial_number
            WHERE ei.well_id = :wid
              AND ei.installed_at <= :obs_to
              AND (ei.removed_at IS NULL OR ei.removed_at >= :obs_from)
            ORDER BY ei.installed_at ASC
        """), {
            "wid": well_id, "obs_from": obs_from_dt, "obs_to": obs_to_dt,
        }).fetchall()
    except Exception:
        log.exception("observation-intro sensor query failed for well %s",
                      well_id)
        rows = []

    for serial, label, csv_col, installed_at, _removed_at in rows:
        position = "tube" if csv_col == "Ptr" else "line"
        position_label = "устье" if position == "tube" else "шлейф"
        sensors.append({
            "serial": serial,
            "label": label,
            "position": position,
            "position_label": position_label,
            "installed_at": installed_at,
            "installed_at_fmt": (
                installed_at.strftime("%d.%m.%Y")
                if hasattr(installed_at, "strftime") else None
            ),
        })

    earliest_dt = None
    if sensors:
        with_dt = [s for s in sensors if s.get("installed_at")]
        if with_dt:
            earliest_dt = min(s["installed_at"] for s in with_dt)
    earliest_fmt = (earliest_dt.strftime("%d.%m.%Y")
                    if earliest_dt else None)

    # Дата приёмки: ранняя установка датчика, иначе старт этапа наблюдения.
    if earliest_dt:
        acceptance_date = earliest_dt
    else:
        acceptance_date = obs_from_dt
    acceptance_fmt = acceptance_date.strftime("%d.%m.%Y")

    gateway_text = (
        "Для дозирования реагента на устье установлен "
        "Шлюз устьевой ШУ\\,50--36--550."
    )

    # Текст-вступление целиком (для LaTeX). Список датчиков — в отдельной
    # таблице через шаблон, здесь только основная фраза.
    if sensors:
        sensor_count = len(sensors)
        intro_text = (
            f"С {acceptance_fmt} скважина принята для проведения работ по "
            f"адаптации согласно договору. Установлено {sensor_count} "
            f"датчик(ов) давления (см. таблицу 3.0); "
            f"для дозирования реагента на устье установлен Шлюз устьевой "
            f"ШУ\\,50--36--550."
        )
    else:
        intro_text = (
            f"С {acceptance_fmt} скважина принята для проведения работ по "
            f"адаптации согласно договору. Для дозирования реагента на "
            f"устье установлен Шлюз устьевой ШУ\\,50--36--550."
        )

    return {
        "sensors": sensors,
        "earliest_install_dt": earliest_dt,
        "earliest_install_fmt": earliest_fmt,
        "acceptance_date_fmt": acceptance_fmt,
        "gateway_text": gateway_text,
        "intro_text": intro_text,
    }


def _build_adaptation_intro(
    db: Session, well_id: int, well_number: str,
    adapt_from: datetime, adapt_to: datetime,
) -> dict[str, Any]:
    """Сводка для §4.1: введение в этап адаптации с информацией о датчиках.

    Возвращает dict:
      {
        "sensors": [{serial, model, position_label}, ...],
        "date_from_fmt": "DD.MM.YYYY",
        "date_to_fmt": "DD.MM.YYYY",
        "intro_text": "...",   # готовая фраза целиком
        "intro_text_tex": "...",  # LaTeX-escaped версия
      }
    """
    sensors: list[dict[str, Any]] = []
    try:
        rows = db.execute(text("""
            SELECT e.serial_number, e.name, ls.csv_column
            FROM equipment_installation ei
            JOIN equipment e ON e.id = ei.equipment_id
            LEFT JOIN lora_sensors ls ON ls.serial_number = e.serial_number
            WHERE ei.well_id = :wid
              AND ei.installed_at <= :adapt_to
              AND (ei.removed_at IS NULL OR ei.removed_at >= :adapt_from)
              AND e.name ILIKE '%SMOD%'
            ORDER BY ei.installed_at ASC
        """), {
            "wid": well_id, "adapt_from": adapt_from, "adapt_to": adapt_to,
        }).fetchall()
    except Exception:
        log.exception("adaptation-intro sensor query failed for well %s", well_id)
        rows = []

    sensor_serials = []
    sensor_model = None
    for serial, name, csv_col in rows:
        position_label = "устье" if csv_col == "Ptr" else "шлейф" if csv_col else "—"
        sensors.append({
            "serial": serial,
            "model": name,
            "position_label": position_label,
        })
        sensor_serials.append(serial)
        if not sensor_model and name:
            sensor_model = name

    date_from_fmt = adapt_from.strftime("%d.%m.%Y")
    date_to_fmt = adapt_to.strftime("%d.%m.%Y")

    # Формируем текст введения
    if sensors:
        serials_str = ", ".join(sensor_serials)
        model_str = sensor_model or "SMOD"
        intro_text = (
            f"В период с {date_from_fmt} по {date_to_fmt} с целью адаптации технологии "
            f"к условиям скважины №{well_number} проводились работы по выбору "
            f"оптимального реагента, оптимальной концентрации и периода дозирования. "
            f"Параметры работы скважины фиксировались в режиме реального времени "
            f"датчиками {model_str} (серийные номера: {serials_str}). "
            f"Частота опроса — 1 измерение в минуту."
        )
    else:
        intro_text = (
            f"В период с {date_from_fmt} по {date_to_fmt} с целью адаптации технологии "
            f"к условиям скважины №{well_number} проводились работы по выбору "
            f"оптимального реагента, оптимальной концентрации и периода дозирования."
        )

    # LaTeX-версия
    from backend.services.daily_report_service import _tex_escape
    intro_text_tex = _tex_escape(intro_text)

    return {
        "sensors": sensors,
        "date_from_fmt": date_from_fmt,
        "date_to_fmt": date_to_fmt,
        "intro_text": intro_text,
        "intro_text_tex": intro_text_tex,
    }


def _build_monthly_uzkor_unitool_blocks(
    month_descriptions: list[dict],
    months: list[dict],
    unitool_by_month: dict,
    unitool_meta: dict,
) -> list[dict]:
    """Готовит для PDF помесячные блоки UzKor + UniTool.

    Зеркало JS-логики в customer_daily.html (≈4366-4429). Для каждой записи
    `month_descriptions` (получена из snapshot.monthly_desc) собирает:

      {
        "label":     "Январь 2026",
        "uzkor": {
          "text":  "За месяц в выборке … (полный текст UzKorGaz)"
        },
        "unitool": {              # отсутствует, если данных UniTool нет
          "available":   True/False,
          "days":        15,                  # сут измерений UniTool
          "lines": [                          # готовые строки-факты
            "За месяц записано 15 сут измерений.",
            "Q (расчётный): среднее 14.3, медиана 14.5 тыс.м³/сут.",
            ...
          ],
          "compat_text": "Сопоставимо…" | "⚠ Несопоставимо…" | "",
        }
      }

    Если UniTool overlay вообще не включён (`unitool_meta` пустой и by_month
    пустой) — поле `unitool` в результате не появляется (блок не рисуется).
    """
    def _fnum(v, d: int = 2) -> str:
        if v is None:
            return "—"
        try:
            f = float(v)
            if f != f:
                return "—"
            return f"{f:.{d}f}"
        except (TypeError, ValueError):
            return "—"

    def _pct_signed(v) -> str:
        if v is None:
            return ""
        try:
            f = float(v)
            if f != f:
                return ""
            return ("+" if f >= 0 else "") + f"{f:.1f}\\%"  # \\% для LaTeX
        except (TypeError, ValueError):
            return ""

    cust_by_label = {m.get("month_label"): m for m in (months or [])}
    has_unitool_overlay = bool(unitool_by_month) or bool(unitool_meta)
    out: list[dict] = []

    def _escape_pct(s: str) -> str:
        """Экранирует % как \\% для LaTeX, если ещё не экранировано."""
        import re
        # Заменяем % на \%, но не трогаем уже экранированные \%
        return re.sub(r'(?<!\\)%', r'\\%', s)

    for d in month_descriptions or []:
        label = d.get("label") or ""
        # Экранируем % в тексте из snapshot (старые снимки могут содержать голый %)
        raw_text = d.get("text") or ""
        item: dict = {"label": label, "uzkor": {"text": _escape_pct(raw_text)}}

        if not has_unitool_overlay:
            out.append(item)
            continue

        o = unitool_by_month.get(label) or {}
        cust_r = cust_by_label.get(label) or {}

        if o and o.get("days"):
            lines: list[str] = []
            lines.append(f"За месяц записано {int(o.get('days'))} сут измерений.")
            if o.get("mean_q") is not None or o.get("median_q") is not None:
                lines.append(
                    f"Q (расчётный): среднее {_fnum(o.get('mean_q'))}, "
                    f"медиана {_fnum(o.get('median_q'))} тыс.м³/сут."
                )
            if o.get("mean_dp") is not None or o.get("median_dp") is not None:
                lines.append(
                    f"Перепад давления: среднее {_fnum(o.get('mean_dp'))}, "
                    f"медиана {_fnum(o.get('median_dp'))} кгс/см²."
                )
            if o.get("mean_p_tube") is not None:
                lines.append(
                    f"Среднее устьевое давление: {_fnum(o.get('mean_p_tube'))} кгс/см²."
                )

            cust_days = int(cust_r.get("days") or 0)
            our_days = int(o.get("days") or 0)
            denom = max(cust_days, our_days)
            cmp_ratio = (abs(cust_days - our_days) / denom * 100.0) if denom else 0.0
            compat = cmp_ratio <= 10.0

            if compat and cust_r.get("mean_q_total") not in (None, 0) \
                    and o.get("mean_q") is not None:
                try:
                    dQ = (float(o["mean_q"]) - float(cust_r["mean_q_total"])) \
                        / float(cust_r["mean_q_total"]) * 100.0
                except (TypeError, ValueError, ZeroDivisionError):
                    dQ = None
                dDp = None
                if cust_r.get("mean_dp") not in (None, 0) \
                        and o.get("mean_dp") is not None:
                    try:
                        dDp = (float(o["mean_dp"]) - float(cust_r["mean_dp"])) \
                            / float(cust_r["mean_dp"]) * 100.0
                    except (TypeError, ValueError, ZeroDivisionError):
                        dDp = None
                parts: list[str] = []
                if dQ is not None:
                    parts.append(
                        f"Q {'↑' if dQ >= 0 else '↓'} {_pct_signed(dQ)}"
                    )
                if dDp is not None:
                    parts.append(
                        f"ΔP {'↑' if dDp >= 0 else '↓'} {_pct_signed(dDp)}"
                    )
                if parts:
                    lines.append("Расхождение с UzKorGaz: " + ", ".join(parts) + ".")

            compat_text = (
                f"Сопоставимо с UzKorGaz ({cust_days} сут): "
                f"разница {cmp_ratio:.1f}\\% ≤ 10\\%."  # \\% для LaTeX
                if compat else
                f"⚠ Несопоставимо с UzKorGaz ({cust_days} сут): "
                f"разница {cmp_ratio:.1f}\\% > 10\\%."  # \\% для LaTeX
            )

            item["unitool"] = {
                "available": True,
                "days": our_days,
                "lines": lines,
                "compat_text": compat_text,
            }
        else:
            equip_dt = unitool_meta.get("equip_dt") if unitool_meta else None
            note = (
                f"UniTool: нет данных за этот месяц"
                + (f" (датчик установлен {str(equip_dt)[:10]})." if equip_dt else ".")
            )
            item["unitool"] = {
                "available": False,
                "days": 0,
                "lines": [note],
                "compat_text": "",
            }

        out.append(item)

    return out


def _enrich_period_with_customer_data(
    db: Session, well: Well,
    period_from: date, period_to: date,
    analysis: dict, render_charts: bool, p_idx: int,
    block_snapshot: dict | None = None,
    chart_dir: Path | None = None,
) -> None:
    """Дополняет analysis-словарь данными со страницы /customer-daily.

    Принцип (см. TZ_chapter2_customer_data.md §3.1):
    **snapshot — единственный источник правды для PDF.** Если
    `block_snapshot` передан и содержит ключевые поля (describe/monthly/
    monthly_desc/chart/unitool/strict_compare) — берём их оттуда.
    Если snapshot пустой (старые блоки) — fallback на пересчёт из well_daily.

    Поля analysis:
      - describe (описательная статистика, 8 колонок)
      - months   (помесячная агрегация и тренды)
      - month_descriptions (текст по месяцам)
      - month_descriptions_blocks (UzKor + UniTool блоки на каждый месяц)
      - overlap_blocks (3 текстовых блока UzKorGaz/UniTool/Совместный)
      - strict_compare (таблица строгого сравнения, из snapshot)
      - unitool_daily (для overlay на 4 графиках, из snapshot)
      - unitool_by_month (по месяцам, из snapshot — для блоков UniTool)
      - chart_paths (4 PNG-пути)

    chart_dir: эфемерный temp-каталог per-request (если None — TEMP_DIR).
    Используется чтобы PNG не накапливались в static/generated/temp.

    Изменяет analysis in-place. Все ошибки молча проглатываются — глава
    останется частичной, но не упадёт.
    """
    try:
        from backend.services.customer_daily_service import (
            load_for_well, describe_well_period,
            monthly_stats, monthly_description, well_chart_payload,
            period_summary_text, build_overlap_blocks,
        )
    except Exception:
        log.exception("customer_daily_service import failed")
        return

    snap = block_snapshot or {}

    # ── 3 текстовых блока + строгое сравнение + unitool overlay из snapshot ──
    # Это ЕДИНСТВЕННЫЙ источник для этих элементов: пересчёта нет.
    try:
        from backend.services.daily_report_service import _tex_escape
        raw_overlap = build_overlap_blocks(snap) or []
        # Эскейпим title и lines.{label,value} — они приходят из снапшота
        # и могут содержать LaTeX-спецсимволы (типичный случай: "well_daily").
        for _ob in raw_overlap:
            if _ob.get("title"):
                _ob["title"] = _tex_escape(str(_ob["title"]))
            for _ln in (_ob.get("lines") or []):
                if _ln.get("label"):
                    _ln["label"] = _tex_escape(str(_ln["label"]))
                if _ln.get("value"):
                    _ln["value"] = _tex_escape(str(_ln["value"]))
        analysis["overlap_blocks"] = raw_overlap
    except Exception:
        log.exception("build_overlap_blocks failed")
        analysis["overlap_blocks"] = []
    analysis["strict_compare"] = snap.get("strict_compare") or None
    analysis["unitool_daily"]  = ((snap.get("unitool") or {}).get("daily")) or None
    # by_month: dict ключ → {days, mean_q, median_q, mean_dp, median_dp, mean_p_tube}.
    # Ключи продублированы в snapshot (YYYY-MM и "Месяц YYYY") — берём как есть.
    analysis["unitool_by_month"] = ((snap.get("unitool") or {}).get("by_month")) or {}
    # Метаданные UniTool для пояснений в блоках по месяцам без данных.
    _ut = snap.get("unitool") or {}
    analysis["unitool_meta"] = {
        "equip_dt": _ut.get("equip_dt"),
        "first_date": _ut.get("first_date"),
        "last_date": _ut.get("last_date"),
        "choke_mm": _ut.get("choke_mm"),
    } if _ut else {}

    # Готовим 5 строк таблицы строгого сравнения (для LaTeX).
    # snapshot.strict_compare: {cust_q_total,cust_q_working,cust_dp,cust_p_tube,
    #   cust_p_line, our_q, our_dp, our_p_tube, our_p_line} с {n,mean,median,...}
    sc = analysis["strict_compare"]
    if sc:
        def _f(v, d=2):
            try:
                f = float(v);
                if f != f or f in (float('inf'), float('-inf')): return "—"
                return f"{f:.{d}f}"
            except (TypeError, ValueError):
                return "—"
        def _sf(v, d=2):
            try:
                f = float(v)
                if f != f: return "—"
                return f"{f:+.{d}f}"
            except (TypeError, ValueError):
                return "—"
        def _delta(o, c):
            if o is None or c is None: return None
            try: return float(o) - float(c)
            except (TypeError, ValueError): return None
        rows = []
        for label, ckey, okey in (
            ("Q общий, тыс.м³/сут", "cust_q_total", "our_q"),
            ("Q рабочий, тыс.м³/сут", "cust_q_working", "our_q"),
            ("ΔP, кгс/см²", "cust_dp", "our_dp"),
            ("P устье, кгс/см²", "cust_p_tube", "our_p_tube"),
            ("P шлейф, кгс/см²", "cust_p_line", "our_p_line"),
        ):
            c = sc.get(ckey) or {}
            o = sc.get(okey) or {}
            rows.append({
                "label":         label,
                "cust_mean":     _f(c.get("mean")),
                "cust_median":   _f(c.get("median")),
                "our_mean":      _f(o.get("mean")),
                "our_median":    _f(o.get("median")),
                "delta_mean":    _sf(_delta(o.get("mean"),   c.get("mean"))),
                "delta_median":  _sf(_delta(o.get("median"), c.get("median"))),
            })
        analysis["strict_compare_rows"] = rows
    else:
        analysis["strict_compare_rows"] = []

    try:
        df = load_for_well(db, str(well.number),
                           d_from=period_from, d_to=period_to)
    except Exception:
        log.exception("load_for_well failed for %s %s..%s",
                      well.number, period_from, period_to)
        return

    if df is None or df.empty:
        analysis["describe"] = []
        analysis["months"] = []
        analysis["month_descriptions"] = []
        analysis["chart_paths"] = {}
        return

    # describe — из snapshot если есть, иначе пересчитываем
    if snap.get("describe"):
        analysis["describe"] = snap["describe"]
    else:
        try:
            analysis["describe"] = describe_well_period(df) or []
        except Exception:
            log.exception("describe_well_period failed")
            analysis["describe"] = []

    # monthly + monthly_desc — из snapshot если есть, иначе пересчитываем
    if snap.get("monthly"):
        analysis["months"] = snap["monthly"]
        analysis["month_descriptions"] = snap.get("monthly_desc") or []
    else:
        try:
            months = monthly_stats(df) or []
            analysis["months"] = months
            analysis["month_descriptions"] = monthly_description(months) or []
        except Exception:
            log.exception("monthly_stats / description failed")
            analysis.setdefault("months", [])
            analysis.setdefault("month_descriptions", [])

    # Динамическая сводка периода (1–2 предложения с ключевыми числами).
    try:
        analysis["period_summary"] = period_summary_text(
            analysis, analysis.get("months") or [],
        )
    except Exception:
        log.exception("period_summary_text failed")
        analysis["period_summary"] = ""

    # ── Помесячные блоки UzKor + UniTool (для §«Развёрнутое описание»). ──
    # Зеркало JS-логики в customer_daily.html (≈ строки 4366-4429): для каждой
    # записи в month_descriptions добавляем UniTool-блок с метриками датчика
    # и оценкой «сопоставимо / несопоставимо» (разница длительности ≤ 10%).
    analysis["month_descriptions_blocks"] = _build_monthly_uzkor_unitool_blocks(
        analysis.get("month_descriptions") or [],
        analysis.get("months") or [],
        analysis.get("unitool_by_month") or {},
        analysis.get("unitool_meta") or {},
    )

    # Динамические подписи графиков (короткие, с числами для PDF-caption).
    try:
        analysis["chart_captions"] = _build_chart_captions(analysis)
    except Exception:
        log.exception("chart captions build failed")
        analysis["chart_captions"] = {}

    # ── 4 PNG-диаграммы ──
    chart_paths: dict[str, str] = {}
    if render_charts:
        try:
            from backend.services import customer_chart_renderer as ccr
            payload = well_chart_payload(df)
            # Эфемерный каталог per-request — PNG не лежат в static/generated/temp.
            # Если chart_dir не задан (старые вызовы) — fallback на TEMP_DIR.
            if chart_dir is not None:
                chart_dir.mkdir(parents=True, exist_ok=True)
                base = chart_dir / f"cust_p{p_idx}_w{well.id}"
            else:
                _ensure_dirs()
                base = TEMP_DIR / f"cust_p{p_idx}_w{well.id}"

            # Передаём overlay UniTool (если есть в snapshot) в каждый рендер.
            # Поддерживают параметр render_pressures/dp/flow_dt (см. §6.1 ТЗ).
            _overlay = analysis.get("unitool_daily")
            renders = (
                ("pressures",    ccr.render_pressures_chart),
                ("dp",           ccr.render_dp_chart),
                ("flow_dt",      ccr.render_flow_downtime_chart),
            )
            for key, fn in renders:
                try:
                    out = fn(payload, str(base) + f"_{key}.png",
                             unitool_overlay=_overlay)
                    if out:
                        chart_paths[key] = str(Path(out).resolve())
                except Exception:
                    log.exception("customer chart %s failed", key)

            try:
                m_out = ccr.render_monthly_bars(
                    analysis.get("months") or [],
                    str(base) + "_monthly.png",
                )
                if m_out:
                    chart_paths["monthly"] = str(Path(m_out).resolve())
            except Exception:
                log.exception("customer chart monthly failed")
        except Exception:
            log.exception("customer chart renderer setup failed")

    analysis["chart_paths"] = chart_paths

    # Адаптивная раскладка графиков для LaTeX. Шаблон итерирует ряды
    # (по 1-2 графика в ряду). Зависит от parts.* и chart_paths.
    analysis["chart_grid_rows"] = _build_chart_grid_rows(
        analysis.get("parts") or {}, chart_paths,
        analysis.get("chart_captions") or {},
    )


def _build_chart_grid_rows(
    parts: dict, chart_paths: dict, chart_captions: dict,
) -> list[list[dict]]:
    """Готовит адаптивную сетку графиков для LaTeX-шаблона.

    Возвращает список рядов, где каждый ряд — список графиков
    (`{key, path, label, caption}`). Включает только графики, у которых
    есть и `parts[key]=True`, и реальный PNG-путь.

    Раскладка:
      4 графика  →  2×2  (2 ряда по 2)
      3 графика  →  1×2 + 1×1 на отдельной строке (full-width)
      2 графика  →  1×2  (одна строка)
      1 график   →  1×1  (full-width)
      0          →  пусто

    Этим решается проблема «дырки в 2×2 при отключении графика»:
    раскладка адаптируется под количество включённых.
    """
    labels = {
        "pressures": "Давления, кгс/см²",
        "dp":        "Перепад давления, кгс/см²",
        "flow_dt":   "Дебиты и простой",
        "monthly":   "Помесячно: Q",
    }
    part_keys = {
        "pressures": "pressures_chart",
        "dp":        "dp_chart",
        "flow_dt":   "flow_dt_chart",
        "monthly":   "monthly_chart",
    }

    enabled: list[dict] = []
    for key in ("pressures", "dp", "flow_dt", "monthly"):
        if not parts.get(part_keys[key], True):
            continue
        path = chart_paths.get(key)
        if not path:
            continue
        enabled.append({
            "key":     key,
            "path":    path,
            "label":   labels[key],
            "caption": chart_captions.get(key) or "",
        })

    n = len(enabled)
    if n == 0:
        return []
    if n == 1:
        return [[enabled[0]]]
    if n == 2:
        return [enabled[:2]]
    if n == 3:
        # Первые два в паре, третий full-width — визуально лучше «1×3 в ряд».
        return [enabled[:2], [enabled[2]]]
    # n >= 4: первые 4 разбиваем 2×2, остальные (теоретически невозможно сейчас,
    # но на вырост — full-width одиночные).
    rows = [enabled[:2], enabled[2:4]]
    for extra in enabled[4:]:
        rows.append([extra])
    return rows


# ═══════════════════════════════════════════════════════════════════
# Основной сборщик данных (для debug-эндпоинта Этапа 1)
# ═══════════════════════════════════════════════════════════════════

def collect_report_data(
    db: Session, well_id: int,
    obs_from: date | datetime | None = None,
    obs_to: date | datetime | None = None,
    adapt_from: date | datetime | None = None,
    adapt_to: date | datetime | None = None,
    render_charts: bool = True,
    include_reagent_effectiveness: bool = False,
    obs_description_override: str | None = None,
    adapt_description_override: str | None = None,
    # Оптимальный режим
    optimal_from: datetime | None = None,    # manual override (начало окна)
    optimal_to: datetime | None = None,      # manual override (конец окна)
    optimal_window_days: int = 3,            # ширина окна для автодетекта
    optimal_mode: str = "auto",              # "auto" | "manual" | "off"
    # Глава «Анализ исходных данных» (по данным заказчика)
    include_customer_chapter: bool = False,
    customer_periods: list[dict] | None = None,
    # [{"period_from": "YYYY-MM-DD", "period_to": "YYYY-MM-DD", "description": "..."}]
    # Порог простоя: ΔP < threshold ИЛИ purge_flag → точка считается простоем.
    dp_threshold: float = 0.1,
    # ── Режимы быстрого превью (минимизировать работу) ──
    # fast_chapter_only — пропустить расчёты глав, не относящихся к выбранной.
    # Допустимые значения: 'well_info' | 'customer_data' | 'observation' |
    # 'adaptation' | 'comparison' | 'charts_compare' | None.
    fast_chapter_only: str | None = None,
    # fast_block_only — превью одного блока (only_block_id на уровне роутера).
    # Подавляет рендер графиков самой главы и расчёты сравнений: на странице
    # видна только сама плитка блока + базовая шапка главы. Самый быстрый режим.
    fast_block_only: bool = False,
    # chart_dir — эфемерный каталог per-request для PNG главы 2 (плитки заказчика,
    # сравнения участков, розы критериев). Если None — fallback на TEMP_DIR
    # (старое поведение, может приводить к накоплению файлов). Роутер
    # `_build_pdf_response` создаёт TemporaryDirectory и передаёт его сюда.
    chart_dir: Path | None = None,
) -> dict[str, Any]:
    """Собрать все данные для отчёта об адаптации.

    Если указаны все 4 даты периодов, используются они (с базовой валидацией:
    adapt_from > obs_to, adapt_to >= adapt_from, obs_to >= obs_from).
    Если даты не указаны — автодетект из well_status через validate_stages().

    render_charts: рендерить ли PNG-графики (медленно). На странице настройки
    лучше False для быстрого превью статистики.

    fast_chapter_only / fast_block_only — режимы быстрого превью одной главы
    или одного блока: пропускают тяжёлые расчёты глав, которые не отображаются
    в результирующем PDF. Уменьшают время с десятков секунд до единиц секунд.
    """
    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        return {"ok": False, "error": f"Скважина с id={well_id} не найдена"}

    warnings: list[str] = []
    obs_note: str | None = None
    adapt_note: str | None = None

    # Два режима: явные даты (user override) или автодетект
    all_dates_provided = all(
        d is not None for d in (obs_from, obs_to, adapt_from, adapt_to)
    )

    if all_dates_provided:
        # Нормализуем всё к datetime
        obs_from = _to_dt(obs_from, end_of_day=False)
        obs_to = _to_dt(obs_to, end_of_day=True)
        adapt_from = _to_dt(adapt_from, end_of_day=False)
        adapt_to = _to_dt(adapt_to, end_of_day=True)

        # Базовая валидация
        if obs_to <= obs_from:
            return {"ok": False, "error": "Окончание наблюдения раньше начала."}
        if adapt_to <= adapt_from:
            return {"ok": False, "error": "Окончание адаптации раньше начала."}
        if adapt_from < obs_to:
            new_obs_to = adapt_from
            # Не блокируем: гарантируем минимум 1 час наблюдения
            if new_obs_to <= obs_from:
                new_obs_to = obs_from + timedelta(hours=1)
                warnings.append(
                    "Этапы начинаются почти одновременно — "
                    "наблюдение сведено к минимальному окну."
                )
            else:
                warnings.append(
                    f"Периоды перекрывались. Наблюдение укорочено до "
                    f"{new_obs_to.strftime('%d.%m.%Y %H:%M')} (приоритет адаптации)."
                )
            obs_to = new_obs_to
        # Подтянуть note из БД (ближайшая запись по каждому статусу)
        obs_row = db.execute(text("""
            SELECT note FROM well_status
            WHERE well_id = :wid AND status = :st
            ORDER BY dt_start DESC LIMIT 1
        """), {"wid": well_id, "st": STATUS_OBSERVATION}).fetchone()
        adapt_row = db.execute(text("""
            SELECT note FROM well_status
            WHERE well_id = :wid AND status = :st
            ORDER BY dt_start DESC LIMIT 1
        """), {"wid": well_id, "st": STATUS_ADAPTATION}).fetchone()
        obs_note = obs_row[0] if obs_row else None
        adapt_note = adapt_row[0] if adapt_row else None
    else:
        # Автодетект через validate_stages
        v = validate_stages(db, well_id)
        if not v.ok:
            return {
                "ok": False,
                "error": v.error,
                "well": {
                    "id": well.id, "number": str(well.number), "name": well.name,
                },
            }
        obs_from, obs_to = v.obs_from, v.obs_to
        adapt_from, adapt_to = v.adapt_from, v.adapt_to
        obs_note = v.obs_note
        adapt_note = v.adapt_note
        warnings = list(v.warnings)

    # Диаметр штуцера из well_construction
    row = db.execute(text("""
        SELECT choke_diam_mm, horizon FROM well_construction
        WHERE well_no = :wno
        ORDER BY data_as_of DESC NULLS LAST LIMIT 1
    """), {"wno": str(well.number)}).fetchone()
    choke_mm = float(row[0]) if row and row[0] else None
    horizon = str(row[1]) if row and row[1] else None

    if not choke_mm:
        return {
            "ok": False,
            "error": (
                "Не указан диаметр штуцера в конструкции скважины. "
                "Заполните well_construction или задайте штуцер вручную."
            ),
            "well": {"id": well.id, "number": str(well.number), "name": well.name},
        }

    # ── Гейты быстрого превью ──
    # Если выбран только один chapter — пропустим расчёты остальных глав.
    # Если fast_block_only — графики самих stages не рендерим (видна только
    # плитка блока, графики этапа в этом превью не показываются).
    _need_obs    = (fast_chapter_only in (None, "observation"))
    _need_adapt  = (fast_chapter_only in (None, "adaptation"))
    _need_customer = (fast_chapter_only in (None, "customer_data"))
    _need_optimal = (fast_chapter_only in (None, "adaptation"))
    _need_reagent = include_reagent_effectiveness and (fast_chapter_only in (None, "adaptation"))
    _need_tiles   = (fast_chapter_only in (None, "observation", "adaptation"))
    _need_map     = render_charts and (fast_chapter_only in (None, "well_info"))
    # render_charts для самих stage_stats — выключаем при превью блока
    # (внутри §3.4/§4.7 рендерим графики на каждый блок отдельно).
    _stage_render = render_charts and not fast_block_only

    # ═══════════════════════════════════════════════════════════════
    # Быстрый путь для превью одиночного блока: НЕ вызываем
    # collect_stage_stats (он тяжёлый — компьют миллионных рядов).
    # Шаблон в режиме only_attached_blocks=True не обращается к
    # observation.*/adaptation.* — только к observation_blocks /
    # adaptation_blocks. Поэтому возвращаем пустые stubs и тонкий
    # набор данных, нужный только для рендера выбранного блока.
    # ═══════════════════════════════════════════════════════════════
    if fast_block_only:
        # Минимальный stub для шаблона — только метаданные периода.
        # Поля, к которым шаблон может попытаться обратиться при
        # отрисовке header'а § или сноски, заполняем None / пустыми.
        def _stage_stub(d_from, d_to):
            try:
                dur = (d_to - d_from).total_seconds() / 86400.0 if (d_from and d_to) else None
            except Exception:
                dur = None
            return {
                "date_from": str(d_from)[:16] if d_from else None,
                "date_to":   str(d_to)[:16]   if d_to   else None,
                "duration_days": round(dur, 1) if dur else None,
                "duration_label": None,
                "choke_label": f"{choke_mm:g} мм" if choke_mm else None,
                "description": None, "intro": None, "narrative": None,
                # числовые метрики — None, чтобы _add_formatted_fields поставил '—'
                "p_tube_median": None, "p_line_median": None, "dp_median": None,
                "flow_median": None, "flow_avg": None, "flow_cumulative": None,
                "purge_count": 0, "reagent_count": 0, "reagent_qty": 0,
                "reagent_stats": [],
                "hours_with_data": 0, "working_hours": 0, "downtime_hours": 0,
                "utilization_pct": None,
                "pressure_chart_path": None, "dp_chart_path": None,
                "flow_chart_path": None, "combined_chart_path": None,
                "chart_data": [], "events_for_chart": [],
            }
        obs_stub   = _stage_stub(obs_from, obs_to)
        adapt_stub = _stage_stub(adapt_from, adapt_to)

        # Только блоки — никаких customer_chapter, baselines, well_tech.
        observation_blocks: list[dict] = []
        adaptation_blocks:  list[dict] = []
        try:
            from backend.services import customer_daily_service as csvc
            csvc.ensure_blocks_table(db)
            blocks = csvc.get_blocks_for_report(db, well.id)
        except Exception:
            log.exception("Blocks fetch failed for well %s (fast_block_only)", well.id)
            blocks = []
        for b in blocks:
            kind = b.get("kind")
            p = b.get("params") or {}
            if kind == "observation_analysis":
                observation_blocks.append({
                    "block_id":      b.get("id"),
                    "well_id":       well.id,
                    "title":         b.get("title"),
                    "comment":       b.get("comment"),
                    "params":        p,
                    "data_snapshot": b.get("data_snapshot") or {},
                })
            elif kind == "segment_analysis" and (p.get("chapter") == "observation" or p.get("source") == "observation"):
                # segment_analysis из wizard'а Наблюдения → observation_blocks
                observation_blocks.append({
                    "block_id":      b.get("id"),
                    "well_id":       well.id,
                    "kind":          "segment_analysis",
                    "title":         b.get("title"),
                    "comment":       b.get("comment"),
                    "params":        p,
                    "data_snapshot": b.get("data_snapshot") or {},
                    "parts":         p.get("parts") or {},
                    "prefix_note":   p.get("prefix_note") or "",
                    "suffix_note":   p.get("suffix_note") or "",
                })
            elif kind in ("adaptation_period_analysis", "adaptation_comparison",
                          "optimal_window", "reagent_irv_summary"):
                adaptation_blocks.append({
                    "block_id":      b.get("id"),
                    "well_id":       well.id,
                    "kind":          kind,
                    "title":         b.get("title"),
                    "comment":       b.get("comment"),
                    "params":        p,
                    "data_snapshot": b.get("data_snapshot") or {},
                })

        # Форматирование блоков для fast path (превью одного блока)
        observation_blocks_fmt = []
        for ob in observation_blocks:
            kind = ob.get("kind")
            if kind == "segment_analysis":
                observation_blocks_fmt.append(
                    _format_segment_analysis_for_observation(ob, render_charts=render_charts)
                )
            elif kind == "segment_comparison":
                observation_blocks_fmt.append(
                    _format_segment_comparison_for_observation(ob, render_charts=render_charts)
                )
            else:
                observation_blocks_fmt.append(
                    _format_observation_block(ob, render_charts=render_charts)
                )

        adaptation_blocks_fmt = []
        for ab in adaptation_blocks:
            kind = ab.get("kind")
            if kind == "segment_analysis":
                adaptation_blocks_fmt.append(
                    _format_segment_block(ab, render_charts=render_charts, chart_dir=None)
                )
            else:
                adaptation_blocks_fmt.append(
                    _format_adaptation_block(ab, render_charts=render_charts, db=db)
                )

        return {
            "ok": True,
            "warnings": warnings,
            "well": {
                "id": well.id, "number": str(well.number), "name": well.name,
                "horizon": horizon, "choke_mm": choke_mm,
                "choke_history": [],
                "construction": None, "perforation": None,
                "neighbors": None, "map_chart_path": None,
            },
            "observation": obs_stub,
            "adaptation":  adapt_stub,
            "optimal_regime": None, "optimal_candidates": [],
            "optimal_window_days": optimal_window_days,
            "comparison": {}, "comparison_optimal": None,
            "adapt_vs_baseline": None, "obs_vs_baseline": None,
            "conclusions": [], "reagent_effectiveness": None,
            "customer_chapter": None,
            "observation_blocks": observation_blocks_fmt,
            "adaptation_blocks": adaptation_blocks_fmt,
            "b2_tile": None, "b1_tile": None,
        }

    # Сбор статистики обоих этапов
    obs_stats = collect_stage_stats(
        db, well, choke_mm, obs_from, obs_to,
        render_charts=(_stage_render and _need_obs), chart_tag="obs",
        dp_threshold=dp_threshold,
    )
    # Описание: приоритет у явного override, иначе из well_status.note
    obs_stats["description"] = (
        obs_description_override
        if obs_description_override is not None
        else obs_note
    )

    # Вступление к §3.1: дата приёмки + датчики + шлюз дозирования.
    try:
        obs_stats["intro"] = _build_observation_intro(
            db, well.id, obs_from, obs_to,
        )
    except Exception:
        log.exception("observation intro build failed for well %s", well.id)
        obs_stats["intro"] = None

    # ─── Сначала детектим кандидатов, чтобы передать в график адаптации ───
    optimal_candidates: list[dict] = []
    optimal_regime: dict | None = None

    if optimal_mode != "off" and _need_optimal:
        dt_adapt_from = _to_dt(adapt_from, False)
        dt_adapt_to = _to_dt(adapt_to, True)
        window_hours = max(24, int(optimal_window_days * 24))
        try:
            optimal_candidates = detect_optimal_windows(
                db, well, choke_mm,
                dt_adapt_from, dt_adapt_to,
                window_hours=window_hours, top_n=3,
            )
        except Exception:
            log.exception("detect_optimal_windows failed for well %s", well.id)

    adapt_stats = collect_stage_stats(
        db, well, choke_mm, adapt_from, adapt_to,
        render_charts=(_stage_render and _need_adapt), chart_tag="adapt",
        highlight_windows=optimal_candidates if optimal_candidates else None,
        dp_threshold=dp_threshold,
    )
    adapt_stats["description"] = (
        adapt_description_override
        if adapt_description_override is not None
        else adapt_note
    )

    # Вступление к §4.1: период адаптации + датчики SMOD.
    try:
        adapt_stats["intro"] = _build_adaptation_intro(
            db, well.id, str(well.number),
            _to_dt(adapt_from, False), _to_dt(adapt_to, True),
        )
    except Exception:
        log.exception("adaptation intro build failed for well %s", well.id)
        adapt_stats["intro"] = None

    if optimal_mode != "off" and _need_optimal:

        # Выбор окна для детальной статистики
        selected_from: datetime | None = None
        selected_to: datetime | None = None
        selected_source: str | None = None

        if optimal_mode == "manual" and optimal_from and optimal_to:
            selected_from = _to_dt(optimal_from, False)
            selected_to = _to_dt(optimal_to, True)
            selected_source = "manual"
        elif optimal_mode == "auto" and optimal_candidates:
            best = optimal_candidates[0]
            selected_from = best["start"]
            selected_to = best["end"]
            selected_source = "auto"

        if selected_from and selected_to:
            try:
                opt_stats = collect_stage_stats(
                    db, well, choke_mm,
                    selected_from, selected_to,
                    render_charts=(_stage_render and _need_optimal),
                    chart_tag="optimal",
                    dp_threshold=dp_threshold,
                )
                opt_stats["description"] = None
                opt_stats["source"] = selected_source
                opt_stats["narrative"] = generate_stage_narrative(
                    opt_stats, "оптимального режима",
                )
                # Если авто — добавим Score и перечень метрик выбранного окна
                if selected_source == "auto" and optimal_candidates:
                    opt_stats["score"] = optimal_candidates[0].get("score")
                    opt_stats["score_breakdown"] = {
                        "rank_q": optimal_candidates[0].get("rank_q"),
                        "rank_stability": optimal_candidates[0].get("rank_stability"),
                        "rank_utilization": optimal_candidates[0].get("rank_utilization"),
                        "rank_purge": optimal_candidates[0].get("rank_purge"),
                        "cv_q": optimal_candidates[0].get("cv_q"),
                        "purge_freq_per_day": optimal_candidates[0].get("purge_freq_per_day"),
                    }
                optimal_regime = opt_stats
            except Exception:
                log.exception(
                    "Optimal window stats failed for well %s (%s..%s)",
                    well.id, selected_from, selected_to,
                )

    # Сравнение: всегда наблюдение ↔ адаптация, + доп. ↔ оптимальный если есть
    comparison = build_comparison(obs_stats, adapt_stats)
    comparison_optimal = (
        build_comparison(obs_stats, optimal_regime) if optimal_regime else None
    )
    # Сравнение-часть выводов считаем против лучшего (оптимум, если есть);
    # длительности — всегда по реальным этапам.
    conclusions = generate_conclusions(
        obs_stats,
        adapt_stats,
        comparison_optimal or comparison,
        optimal=optimal_regime,
    )

    # Генерация текстового описания этапов
    # Этап наблюдения: без качественной интерпретации через реагент —
    # работы ещё не начались, рассуждения «рост Q → эффективность реагента»
    # неуместны.
    obs_stats["narrative"] = generate_stage_narrative(
        obs_stats, "наблюдения", with_reagent_interp=False,
    )
    adapt_stats["narrative"] = generate_stage_narrative(adapt_stats, "адаптации")

    # Эффективность реагентов (за этап адаптации) — пропускаем для превью
    # любой главы кроме адаптации (тяжёлый расчёт по сегментам ИРВ).
    reagent_effectiveness = None
    if _need_reagent:
        try:
            reagent_effectiveness = analyze_reagent_for_stage(
                well_id, adapt_from, adapt_to,
            )
        except Exception:
            log.exception("Reagent effectiveness analysis failed for well %s", well.id)
            reagent_effectiveness = {"error": "Анализ эффективности реагента не выполнен"}

    # ─── Глава «Анализ исходных данных» (по данным заказчика) ───
    # Источники периодов:
    #   1. customer_periods — старый аргумент (передаётся вручную из вызова).
    #   2. customer_report_block — все виды блоков, добавленные пользователем
    #      со страниц /customer-daily и /adaptation-wizard.
    #
    # Распределение блоков по главам PDF:
    #   • period_analysis, comparison       → §2 «Анализ исходных данных»
    #   • observation_analysis              → §3 «Этап наблюдения»
    #   • adaptation_period_analysis,
    #     adaptation_comparison,
    #     optimal_window,
    #     reagent_irv_summary               → §4 «Этап адаптации»
    customer_chapter = None
    observation_blocks: list[dict] = []
    adaptation_blocks: list[dict] = []
    comparisons_input: list[dict] = []
    rose_blocks_input: list[dict] = []
    segment_blocks_input: list[dict] = []  # PLAN B — kind='segment_analysis'
    chapter_intros_input: list[dict] = []
    periods_input = list(customer_periods or [])

    # ── Фетч всех блоков (для §3/§4 — независимо от чекбокса §2) ──
    blocks: list[dict] = []
    try:
        from backend.services import customer_daily_service as csvc
        csvc.ensure_blocks_table(db)
        blocks = csvc.get_blocks_for_report(db, well.id)
    except Exception:
        log.exception("Blocks fetch failed for well %s", well.id)
        blocks = []

    for b in blocks:
        p = b.get("params") or {}
        snap = b.get("data_snapshot") or {}
        kind = b.get("kind")
        if kind == "chapter_intro":
            # Раздел 1 главы 2 — общий пользовательский текст.
            # params.text — содержимое.
            from backend.services.daily_report_service import _tex_escape
            intro_text = str(p.get("text") or "").strip()
            if intro_text:
                chapter_intros_input.append({
                    "text":     intro_text,
                    "text_tex": _tex_escape(intro_text),
                    "sort_order": int(b.get("sort_order") or 0),
                })
            continue
        if kind == "period_analysis" or kind == "baseline":
            # baseline идёт через тот же цикл что period_analysis — получает
            # графики, таблицы, описание + дополнительно is_baseline flag и
            # justification (обоснование выбора B1, ТЗ §3 раздел 4).
            periods_input.append({
                "period_from": p.get("date_from"),
                "period_to":   p.get("date_to"),
                "description": b.get("comment") or b.get("title"),
                "block_id":    b.get("id"),
                "block_title": b.get("title"),
                "parts":       p.get("parts") or {},
                "prefix_note": p.get("prefix_note") or "",
                "suffix_note": p.get("suffix_note") or "",
                "data_snapshot": snap,
                "is_baseline":  (kind == "baseline"),
                "justification": p.get("justification") or "",
            })
        elif kind == "comparison":
            comparisons_input.append({
                "block_id":      b.get("id"),
                "title":         b.get("title"),
                "comment":       b.get("comment"),
                "params":        p,
                "data_snapshot": snap,
                "parts":         p.get("parts") or {},
            })
        elif kind == "criteria_rose":
            rose_blocks_input.append({
                "block_id":      b.get("id"),
                "title":         b.get("title"),
                "comment":       b.get("comment"),
                "params":        p,
                "data_snapshot": snap,
                "parts":         p.get("parts") or {},
            })
        elif kind == "segment_analysis":
            # Блоки из wizard'а Наблюдения (chapter=observation) → глава 3 (Наблюдение)
            # Блоки из wizard'а Адаптации (chapter=adaptation) → глава 4 (Адаптация)
            # Остальные segment_analysis → глава 2 (Заказчик)
            if p.get("chapter") == "observation" or p.get("source") == "observation":
                observation_blocks.append({
                    "block_id":      b.get("id"),
                    "well_id":       well.id,
                    "kind":          "segment_analysis",  # для выбора форматтера
                    "title":         b.get("title"),
                    "comment":       b.get("comment"),
                    "params":        p,
                    "data_snapshot": snap,
                    "parts":         p.get("parts") or {},
                    "prefix_note":   p.get("prefix_note") or "",
                    "suffix_note":   p.get("suffix_note") or "",
                })
            elif p.get("chapter") == "adaptation":
                # Сегментный анализ для главы Адаптация
                adaptation_blocks.append({
                    "block_id":      b.get("id"),
                    "well_id":       well.id,
                    "kind":          "segment_analysis",
                    "title":         b.get("title"),
                    "comment":       b.get("comment"),
                    "params":        p,
                    "data_snapshot": snap,
                    "parts":         p.get("parts") or {},
                    "prefix_note":   p.get("prefix_note") or "",
                    "suffix_note":   p.get("suffix_note") or "",
                })
            else:
                # PLAN B: Core Segment Analysis интегрирован в PDF главы 2.
                segment_blocks_input.append({
                    "block_id":      b.get("id"),
                    "title":         b.get("title"),
                    "comment":       b.get("comment"),
                    "params":        p,
                    "data_snapshot": snap,
                    "parts":         p.get("parts") or {},
                    "prefix_note":   p.get("prefix_note") or "",
                    "suffix_note":   p.get("suffix_note") or "",
                })
        elif kind == "segment_comparison":
            # segment_comparison из wizard'а Наблюдения → observation_blocks
            if p.get("chapter") == "observation" or p.get("source") == "observation":
                observation_blocks.append({
                    "block_id":      b.get("id"),
                    "well_id":       well.id,
                    "kind":          "segment_comparison",
                    "title":         b.get("title"),
                    "comment":       b.get("comment"),
                    "params":        p,
                    "data_snapshot": snap,
                    "parts":         p.get("parts") or {},
                    "prefix_note":   p.get("prefix_note") or "",
                    "suffix_note":   p.get("suffix_note") or "",
                })
        elif kind == "observation_analysis":
            # Обогащаем события в snapshot данными из БД
            if snap and snap.get("adaptation"):
                evts = snap["adaptation"].get("events_for_chart")
                if evts:
                    _enrich_events_from_db(db, evts, str(well.number))
            observation_blocks.append({
                "block_id":      b.get("id"),
                "well_id":       well.id,
                "title":         b.get("title"),
                "comment":       b.get("comment"),
                "params":        p,
                "data_snapshot": snap,
            })
        elif kind in (
            "adaptation_period_analysis",
            "adaptation_comparison",
            "optimal_window",
            "reagent_irv_summary",
        ):
            # Обогащаем события в snapshot данными из БД (p_tube, p_line, reagent, qty, description)
            if snap and snap.get("adaptation"):
                evts = snap["adaptation"].get("events_for_chart")
                if evts:
                    _enrich_events_from_db(db, evts, str(well.number))
            adaptation_blocks.append({
                "block_id":      b.get("id"),
                "well_id":       well.id,
                "kind":          kind,
                "title":         b.get("title"),
                "comment":       b.get("comment"),
                "params":        p,
                "data_snapshot": snap,
            })

    # Глава §2 собирается, если:
    #   а) пользователь явно включил `include_customer_chapter`, ИЛИ
    #   б) в БД есть хотя бы один блок period_analysis/comparison/baseline
    #      (флаг от фронта может быть False, если форма Шага 2 пустая —
    #      но блоки в БД сами по себе должны попадать в PDF).
    _has_customer_blocks = bool(periods_input or comparisons_input or rose_blocks_input or chapter_intros_input or segment_blocks_input)
    try:
        from backend.services import customer_baseline_service as bsvc
        _has_baselines = bool(bsvc.list_baselines(db, well.id))
    except Exception:
        _has_baselines = False

    # Пропускаем сборку §2 целиком при превью других глав (тяжело —
    # для каждого периода идёт compute_period_analysis + 4 PNG).
    if _need_customer and (include_customer_chapter or _has_customer_blocks or _has_baselines):
        try:
            from backend.services import customer_baseline_service as bsvc

            baselines = bsvc.list_baselines(db, well.id)

            # Дедупликация периодов по (period_from, period_to). При совпадении
            # окна склеиваем описания/комментарии через "; ".
            def _norm_d(v):
                if v is None:
                    return None
                if isinstance(v, str):
                    return v[:10]
                if hasattr(v, "isoformat"):
                    return v.isoformat()[:10]
                return str(v)[:10]

            # Дедупликация одинаковых период-блоков. ВАЖНО: ключ должен
            # включать is_baseline — иначе baseline B1 с теми же датами
            # что period_analysis (типичный кейс: «утвердили текущий период
            # как B1») потеряется и не попадёт в последний подраздел главы.
            seen_ranges: dict[tuple, dict] = {}
            for _p in periods_input:
                key = (_norm_d(_p.get("period_from")),
                       _norm_d(_p.get("period_to")),
                       bool(_p.get("is_baseline")))
                if key in seen_ranges:
                    base = seen_ranges[key]
                    extra_descr = _p.get("description")
                    if extra_descr and extra_descr != base.get("description"):
                        base_descr = base.get("description") or ""
                        base["description"] = (
                            (base_descr + "; " + extra_descr).strip("; ")
                        )
                else:
                    seen_ranges[key] = dict(_p)
            periods_input = list(seen_ranges.values())

            periods_data = []
            for p_idx, p in enumerate(periods_input):
                pf = p.get("period_from"); pt = p.get("period_to")
                if isinstance(pf, str):
                    pf = date.fromisoformat(pf)
                if isinstance(pt, str):
                    pt = date.fromisoformat(pt)
                if not pf or not pt:
                    continue
                analysis = bsvc.compute_period_analysis(
                    db, str(well.number), pf, pt,
                    p.get("description") or p.get("block_title"),
                )
                analysis["baselines_comparison"] = bsvc.compare_to_baselines(
                    analysis, baselines,
                )
                # Прокидываем title/comment/block_id чтобы LaTeX мог
                # подписать раздел «по выбору пользователя».
                analysis["block_id"] = p.get("block_id")
                analysis["block_title"] = p.get("block_title")

                # Атомарные тогглы блока (per-block parts). Если ключа нет
                # в params — считаем включённым (бэкап-совместимо со старыми
                # блоками). См. фронт: customer_daily.html PART_LABELS.
                _saved_parts = dict(p.get("parts") or {})
                _DEFAULT_PERIOD_PARTS = (
                    "prefix_note", "intro", "description",
                    "key_metrics",  # плитки «Ключевые показатели за период»
                    "pressures_chart", "dp_chart", "flow_dt_chart",
                    "monthly_chart", "describe_table",
                    "monthly_table", "monthly_descriptions",
                    "overlap_text", "strict_compare",
                    "suffix_note",
                )
                analysis["parts"] = {
                    k: (bool(_saved_parts.get(k)) if k in _saved_parts else True)
                    for k in _DEFAULT_PERIOD_PARTS
                }
                # ── prefix/suffix_note: пользовательский текст до/после блока ──
                from backend.services.daily_report_service import _tex_escape
                _pre = str(p.get("prefix_note") or "").strip()
                _suf = str(p.get("suffix_note") or "").strip()
                analysis["prefix_note"] = _pre
                analysis["suffix_note"] = _suf
                analysis["prefix_note_tex"] = _tex_escape(_pre) if _pre else ""
                analysis["suffix_note_tex"] = _tex_escape(_suf) if _suf else ""
                # ── baseline-специфика (ТЗ §3 раздел 4): обоснование выбора ──
                analysis["is_baseline"] = bool(p.get("is_baseline"))
                _just = str(p.get("justification") or "").strip()
                analysis["justification"] = _just
                analysis["justification_tex"] = _tex_escape(_just) if _just else ""

                # ── Расширяем данными со страницы /customer-daily ──
                # describe_well_period, monthly_stats, monthly_description
                # + 4 PNG диаграммы (давления, ΔP, дебит+простой, помесячно).
                # snapshot блока (если есть) — единственный источник правды
                # для overlay/strict/overlap (см. ТЗ §3.1).
                _enrich_period_with_customer_data(
                    db, well, pf, pt, analysis, render_charts, p_idx,
                    block_snapshot=p.get("data_snapshot") or None,
                    chart_dir=chart_dir,
                )

                periods_data.append(analysis)

            # Если по этой скв. вообще ничего нет — глава пустая (None).
            if not (baselines or periods_data or comparisons_input or segment_blocks_input):
                customer_chapter = None
            else:
                # Подготавливаем comparisons под формат для LaTeX: каждый
                # сегмент дополняется готовыми форматированными строками,
                # чтобы шаблон не считал арифметику.
                comparisons_fmt = []
                for c in comparisons_input:
                    comparisons_fmt.append(
                        _format_comparison_block(c, chart_dir=chart_dir),
                    )
                rose_blocks_fmt = []
                for rb in rose_blocks_input:
                    rose_blocks_fmt.append(
                        _format_rose_block(
                            rb, render_charts=render_charts,
                            chart_dir=chart_dir,
                        ),
                    )
                # PLAN B — Сегментный анализ: отдельный подраздел.
                segment_blocks_fmt = [
                    _format_segment_block(
                        sb, render_charts=render_charts,
                        chart_dir=chart_dir,
                    )
                    for sb in segment_blocks_input
                ]
                # Принцип ТЗ: «раздел заканчивается выбором базового периода
                # и метриками» — поэтому baseline-блоки выносим из общего
                # списка periods и рендерим ОТДЕЛЬНО, последним подразделом
                # главы. Порядок главы 2:
                #   1. period_analysis (анализы периодов)
                #   2. comparison      (сравнения участков)
                #   3. criteria_rose   (диагностика — роза критериев)
                #   4. baseline (B1)   ← всегда последним
                periods_only = [a for a in periods_data if not a.get("is_baseline")]
                baselines_only = [a for a in periods_data if a.get("is_baseline")]
                customer_chapter = {
                    "baselines": baselines,
                    "periods": periods_only,
                    "comparisons": comparisons_fmt,
                    "segment_blocks": segment_blocks_fmt,
                    "rose_blocks": rose_blocks_fmt,
                    "baseline_blocks": baselines_only,
                    "chapter_intros": chapter_intros_input,
                }
        except Exception:
            log.exception("Customer chapter build failed for well %s", well.id)

    # Сравнение этапа адаптации с baseline-ами (customer + observation).
    # Берём 1 закреплённый baseline каждого источника (если несколько —
    # самый свежий).
    base_customer = None
    base_observation = None
    try:
        from backend.services import customer_baseline_service as _bsvc
        all_bls = _bsvc.list_baselines(db, well.id)
        # сортируем: pinned первыми, потом по created_at desc
        all_bls_sorted = sorted(
            all_bls,
            key=lambda b: (
                0 if b.get("is_pinned") else 1,
                -(b.get("id") or 0),
            ),
        )
        for b in all_bls_sorted:
            if b.get("source") == "customer" and base_customer is None:
                base_customer = b
            elif b.get("source") == "observation" and base_observation is None:
                base_observation = b
    except Exception:
        log.exception("baseline lookup failed for well %s", well.id)

    adapt_vs_baseline = {
        "customer":    _bsvc.compare_stage_to_baseline(adapt_stats, base_customer),
        "observation": _bsvc.compare_stage_to_baseline(adapt_stats, base_observation),
        "baselines": {
            "customer":    base_customer,
            "observation": base_observation,
        },
    } if (base_customer or base_observation) else None

    # Сравнение этапа НАБЛЮДЕНИЯ с customer-baseline (на этапе наблюдения
    # сравниваем только с данными заказчика — это «до начала работ»).
    # Если customer-baseline не выбран — раздел §3.X в PDF не появится.
    obs_vs_baseline = None
    if base_customer:
        try:
            obs_vs_baseline = {
                "customer": _bsvc.compare_stage_to_baseline(
                    obs_stats, base_customer,
                ),
                "baseline": base_customer,
            }
        except Exception:
            log.exception("obs vs baseline compare failed")
            obs_vs_baseline = None

    # История изменений штуцера для §1 + диапазон-метка для каждого этапа
    choke_history: list[dict[str, Any]] = []
    try:
        rows = db.execute(text("""
            SELECT choke_diam_mm, data_as_of, id
            FROM well_construction
            WHERE TRIM(well_no) = :wno AND choke_diam_mm IS NOT NULL
            ORDER BY data_as_of NULLS LAST, id
        """), {"wno": str(well.number).strip()}).fetchall()
        for r in rows:
            choke_history.append({
                "choke_mm": float(r[0]),
                "data_as_of": r[1].isoformat() if r[1] else None,
            })
    except Exception:
        log.warning("choke history lookup failed for well %s", well.id)

    def _choke_for_period(d_from: datetime | date | None,
                         d_to: datetime | date | None) -> str:
        """Текстовая подпись 'Штуцер N мм' для периода: смена в течение
        — диапазон, иначе одно значение."""
        if not choke_history:
            return f"{choke_mm} мм" if choke_mm else "—"
        if d_from is None or d_to is None:
            return f"{choke_mm} мм" if choke_mm else "—"
        df = d_from.date() if isinstance(d_from, datetime) else d_from
        dt = d_to.date() if isinstance(d_to, datetime) else d_to
        # Какие записи актуальны на любой момент периода — берём ту,
        # data_as_of которой ≤ текущей; меняем при пересечении
        active: list[tuple[date, float]] = []
        for h in choke_history:
            if h.get("data_as_of"):
                hd = date.fromisoformat(h["data_as_of"])
                if hd <= dt:
                    active.append((hd, h["choke_mm"]))
        if not active:
            return f"{choke_mm} мм" if choke_mm else "—"
        # Уникальные значения внутри периода
        relevant = [v for d, v in active if d <= dt]
        # Берём те что покрывают период (не меньше начала)
        in_period = [(d, v) for d, v in active if df <= d <= dt]
        if not in_period:
            # Изменений в течение периода нет — берём последнее до df
            return f"{relevant[-1]:g} мм"
        unique_vals = sorted({v for _, v in active})
        if len(unique_vals) == 1:
            return f"{unique_vals[0]:g} мм"
        # Был хотя бы 1 переход в периоде
        return f"{relevant[0]:g} → {relevant[-1]:g} мм (смена)"

    obs_choke   = _choke_for_period(obs_stats.get("date_from"), obs_stats.get("date_to"))
    adapt_choke = _choke_for_period(adapt_stats.get("date_from"), adapt_stats.get("date_to"))
    obs_stats["choke_label"]   = obs_choke
    adapt_stats["choke_label"] = adapt_choke

    # Технические данные §1: конструкция, перфорация, карта (только при render_charts
    # и только когда §1 нужна — карта тяжёлая, для превью других глав пропускаем).
    well_tech = _collect_well_tech_data(db, well, render_map=_need_map)

    # ── Форматирование observation_blocks для LaTeX ──
    # Блоки segment_analysis / segment_comparison требуют генерации PNG.
    observation_blocks_fmt = []
    for ob in observation_blocks:
        kind = ob.get("kind")
        if kind == "segment_analysis":
            observation_blocks_fmt.append(
                _format_segment_analysis_for_observation(ob, render_charts=render_charts)
            )
        elif kind == "segment_comparison":
            observation_blocks_fmt.append(
                _format_segment_comparison_for_observation(ob, render_charts=render_charts)
            )
        else:
            # observation_analysis и другие
            observation_blocks_fmt.append(
                _format_observation_block(ob, render_charts=render_charts)
            )

    # ── Форматирование adaptation_blocks для LaTeX ──
    # Блоки segment_analysis требуют генерации PNG через _format_segment_block.
    # Остальные kind (adaptation_period_analysis, optimal_window, etc.)
    # форматируются через _format_adaptation_block.
    adaptation_blocks_fmt = []
    for ab in adaptation_blocks:
        kind = ab.get("kind")
        if kind == "segment_analysis":
            # Сегментный анализ главы Адаптация — рендер PNG + форматирование
            adaptation_blocks_fmt.append(
                _format_segment_block(
                    ab, render_charts=render_charts, chart_dir=chart_dir,
                )
            )
        else:
            # adaptation_period_analysis, adaptation_comparison, optimal_window, etc.
            adaptation_blocks_fmt.append(
                _format_adaptation_block(ab, render_charts=render_charts, db=db)
            )

    return {
        "ok": True,
        "warnings": warnings,
        "well": {
            "id": well.id,
            "number": str(well.number),
            "name": well.name,
            "horizon": horizon,
            "choke_mm": choke_mm,
            "choke_history": choke_history,
            # §1 «Технические данные»
            "construction": well_tech["construction"],
            "perforation": well_tech["perforation"],
            "neighbors": well_tech["neighbors"],
            "map_chart_path": well_tech["map_chart_path"],
        },
        "observation": obs_stats,
        "adaptation": adapt_stats,
        "optimal_regime": optimal_regime,
        "optimal_candidates": optimal_candidates,
        "optimal_window_days": optimal_window_days,
        "comparison": comparison,
        "comparison_optimal": comparison_optimal,
        "adapt_vs_baseline": adapt_vs_baseline,
        "obs_vs_baseline": obs_vs_baseline,
        "conclusions": conclusions,
        "reagent_effectiveness": reagent_effectiveness,
        "customer_chapter": customer_chapter,
        "observation_blocks": observation_blocks_fmt,
        "adaptation_blocks": adaptation_blocks_fmt,
        # ── Плитки утверждённых baseline для §2 / §3 PDF ──
        # B2 нужна на §3 (наблюдение); B1 — на §2 (заказчик).
        # render_charts на B2 = тяжёлый рендер 3 PNG → выключаем при превью
        # других глав или при превью одиночного блока.
        "b2_tile": _format_baseline_tile(
            db, well.id, "observation",
            render_charts=(render_charts and _need_tiles and not fast_block_only),
        ) if _need_tiles else None,
        "b1_tile": _format_baseline_tile(
            db, well.id, "customer",    render_charts=False,
        ) if _need_customer else None,
    }


def analyze_reagent_for_stage(
    well_id: int, date_from: date, date_to: date,
) -> dict[str, Any]:
    """Обёртка над analyze_reagent_effectiveness — возвращает компактный dict.

    Выделяет ключевые метрики: количество вбросов, ИРВ, лучший реагент, scores.
    """
    from backend.services.reagent_effectiveness_service import (
        analyze_reagent_effectiveness, DEFAULT_CONFIG,
    )

    period_start = datetime.combine(date_from, time(0, 0))
    period_end = datetime.combine(date_to, time(23, 59, 59))

    result = analyze_reagent_effectiveness(
        well_id=well_id,
        period_start=period_start,
        period_end=period_end,
        cfg=DEFAULT_CONFIG,
        include_pressure_data=False,
    )

    # Компактный summary для UI
    irv_results = result.get("irv_results", []) or []
    scores = result.get("scores", []) or []
    best = result.get("best_reagent")

    valid_irv = [r for r in irv_results if not r["metrics"].get("invalid_reason")]

    return {
        "injections_total": result.get("injections_total", 0),
        "merged_injections": result.get("merged_injections", 0),
        "irv_count": len(irv_results),
        "valid_irv_count": len(valid_irv),
        "scores": scores,
        "best_reagent": best,
        "warnings": result.get("warnings") or [],
    }


# ═══════════════════════════════════════════════════════════════════
# Генерация PDF (Этап 3)
# ═══════════════════════════════════════════════════════════════════

def _fmt_num(val, decimals: int = 2, suffix: str = "") -> str:
    """Форматирование числа для LaTeX, None → ---."""
    if val is None:
        return "---"
    try:
        fv = float(val)
        if np.isnan(fv) or np.isinf(fv):
            return "---"
        return f"{fv:.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return str(val)


def _fmt_signed(val, decimals: int = 2, suffix: str = "") -> str:
    """Форматирование числа со знаком (+/-) и опциональным суффиксом."""
    if val is None:
        return "---"
    try:
        fv = float(val)
        if np.isnan(fv) or np.isinf(fv):
            return "---"
        return f"{fv:+.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return "---"


def _fmt_date(d) -> str:
    """Форматирование даты/datetime: если время не 00:00 — покажем и его."""
    if d is None:
        return "---"
    if isinstance(d, datetime):
        if d.hour == 0 and d.minute == 0 and d.second == 0:
            return d.strftime("%d.%m.%Y")
        return d.strftime("%d.%m.%Y %H:%M")
    return d.strftime("%d.%m.%Y")


def _narrative_to_latex(text: str) -> str:
    """Многострочный narrative → LaTeX с \\\\ на концах строк.

    Экранирует спецсимволы, строки списков "• ..." превращает в отдельные абзацы.
    """
    from backend.services.daily_report_service import _tex_escape

    if not text:
        return ""
    lines = text.split("\n")
    out_lines = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        escaped = _tex_escape(ln)
        # Символ '•' остаётся как есть (Unicode поддерживается fontspec)
        out_lines.append(escaped)
    # Соединяем через \\\\ (перенос строки в LaTeX) и \vspace для разделов
    return "\\\\\n".join(out_lines)


def _add_formatted_fields(stage: dict) -> dict:
    """Добавить в stage *_fmt строковые представления для LaTeX."""
    from backend.services.daily_report_service import _tex_escape

    stage["date_from"] = _fmt_date(stage.get("date_from"))
    stage["date_to"] = _fmt_date(stage.get("date_to"))

    stage["p_tube_median_fmt"] = _fmt_num(stage.get("p_tube_median"))
    stage["p_line_median_fmt"] = _fmt_num(stage.get("p_line_median"))
    stage["dp_median_fmt"] = _fmt_num(stage.get("dp_median"))

    dp_trend = stage.get("dp_trend")
    if dp_trend and dp_trend.get("slope_per_day") is not None:
        stage["dp_trend_fmt"] = _fmt_signed(dp_trend.get("slope_per_day"), 3)
    else:
        stage["dp_trend_fmt"] = "---"

    stage["flow_median_fmt"] = _fmt_num(stage.get("flow_median"))
    stage["flow_avg_fmt"] = _fmt_num(stage.get("flow_avg"))
    stage["flow_cumulative_fmt"] = _fmt_num(stage.get("flow_cumulative"), 1)
    stage["q_max_fmt"] = _fmt_num(stage.get("q_max"))
    stage["q_min_fmt"] = _fmt_num(stage.get("q_min"))
    if stage.get("q_max_time"):
        stage["q_max_time_fmt"] = _fmt_date(stage["q_max_time"])
    else:
        stage["q_max_time_fmt"] = "---"
    stage["working_hours_fmt"] = _fmt_num(stage.get("working_hours"), 0)
    stage["downtime_hours_fmt"] = _fmt_num(stage.get("downtime_hours"), 0)
    stage["utilization_pct_fmt"] = _fmt_num(stage.get("utilization_pct"), 1)
    stage["downtime_loss_fmt"] = _fmt_num(stage.get("downtime_loss"), 2)
    stage["reagent_qty_fmt"] = _fmt_num(stage.get("reagent_qty"), 1)

    # Описание этапа (из well_status.note) — экранируем
    if stage.get("description"):
        stage["description"] = _tex_escape(str(stage["description"]).strip())
    else:
        stage["description"] = None

    # Narrative-текст (многострочный) — экранируем с переносами для LaTeX
    if stage.get("narrative"):
        stage["narrative"] = _narrative_to_latex(stage["narrative"])

    # Score (для оптимального режима)
    if "score" in stage:
        stage["score_fmt"] = _fmt_num(stage.get("score"), 2)

    # Реагенты: total_qty_fmt
    for r in stage.get("reagent_stats", []) or []:
        r["name"] = _tex_escape(r.get("name", ""))
        r["total_qty_fmt"] = _fmt_num(r.get("total_qty"), 1)

    # Вступление к §3 — экранируем неконтролируемые поля (serial/label),
    # но НЕ трогаем intro_text/gateway_text (мы авторим их сами с
    # литералами LaTeX вроде \, и --).
    intro = stage.get("intro")
    if intro:
        for s in (intro.get("sensors") or []):
            if s.get("serial") is not None:
                s["serial"] = _tex_escape(str(s["serial"]))
            if s.get("label") is not None:
                s["label"] = _tex_escape(str(s["label"]))

    return stage


def _format_comparison(comparison: dict) -> dict:
    """Добавить *_fmt поля в comparison для LaTeX."""
    out = dict(comparison)
    out["delta_p_tube_fmt"] = _fmt_signed(comparison.get("delta_p_tube"))
    out["delta_p_line_fmt"] = _fmt_signed(comparison.get("delta_p_line"))
    out["delta_dp_fmt"] = _fmt_signed(comparison.get("delta_dp"))
    out["delta_p_tube_pct_fmt"] = _fmt_signed(comparison.get("delta_p_tube_pct"), 1)
    out["delta_p_line_pct_fmt"] = _fmt_signed(comparison.get("delta_p_line_pct"), 1)
    out["delta_dp_pct_fmt"] = _fmt_signed(comparison.get("delta_dp_pct"), 1)
    out["delta_flow_median_fmt"] = _fmt_signed(comparison.get("delta_flow_median"))
    out["delta_flow_avg_fmt"] = _fmt_signed(comparison.get("delta_flow_avg"))
    out["delta_flow_median_pct_fmt"] = _fmt_signed(
        comparison.get("delta_flow_median_pct"), 1,
    )
    out["delta_flow_avg_pct_fmt"] = _fmt_signed(
        comparison.get("delta_flow_avg_pct"), 1,
    )
    out["delta_utilization_pct_fmt"] = _fmt_signed(
        comparison.get("delta_utilization_pct"), 1,
    )

    dpurge = comparison.get("delta_purge_count", 0)
    out["delta_purge_count_fmt"] = f"{dpurge:+d}" if dpurge else "0"
    dreag = comparison.get("delta_reagent_count", 0)
    out["delta_reagent_count_fmt"] = f"{dreag:+d}" if dreag else "0"
    return out


def _format_comparison_block(
    c: dict, *, render_chart: bool = True,
    chart_dir: Path | None = None,
) -> dict:
    """Готовит блок kind='comparison'/'adaptation_comparison' под LaTeX.

    Снапшот формата `wz3cmp_v1`:
        { _v, metric, metric_label, method, toggles,
          segments: [{ letter, source, from, to, from_ts, to_ts,
                       duration_hours, n, mean, median, min, max,
                       slope_per_hour, slope_per_day, r2,
                       curve_x_hours, curve_y, intercept }, …] }

    Если `render_chart=True` — рендерит PNG сравнения участков
    (Δt от начала, как в UI превью блока) и кладёт путь в `chart_path`.
    """
    from backend.services.daily_report_service import _tex_escape
    snap = c.get("data_snapshot") or {}
    segs_raw = snap.get("segments") or []
    method = snap.get("method") or ""
    method_label = "Theil-Sen" if method == "theil_sen" else "МНК линейная"

    # ── PNG графика сравнения (если есть кривые в снапшоте) ──
    chart_path: str | None = None
    if render_chart and segs_raw:
        try:
            from backend.services.block_chart_renderer import (
                render_segments_comparison_chart,
            )
            from backend.services.daily_report_service import _ensure_dirs, TEMP_DIR
            block_id = c.get("block_id") or "x"
            # Абсолютный путь — xelatex запускается с cwd=TEMP_DIR,
            # относительный путь не разрешится. chart_dir per-request
            # (эфемерный) или TEMP_DIR (legacy).
            if chart_dir is not None:
                chart_dir.mkdir(parents=True, exist_ok=True)
                png_path = (chart_dir / f"cmp_block_{block_id}.png").resolve()
            else:
                _ensure_dirs()
                png_path = (TEMP_DIR / f"cmp_block_{block_id}.png").resolve()
            result = render_segments_comparison_chart(snap, png_path)
            if result:
                chart_path = str(result)
        except Exception:
            log.exception("comparison chart render failed for block %s", c.get("block_id"))

    segs_fmt = []
    for s in segs_raw:
        slope_h = s.get("slope_per_hour")
        if slope_h is None:
            arrow = "→"
        elif slope_h > 0.001:
            arrow = "↑"
        elif slope_h < -0.001:
            arrow = "↓"
        else:
            arrow = "→"
        dur_h = s.get("duration_hours") or 0
        dur_d = int(dur_h // 24)
        dur_rem = dur_h - dur_d * 24
        source = s.get("source") or ""
        src_label = "UzKorGaz" if source == "customer" else "UniTool"
        segs_fmt.append({
            "letter":     _tex_escape(str(s.get("letter") or "")),
            "source":     _tex_escape(src_label),
            "from":       _tex_escape(str(s.get("from") or s.get("from_ts") or "")[:16]),
            "to":         _tex_escape(str(s.get("to")   or s.get("to_ts")   or "")[:16]),
            "duration":   f"{dur_d} сут {dur_rem:.1f} ч" if dur_h else "—",
            "n":          s.get("n") or 0,
            "mean_fmt":   _fmt_num(s.get("mean"), 2),
            "median_fmt": _fmt_num(s.get("median"), 2),
            "min_fmt":    _fmt_num(s.get("min"), 2),
            "max_fmt":    _fmt_num(s.get("max"), 2),
            "slope_h_fmt": _fmt_signed(slope_h, 4),
            "slope_d_fmt": _fmt_signed(s.get("slope_per_day"), 3),
            "r2_fmt":     _fmt_num(s.get("r2"), 3),
            "arrow":      arrow,
        })

    _parts_in = dict(c.get("parts") or {})
    _DEFAULT_CMP_PARTS = ("prefix_note", "segments_table", "chart",
                          "description", "suffix_note")
    parts = {k: (bool(_parts_in.get(k)) if k in _parts_in else True)
             for k in _DEFAULT_CMP_PARTS}
    _pre = str((c.get("params") or {}).get("prefix_note")
               or c.get("prefix_note") or "").strip()
    _suf = str((c.get("params") or {}).get("suffix_note")
               or c.get("suffix_note") or "").strip()
    return {
        "block_id":         c.get("block_id"),
        "title":            _tex_escape(str(c.get("title") or "")),
        "comment":          _tex_escape(str(c.get("comment") or "")),
        "metric_label":     _tex_escape(str(snap.get("metric_label") or snap.get("metric") or "—")),
        "method_label":     _tex_escape(method_label),
        "segments":         segs_fmt,
        "chart_path":       chart_path,
        "has_data":         bool(segs_fmt),
        "parts":            parts,
        "prefix_note":      _pre,
        "suffix_note":      _suf,
        "prefix_note_tex":  _tex_escape(_pre) if _pre else "",
        "suffix_note_tex":  _tex_escape(_suf) if _suf else "",
    }


def _format_baseline_tile(
    db: Session, well_id: int, source: str,
    *, render_charts: bool = True,
) -> dict | None:
    """Готовит плитку утверждённого baseline для PDF (B1/B2/Адаптация).

    Возвращает None, если для скважины нет утверждённого baseline данного
    источника. Иначе — dict с готовым контентом плитки:
      • заголовок, период, дни, описание для PDF (`notes`)
      • 5 «карточек» метрик: Q общий / Q рабочий / ΔP / P уст-шл / простой
        (каждая — пара ср./мед.)
      • при render_charts: 3 PNG (давления / ΔP / Q) через тот же
        compute_full_flow, что страница скважины.

    Параметр `source`:
      * 'observation' → B2 (наши данные UniTool за этап наблюдения)
      * 'customer'    → B1 (данные заказчика UzKorGaz)
    """
    from backend.services.daily_report_service import _tex_escape
    from backend.models.customer_baseline import CustomerBaseline

    bl = (
        db.query(CustomerBaseline)
        .filter(CustomerBaseline.well_id == well_id,
                CustomerBaseline.source == source,
                CustomerBaseline.is_pinned.is_(True))
        .order_by(CustomerBaseline.id.desc())
        .first()
    )
    if not bl:
        return None

    sd_min = bl.shutdown_min_total or 0
    sd_h   = (sd_min / 60.0) if sd_min else 0.0
    sd_days = bl.shutdown_days_count

    tile = {
        "id":          bl.id,
        "name":        _tex_escape(bl.name or ""),
        "source":      source,
        "period_from": bl.period_from.isoformat() if bl.period_from else "",
        "period_to":   bl.period_to.isoformat()   if bl.period_to   else "",
        "days":        bl.days_count or 0,
        "notes":       _tex_escape((bl.notes or "").strip()),
        # 5 карточек метрик ─────────────────────────────
        "q_total_avg_fmt":      _fmt_num(bl.q_total_avg,    2),
        "q_total_median_fmt":   _fmt_num(bl.q_total_median, 2),
        "q_working_avg_fmt":    _fmt_num(bl.q_working_avg,    2),
        "q_working_median_fmt": _fmt_num(bl.q_working_median, 2),
        "dp_avg_fmt":           _fmt_num(bl.dp_avg,    2),
        "dp_median_fmt":        _fmt_num(bl.dp_median, 2),
        "p_wellhead_median_fmt":_fmt_num(bl.p_wellhead_median, 2),
        "p_flowline_median_fmt":_fmt_num(bl.p_flowline_median, 2),
        "shutdown_min_total":   int(sd_min) if sd_min else 0,
        "shutdown_hours_fmt":   _fmt_num(sd_h, 1),
        "shutdown_days":        sd_days or 0,
    }

    # ── 3 PNG-графика (только для UniTool/B2/Адаптация — у заказчика B1
    # источник суточный, графики иначе строятся; их пока пропускаем) ──
    tile["chart_pressures"] = None
    tile["chart_dp"]        = None
    tile["chart_flow"]      = None
    if render_charts and source != "customer":
        try:
            from backend.services.block_chart_renderer import (
                render_baseline_tile_charts,
            )
            from backend.services.daily_report_service import _ensure_dirs, TEMP_DIR
            _ensure_dirs()
            prefix = (TEMP_DIR / f"tile_{source}_{well_id}_{bl.id}").resolve()
            paths = render_baseline_tile_charts(
                well_id, bl.period_from, bl.period_to, prefix,
            )
            tile["chart_pressures"] = paths.get("pressures")
            tile["chart_dp"]        = paths.get("dp")
            tile["chart_flow"]      = paths.get("flow")
        except Exception:
            log.exception("baseline tile charts failed for well %s", well_id)

    return tile


def _format_rose_block(
    b: dict, *, render_charts: bool = True,
    chart_dir: Path | None = None,
) -> dict:
    """Готовит блок kind='criteria_rose' под LaTeX.

    Снапшот формата (см. backend/services/customer_rose_service.compute_rose):
        { ok, well_number, period_from, period_to, period_days,
          mode, weights_raw, weights, current_raw, history_median_raw,
          history{choke_mm, windows_count, ...}, ranks, contributions,
          score, weak_data, warnings, labels, labels_short, units }

    При render_charts=True — рендерит 2 PNG: розу и бар-чарт «вклад в балл».
    """
    from backend.services.daily_report_service import _tex_escape
    snap = b.get("data_snapshot") or {}
    p = b.get("params") or {}

    chart_rose_path = None
    chart_contrib_path = None
    if render_charts and snap.get("ok"):
        try:
            from backend.services.rose_chart_renderer import (
                render_rose_chart_png, render_contributions_chart_png,
            )
            from backend.services.daily_report_service import _ensure_dirs, TEMP_DIR
            block_id = b.get("block_id") or "x"
            if chart_dir is not None:
                chart_dir.mkdir(parents=True, exist_ok=True)
                rose_path = (chart_dir / f"rose_block_{block_id}.png").resolve()
                bar_path = (chart_dir / f"rose_bar_block_{block_id}.png").resolve()
            else:
                _ensure_dirs()
                rose_path = (TEMP_DIR / f"rose_block_{block_id}.png").resolve()
                bar_path = (TEMP_DIR / f"rose_bar_block_{block_id}.png").resolve()
            r1 = render_rose_chart_png(snap, rose_path)
            r2 = render_contributions_chart_png(snap, bar_path)
            chart_rose_path = str(r1) if r1 else None
            chart_contrib_path = str(r2) if r2 else None
        except Exception:
            log.exception("rose chart render failed for block %s", b.get("block_id"))

    # Сводная таблица 6 критериев — для LaTeX
    keys = ("decline", "p_wh_down", "p_wh_cv", "p_fl_up", "shutdown", "freq")
    labels = snap.get("labels") or {}
    units = snap.get("units") or {}
    current_raw = snap.get("current_raw") or {}
    history_med = snap.get("history_median_raw") or {}
    ranks = snap.get("ranks") or {}
    contrib = snap.get("contributions") or {}

    def _fmt_raw(v):
        if v is None:
            return "—"
        try:
            f = float(v)
        except (TypeError, ValueError):
            return "—"
        av = abs(f)
        if av >= 100:
            return f"{f:.1f}"
        if av >= 1:
            return f"{f:.2f}"
        if av >= 0.01:
            return f"{f:.3f}"
        return f"{f:.4f}"

    metric_rows = []
    for k in keys:
        rk = ranks.get(k)
        c = contrib.get(k) or {}
        # Зона: «лучше / норма / хуже / риск / нет данных»
        if rk is None:
            zone = "нет данных"
        elif rk > 75:
            zone = "риск (>75)"
        elif rk > 50:
            zone = "хуже нормы"
        elif rk < 50:
            zone = "лучше нормы"
        else:
            zone = "норма"
        metric_rows.append({
            "label":         _tex_escape(str(labels.get(k, k))),
            "current_fmt":   _fmt_raw(current_raw.get(k)),
            "history_fmt":   _fmt_raw(history_med.get(k)),
            "unit":          _tex_escape(str(units.get(k, ""))),
            "rank_fmt":      "—" if rk is None else f"{float(rk):.0f}",
            "weight_fmt":    f"{float(c.get('weight') or 0.0):.2f}",
            "actual_fmt":    f"{float(c.get('actual') or 0.0):.2f}",
            "zone":          _tex_escape(zone),
        })

    _parts_in = dict(b.get("parts") or p.get("parts") or {})
    _DEFAULT_ROSE_PARTS = (
        "prefix_note", "rose_chart", "contributions_chart", "metrics_table",
        "warnings", "description", "suffix_note",
    )
    parts = {k: (bool(_parts_in.get(k)) if k in _parts_in else True)
             for k in _DEFAULT_ROSE_PARTS}
    _pre = str(p.get("prefix_note") or b.get("prefix_note") or "").strip()
    _suf = str(p.get("suffix_note") or b.get("suffix_note") or "").strip()

    history = snap.get("history") or {}
    mode = str(snap.get("mode") or p.get("mode") or "balanced")
    mode_label = {
        "balanced": "сбалансированный",
        "liquid": "жидкость/обводнение",
        "gsp": "газ-сепаратор/шлейф",
        "purge_cycles": "продувочные циклы",
        "custom": "пользовательские веса",
    }.get(mode, mode)

    return {
        "block_id":           b.get("block_id"),
        "title":              _tex_escape(str(b.get("title") or "")),
        "comment":            _tex_escape(str(b.get("comment") or "")),
        "well_number":        _tex_escape(str(snap.get("well_number") or "")),
        "period_from":        _tex_escape(str(snap.get("period_from") or "")),
        "period_to":          _tex_escape(str(snap.get("period_to") or "")),
        "period_days":        int(snap.get("period_days") or 0),
        "mode":               _tex_escape(mode),
        "mode_label":         _tex_escape(mode_label),
        "score_fmt":          f"{float(snap.get('score') or 0.0):.1f}",
        "score_value":        float(snap.get("score") or 0.0),
        "weak_data":          bool(snap.get("weak_data")),
        "choke_mm_fmt":       (
            f"{float(history.get('choke_mm')):.1f}"
            if history.get("choke_mm") is not None else "—"
        ),
        "history_windows":    int(history.get("windows_count") or 0),
        "history_from":       _tex_escape(str(history.get("history_from") or "")),
        "history_to":         _tex_escape(str(history.get("history_to") or "")),
        "warnings":           [_tex_escape(str(w)) for w in (snap.get("warnings") or [])],
        "metric_rows":        metric_rows,
        "chart_rose_path":    chart_rose_path,
        "chart_contrib_path": chart_contrib_path,
        "has_data":           bool(snap.get("ok")),
        "parts":              parts,
        "prefix_note":        _pre,
        "suffix_note":        _suf,
        "prefix_note_tex":    _tex_escape(_pre) if _pre else "",
        "suffix_note_tex":    _tex_escape(_suf) if _suf else "",
    }


# ═══════════════════════════════════════════════════════════════════
#  _format_segment_block (PLAN B — PDF интеграция Core Segment Analysis)
# ═══════════════════════════════════════════════════════════════════
# Подготавливает блок kind='segment_analysis' под LaTeX-шаблон.
# Использует snapshot segment_v1 (PLAN A) — НЕ изменяет схему.
#
# Архитектура (по согласованию PLAN B):
#   • Renderer (segment_chart_renderer)  отвечает только за PNG-figure.
#   • Formatter (эта функция)            отвечает за данные + LaTeX-safe
#                                         форматирование + parts toggles.
#   • LaTeX template                     отвечает за compose/spacing.
#
# ОГРАНИЧЕНИЕ: краткие описания сегментов — ТОЛЬКО фактические
# (Q снизился на X%, ΔP снизился на Y%, простой Z мин/сут).
# Никаких diagnostic/causal формулировок («диагностический признак»,
# «возможная интерпретация», «требует проверки») — это отдельный этап.

#  PART_LABELS контракт — см. docs/contracts/segment_snapshot_contract.md §5
#  и backend/constants/segment_parts.py.
#
#  Источник правды для ключей чекбоксов — HTML PART_LABELS.segment_analysis
#  (backend/templates/customer_daily.html). Этот список должен оставаться
#  симметричным HTML, за исключением PAV-частей, которые намеренно
#  ИСКЛЮЧЕНЫ из PDF по scope PB-V2 (SEGMENT_ANALYSIS_PDF_EXCLUDED_PARTS).
#  dual_comparison не управляется чекбоксом — см.
#  SEGMENT_ANALYSIS_ALWAYS_ON_PARTS (выводится всегда).
#
#  При добавлении нового ключа: обновить и shared constant, и HTML, и
#  LaTeX-подсекцию `adaptation_report.tex` (см. контракт §9).
from backend.constants.segment_parts import (
    SEGMENT_ANALYSIS_RENDER_PARTS,
    SEGMENT_SNAPSHOT_SCHEMA_VERSION_SUPPORTED,
)
_DEFAULT_SEGMENT_PARTS = SEGMENT_ANALYSIS_RENDER_PARTS


def _format_segment_block(
    b: dict,
    *,
    render_charts: bool = True,
    chart_dir: Path | None = None,
) -> dict:
    """Готовит блок kind='segment_analysis' под LaTeX-шаблон.

    Args:
        b: dict с полями block_id, title, comment, params, data_snapshot,
            prefix_note, suffix_note, parts (опц.).
        render_charts: если True — генерирует PNG через
            segment_chart_renderer.render_segment_q_chart.
        chart_dir: эфемерный каталог per-request (см.
            adaptation_report._build_pdf_response). Если None —
            fallback на TEMP_DIR.

    Returns:
        dict для LaTeX-шаблона (см. блок 2.N segment_analysis в
        adaptation_report.tex). Все текстовые поля прошли _tex_escape.

    ВАЖНО: если snap пустой или ok=False — возвращаем `has_data=False`,
    LaTeX-шаблон выведет короткое сообщение без падения.
    """
    from backend.services.daily_report_service import _tex_escape

    snap = b.get("data_snapshot") or {}
    p = b.get("params") or {}
    block_id = b.get("block_id") or "x"

    # ── Soft schema_version check (см. контракт §7) ────────────────
    # Legacy snapshot без schema_version трактуется как v=1.
    # Версия выше поддерживаемой не приводит к падению — рендер
    # продолжается best-effort, в лог пишется warning. В пользовательский
    # PDF никаких diagnostic-вставок не добавляем.
    try:
        _raw_v = snap.get("schema_version")
        _snap_v = int(_raw_v) if _raw_v is not None else 1
    except (TypeError, ValueError):
        _snap_v = 1
    if _snap_v > SEGMENT_SNAPSHOT_SCHEMA_VERSION_SUPPORTED:
        log.warning(
            "segment snapshot schema_version=%s > supported=%s "
            "(block_id=%s) — рендерю best-effort",
            _snap_v, SEGMENT_SNAPSHOT_SCHEMA_VERSION_SUPPORTED, block_id,
        )

    # ── PNG figure (только если render_charts=True и snapshot валиден) ──
    # Поддерживаем оба индикатора: ok=True (segment_v1) и block_status="ok" (obs_segment)
    chart_q_path: str | None = None
    is_valid = snap.get("ok") or snap.get("block_status") == "ok"
    if render_charts:
        if not is_valid:
            log.warning(
                "segment block %s: skipping chart (ok=%s, block_status=%s)",
                block_id, snap.get("ok"), snap.get("block_status"),
            )
        else:
            try:
                from backend.services.segment_chart_renderer import (
                    render_segment_q_chart,
                )
                from backend.services.daily_report_service import _ensure_dirs, TEMP_DIR
                if chart_dir is not None:
                    chart_dir.mkdir(parents=True, exist_ok=True)
                    png_path = (chart_dir / f"segment_q_{block_id}.png").resolve()
                else:
                    _ensure_dirs()
                    png_path = (TEMP_DIR / f"segment_q_{block_id}.png").resolve()
                result = render_segment_q_chart(snap, png_path)
                chart_q_path = str(result) if result else None
                if result is None:
                    log.warning(
                        "segment block %s: render_segment_q_chart returned None "
                        "(check logs for details: dates=%d, segments=%d)",
                        block_id,
                        len((snap.get("chart_data") or {}).get("dates") or []),
                        len(snap.get("segments_extended") or snap.get("segments") or []),
                    )
            except Exception:
                log.exception(
                    "render_segment_q_chart failed for block %s", block_id,
                )

    # ── Подготовка таблицы сегментов (11 колонок § PLAN B 3.3) ──
    # Поддерживаем оба формата: segments_extended (segment_v1) и segments (obs_segment)
    segments_rows = []
    for s in snap.get("segments_extended") or snap.get("segments") or []:
        change_pct = s.get("change_pct")
        # change_pct это число, например -9.0. Форматируем как «-9» + LaTeX-эскейпленный %.
        if change_pct is None:
            change_fmt_tex = "---"
        else:
            # _fmt_signed(change_pct, 0) даёт «-9» без знака %. Добавляем \% (LaTeX literal).
            value_str = _fmt_signed(change_pct, 0)
            if change_pct > 0:
                change_fmt_tex = (
                    r"\textcolor{green!50!black}{" + value_str + r"\%}"
                )
            elif change_pct < 0:
                change_fmt_tex = (
                    r"\textcolor{red!75!black}{" + value_str + r"\%}"
                )
            else:
                change_fmt_tex = r"0\%"
        period_tex = _tex_escape(
            f"{s.get('start','')}--{s.get('end','')}"
        )
        segments_rows.append({
            "num":           str(s.get("num", "")),
            "period_tex":    period_tex,
            "days":          str(s.get("days", "")),
            "q_total_fmt":   _fmt_num(s.get("mean_q"), 1),
            "q_working_fmt": _fmt_num(s.get("mean_q_working"), 1),
            "slope_fmt":     (
                _fmt_signed(s.get("slope_total"), 3)
                if s.get("slope_total") is not None else "---"
            ),
            "change_fmt_tex": change_fmt_tex,
            "shutdown_fmt":  _fmt_num(s.get("mean_shutdown"), 0),
            "working_fmt":   _fmt_num(s.get("working_pct"), 0),
            "dp_fmt":        _fmt_num(s.get("mean_dp"), 2),
            "type_tex":      _tex_escape(str(s.get("type") or "—")),
        })

    # ── Богатое «Описание сегментов» (HTML §6.4 эквивалент) ────
    # Использует cause / preshutdown_verdict / gradual_trend /
    # change_q_working_pct / mean_p_wellhead / mean_p_flowline /
    # mean_choke_mm / change_shutdown / change_dp_pct.
    # ВАЖНО: cause/preshutdown_verdict/gradual_trend — описания факта
    # из segment_analysis_module (разрешены diagnostic-policy).
    rich_segment_describes = _build_rich_segment_describes(
        snap.get("segments_extended") or [],
    )

    # ── Развёрнутые описания из алгоритма (snap.descriptions[]) ─
    descriptions_lines = _build_descriptions_lines(
        snap.get("descriptions") or [],
    )

    # ── События переломов: предпочитаем snap.cp_descriptions[] ─
    # Fallback на короткие cp_bullets (legacy snapshot без cp_descriptions).
    cp_descriptions_lines = _build_descriptions_lines(
        snap.get("cp_descriptions") or [],
    )
    cp_bullets = _build_cp_bullets(
        snap.get("cp_marks") or [],
        snap.get("segments_extended") or [],
    )

    # ── Dual comparison ───────────────────────────────────────
    dual_summary = snap.get("dual_summary") or {}
    dual_summary_tex = {
        "primary":          str(dual_summary.get("primary_cp_count", 0)),
        "working":          str(dual_summary.get("working_cp_count", 0)),
        "common":           str(dual_summary.get("common_count", 0)),
        "common_dates_tex": _tex_escape(
            ", ".join(dual_summary.get("common_dates") or [])
        ),
        "only_total_tex":   _tex_escape(
            ", ".join(dual_summary.get("only_total_dates") or [])
        ),
        "only_working_tex": _tex_escape(
            ", ".join(dual_summary.get("only_working_dates") or [])
        ),
    }

    # ── Parts (atomic toggles, default True) ───────────────────
    saved_parts = dict(p.get("parts") or {})
    parts = {
        k: (bool(saved_parts.get(k)) if k in saved_parts else True)
        for k in _DEFAULT_SEGMENT_PARTS
    }

    # ── prefix/suffix_note ─────────────────────────────────────
    _pre = str(p.get("prefix_note") or b.get("prefix_note") or "").strip()
    _suf = str(p.get("suffix_note") or b.get("suffix_note") or "").strip()

    return {
        "block_id":       block_id,
        "kind":           "segment_analysis",
        "title":          _tex_escape(str(b.get("title") or "Сегментный анализ")),
        "well_number":    _tex_escape(str(snap.get("well_number") or "")),
        "date_from":      _tex_escape(str(snap.get("date_from") or "")),
        "date_to":        _tex_escape(str(snap.get("date_to") or "")),
        "days_count":     snap.get("days_count") or 0,
        "n_points":       snap.get("n_points") or 0,
        "comment":        _tex_escape(str(b.get("comment") or "")),
        "prefix_note":    _pre,
        "suffix_note":    _suf,
        "prefix_note_tex": _tex_escape(_pre) if _pre else "",
        "suffix_note_tex": _tex_escape(_suf) if _suf else "",
        "chart_q_path":   chart_q_path,
        "segments_rows":  segments_rows,
        # HTML-эквивалент «Описание сегментов» (§6.4 — rich).
        "rich_segment_describes": rich_segment_describes,
        # HTML-эквивалент «Развёрнутые описания из алгоритма»
        "descriptions_lines": descriptions_lines,
        # HTML-эквивалент «События переломов» (snap.cp_descriptions).
        # Если пуст — fallback на cp_bullets из cp_marks (legacy snapshot).
        "cp_descriptions_lines": cp_descriptions_lines,
        "cp_bullets":     cp_bullets,
        "dual_summary_tex": dual_summary_tex,
        "parts":          parts,
        "has_data":       bool(
            (snap.get("ok") or snap.get("block_status") == "ok")
            and (snap.get("segments_extended") or snap.get("segments") or [])
        ),
    }


def _build_factual_segment_notes(segments_extended: list) -> list[dict]:
    """Компактные ФАКТИЧЕСКИЕ описания сегментов (без causal claims).

    1-3 коротких строки на сегмент. Только числа и тип режима из
    snapshot. НЕТ формулировок «диагностический признак», «возможная
    интерпретация», «требует проверки».

    Returns: [{num, lines: [str, ...]}, ...]
        — lines уже прошли _tex_escape.
    """
    from backend.services.daily_report_service import _tex_escape
    out = []
    for i, s in enumerate(segments_extended):
        lines: list[str] = []
        seg_type = s.get("type") or "—"
        q_tot = s.get("mean_q")
        q_work = s.get("mean_q_working")
        mean_dp = s.get("mean_dp")
        shutdown = s.get("mean_shutdown")
        working_pct = s.get("working_pct")
        change_pct = s.get("change_pct")
        change_dp_pct = s.get("change_dp_pct")
        change_shutdown = s.get("change_shutdown")
        change_choke = s.get("change_choke")

        # Строка 1: тип, базовые средние.
        # ВАЖНО: пишем чистые строки с '%' — _tex_escape ниже заменит на \%.
        # Двойной эскейп категорически запрещён.
        l1 = f"Тип режима: {seg_type}."
        l1 += f" Q общ {_fmt_num(q_tot, 1)}, Q раб {_fmt_num(q_work, 1)} тыс.м³/сут,"
        l1 += f" ΔP {_fmt_num(mean_dp, 2)} кгс/см²,"
        l1 += f" простой {_fmt_num(shutdown, 0)} мин/сут (рабочее время {_fmt_num(working_pct, 0)}%)."
        lines.append(l1)

        # Строка 2 (только если есть prev и change_pct): изменения
        if i > 0 and change_pct is not None:
            parts2 = [f"Q общ {_fmt_signed(change_pct, 0)}%"]
            if change_dp_pct is not None:
                parts2.append(f"ΔP {_fmt_signed(change_dp_pct, 0)}%")
            if change_shutdown is not None:
                parts2.append(f"простой {_fmt_signed(change_shutdown, 0)} мин")
            if change_choke is not None and abs(change_choke) > 0.01:
                parts2.append(f"штуцер {_fmt_signed(change_choke, 1)} мм")
            l2 = "Изменение к предыдущему сегменту: " + ", ".join(parts2) + "."
            lines.append(l2)

        # Эскейп всех строк перед передачей в LaTeX (% → \%, & → \&, ...)
        out.append({
            "num":   str(s.get("num", "")),
            "lines": [_tex_escape(ln) for ln in lines],
        })
    return out


def _build_rich_segment_describes(segments_extended: list) -> list[dict]:
    """Точный эквивалент HTML «📝 Описание сегментов» (§6.4).

    Источник: customer_daily.html `_buildSegmentBlockHtml`, ветка
    `on('descriptions') && segs.length` (карточка на сегмент).

    Использует поля из segments_extended:
      mean_q, mean_q_working, mean_dp, mean_p_wellhead,
      mean_p_flowline, mean_choke_mm, working_pct, type, color,
      change_pct, change_q_working_pct, change_dp_pct, change_shutdown,
      change_choke, gradual_trend, gradual_drift_pct,
      preshutdown_q, preshutdown_delta_pct, preshutdown_verdict, cause.

    cause / preshutdown_verdict / gradual_trend — описания факта
    (см. policy в feedback_diagnostic_interpretation_style.md). НИКАКИХ
    новых diagnostic-формулировок здесь НЕ добавляется.

    Returns: [{num, header, lines: [str, ...]}, ...] — каждая строка
        уже эскейплена через _tex_escape.
    """
    from backend.services.daily_report_service import _tex_escape
    out = []
    for i, s in enumerate(segments_extended):
        prev = segments_extended[i - 1] if i > 0 else None
        num = str(s.get("num", ""))
        seg_type = str(s.get("type") or "—")
        # Шапка сегмента (как в HTML: «Сегмент N · DD.MM.YYYY–DD.MM.YYYY · X дн · тип: ...»)
        header_plain = (
            f"Сегмент {num} · "
            f"{s.get('start','')}–{s.get('end','')} · "
            f"{s.get('days','—')} дн · тип: {seg_type}"
        )

        lines: list[str] = []

        # Строка 1 — Средние. ВАЖНО: пишем чистый '%', _tex_escape сам в \%.
        l1 = (
            f"Средние: Q общ {_fmt_num(s.get('mean_q'), 1)}, "
            f"Q раб {_fmt_num(s.get('mean_q_working'), 1)} тыс.м³/сут, "
            f"ΔP {_fmt_num(s.get('mean_dp'), 2)} кгс/см², "
            f"P уст {_fmt_num(s.get('mean_p_wellhead'), 1)}, "
            f"P шл {_fmt_num(s.get('mean_p_flowline'), 1)} кгс/см², "
            f"штуцер {_fmt_num(s.get('mean_choke_mm'), 1)} мм, "
            f"раб. время {_fmt_num(s.get('working_pct'), 0)}%."
        )
        lines.append(l1)

        # Строка 2 — Изменение vs предыдущий (если есть prev).
        if prev is not None:
            chg_q   = s.get("change_pct")
            chg_qw  = s.get("change_q_working_pct")
            chg_dp  = s.get("change_dp_pct")
            chg_sd  = s.get("change_shutdown")
            chg_ch  = s.get("change_choke")
            parts2: list[str] = []
            if chg_q is not None:
                parts2.append(f"Q общ {_fmt_signed(chg_q, 1)}%")
            if chg_qw is not None:
                parts2.append(f"Q раб {_fmt_signed(chg_qw, 1)}%")
            if chg_dp is not None:
                parts2.append(f"ΔP {_fmt_signed(chg_dp, 1)}%")
            if chg_sd is not None:
                parts2.append(f"простой {_fmt_signed(chg_sd, 0)} мин")
            if chg_ch is not None and abs(chg_ch) > 0.01:
                parts2.append(f"штуцер {_fmt_signed(chg_ch, 1)} мм")
            if parts2:
                lines.append(
                    f"Изменение vs Сег.{prev.get('num','—')}: "
                    + ", ".join(parts2) + "."
                )

        # Строка 3 — Плавный тренд (если есть).
        gt = s.get("gradual_trend")
        if gt:
            gt_word = "спад" if gt == "down" else "рост"
            drift = s.get("gradual_drift_pct")
            lines.append(
                f"Плавный тренд: {gt_word} на {_fmt_signed(drift, 1)}% за период."
            )

        # Строка 4 — До простоя / после.
        psh_q = s.get("preshutdown_q")
        psh_v = s.get("preshutdown_verdict")
        if psh_q is not None and psh_v:
            psh_delta = s.get("preshutdown_delta_pct")
            lines.append(
                f"До простоя/после: {_fmt_num(psh_q, 1)} → "
                f"{_fmt_num(s.get('mean_q'), 1)} "
                f"({_fmt_signed(psh_delta, 0)}%). {psh_v}"
            )

        # Строка 5 — Основной фактор (cause, §6.2: описание факта).
        cause = s.get("cause")
        if cause:
            lines.append(f"Основной фактор: {cause}")

        out.append({
            "num":    num,
            "color":  str(s.get("color") or "#666666"),
            "header": _tex_escape(header_plain),
            "lines":  [_tex_escape(ln) for ln in lines],
        })
    return out


def _safe_tex_full(s: str) -> str:
    """_tex_escape + защита от backslash.

    _tex_escape не покрывает '\\' (он не в списке). Чтобы текст из
    snap.descriptions[] / cp_descriptions[] не ронял xelatex на словах
    типа 'C:\\Users' или regexp-литералах, экранируем backslash вручную.

    Подход: сначала заменить '\\' на сентинель (любой символ,
    который точно не может встретиться), затем _tex_escape (он не
    тронет сентинель), затем сентинель → '\\textbackslash{}'.
    Так не возникает повторного эскейпа после первого прохода.
    """
    from backend.services.daily_report_service import _tex_escape
    if not s:
        return ""
    SENT = "\x00BS\x00"
    s = s.replace("\\", SENT)
    s = _tex_escape(s)
    return s.replace(SENT, r"\textbackslash{}")


def _build_descriptions_lines(items: list) -> list[str]:
    """Эскейпит и нормализует список текстовых описаний.

    Используется для:
      • snap.descriptions[]    — «📑 Развёрнутые описания (из алгоритма)»
      • snap.cp_descriptions[] — «🔻 События переломов»

    Каждый элемент:
      • приводится к str;
      • пропускается _safe_tex_full (% & _ # $ { } ~ ^ \\);
      • '\\n' → ' \\\\ ' (мягкий перенос внутри одного абзаца);
      • '\\r' удаляется;
      • пустые элементы пропускаются.
    """
    out: list[str] = []
    for raw in items or []:
        if raw is None:
            continue
        s = str(raw).replace("\r", "")
        if not s.strip():
            continue
        # Сначала экранируем (символы \n остаются), потом конвертируем \n.
        escaped = _safe_tex_full(s)
        # \n → '\\\\' (force linebreak в LaTeX без новой строки в исходнике)
        escaped = escaped.replace("\n", " \\\\ ")
        out.append(escaped)
    return out


def _build_cp_bullets(cp_marks: list, segments_extended: list) -> list[str]:
    """Короткие LaTeX-bullets для событий переломов.

    Формат: «дата | tag | Q prev→curr тыс.м³/сут | ΔP ±X% | [source]»
    Только фактические числа из snapshot. Уже эскейплено для LaTeX.
    """
    from backend.services.daily_report_service import _tex_escape
    out = []
    # Маппинг cp.idx → next-segment для контекста
    seg_by_start = {s.get("start_idx"): s for s in segments_extended}
    for cp in cp_marks:
        date = cp.get("date") or ""
        tag = cp.get("tag") or ""
        source = cp.get("source") or "only_total"
        source_label_map = {
            "confirmed":    "подтверждено обоими",
            "only_total":   "только в Q общем",
            "only_working": "только в Q рабочем",
        }
        source_lbl = source_label_map.get(source, source)
        # Контекст из следующего сегмента — фактические числа.
        # ВАЖНО: '%' пишем чистым — _tex_escape сам экранирует в \%.
        curr_seg = seg_by_start.get(cp.get("idx"))
        ctx_parts: list[str] = []
        if curr_seg is not None:
            cp_pct = curr_seg.get("change_pct")
            if cp_pct is not None:
                ctx_parts.append(f"Q {_fmt_signed(cp_pct, 0)}%")
            dp_pct = curr_seg.get("change_dp_pct")
            if dp_pct is not None:
                ctx_parts.append(f"ΔP {_fmt_signed(dp_pct, 0)}%")
        ctx = " | " + ", ".join(ctx_parts) if ctx_parts else ""
        # Каждый фрагмент эскейпим отдельно. Затем собираем в одну строку.
        bullet = (
            _tex_escape(f"{date} | ")
            + _tex_escape(tag)
            + _tex_escape(ctx)
            + " | [" + _tex_escape(source_lbl) + "]"
        )
        out.append(bullet)
    return out


def _format_observation_block(b: dict, *, render_charts: bool = True) -> dict:
    """Готовит блок kind='observation_analysis' под LaTeX.

    Снапшот формата `wz3obs_v1`/`v2` (рецепт):
        { date_from, date_to, days, q_total_avg/median,
          q_working_avg/median, p_wellhead_avg/median, p_flowline_avg/median,
          dp_avg/median, shutdown_min_total, utilization_pct, working_hours,
          dp_threshold, purge_count, reagent_count }

    При render_charts=True — рендерит 3 PNG (давления / ΔP / Q) через
    тот же compute_full_flow, что страница скважины (АКСИОМА),
    по period_from/period_to. Это «JSON-рецепт»: backend сам строит
    графики заново из живых данных.
    """
    from backend.services.daily_report_service import _tex_escape
    snap = b.get("data_snapshot") or {}
    p = b.get("params") or {}
    sd_min = snap.get("shutdown_min_total") or 0

    # ── Live-рендер 3 графиков по датам блока (АКСИОМА) ──
    chart_pressures = chart_dp = chart_flow = None
    if render_charts:
        try:
            from datetime import date as _date
            from backend.services.block_chart_renderer import render_baseline_tile_charts
            from backend.services.daily_report_service import _ensure_dirs, TEMP_DIR
            _ensure_dirs()
            well_id  = b.get("well_id")
            block_id = b.get("block_id") or "x"
            d_from = snap.get("date_from") or p.get("date_from")
            d_to   = snap.get("date_to")   or p.get("date_to")
            if well_id and d_from and d_to:
                pf = _date.fromisoformat(str(d_from)[:10])
                pt = _date.fromisoformat(str(d_to)[:10])
                prefix = (TEMP_DIR / f"obs_block_{block_id}_w{well_id}").resolve()
                paths = render_baseline_tile_charts(well_id, pf, pt, prefix)
                chart_pressures = paths.get("pressures")
                chart_dp        = paths.get("dp")
                chart_flow      = paths.get("flow")
        except Exception:
            log.exception("observation block charts failed for block %s", b.get("block_id"))

    # Описание блока разбиваем на абзацы для корректной вёрстки в LaTeX
    # (одной строкой в \textit{} тонет длинный текст и теряет переносы).
    desc_raw = str(b.get("comment") or "").strip()
    description_paragraphs = [
        _tex_escape(line) for line in desc_raw.split("\n") if line.strip()
    ]

    return {
        "block_id":   b.get("block_id"),
        "title":      _tex_escape(str(b.get("title") or "")),
        "is_b2":      snap.get("is_b2", False),  # флаг B2-блока для спец.стилизации
        "comment":    _tex_escape(desc_raw),  # совместимость с прежним шаблоном
        "description_paragraphs": description_paragraphs,
        "date_from":  _tex_escape(str(snap.get("date_from") or p.get("date_from") or "—")),
        "date_to":    _tex_escape(str(snap.get("date_to")   or p.get("date_to")   or "—")),
        "days":       snap.get("days") or p.get("days") or 0,
        "q_total_avg_fmt":      _fmt_num(snap.get("q_total_avg"),    2),
        "q_total_median_fmt":   _fmt_num(snap.get("q_total_median"), 2),
        "q_working_avg_fmt":    _fmt_num(snap.get("q_working_avg"),    2),
        "q_working_median_fmt": _fmt_num(snap.get("q_working_median"), 2),
        "p_wellhead_avg_fmt":   _fmt_num(snap.get("p_wellhead_avg"),    2),
        "p_wellhead_median_fmt":_fmt_num(snap.get("p_wellhead_median"), 2),
        "p_flowline_avg_fmt":   _fmt_num(snap.get("p_flowline_avg"),    2),
        "p_flowline_median_fmt":_fmt_num(snap.get("p_flowline_median"), 2),
        "dp_avg_fmt":           _fmt_num(snap.get("dp_avg"),    2),
        "dp_median_fmt":        _fmt_num(snap.get("dp_median"), 2),
        "shutdown_min_total":   int(sd_min) if sd_min else 0,
        "shutdown_hours_fmt":   _fmt_num((sd_min or 0) / 60.0, 1),
        "utilization_pct_fmt":  _fmt_num(snap.get("utilization_pct"), 1),
        "purge_count":          snap.get("purge_count") or 0,
        "reagent_count":        snap.get("reagent_count") or 0,
        "chart_pressures":      chart_pressures,
        "chart_dp":             chart_dp,
        "chart_flow":           chart_flow,
    }


def _format_segment_analysis_for_observation(b: dict, *, render_charts: bool = True) -> dict:
    """Готовит блок kind='segment_analysis' под LaTeX для §3.4.

    Использует render_segment_analysis() из observation_chapter_renderer,
    который генерирует и HTML, и LaTeX. Возвращаем LaTeX для вставки в шаблон.
    """
    from backend.services.daily_report_service import _tex_escape, TEMP_DIR, _ensure_dirs
    from backend.services.observation_chapter_renderer import (
        render_segment_analysis, RenderContext,
    )

    snap = b.get("data_snapshot") or {}
    p = b.get("params") or {}
    block_id = b.get("block_id") or 0

    # Директория для PNG графиков
    _ensure_dirs()
    output_dir = str(TEMP_DIR)

    # Контекст для рендера
    ctx = RenderContext(
        output_dir=output_dir,
        block_id=block_id,
        title=str(b.get("title") or ""),
        block_status="ok",
        skip_figures=not render_charts,
        parts=p.get("parts") or {},
        prefix_note=p.get("prefix_note") or "",
        suffix_note=p.get("suffix_note") or "",
        comment=str(b.get("comment") or ""),
    )

    # Рендер через observation_chapter_renderer
    try:
        result = render_segment_analysis(snap, ctx)
        latex_content = result.latex
        chart_paths = [fig.absolute_path for fig in result.figures]
    except Exception:
        log.exception("segment_analysis render failed for block %s", block_id)
        latex_content = f"\\textit{{Ошибка рендера блока {block_id}}}"
        chart_paths = []

    return {
        "block_id": block_id,
        "title": _tex_escape(str(b.get("title") or "")),
        "kind": "segment_analysis",
        "latex_content": latex_content,
        "_chart_paths": chart_paths,  # для cleanup после генерации PDF
    }


def _format_segment_comparison_for_observation(b: dict, *, render_charts: bool = True) -> dict:
    """Готовит блок kind='segment_comparison' под LaTeX для §3.4.

    Использует render_segment_comparison() из observation_chapter_renderer.
    """
    from backend.services.daily_report_service import _tex_escape, TEMP_DIR, _ensure_dirs
    from backend.services.observation_chapter_renderer import (
        render_segment_comparison, RenderContext,
    )

    snap = b.get("data_snapshot") or {}
    p = b.get("params") or {}
    block_id = b.get("block_id") or 0

    _ensure_dirs()
    output_dir = str(TEMP_DIR)

    ctx = RenderContext(
        output_dir=output_dir,
        block_id=block_id,
        title=str(b.get("title") or ""),
        block_status="ok",
        skip_figures=not render_charts,
        parts=p.get("parts") or {},
        prefix_note=p.get("prefix_note") or "",
        suffix_note=p.get("suffix_note") or "",
        comment=str(b.get("comment") or ""),
    )

    try:
        result = render_segment_comparison(snap, ctx)
        latex_content = result.latex
        chart_paths = [fig.absolute_path for fig in result.figures]
    except Exception:
        log.exception("segment_comparison render failed for block %s", block_id)
        latex_content = f"\\textit{{Ошибка рендера блока {block_id}}}"
        chart_paths = []

    return {
        "block_id": block_id,
        "title": _tex_escape(str(b.get("title") or "")),
        "kind": "segment_comparison",
        "latex_content": latex_content,
        "_chart_paths": chart_paths,
    }


def _format_adaptation_block(b: dict, *, render_charts: bool = True, db=None) -> dict:
    """Готовит блок одного из видов §4 под LaTeX.

    Поддерживаемые kind:
      • adaptation_period_analysis — снапшот { adaptation, observation,
        b1, comparison, optimal }
      • adaptation_comparison      — то же что comparison (wz3cmp_v1)
      • optimal_window             — { regime, observation, b1 }
      • reagent_irv_summary        — { injections_total, irv_count,
        scores, best_reagent }

    При render_charts=True для adaptation_period_analysis — рендерит 3 PNG
    (давления / ΔP / Q) из данных снапшота chart_data.

    Параметр db: SQLAlchemy session для запроса датчиков SMOD (если intro нет в снапшоте).
    """
    from backend.services.daily_report_service import _tex_escape
    kind = b.get("kind")
    snap = b.get("data_snapshot") or {}
    p = b.get("params") or {}
    base = {
        "block_id":   b.get("block_id"),
        "kind":       kind,
        "title":      _tex_escape(str(b.get("title") or "")),
        "comment":    _tex_escape(str(b.get("comment") or "")),
        "date_from":  _tex_escape(str(p.get("date_from") or "—")),
        "date_to":    _tex_escape(str(p.get("date_to")   or "—")),
    }

    # ── Рендер 3 графиков ИЗ ДАННЫХ СНАПШОТА (не из БД!) ──
    chart_pressures = chart_dp = chart_flow = None
    if render_charts and kind == "adaptation_period_analysis":
        try:
            from backend.services.block_chart_renderer import render_adaptation_charts_from_snapshot
            from backend.services.daily_report_service import _ensure_dirs, TEMP_DIR
            _ensure_dirs()

            well_id = b.get("well_id")
            block_id = b.get("block_id") or "x"
            adapt = snap.get("adaptation") or {}

            # Берём данные из снапшота — это то, что было проанализировано
            chart_data = adapt.get("chart_data") or []
            events = adapt.get("events_for_chart") or []

            if chart_data:
                prefix = (TEMP_DIR / f"adapt_block_{block_id}_w{well_id}").resolve()
                paths = render_adaptation_charts_from_snapshot(chart_data, events, prefix)
                chart_pressures = paths.get("pressures")
                chart_dp = paths.get("dp")
                chart_flow = paths.get("flow")
        except Exception:
            log.exception("adaptation block charts failed for block %s", b.get("block_id"))

    base.update({
        "chart_pressures": chart_pressures,
        "chart_dp": chart_dp,
        "chart_flow": chart_flow,
    })

    if kind == "adaptation_comparison":
        cmp_fmt = _format_comparison_block(b)
        base.update({
            "metric_label": cmp_fmt["metric_label"],
            "method_label": cmp_fmt["method_label"],
            "segments":     cmp_fmt["segments"],
            "chart_path":   cmp_fmt["chart_path"],
            "has_data":     cmp_fmt["has_data"],
        })
        return base

    if kind == "optimal_window":
        regime = snap.get("regime") or {}
        base.update({
            "regime_from":      _tex_escape(str(regime.get("start") or p.get("optimal_from") or "—")),
            "regime_to":        _tex_escape(str(regime.get("end")   or p.get("optimal_to")   or "—")),
            "p_tube_median_fmt":  _fmt_num(regime.get("p_tube_median"),   2),
            "p_line_median_fmt":  _fmt_num(regime.get("p_line_median"),   2),
            "dp_median_fmt":      _fmt_num(regime.get("dp_median"),       2),
            "flow_median_fmt":    _fmt_num(regime.get("flow_median"),     2),
            "flow_avg_fmt":       _fmt_num(regime.get("flow_avg"),        2),
            "utilization_pct_fmt":_fmt_num(regime.get("utilization_pct"), 1),
            "score_fmt":          _fmt_num(regime.get("score"), 2),
            "duration_label":     _tex_escape(str(regime.get("duration_label") or "")),
            "has_data":           bool(regime),
        })
        return base

    if kind == "reagent_irv_summary":
        scores = snap.get("scores") or {}
        rows = []
        if isinstance(scores, dict):
            for name, sc in scores.items():
                rows.append({
                    "name":     _tex_escape(str(name)),
                    "score_fmt": _fmt_num(sc, 1),
                })
        base.update({
            "injections_total": snap.get("injections_total") or 0,
            "irv_count":        snap.get("irv_count") or 0,
            "best_reagent":     _tex_escape(str(snap.get("best_reagent") or "—")),
            "scores":           rows,
            "has_data":         bool(rows or snap.get("injections_total")),
        })
        return base

    # adaptation_period_analysis (полная сводка с 15+ полями)
    adapt = snap.get("adaptation") or {}
    obs = snap.get("observation") or {}
    b1 = snap.get("b1") or {}
    cmp_ = snap.get("comparison") or {}
    opt = snap.get("optimal") or {}
    seg = snap.get("segment_analysis") or {}

    # ─── Сегментный анализ (динамическое обогащение) ───
    # Если segment_analysis пуст и есть db — вычисляем из данных ДАТЧИКОВ
    # (как на странице «Наблюдение», не из well_daily)
    if db is not None and not seg.get("cp_marks") and not seg.get("segments"):
        try:
            from backend.services.observation_segment_service import compute_segment_preview

            well_id = b.get("well_id")
            date_from = adapt.get("date_from") or p.get("date_from") or ""
            date_to = adapt.get("date_to") or p.get("date_to") or ""

            if date_from and date_to and well_id:
                seg_data = compute_segment_preview(
                    db=db,
                    well_id=well_id,
                    d_from=str(date_from)[:10],
                    d_to=str(date_to)[:10],
                    aggregation="daily",
                    sensitivity="medium",
                    include_raw_chart=True,
                )

                if seg_data:
                    segments = seg_data.get("segments") or []
                    changepoints = seg_data.get("changepoints") or []
                    raw = seg_data.get("raw") or {}
                    chart_payload = raw.get("chart_payload") or {}

                    # Генерируем cp_marks
                    cp_marks = []
                    for i, cp in enumerate(changepoints):
                        cp_marks.append({
                            "idx": cp.get("idx"),
                            "tag": f"CP{i+1}",
                            "date": cp.get("date") or "",
                            "source": cp.get("source") or "total",
                        })

                    # Генерируем описания
                    descriptions = []
                    for s in segments:
                        direction = s.get("direction") or "stable"
                        direction_ru = {"stable": "стабильный", "up": "рост", "down": "снижение"}.get(direction, direction)
                        q_mean = s.get("mean_q")
                        start = s.get("start_date") or ""
                        end = s.get("end_date") or ""
                        if q_mean is not None:
                            descriptions.append(f"{start}–{end}: {direction_ru}, Q = {q_mean:.2f}")
                        else:
                            descriptions.append(f"{start}–{end}: {direction_ru}")

                    n_seg = len(segments)
                    n_cp = len(changepoints)
                    summary = f"Выделено {n_seg} сегментов и {n_cp} точек изменения режима."

                    seg = {
                        "period": seg_data.get("period") or {"from": str(date_from)[:10], "to": str(date_to)[:10]},
                        "date_from": str(date_from)[:10],
                        "date_to": str(date_to)[:10],
                        "n_points": len(chart_payload.get("dates") or []),
                        "changepoints": changepoints,
                        "cp_marks": cp_marks,
                        "segments": segments,
                        "chart_data": chart_payload,
                        "interpretation": {
                            "summary": summary,
                            "descriptions": descriptions,
                        },
                    }
        except Exception:
            log.exception("segment_analysis enrichment failed for block %s", b.get("block_id"))

    # ─── Intro (введение с датчиками) ───
    # Если intro есть в снапшоте — используем. Иначе формируем из дат + датчиков SMOD.
    intro = adapt.get("intro") or {}
    intro_text = intro.get("intro_text_tex") or intro.get("intro_text") or ""

    if not intro_text and db is not None:
        # Формируем intro динамически из снапшота + БД
        try:
            from datetime import datetime as _dt
            well_id = b.get("well_id")
            date_from = adapt.get("date_from") or p.get("date_from") or ""
            date_to = adapt.get("date_to") or p.get("date_to") or ""

            # Форматируем даты
            try:
                df_dt = _dt.fromisoformat(str(date_from)[:19].replace("Z", ""))
                date_from_fmt = df_dt.strftime("%d.%m.%Y")
            except:
                date_from_fmt = str(date_from)[:10]
            try:
                dt_dt = _dt.fromisoformat(str(date_to)[:19].replace("Z", ""))
                date_to_fmt = dt_dt.strftime("%d.%m.%Y")
            except:
                date_to_fmt = str(date_to)[:10]

            # Получаем номер скважины
            well_number = "—"
            if well_id:
                row = db.execute(text("SELECT number FROM wells WHERE id = :wid"), {"wid": well_id}).fetchone()
                if row:
                    well_number = str(row[0])

            # Получаем датчики SMOD
            sensors_info = []
            sensor_serials = []
            sensor_model = None
            if well_id and date_from and date_to:
                rows = db.execute(text("""
                    SELECT e.serial_number, e.name
                    FROM equipment_installation ei
                    JOIN equipment e ON e.id = ei.equipment_id
                    WHERE ei.well_id = :wid
                      AND ei.installed_at <= :dt_to
                      AND (ei.removed_at IS NULL OR ei.removed_at >= :dt_from)
                      AND e.name ILIKE '%SMOD%'
                    ORDER BY ei.installed_at ASC
                """), {"wid": well_id, "dt_from": date_from, "dt_to": date_to}).fetchall()
                for serial, name in rows:
                    sensor_serials.append(serial)
                    if not sensor_model and name:
                        sensor_model = name

            # Формируем текст введения по ТЗ
            if sensor_serials:
                serials_str = ", ".join(sensor_serials)
                model_str = sensor_model or "SMOD"
                intro_text = (
                    f"В период с {date_from_fmt} по {date_to_fmt} с целью адаптации технологии "
                    f"к условиям скважины №{well_number} проводились работы по выбору "
                    f"оптимального реагента, оптимальной концентрации и периода дозирования. "
                    f"Параметры работы скважины фиксировались в режиме реального времени "
                    f"датчиками {model_str} (серийные номера: {serials_str}). "
                    f"Частота опроса — 1 измерение в минуту."
                )
            else:
                intro_text = (
                    f"В период с {date_from_fmt} по {date_to_fmt} с целью адаптации технологии "
                    f"к условиям скважины №{well_number} проводились работы по выбору "
                    f"оптимального реагента, оптимальной концентрации и периода дозирования."
                )
        except Exception:
            log.exception("adaptation block intro generation failed for block %s", b.get("block_id"))

    base.update({
        "intro_text":       _tex_escape(str(intro_text)[:2000]),
        "has_intro":        bool(intro_text),
        "sensors":          [_tex_escape(s) for s in (intro.get("sensors") or [])],
    })

    # ─── Adaptation (R) метрики ───
    base.update({
        "adapt_flow_median_fmt":   _fmt_num(adapt.get("flow_median"),     2),
        "adapt_flow_avg_fmt":      _fmt_num(adapt.get("flow_avg"),        2),
        "adapt_dp_median_fmt":     _fmt_num(adapt.get("dp_median"),       2),
        "adapt_p_tube_fmt":        _fmt_num(adapt.get("p_tube_median"),   2),
        "adapt_p_line_fmt":        _fmt_num(adapt.get("p_line_median"),   2),
        "adapt_utilization_fmt":   _fmt_num(adapt.get("utilization_pct"), 1),
        "adapt_working_hours_fmt": _fmt_num(adapt.get("working_hours"),   1),
        "adapt_duration_days":     adapt.get("duration_days") or 0,
        "adapt_purge_count":       adapt.get("purge_count") or 0,
        "adapt_reagent_count":     adapt.get("reagent_count") or 0,
    })

    # ─── Observation (B2) метрики ───
    base.update({
        "obs_flow_median_fmt":     _fmt_num(obs.get("flow_median"),       2),
        "obs_flow_avg_fmt":        _fmt_num(obs.get("flow_avg"),          2),
        "obs_dp_median_fmt":       _fmt_num(obs.get("dp_median"),         2),
        "obs_p_tube_fmt":          _fmt_num(obs.get("p_tube_median"),     2),
        "obs_p_line_fmt":          _fmt_num(obs.get("p_line_median"),     2),
        "obs_utilization_fmt":     _fmt_num(obs.get("utilization_pct"),   1),
        "obs_duration_days":       obs.get("duration_days") or 0,
        "obs_purge_count":         obs.get("purge_count") or 0,
        "obs_reagent_count":       obs.get("reagent_count") or 0,
        "has_observation":         bool(obs.get("flow_median") or obs.get("p_tube_median")),
    })

    # ─── B1 (Базовый период заказчика) метрики ───
    # B1 может иметь другую структуру ключей (q_total_median vs flow_median)
    b1_flow = b1.get("flow_median") or b1.get("q_total_median")
    b1_flow_avg = b1.get("flow_avg") or b1.get("q_total_avg")
    b1_p_tube = b1.get("p_tube_median") or b1.get("p_wellhead_median")
    b1_p_line = b1.get("p_line_median") or b1.get("p_flowline_median")
    base.update({
        "b1_flow_median_fmt":      _fmt_num(b1_flow,                      2),
        "b1_flow_avg_fmt":         _fmt_num(b1_flow_avg,                  2),
        "b1_dp_median_fmt":        _fmt_num(b1.get("dp_median"),          2),
        "b1_p_tube_fmt":           _fmt_num(b1_p_tube,                    2),
        "b1_p_line_fmt":           _fmt_num(b1_p_line,                    2),
        "b1_duration_days":        b1.get("duration_days") or b1.get("days_count") or 0,
        "has_b1":                  bool(b1_flow or b1_p_tube),
    })

    # ─── Comparison (Δ) метрики ───
    base.update({
        "delta_flow_median_fmt":   _fmt_signed(cmp_.get("delta_flow_median"),   2),
        "delta_flow_median_pct":   _fmt_signed(cmp_.get("delta_flow_median_pct"), 1, suffix=r"\%"),
        "delta_flow_avg_fmt":      _fmt_signed(cmp_.get("delta_flow_avg"),      2),
        "delta_dp_fmt":            _fmt_signed(cmp_.get("delta_dp"),            2),
        "delta_dp_pct":            _fmt_signed(cmp_.get("delta_dp_pct"),        1, suffix=r"\%"),
        "delta_p_tube_fmt":        _fmt_signed(cmp_.get("delta_p_tube"),        2),
        "delta_p_line_fmt":        _fmt_signed(cmp_.get("delta_p_line"),        2),
        "delta_utilization_fmt":   _fmt_signed(cmp_.get("delta_utilization_pct"), 1, suffix=r"\%"),
        "delta_purge_count":       cmp_.get("delta_purge_count") or 0,
        "delta_reagent_count":     cmp_.get("delta_reagent_count") or 0,
    })

    # ─── Trends (Q, ΔP, P труб, P лин) ───
    q_trend = adapt.get("q_trend") or {}
    dp_trend = adapt.get("dp_trend") or {}
    p_tube_trend = adapt.get("p_tube_trend") or {}
    p_line_trend = adapt.get("p_line_trend") or {}
    base.update({
        "q_trend_dir":         q_trend.get("direction") or "—",
        "q_trend_slope_fmt":   _fmt_signed(q_trend.get("slope_per_day"), 3),
        "q_trend_sig":         "да" if q_trend.get("mk_significant") else "нет",
        "dp_trend_dir":        dp_trend.get("direction") or "—",
        "dp_trend_slope_fmt":  _fmt_signed(dp_trend.get("slope_per_day"), 4),
        "p_tube_trend_dir":    p_tube_trend.get("direction") or "—",
        "p_tube_trend_slope_fmt": _fmt_signed(p_tube_trend.get("slope_per_day"), 3),
        "p_line_trend_dir":    p_line_trend.get("direction") or "—",
        "p_line_trend_slope_fmt": _fmt_signed(p_line_trend.get("slope_per_day"), 3),
        "has_trends":          bool(q_trend or dp_trend or p_tube_trend or p_line_trend),
    })

    # ─── Optimal (R★) окно ───
    base.update({
        "opt_date_from":       _tex_escape(str(opt.get("date_from") or "—")[:10]),
        "opt_date_to":         _tex_escape(str(opt.get("date_to") or "—")[:10]),
        "opt_flow_median_fmt": _fmt_num(opt.get("flow_median"), 2),
        "opt_flow_avg_fmt":    _fmt_num(opt.get("flow_avg"),    2),
        "opt_dp_median_fmt":   _fmt_num(opt.get("dp_median"),   2),
        "opt_p_tube_fmt":      _fmt_num(opt.get("p_tube_median"), 2),
        "opt_p_line_fmt":      _fmt_num(opt.get("p_line_median"), 2),
        "opt_utilization_fmt": _fmt_num(opt.get("utilization_pct"), 1),
        "opt_duration_days":   opt.get("duration_days") or 0,
        "opt_score_fmt":       _fmt_num(opt.get("score"),       1),
        "opt_duration_label":  _tex_escape(str(opt.get("duration_label") or "")),
        "has_optimal":         bool(opt.get("flow_median") or opt.get("score")),
    })

    # ─── Narrative ───
    narr = str(adapt.get("narrative") or "")
    base.update({
        "narrative":      _tex_escape(narr[:2000]) if narr else "",
        "has_narrative":  bool(narr.strip()),
    })

    # ─── Events (таблица событий) ───
    events_raw = adapt.get("events_for_chart") or []
    events_fmt = []
    for ev in events_raw[:50]:  # лимит 50 событий для PDF
        ev_type = ev.get("type") or "—"
        # Логика типов и описаний:
        # - reagent → Тип: "Вброс реагента", Описание: название реагента
        # - other → Тип: "Прочее", Описание: description из события
        # - purge → Тип: "Продувка", Описание: —
        if ev_type == "purge":
            type_label = "Продувка"
            descr = "—"
        elif ev_type == "reagent":
            type_label = "Вброс реагента"
            descr = ev.get("reagent") or "—"
        elif ev_type == "other":
            type_label = "Прочее"
            descr = ev.get("description") or ev.get("label") or "—"
        else:
            type_label = ev_type
            descr = ev.get("label") or "—"
        events_fmt.append({
            "ts":          _tex_escape(str(ev.get("t") or ev.get("ts") or "—")[:16]),
            "type":        _tex_escape(str(type_label)[:50]),
            "label":       _tex_escape(str(descr)[:80]),  # увеличен лимит для description
            "qty":         _fmt_num(ev.get("qty") or ev.get("amount"), 1) if ev.get("qty") or ev.get("amount") else "—",
            "p_tube":      _fmt_num(ev.get("p_tube"), 1) if ev.get("p_tube") is not None else "—",
            "p_line":      _fmt_num(ev.get("p_line"), 1) if ev.get("p_line") is not None else "—",
        })
    reagent_events = [e for e in events_raw if e.get("type") == "reagent"]
    purge_events = [e for e in events_raw if e.get("type") == "purge"]
    base.update({
        "events":           events_fmt,
        "events_count":     len(events_raw),
        "reagent_count_ev": len(reagent_events),
        "reagent_qty_sum":  _fmt_num(adapt.get("reagent_qty"), 1) if adapt.get("reagent_qty") else "—",
        "purge_count_ev":   len(purge_events),
        "has_events":       bool(events_raw),
    })

    # ─── Segment analysis (сегментный анализ) ───
    interp = seg.get("interpretation") or {}
    cp_marks = seg.get("cp_marks") or []
    descriptions = interp.get("descriptions") or []

    # Рендер графика сегментов Q (PNG)
    segment_chart_path = None
    if render_charts and seg.get("chart_data"):
        try:
            from backend.services.observation_chart_renderer import render_segment_analysis_chart
            from backend.services.daily_report_service import _ensure_dirs, TEMP_DIR
            _ensure_dirs()

            block_id = b.get("block_id") or "x"
            well_id_seg = b.get("well_id") or 0
            png_path = (TEMP_DIR / f"adapt_seg_{block_id}_w{well_id_seg}.png").resolve()

            # Передаём snapshot в формате, который ожидает render_segment_analysis_chart
            seg_snapshot = {
                "chart_data": seg.get("chart_data") or {},
                "segments_extended": seg.get("segments_extended") or seg.get("segments") or [],
                "cp_marks": seg.get("cp_marks") or [],
            }
            render_segment_analysis_chart(seg_snapshot, str(png_path))
            if png_path.exists():
                segment_chart_path = str(png_path)
        except Exception:
            log.exception("adaptation segment chart failed for block %s", b.get("block_id"))

    base.update({
        "segment_summary":     _tex_escape(str(interp.get("summary") or "")[:1500]),
        "segment_descriptions": [_tex_escape(str(d)[:200]) for d in descriptions[:12]],
        "segment_cp_count":    len(seg.get("changepoints") or seg.get("cp_marks") or []),
        "segment_cp_marks":    [{"tag": _tex_escape(cp.get("tag") or ""), "date": _tex_escape(str(cp.get("date") or "")[:16])} for cp in cp_marks[:10]],
        "has_segments":        bool(seg.get("changepoints") or seg.get("cp_marks") or interp),
        "segment_chart_path":  segment_chart_path,
    })

    base["has_data"] = bool(adapt or obs or cmp_)
    return base


def _build_observation_chapter_latex(db: Session, well_id: int) -> str:
    """g-fix-1: Загрузить observation-блоки (in_report=true) и собрать LaTeX-фрагмент.

    Renderer (F) — snapshot-only. Блоки загружаются через C3
    `load_and_validate_snapshot` (single gateway, RFC §10a).
    При отсутствии блоков возвращает пустую строку — Jinja2 placeholder
    раскрывается в пустоту, PDF компилируется без главы.

    Args:
        db: SQLAlchemy session
        well_id: id скважины

    Returns:
        LaTeX-фрагмент (str). Пустая строка если нет блоков с in_report=true
        или произошла ошибка загрузки (graceful, не падает).
    """
    from sqlalchemy import text as _sql_text
    from backend.services.observation_chapter_renderer import (
        render_observation_chapter_latex,
    )
    from backend.services.observation_snapshot_service import (
        load_and_validate_snapshot,
    )

    try:
        rows = db.execute(_sql_text("""
            SELECT id, title, params, comment FROM customer_report_block
            WHERE well_id = :wid
              AND params->>'source' = 'observation'
              AND in_report = true
            ORDER BY sort_order, created_at
        """), {"wid": well_id}).fetchall()
    except Exception as e:
        log.warning("[observation_chapter] failed to load blocks for well %s: %s",
                    well_id, e)
        return ""

    if not rows:
        return ""

    blocks_for_render: list[dict] = []
    for row in rows:
        block_id = row[0]
        title = row[1] or ""
        params = row[2] if isinstance(row[2], dict) else {}
        comment = row[3] or ""
        try:
            sanitized = load_and_validate_snapshot(db, block_id)
        except ValueError as e:
            log.warning("[observation_chapter] block %s skipped: %s", block_id, e)
            continue
        except Exception as e:
            log.warning("[observation_chapter] block %s validation error: %s",
                        block_id, e)
            continue

        blocks_for_render.append({
            "block_id": block_id,
            "kind": sanitized.expected_kind,
            "title": title,
            "snapshot": sanitized.snapshot,
            "block_status": sanitized.block_status,
            "sort_order": 0,  # уже отсортировано через SQL
            # Phase O1 — parts/notes/comment для гейтинга в renderer (PDF).
            "params": params,
            "comment": comment,
        })

    if not blocks_for_render:
        # g-fix-5: явный warning при аномалии — блоки в БД были (rows непустой),
        # но НИ ОДИН не прошёл валидацию. Это потенциальный silent production failure.
        if rows:
            log.warning(
                "[observation_chapter] well %s: %d observation-блок(ов) с in_report=true, "
                "но НИ ОДИН не прошёл C3-валидацию — глава будет пустой в PDF",
                well_id, len(rows),
            )
        return ""

    try:
        latex = render_observation_chapter_latex(
            blocks_for_render,
            skip_figures=False,  # PDF mode — нужны PNG
        )
    except Exception as e:
        # g-fix-5: render-ошибка не должна быть silent — это блокирующая
        # проблема для PDF главы. Логируем на уровне error.
        log.error(
            "[observation_chapter] well %s: render_observation_chapter_latex "
            "FAILED для %d блок(ов) — глава НЕ попадёт в PDF: %s",
            well_id, len(blocks_for_render), e,
        )
        return ""

    if not latex.strip():
        log.warning(
            "[observation_chapter] well %s: renderer вернул пустой LaTeX "
            "для %d блок(ов)", well_id, len(blocks_for_render),
        )
    return latex


def generate_adaptation_report_pdf(doc, db: Session) -> str:
    """Сгенерировать PDF отчёта об адаптации.

    doc.meta должен содержать:
      - well_id (int)
      - work_start_date (str, ISO, опционально)
      - obs_date_from/obs_date_to (опциональные переопределения)
      - adapt_date_from/adapt_date_to (опциональные переопределения)

    Возвращает относительный путь к PDF для doc.pdf_filename.
    """
    from backend.services.daily_report_service import (
        _ensure_dirs, _get_latex_env, _compile_latex, _tex_escape,
    )

    _ensure_dirs()

    meta = doc.meta or {}
    well_id = meta.get("well_id") or doc.well_id
    if not well_id:
        raise ValueError("В метаданных документа не указан well_id")

    # Сбор данных через существующую функцию
    data = collect_report_data(db, int(well_id))
    if not data.get("ok"):
        raise ValueError(f"Не удалось собрать данные: {data.get('error')}")

    # Форматирование для LaTeX
    observation = _add_formatted_fields(dict(data["observation"]))
    adaptation = _add_formatted_fields(dict(data["adaptation"]))
    comparison = _format_comparison(data["comparison"])

    # Дата начала работ (из meta)
    work_start = meta.get("work_start_date")
    if work_start:
        try:
            work_start = _fmt_date(date.fromisoformat(work_start))
        except (ValueError, TypeError):
            work_start = None

    # Well — экранируем текстовые поля
    well_ctx = dict(data["well"])
    well_ctx["name"] = _tex_escape(well_ctx.get("name") or "")
    well_ctx["horizon"] = _tex_escape(str(well_ctx.get("horizon") or "---"))
    well_ctx["choke_mm"] = _fmt_num(well_ctx.get("choke_mm"), 1)

    # Предупреждения
    warnings_list = [_tex_escape(w) for w in (data.get("warnings") or [])]

    now_kungrad = datetime.utcnow() + KUNGRAD_OFFSET

    # Toggle разделов PDF — берём из meta, незаполненные → True.
    _SEC_KEYS = ("well_info", "customer_data", "observation",
                 "adaptation", "charts_compare", "comparison")
    _meta_sections = (meta.get("sections") or {})
    include_sections = {k: bool(_meta_sections.get(k, True)) for k in _SEC_KEYS}

    # g-fix-1: observation chapter (главa «Наблюдение») — LaTeX-фрагмент
    # из observation-блоков (kind=observation_baseline/period/segment).
    # Renderer (F) — snapshot-only; читаем блоки через C3 sanitization gateway.
    observation_chapter_latex = _build_observation_chapter_latex(db, int(well_id))

    context = {
        "doc_number": _tex_escape(doc.doc_number or f"ID{doc.id}"),
        "generated_at": now_kungrad.strftime("%d.%m.%Y %H:%M"),
        "well": well_ctx,
        "work_start_date": work_start,
        "equipment_acts": [],  # Этап 4: заполнить из equipment_installation
        "observation": observation,
        "adaptation": adaptation,
        "comparison": comparison,
        "conclusions": [_tex_escape(c) for c in (data.get("conclusions") or [])],
        "warnings": warnings_list,
        "include_sections": include_sections,
        "observation_chapter_latex": observation_chapter_latex,
    }

    env = _get_latex_env()
    template = env.get_template("adaptation_report.tex")
    latex_source = template.render(**context)

    base_name = f"adaptation_report_{doc.id}"
    pdf_path = _compile_latex(latex_source, base_name)

    # Чистка временных PNG графиков
    from pathlib import Path
    for stage in (observation, adaptation):
        for key in ("pressure_chart_path", "dp_chart_path", "flow_chart_path"):
            p = stage.get(key)
            if p:
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:
                    pass
    # PNG карты §1
    _map_path = well_ctx.get("map_chart_path") if isinstance(well_ctx, dict) else None
    if _map_path:
        try:
            Path(_map_path).unlink(missing_ok=True)
        except Exception:
            pass

    log.info("Adaptation report PDF generated: %s", pdf_path)
    return f"generated/pdf/{base_name}.pdf"
