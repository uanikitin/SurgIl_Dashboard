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
    _linear_trend,
)

log = logging.getLogger(__name__)


STATUS_OBSERVATION = "Наблюдение"
STATUS_ADAPTATION = "Адаптация"

# Дефолтная длительность этапа адаптации при автодетекте (от первого вброса)
DEFAULT_ADAPT_DURATION_DAYS = 10


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
    working_hours = 0
    hours_with_data = 0
    utilization_pct = None
    dp_trend = None
    p_tube_trend = None
    p_line_trend = None
    q_trend = None

    if not hourly.empty:
        pt = hourly["p_tube"].dropna()
        pl = hourly["p_line"].dropna()
        dp = (hourly["p_tube"] - hourly["p_line"]).dropna()

        if len(pt) > 0:
            p_tube_median = float(pt.median())
        if len(pl) > 0:
            p_line_median = float(pl.median())
        if len(dp) > 0:
            dp_median = float(dp.median())

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

        # Тренды за период (линейная регрессия по часовым точкам)
        duration_hours = (hourly.index.max() - hourly.index.min()).total_seconds() / 3600.0
        if duration_hours > 0:
            if len(dp) >= 3:
                dp_trend = _linear_trend(dp.tolist(), duration_hours)
            if len(pt) >= 3:
                p_tube_trend = _linear_trend(pt.tolist(), duration_hours)
            if len(pl) >= 3:
                p_line_trend = _linear_trend(pl.tolist(), duration_hours)
    else:
        downtime_hours = 0

    # 2. Дебит через полный pipeline (те же функции, что и daily_report)
    flow_median = None
    flow_avg = None
    flow_cumulative = None
    working_days = 0
    q_max = None
    q_min = None
    q_max_time = None   # datetime (Kungrad-local) пика

    df_flow = None   # DataFrame с колонкой flow_rate для графика

    if choke_mm and choke_mm > 0:
        try:
            # _compute_month_flow_from_raw принимает date — даём .date() от datetime
            flow_result = _compute_month_flow_from_raw(
                db, well, choke_mm,
                dt_from.date(), dt_to.date(),
                get_pressure_data, clean_pressure,
                calculate_flow_rate, calculate_cumulative,
                comparison_days=duration_days,
            )
            if flow_result:
                flow_median = flow_result.get("month_median_flow")
                flow_avg = flow_result.get("month_avg_flow")
                flow_cumulative = flow_result.get("month_cumulative")
                working_days = flow_result.get("working_days") or 0
        except Exception:
            log.exception(
                "collect_stage_stats: flow calc failed for well %s (%s..%s)",
                well.number, date_from, date_to,
            )

        # Для графика Q(t) и Q-тренда — строим по hourly (одна точка в час)
        if not hourly.empty:
            df_flow = _compute_hourly_flow(hourly, choke_mm)
            if df_flow is not None and not df_flow.empty:
                q_series = df_flow["flow_rate"].dropna()
                # Только рабочие точки (Q > 0)
                q_working = q_series[q_series > 0]
                if len(q_working) > 0:
                    q_max = float(q_working.max())
                    q_min = float(q_working.min())
                    # Время пика (UTC → Kungrad-local)
                    q_max_ts_utc = q_working.idxmax()
                    q_max_time = q_max_ts_utc + KUNGRAD_OFFSET
                if len(q_working) >= 3:
                    dur_h = (
                        df_flow.index.max() - df_flow.index.min()
                    ).total_seconds() / 3600.0
                    if dur_h > 0:
                        q_trend = _linear_trend(q_working.tolist(), dur_h)

    # 3. События за период (продувки, вбросы реагентов)
    local_start = dt_from
    local_end = dt_to

    event_counts = db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE event_type = 'purge') AS purges,
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

    # Маркеры событий для графиков (все значимые типы)
    event_markers: list[dict] = []
    marker_rows = db.execute(text("""
        SELECT event_time, event_type FROM events
        WHERE well = :wno
          AND event_type IN ('purge', 'reagent', 'equip', 'other')
          AND event_time >= :start AND event_time <= :end
    """), {
        "wno": str(well.number), "start": local_start, "end": local_end,
    }).fetchall()
    for r in marker_rows:
        if r[0]:
            # events хранятся в Kungrad-локальном времени → переводим в UTC
            event_markers.append({
                "time_utc": r[0] - KUNGRAD_OFFSET,
                "event_type": r[1],
            })

    # 4. Графики
    pressure_chart_path = None
    dp_chart_path = None
    flow_chart_path = None

    combined_chart_path = None

    # События в Kungrad-local (для отображения на frontend-графике)
    events_for_chart: list[dict] = []
    for m in event_markers:
        ts_local = m["time_utc"] + KUNGRAD_OFFSET
        events_for_chart.append({
            "t": ts_local.isoformat(timespec="minutes"),
            "type": m["event_type"],
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
        # Давления (raw float или None)
        "p_tube_median": p_tube_median,
        "p_line_median": p_line_median,
        "dp_median": dp_median,
        "dp_trend": dp_trend,
        "p_tube_trend": p_tube_trend,
        "p_line_trend": p_line_trend,
        "q_trend": q_trend,
        # Дебит (raw float или None)
        "flow_median": flow_median,
        "flow_avg": flow_avg,
        "flow_cumulative": flow_cumulative,
        "q_min": q_min,
        "q_max": q_max,
        "q_max_time": q_max_time,
        # Рабочее время
        "hours_with_data": hours_with_data,
        "working_hours": working_hours,
        "downtime_hours": downtime_hours,
        "utilization_pct": utilization_pct,
        "working_days": working_days,
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

    # Маркеры событий (нужны для частоты продувок)
    local_start = dt_from
    local_end = dt_to
    rows = db.execute(text("""
        SELECT event_time, event_type FROM events
        WHERE well = :wno AND event_type = 'purge'
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

    # События помесячно — одним запросом
    events_rows = db.execute(text("""
        SELECT
            to_char(event_time, 'YYYY-MM') AS ym,
            event_type,
            COUNT(*) AS cnt
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

        # Тренд Q за месяц (по рабочим часам)
        trend = None
        if len(q_working) >= 12:  # минимум 12 часов для осмысленного тренда
            t0 = q_working.index.min()
            t_h = np.array(
                [(t - t0).total_seconds() / 3600.0 for t in q_working.index]
            )
            dur_h = float(t_h[-1] - t_h[0])
            if dur_h > 0:
                trend = _linear_trend(q_working.tolist(), dur_h)

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


def _compute_hourly_flow(hourly: pd.DataFrame, choke_mm: float) -> pd.DataFrame:
    """Посчитать мгновенный дебит Q для каждой часовой точки.

    Использует ту же формулу, что scenario_service (критический/докритический режим).
    """
    from backend.services.flow_rate.config import DEFAULT_FLOW as cfg

    choke_sq = (choke_mm / cfg.C2) ** 2
    q_values = []
    for _, row in hourly.iterrows():
        pt, pl = row["p_tube"], row["p_line"]
        if pd.isna(pt) or pd.isna(pl) or pt <= 0 or pt <= pl:
            q_values.append(np.nan)
            continue
        pt, pl = float(pt), float(pl)
        ratio = (pt - pl) / pt
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

    # Линия тренда
    if dp_trend and dp_trend.get("slope_per_day") is not None:
        slope_per_h = dp_trend["slope_per_day"] / 24.0
        t0 = df_local.index.min()
        t_hours = np.array([(t - t0).total_seconds() / 3600.0 for t in df_local.index])
        dp_mean = float(dp.mean())
        trend_line = dp_mean - slope_per_h * (t_hours.mean() - t_hours) * 0 + (
            slope_per_h * t_hours + (dp_mean - slope_per_h * t_hours.mean())
        )
        ax.plot(df_local.index, trend_line, color="#333", linewidth=1.0,
                linestyle="--",
                label=f"Тренд ({dp_trend.get('direction', 'flat')}, "
                      f"{dp_trend.get('slope_per_day', 0):+.3f}/сут)")

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

    # ── 2. ΔP + тренд ──
    ax_dp.plot(df_local.index, dp, color="#ef6c00", linewidth=0.9, label="ΔP")
    ax_dp.fill_between(df_local.index, dp, alpha=0.15, color="#ef6c00")
    if dp_trend and dp_trend.get("slope_per_day") is not None:
        slope_per_h = dp_trend["slope_per_day"] / 24.0
        t0 = df_local.index.min()
        t_hours = np.array([(t - t0).total_seconds() / 3600.0 for t in df_local.index])
        dp_mean = float(dp.mean())
        trend_line = slope_per_h * t_hours + (dp_mean - slope_per_h * t_hours.mean())
        ax_dp.plot(
            df_local.index, trend_line, color="#333", linewidth=1.0, linestyle="--",
            label=f"Тренд ({dp_trend.get('slope_per_day', 0):+.3f}/сут)",
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
            trend_line = slope_per_h * t_hours + (q_mean - slope_per_h * t_hours.mean())
            ax_q.plot(
                df_q.index, trend_line, color="#333", linewidth=1.0, linestyle="--",
                label=f"Тренд ({q_trend.get('slope_per_day', 0):+.2f}/сут)",
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


def generate_stage_narrative(stage: dict, stage_name: str) -> str:
    """Текстовое описание этапа: период, работа, характеристики, тренды."""
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

    # Интерпретация трендов
    interp = _interpret_trends(stage)
    if interp:
        lines.append("Анализ трендов: " + interp)

    return "\n".join(lines)


def _interpret_trends(stage: dict) -> str:
    """Качественная интерпретация основных трендов."""
    parts = []
    dp_tr = stage.get("dp_trend")
    q_tr = stage.get("q_trend")
    pt_tr = stage.get("p_tube_trend")

    if dp_tr and dp_tr.get("direction") == "up" and (dp_tr.get("r_squared") or 0) > 0.3:
        parts.append(
            "рост ΔP свидетельствует об активизации газопроявления — "
            "отбор идёт эффективнее"
        )
    elif dp_tr and dp_tr.get("direction") == "down" and (dp_tr.get("r_squared") or 0) > 0.3:
        parts.append(
            "снижение ΔP указывает на падение интенсивности отбора "
            "(истощение или накопление жидкости)"
        )

    if q_tr and q_tr.get("direction") == "up" and (q_tr.get("r_squared") or 0) > 0.3:
        parts.append("дебит растёт по ходу этапа")
    elif q_tr and q_tr.get("direction") == "down" and (q_tr.get("r_squared") or 0) > 0.3:
        parts.append("дебит снижается по ходу этапа")

    if pt_tr and pt_tr.get("direction") == "down" and (pt_tr.get("r_squared") or 0) > 0.3:
        parts.append(
            "снижение P трубного может быть связано с работой реагента "
            "(очистка ствола от жидкости)"
        )

    return "; ".join(parts) + "." if parts else ""


def generate_conclusions(
    obs: dict, adapt: dict, comparison: dict,
) -> list[str]:
    """Сформировать текстовые выводы по правилам."""
    out: list[str] = []

    # Длительность этапов
    out.append(
        f"Продолжительность этапа наблюдения: {obs['duration_days']} сут., "
        f"адаптации: {adapt['duration_days']} сут."
    )

    # Дебит
    dq = comparison["delta_flow_median"]
    dq_pct = comparison["delta_flow_median_pct"]
    if dq is not None and dq_pct is not None:
        if dq_pct > 10:
            out.append(
                f"Медианный дебит вырос на {dq:+.2f} тыс.м³/сут ({dq_pct:+.1f}%) "
                f"по сравнению с этапом наблюдения."
            )
        elif dq_pct < -10:
            out.append(
                f"Медианный дебит снизился на {abs(dq):.2f} тыс.м³/сут "
                f"({dq_pct:+.1f}%) по сравнению с этапом наблюдения."
            )
        else:
            out.append(
                f"Дебит стабилен: изменение в пределах погрешности "
                f"({dq_pct:+.1f}%)."
            )

    # Перепад давления
    ddp = comparison["delta_dp"]
    ddp_pct = comparison["delta_dp_pct"]
    if ddp is not None:
        if ddp > 0.5:
            out.append(
                f"Перепад давления ΔP увеличился на {ddp:+.2f} кгс/см² "
                f"({ddp_pct:+.1f}% если применимо), что свидетельствует "
                f"об активизации газопроявления."
            )
        elif ddp < -0.5:
            out.append(
                f"Перепад давления ΔP снизился на {abs(ddp):.2f} кгс/см²."
            )

    # Утилизация
    du = comparison["delta_utilization_pct"]
    if du is not None:
        if du > 5:
            out.append(
                f"Коэффициент использования времени вырос на {du:+.1f} п.п. — "
                f"скважина стала работать стабильнее."
            )
        elif du < -5:
            out.append(
                f"Коэффициент использования времени снизился на {abs(du):.1f} п.п."
            )

    # Продувки
    dpurge = comparison["delta_purge_count"]
    if dpurge < 0:
        out.append(f"Количество продувок уменьшилось на {abs(dpurge)}.")
    elif dpurge > 0:
        out.append(f"Количество продувок увеличилось на {dpurge}.")

    # Реагенты
    if adapt["reagent_count"] > 0 and obs["reagent_count"] == 0:
        out.append(
            f"На этапе адаптации применялись реагенты ({adapt['reagent_count']} "
            f"вбросов, расход {adapt['reagent_qty']:.1f})."
        )

    return out


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
) -> dict[str, Any]:
    """Собрать все данные для отчёта об адаптации.

    Если указаны все 4 даты периодов, используются они (с базовой валидацией:
    adapt_from > obs_to, adapt_to >= adapt_from, obs_to >= obs_from).
    Если даты не указаны — автодетект из well_status через validate_stages().

    render_charts: рендерить ли PNG-графики (медленно). На странице настройки
    лучше False для быстрого превью статистики.
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

    # Сбор статистики обоих этапов
    obs_stats = collect_stage_stats(
        db, well, choke_mm, obs_from, obs_to,
        render_charts=render_charts, chart_tag="obs",
    )
    # Описание: приоритет у явного override, иначе из well_status.note
    obs_stats["description"] = (
        obs_description_override
        if obs_description_override is not None
        else obs_note
    )

    # ─── Сначала детектим кандидатов, чтобы передать в график адаптации ───
    optimal_candidates: list[dict] = []
    optimal_regime: dict | None = None

    if optimal_mode != "off":
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
        render_charts=render_charts, chart_tag="adapt",
        highlight_windows=optimal_candidates if optimal_candidates else None,
    )
    adapt_stats["description"] = (
        adapt_description_override
        if adapt_description_override is not None
        else adapt_note
    )

    if optimal_mode != "off":

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
                    render_charts=render_charts, chart_tag="optimal",
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
    conclusions = generate_conclusions(
        obs_stats, optimal_regime or adapt_stats,
        comparison_optimal or comparison,
    )

    # Генерация текстового описания этапов
    obs_stats["narrative"] = generate_stage_narrative(obs_stats, "наблюдения")
    adapt_stats["narrative"] = generate_stage_narrative(adapt_stats, "адаптации")

    # Эффективность реагентов (за этап адаптации)
    reagent_effectiveness = None
    if include_reagent_effectiveness:
        try:
            reagent_effectiveness = analyze_reagent_for_stage(
                well_id, adapt_from, adapt_to,
            )
        except Exception:
            log.exception("Reagent effectiveness analysis failed for well %s", well.id)
            reagent_effectiveness = {"error": "Анализ эффективности реагента не выполнен"}

    return {
        "ok": True,
        "warnings": warnings,
        "well": {
            "id": well.id,
            "number": str(well.number),
            "name": well.name,
            "horizon": horizon,
            "choke_mm": choke_mm,
        },
        "observation": obs_stats,
        "adaptation": adapt_stats,
        "optimal_regime": optimal_regime,
        "optimal_candidates": optimal_candidates,
        "optimal_window_days": optimal_window_days,
        "comparison": comparison,
        "comparison_optimal": comparison_optimal,
        "conclusions": conclusions,
        "reagent_effectiveness": reagent_effectiveness,
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


def _fmt_signed(val, decimals: int = 2) -> str:
    """Форматирование числа со знаком (+/-)."""
    if val is None:
        return "---"
    try:
        fv = float(val)
        if np.isnan(fv) or np.isinf(fv):
            return "---"
        return f"{fv:+.{decimals}f}"
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
    }

    env = _get_latex_env()
    template = env.get_template("adaptation_report.tex")
    latex_source = template.render(**context)

    base_name = f"adaptation_report_{doc.id}"
    pdf_path = _compile_latex(latex_source, base_name)

    # Чистка временных PNG графиков
    for stage in (observation, adaptation):
        for key in ("pressure_chart_path", "dp_chart_path", "flow_chart_path"):
            p = stage.get(key)
            if p:
                try:
                    from pathlib import Path
                    Path(p).unlink(missing_ok=True)
                except Exception:
                    pass

    log.info("Adaptation report PDF generated: %s", pdf_path)
    return f"generated/pdf/{base_name}.pdf"
