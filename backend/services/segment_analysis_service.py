"""Wrapper-сервис сегментного анализа для интеграции в SurgIl Dashboard.

Содержит:
  • compute_segment_block — главная точка входа для UI/PDF
  • build_segment_snapshot — формирует snapshot для customer_report_block

Архитектура:

  /api/customer-daily/segment-analysis/preview  ─┐
                                                  ├──► compute_segment_block(db, well, d_from, d_to)
  customer_daily_service.create_block(kind=     ─┘         │
                       'segment_analysis', ...)            │
                                                           ▼
                                       load_for_well(well, d_from, d_to)  ← переиспользуем
                                                           │
                                                           ▼
                                       _apply_user_overrides()            ← из segment_settings_service
                                                           │
                                                           ▼
                                       _segment_analysis_dual(df)         ← из standalone-модуля
                                       _compute_pav_score(dual, df)       ← из standalone-модуля
                                                           │
                                                           ▼
                                       build_segment_snapshot(...)        ← структура для сохранения

ВАЖНО: standalone-модуль читает SEGMENT_THRESHOLDS как глобальную
переменную (через _segth). Чтобы не править модуль и при этом
поддержать пользовательские overrides — в момент расчёта временно
подменяем dict (с блокировкой mutex, чтобы параллельные запросы
не получили чужие значения), а после — восстанавливаем дефолт.
Это thread-safe для FastAPI sync handlers через threadpool.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from backend.services import segment_analysis_module as sam
from backend.services import segment_settings_service as settings
from backend.services.customer_daily_service import load_for_well

log = logging.getLogger(__name__)

# Mutex для атомарного применения user-overrides к глобальному
# SEGMENT_THRESHOLDS dict в standalone-модуле. Сегментный анализ
# в одной FastAPI-сессии — короткая операция (миллисекунды), lock
# не создаст узкого места.
_thresholds_lock = threading.Lock()


@contextmanager
def _apply_user_overrides():
    """Контекстный менеджер: подменяет SEGMENT_THRESHOLDS на эффективные
    значения (дефолты + user overrides), выполняет блок, восстанавливает.

    Используется внутри compute_segment_block перед вызовами
    _segment_analysis_dual / _compute_pav_score.
    """
    effective = settings.get_effective()
    with _thresholds_lock:
        # Сохраняем оригинал (полная копия — defensive).
        snapshot = dict(sam.SEGMENT_THRESHOLDS)
        try:
            # Применяем эффективные значения. Ключи которых нет в дефолте
            # — игнорируются (set_overrides уже это фильтрует).
            for k, v in effective.items():
                if k in sam.SEGMENT_THRESHOLDS:
                    sam.SEGMENT_THRESHOLDS[k] = v
            yield
        finally:
            # Полное восстановление (а не точечное) — на случай если кто-то
            # внутри анализа подменил ключи (не должны, но defensive).
            sam.SEGMENT_THRESHOLDS.clear()
            sam.SEGMENT_THRESHOLDS.update(snapshot)


# ═══════════════════════════════════════════════════════════════════
#  PLAN A — Core Segment Analysis Integration
# ───────────────────────────────────────────────────────────────────
#  Согласно ТЗ §7 — Core результат должен содержать РЕАЛЬНЫЕ точки и
#  РЕАЛЬНЫЕ тренды (slope × x + intercept), а НЕ горизонтальные средние.
#  ПАВ-блок — optional layer (include_pav_recommendation), по умолчанию
#  отключён для главы «Анализ данных заказчика».
# ═══════════════════════════════════════════════════════════════════

# Палитра цветов для трендов сегментов. Источник: эталонная реализация
# fig_segment_analysis в wells_web.py:4509 (7 цветов) + добавлены 5
# дополнительных ярких для поддержки до 12 сегментов.
# Сегмент с is_shutdown_cluster=True переопределяется на красный
# (#c0392b — соответствует эталону).
SEGMENT_COLORS = [
    "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b",
    "#e377c2", "#17becf", "#bcbd22", "#1f77b4",
    "#d62728", "#7f7f7f", "#c5b0d5", "#aec7e8",
]
SHUTDOWN_CLUSTER_COLOR = "#c0392b"


def _classify_segment_type(seg: dict, prev_seg: dict | None) -> str:
    """Определение «Тип режима» сегмента по §6.1 ТЗ (10 категорий).

    Приоритет правил соответствует таблице §6.1:
        is_workover → «Ремонт/КРС»
        is_shutdown_cluster → «Простой»
        prev_seg.is_shutdown_cluster → «Возобновление»
        |change_choke| ≥ 0.5 → «Смена штуцера»
        gradual_trend = 'down' → «Плавный спад»
        gradual_trend = 'up' → «Плавный рост»
        change_pct ≤ −20 → «Резкое падение»
        change_pct ≤ −10 → «Снижение»
        change_pct ≥ +20 → «Резкий рост»
        change_pct ≥ +10 → «Рост»
        иначе → «Стабильно»
    """
    if seg.get("is_workover"):
        return "Ремонт/КРС"
    if seg.get("is_shutdown_cluster"):
        return "Простой"
    if prev_seg is not None and prev_seg.get("is_shutdown_cluster"):
        return "Возобновление"
    change_choke = seg.get("change_choke")
    if change_choke is not None and abs(change_choke) >= 0.5:
        return "Смена штуцера"
    if seg.get("gradual_trend") == "down":
        return "Плавный спад"
    if seg.get("gradual_trend") == "up":
        return "Плавный рост"
    change_pct = seg.get("change_pct")
    if change_pct is not None:
        if change_pct <= -20:
            return "Резкое падение"
        if change_pct <= -10:
            return "Снижение"
        if change_pct >= 20:
            return "Резкий рост"
        if change_pct >= 10:
            return "Рост"
    return "Стабильно"


def _ddmmyyyy_to_iso(s: str) -> str:
    """`'30.01.2026'` → `'2026-01-30'`. Невалидное на входе возвращается как есть."""
    if not s or not isinstance(s, str):
        return s
    try:
        d, m, y = s.split(".")
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    except Exception:
        return s


def _build_chart_data(df) -> dict:
    """Сериализация исходных суточных точек df → snapshot.chart_data.

    Длина каждого массива = n_points (всех точек после load_for_well).
    NaN допустимы — фронт их корректно обработает как разрывы линии.

    Колонки p_wellhead/p_flowline/dp могут отсутствовать в df — заполняются
    NaN; choke_mm может отсутствовать — массив пустой или из NaN.
    """
    import numpy as np
    d = df.sort_values("date").reset_index(drop=True)
    dates_iso = [pd.Timestamp(v).strftime("%Y-%m-%d") for v in d["date"]]
    def col(name, default=np.nan):
        if name in d.columns:
            arr = d[name].astype(float).tolist()
        else:
            arr = [default] * len(d)
        return [None if (v is None or (isinstance(v, float) and v != v)) else v for v in arr]
    p_wh = col("p_wellhead")
    p_fl = col("p_flowline")
    dp = []
    for a, b in zip(p_wh, p_fl):
        if a is None or b is None:
            dp.append(None)
        else:
            dp.append(a - b)
    return {
        "dates":         dates_iso,
        "q_total":       col("q_gas_total"),
        "q_working":     col("q_gas_working"),
        "shutdown_min":  col("shutdown_min", 0),
        "p_wellhead":    p_wh,
        "p_flowline":    p_fl,
        "dp":            dp,
        "choke_mm":      col("choke_mm"),
    }


def _build_segments_extended(strip_segments: list, df) -> list[dict]:
    """Объединение strip-сегментов с extended-полями (slope_total/intercept_total/
    slope_working/intercept_working/start_idx/end_idx).

    strip — это `_segment_analysis(df)['segments']` (с is_workover/gradual/
    preshutdown_*). Extended — `_segment_trends_extended(...)` (со slopes
    и start_idx/end_idx).

    Берём strip как базу (там уже все post-process поля), добавляем поля
    из extended: start_idx, end_idx, slope_total, intercept_total,
    slope_working, slope_dp. Пересчитываем intercept_working локально
    через _linreg_full (модуль теряет его в _segment_trends_extended).
    """
    import numpy as np
    if not strip_segments:
        return []

    d = df.sort_values("date").reset_index(drop=True)
    dates = d["date"].values
    q_total = d["q_gas_total"].to_numpy(dtype=float)
    q_working = (
        d["q_gas_working"].to_numpy(dtype=float)
        if "q_gas_working" in d.columns else q_total.copy()
    )
    shutdown = (
        d["shutdown_min"].to_numpy(dtype=float)
        if "shutdown_min" in d.columns else np.zeros_like(q_total)
    )
    if "p_wellhead" in d.columns and "p_flowline" in d.columns:
        p_wh = d["p_wellhead"].to_numpy(dtype=float)
        p_fl = d["p_flowline"].to_numpy(dtype=float)
        dp = p_wh - p_fl
    else:
        p_wh = np.full_like(q_total, np.nan)
        p_fl = np.full_like(q_total, np.nan)
        dp = np.full_like(q_total, np.nan)
    choke = (
        d["choke_mm"].to_numpy(dtype=float)
        if "choke_mm" in d.columns else None
    )

    # Восстановим changepoints idx из strip
    # (strip-changepoints — список {date, idx}; для extended нужны idx-ы)
    # Тут strip_segments[*].start_idx уже есть... но мы передали strip из dual,
    # который проходит через стрипп. Стрипп НЕ сохраняет start_idx/end_idx.
    # Вызываем _segment_trends_extended параллельно.
    # Для этого нужны cps_idx. Восстанавливаем из dual.primary.changepoints.
    # Но dual.primary.changepoints мы тут не получаем — он отдельно.
    # Делаем простой путь: запускаем _detect_changepoints_extended на тех же
    # данных и порогах, что внутри _segment_analysis_dual.
    cps_idx = sam._detect_changepoints_extended(
        q_total, shutdown, min_segment=3, threshold_pct=15.0,
        p_flowline=p_fl if np.isfinite(p_fl).any() else None,
        dp=dp if np.isfinite(dp).any() else None,
        choke=choke,
    )
    shutdown_clusters_idx = sam._find_shutdown_clusters(
        shutdown, q_total,
        min_days=2, shutdown_threshold_min=600.0,
        q_drop_threshold_pct=50.0,
    )
    extended = sam._segment_trends_extended(
        dates, q_total, q_working, shutdown, dp,
        cps_idx, shutdown_clusters_idx,
        p_wellhead=p_wh, p_flowline=p_fl, choke=choke,
    )

    # Защита: количество extended может отличаться от strip только при
    # сегментах с days < 2 (strip их фильтрует). Сопоставляем по start_date.
    # Если не сматчилось — вернём strip + предупреждение в логе.
    ext_by_start = {pd.Timestamp(s["start_date"]).strftime("%d.%m.%Y"): s
                    for s in extended}

    out: list[dict] = []
    prev_seg = None
    for i, s in enumerate(strip_segments):
        ext = ext_by_start.get(s.get("start"))
        merged = dict(s)
        if ext is not None:
            merged["start_idx"] = int(ext.get("start_idx"))
            merged["end_idx"]   = int(ext.get("end_idx"))
            merged["slope_total"]     = ext.get("slope")           # модуль использует "slope"=slope_total
            merged["intercept_total"] = ext.get("intercept")
            merged["slope_working"]   = ext.get("slope_working")
            merged["slope_dp"]        = ext.get("slope_dp")
            # intercept_working — модуль не сохраняет; пересчитываем
            start_idx = int(ext["start_idx"])
            end_idx   = int(ext["end_idx"])
            seg_qw = q_working[start_idx:end_idx]
            x_days = np.arange(end_idx - start_idx, dtype=float)
            slope_w, intercept_w = sam._linreg_full(x_days, seg_qw)
            merged["intercept_working"] = float(intercept_w) \
                if np.isfinite(intercept_w) else None
        else:
            log.warning(
                "segment_analysis: extended-сегмент не найден для start=%s",
                s.get("start"))
            merged["start_idx"] = None
            merged["end_idx"]   = None
            merged["slope_total"] = s.get("slope")
            merged["intercept_total"] = None
            merged["slope_working"] = None
            merged["intercept_working"] = None
            merged["slope_dp"] = None

        # ISO даты (start/end в strip — DD.MM.YYYY)
        merged["start_date"] = _ddmmyyyy_to_iso(s.get("start"))
        merged["end_date"]   = _ddmmyyyy_to_iso(s.get("end"))

        # Цвет: кластер простоев → красный; остальные — палитра
        if s.get("is_shutdown_cluster"):
            merged["color"] = SHUTDOWN_CLUSTER_COLOR
        else:
            merged["color"] = SEGMENT_COLORS[i % len(SEGMENT_COLORS)]

        # Тип режима по §6.1
        merged["type"] = _classify_segment_type(s, prev_seg)

        out.append(merged)
        prev_seg = s

    return out


def _build_cp_marks(strip_changepoints: list, ext_segments: list,
                    dual: dict) -> list[dict]:
    """Точки перелома + классификация (confirmed/only_total/only_working)
    + короткий тег причины (§6.3).

    Используются:
      • _changepoint_short_tag(curr, prev) из модуля — для tag
      • dual.common_dates / only_total / only_working — для source
    """
    if not strip_changepoints:
        return []

    # dual.* — даты в формате DD.MM.YYYY
    common_set = set(dual.get("common_dates") or [])
    only_total_set = set(dual.get("only_total") or [])
    only_working_set = set(dual.get("only_working") or [])

    # Сопоставление cp ↔ extended-segment: cp[i] = граница между segment[i-1] и segment[i]
    # strip-changepoints в strip формате [{date, idx}, ...].
    # Связь: после changepoint начинается следующий сегмент.
    out: list[dict] = []
    for i, cp in enumerate(strip_changepoints):
        cp_date_dm = cp.get("date")          # "DD.MM.YYYY"
        cp_idx    = cp.get("idx")
        # Найти сегмент curr (начинается в cp_idx) и prev (заканчивается в cp_idx)
        curr_seg = None
        prev_seg = None
        for s in ext_segments:
            if s.get("start_idx") == cp_idx:
                curr_seg = s
            if s.get("end_idx") == cp_idx:
                prev_seg = s
        # Если не нашли — пробуем по индексу в массиве ext_segments
        if curr_seg is None and i + 1 < len(ext_segments):
            curr_seg = ext_segments[i + 1]
        if prev_seg is None and i < len(ext_segments):
            prev_seg = ext_segments[i]

        if curr_seg is None or prev_seg is None:
            tag = ""
        else:
            try:
                tag = sam._changepoint_short_tag(curr_seg, prev_seg) or ""
            except Exception:
                log.exception("_changepoint_short_tag failed at cp %s", cp_date_dm)
                tag = ""

        # Source classification по dual.*
        if cp_date_dm in common_set:
            source = "confirmed"
        elif cp_date_dm in only_total_set:
            source = "only_total"
        elif cp_date_dm in only_working_set:
            source = "only_working"
        else:
            # cp есть в primary, но не размечена в dual? — считаем only_total
            # (детектирована только в первичном Q общем)
            source = "only_total"

        out.append({
            "idx":    int(cp_idx) if cp_idx is not None else None,
            "date":   _ddmmyyyy_to_iso(cp_date_dm),
            "tag":    tag,
            "source": source,
        })
    return out


def _build_shutdown_clusters(df) -> list[dict]:
    """Кластеры простоев → snapshot.shutdown_clusters (для слоя 5 графика)."""
    import numpy as np
    d = df.sort_values("date").reset_index(drop=True)
    q_total = d["q_gas_total"].to_numpy(dtype=float)
    shutdown = (
        d["shutdown_min"].to_numpy(dtype=float)
        if "shutdown_min" in d.columns else np.zeros_like(q_total)
    )
    clusters_idx = sam._find_shutdown_clusters(
        shutdown, q_total,
        min_days=2, shutdown_threshold_min=600.0,
        q_drop_threshold_pct=50.0,
    )
    dates = d["date"].values
    out = []
    for (s_idx, e_idx) in clusters_idx:
        total = float(np.nansum(shutdown[s_idx:e_idx])) if e_idx > s_idx else 0.0
        out.append({
            "start_idx":     int(s_idx),
            "end_idx":       int(e_idx),
            "start_date":    pd.Timestamp(dates[s_idx]).strftime("%Y-%m-%d"),
            "end_date":      pd.Timestamp(dates[min(e_idx, len(dates)-1)]).strftime("%Y-%m-%d"),
            "total_minutes": total,
        })
    return out


def compute_segment_block(
    db: Session,
    well: str,
    d_from: date,
    d_to: date,
    *,
    include_pav: bool = False,
) -> dict[str, Any]:
    """PLAN A — Core Segment Analysis для главы «Анализ данных заказчика».

    Контракт входа (из таблицы well_daily):
        date, q_gas_total, q_gas_working — обязательны
        shutdown_min, p_wellhead, p_flowline, choke_mm — опциональны

    Параметр include_pav:
        False (default) — ПАВ-балл НЕ считается, в snapshot pav=None.
        True            — вызывается _compute_pav_score, snapshot.pav заполнен.

    Контракт выхода — snapshot формата segment_v1 (см. Refined TS §1.2):
      Core (всегда):
        chart_data: {dates, q_total, q_working, shutdown_min, p_wellhead,
                     p_flowline, dp, choke_mm}                          # §7.2 слой 1/2
        segments_extended: [{num, start_idx, end_idx, start_date, end_date,
                             color, slope_total, intercept_total,
                             slope_working, intercept_working, slope_dp,
                             mean_q_total, mean_q_working, mean_dp,
                             mean_shutdown, mean_p_wellhead, mean_p_flowline,
                             mean_choke_mm, working_pct,
                             change_pct, change_q_working_pct, change_dp_pct,
                             change_shutdown, change_choke,
                             type, cause,
                             is_shutdown_cluster, is_workover,
                             gradual_trend, gradual_drift_pct,
                             preshutdown_q, preshutdown_delta_pct,
                             preshutdown_verdict}, ...]                  # §4.1, §6.1
        cp_marks: [{idx, date, tag, source}]                             # §6.3 + §11
        shutdown_clusters: [{start_idx, end_idx, start_date, end_date,
                             total_minutes}]                             # §7.2 слой 5
        descriptions: list[str]                                          # §6.4
        cp_descriptions: list[str]                                       # §6.5
        dual_summary: {primary_cp_count, working_cp_count, common_count,
                       common_dates, only_total_dates, only_working_dates} # §11
      Optional PAV layer:
        include_pav_recommendation: bool
        pav: dict | None  (заполнен только при include_pav=True)
      Сервисные:
        _v, well_number, date_from, date_to, days_count, n_points,
        dual (полные результаты для совместимости),
        thresholds_used, computed_at

    Если данных нет / период слишком короткий — возвращает dict
    с `ok: False, error: ...` (НЕ выбрасывает исключение).
    """
    from datetime import datetime as _dt

    df = load_for_well(db, str(well), d_from=d_from, d_to=d_to)
    if df is None or df.empty:
        return {
            "ok": False,
            "error": (
                f"Нет данных в well_daily для скв.{well} "
                f"за период {d_from} … {d_to}."
            ),
        }

    n_points = len(df)
    # Минимум для модуля: 6 точек (min_segment × 2). См. ТЗ §13.
    if n_points < 6:
        return {
            "ok": False,
            "error": (
                f"Недостаточно данных: {n_points} сут. (нужно ≥ 6). "
                f"Расширьте период анализа."
            ),
            "n_points": n_points,
        }

    # Применяем user-overrides и запускаем анализ.
    with _apply_user_overrides():
        try:
            dual = sam._segment_analysis_dual(df)
        except Exception as e:
            log.exception("_segment_analysis_dual failed for well=%s", well)
            return {"ok": False, "error": f"Анализ сегментов упал: {e}"}

        # ── Core extensions ──────────────────────────────────────
        try:
            chart_data = _build_chart_data(df)
        except Exception:
            log.exception("_build_chart_data failed for well=%s", well)
            chart_data = {"dates": [], "q_total": [], "q_working": [],
                          "shutdown_min": [], "p_wellhead": [], "p_flowline": [],
                          "dp": [], "choke_mm": []}

        primary = (dual or {}).get("primary") or {}
        strip_segments = primary.get("segments") or []
        strip_cps = primary.get("changepoints") or []

        try:
            segments_extended = _build_segments_extended(strip_segments, df)
        except Exception:
            log.exception("_build_segments_extended failed for well=%s", well)
            segments_extended = []

        try:
            cp_marks = _build_cp_marks(strip_cps, segments_extended, dual)
        except Exception:
            log.exception("_build_cp_marks failed for well=%s", well)
            cp_marks = []

        try:
            shutdown_clusters = _build_shutdown_clusters(df)
        except Exception:
            log.exception("_build_shutdown_clusters failed for well=%s", well)
            shutdown_clusters = []

        dual_summary = {
            "primary_cp_count":   len(strip_cps),
            "working_cp_count":   len((dual.get("working") or {}).get("changepoints", [])
                                      if dual.get("working") else []),
            "common_count":       len(dual.get("common_dates") or []),
            "common_dates":       list(dual.get("common_dates") or []),
            "only_total_dates":   list(dual.get("only_total") or []),
            "only_working_dates": list(dual.get("only_working") or []),
        }

        # ── Optional PAV layer ────────────────────────────────────
        pav: dict | None = None
        if include_pav:
            try:
                pav = sam._compute_pav_score(dual, df)
            except Exception as e:
                log.exception("_compute_pav_score failed for well=%s", well)
                pav = {
                    "score": None, "confidence": "n/a",
                    "scenario": "Ошибка расчёта ПАВ-балла",
                    "recommendation": str(e),
                    "signs_pro": [], "signs_con": [], "breakdown": {},
                }
        # else: ПАВ НЕ вычисляется (требование PLAN A)

    meta = settings.get_metadata()
    days_count = int((d_to - d_from).days) + 1 if (d_from and d_to) else n_points

    return {
        "ok": True,
        "_v": "segment_v1",
        "kind_context": "customer_data",
        "well_number": str(well),
        "date_from": d_from.isoformat(),
        "date_to":   d_to.isoformat(),
        "days_count": days_count,
        "n_points":  n_points,

        # ── CORE (PLAN A) ─────────────────────────────────────────
        "chart_data":        chart_data,
        "segments_extended": segments_extended,
        "cp_marks":          cp_marks,
        "shutdown_clusters": shutdown_clusters,
        "dual_summary":      dual_summary,
        "descriptions":      primary.get("descriptions") or [],
        "cp_descriptions":   primary.get("cp_descriptions") or [],

        # ── Полный dual для совместимости (модалка/legacy) ───────
        "dual": dual,

        # ── Optional PAV layer ───────────────────────────────────
        "include_pav_recommendation": bool(include_pav),
        "pav": pav,

        # ── Сервисное ────────────────────────────────────────────
        "thresholds_used": {
            "has_overrides": meta.get("has_overrides", False),
            "updated_at":    meta.get("updated_at"),
        },
        "computed_at": _dt.utcnow().isoformat() + "Z",
    }


def build_segment_snapshot(
    db: Session,
    well: str,
    d_from: date,
    d_to: date,
) -> dict[str, Any]:
    """Alias для compute_segment_block — используется при сохранении блока.

    Отделён для семантической ясности: при превью вызывают
    compute_segment_block (явное чтение «посчитай»), при создании
    блока — build_segment_snapshot (явное «построй snapshot для БД»).
    Реализация одинакова — snapshot готовится один раз и кладётся
    в customer_report_block.data_snapshot.
    """
    return compute_segment_block(db, well, d_from, d_to)
