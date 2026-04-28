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
    _linear_trend,
    _robust_trend,
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
                if len(q_working) >= 4:
                    # robust slope для Q — на working-точках (q > 0).
                    # x в часах от первой working-точки.
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
            pct_str = f" ({ddp_pct:+.1f}%)" if ddp_pct is not None else ""
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


def _enrich_period_with_customer_data(
    db: Session, well: Well,
    period_from: date, period_to: date,
    analysis: dict, render_charts: bool, p_idx: int,
) -> None:
    """Дополняет analysis-словарь данными со страницы /customer-daily:
      - describe (описательная статистика, 8 колонок)
      - months   (помесячная агрегация и тренды)
      - month_descriptions (текст по месяцам)
      - chart_paths (4 PNG-пути)

    Изменяет analysis in-place. Все ошибки молча проглатываются — глава
    останется частичной, но не упадёт.
    """
    try:
        from backend.services.customer_daily_service import (
            load_for_well, describe_well_period,
            monthly_stats, monthly_description, well_chart_payload,
            period_summary_text,
        )
    except Exception:
        log.exception("customer_daily_service import failed")
        return

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

    try:
        analysis["describe"] = describe_well_period(df) or []
    except Exception:
        log.exception("describe_well_period failed")
        analysis["describe"] = []

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
            _ensure_dirs()
            base = TEMP_DIR / f"cust_p{p_idx}_w{well.id}"

            renders = (
                ("pressures",    ccr.render_pressures_chart),
                ("dp",           ccr.render_dp_chart),
                ("flow_dt",      ccr.render_flow_downtime_chart),
            )
            for key, fn in renders:
                try:
                    out = fn(payload, str(base) + f"_{key}.png")
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

    # ─── Глава «Анализ исходных данных» (по данным заказчика) ───
    # Источники периодов:
    #   1. customer_periods — старый аргумент (передаётся вручную из вызова).
    #   2. customer_report_block — новые блоки (kind=period_analysis, comparison),
    #      добавленные пользователем со страницы /customer-daily.
    # Глава собирается всегда — если ничего нет, останется None.
    customer_chapter = None
    if include_customer_chapter or True:  # авто-сборка независимо от флага
        try:
            from backend.services import customer_baseline_service as bsvc
            from backend.services import customer_daily_service as csvc

            baselines = bsvc.list_baselines(db, well.id)

            # Собираем периоды для анализа: старые аргументы + блоки kind='period_analysis'
            periods_input = list(customer_periods or [])
            comparisons_input = []

            try:
                csvc.ensure_blocks_table(db)
                blocks = csvc.get_blocks_for_report(db, well.id)
            except Exception:
                blocks = []

            for b in blocks:
                p = b.get("params") or {}
                if b.get("kind") == "period_analysis":
                    periods_input.append({
                        "period_from": p.get("date_from"),
                        "period_to":   p.get("date_to"),
                        "description": b.get("comment") or b.get("title"),
                        "block_id":    b.get("id"),
                        "block_title": b.get("title"),
                    })
                elif b.get("kind") == "comparison":
                    comparisons_input.append({
                        "block_id": b.get("id"),
                        "title":    b.get("title"),
                        "comment":  b.get("comment"),
                        "params":   p,
                    })

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

            seen_ranges: dict[tuple, dict] = {}
            for _p in periods_input:
                key = (_norm_d(_p.get("period_from")),
                       _norm_d(_p.get("period_to")))
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

                # ── Расширяем данными со страницы /customer-daily ──
                # describe_well_period, monthly_stats, monthly_description
                # + 4 PNG диаграммы (давления, ΔP, дебит+простой, помесячно).
                _enrich_period_with_customer_data(
                    db, well, pf, pt, analysis, render_charts, p_idx,
                )

                periods_data.append(analysis)

            # Если по этой скв. вообще ничего нет — глава пустая (None).
            if not (baselines or periods_data or comparisons_input):
                customer_chapter = None
            else:
                customer_chapter = {
                    "baselines": baselines,
                    "periods": periods_data,
                    "comparisons": comparisons_input,
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

    # Технические данные §1: конструкция, перфорация, карта (только при render_charts).
    well_tech = _collect_well_tech_data(db, well, render_map=render_charts)

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
