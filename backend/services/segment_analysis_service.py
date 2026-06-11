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


# ─── R1: Phase R1 — Promote Q_working changepoints to segment boundaries ─
#
# Управляющий флаг (default OFF, безопасно для legacy):
#   • False (default): поведение PB-V2 без изменений — segments_extended
#     строятся ТОЛЬКО на primary.changepoints (Q общий).
#   • True: only_working changepoints, которые НЕ совпадают с primary
#     (по dual.only_working) и не лежат внутри shutdown_cluster, ПРОМОУТЯТСЯ
#     до границ сегментов; near-confirmed (working CP в окне merge_close
#     от primary) — primary CP помечается как confirmed, не дублируется.
#
# Параметр не входит в SEGMENT_THRESHOLDS (он не «порог», а флаг режима
# fusion), передаётся через compute_segment_block(..., promote_only_working=...).
PROMOTE_ONLY_WORKING_DEFAULT: bool = False


def _extract_cps_idx(changepoints: list) -> list[tuple[int, str]]:
    """Из списка `[{date: 'DD.MM.YYYY', idx: int}, ...]` → `[(idx, date), ...]`,
    отсортированно по idx. Дропает записи без idx."""
    out: list[tuple[int, str]] = []
    for cp in changepoints or []:
        idx = cp.get("idx")
        if idx is None:
            continue
        out.append((int(idx), str(cp.get("date") or "")))
    out.sort(key=lambda t: t[0])
    return out


def _compute_final_boundaries(
    dual: dict,
    df,
    *,
    promote_only_working: bool,
    merge_close_days: int = 5,
) -> tuple[list[int], set[int], dict[int, str]]:
    """R1 — fusion слой границ сегментов.

    Возвращает tuple:
      • final_idx          — sorted list[int] финальных boundary-индексов
                              (без 0 и без n).
      • promoted_idx_set   — set индексов, добавленных из only_working
                              (используется для cp_marks tagging).
      • near_confirmed_map — {primary_idx: working_date_dd_mm_yyyy} —
                              primary CPs, рядом с которыми оказался
                              working CP (становятся `confirmed` в cp_marks).

    Поведение:
      • Если promote_only_working=False → возвращаем только primary
        boundaries (legacy-режим), promoted/near = {}.
      • Если True:
          1) Берём primary changepoints.
          2) Из working changepoints отбираем те, что:
             — не входят в primary (по близости |Δidx| ≤ merge_close_days),
             — не лежат внутри shutdown_cluster,
             — не находятся в edge-зоне (расстояние до краёв ≥ edge_margin).
          3) При близости primary↔working — оставляем primary, фиксируем
             pair в near_confirmed_map.
          4) Дедуп между промоутами тоже по merge_close.
    """
    import numpy as np

    primary = (dual or {}).get("primary") or {}
    working = (dual or {}).get("working") or {}

    primary_pairs = _extract_cps_idx(primary.get("changepoints") or [])
    working_pairs = _extract_cps_idx(working.get("changepoints") or [])

    primary_idx_list = [i for (i, _) in primary_pairs]
    primary_idx_set = set(primary_idx_list)

    if not promote_only_working:
        return (sorted(primary_idx_set), set(), {})

    # Найдём shutdown_clusters для фильтрации
    d = df.sort_values("date").reset_index(drop=True)
    q_total = d["q_gas_total"].to_numpy(dtype=float)
    shutdown = (
        d["shutdown_min"].to_numpy(dtype=float)
        if "shutdown_min" in d.columns else np.zeros_like(q_total)
    )
    clusters = sam._find_shutdown_clusters(
        shutdown, q_total,
        min_days=2, shutdown_threshold_min=600.0,
        q_drop_threshold_pct=50.0,
    )

    n = len(d)
    edge_margin = int(sam._segth("edge_margin_days"))

    def _in_cluster(idx: int) -> bool:
        return any(start < idx < end for (start, end) in clusters)

    def _in_edge(idx: int) -> bool:
        return idx < edge_margin or idx > n - edge_margin

    near_confirmed_map: dict[int, str] = {}
    promoted_idx_set: set[int] = set()

    # Перебираем working CPs; для каждого решаем — near-confirmed / promote / drop
    for w_idx, w_date in working_pairs:
        if _in_cluster(w_idx) or _in_edge(w_idx):
            continue
        # Ближайший primary CP
        if primary_idx_list:
            closest_p = min(primary_idx_list, key=lambda p: abs(p - w_idx))
            if abs(closest_p - w_idx) <= merge_close_days:
                # near-confirmed → не дублируем, помечаем существующий primary
                near_confirmed_map[closest_p] = w_date
                continue
        # Дедуп между промоутами
        if promoted_idx_set:
            closest_prom = min(promoted_idx_set, key=lambda p: abs(p - w_idx))
            if abs(closest_prom - w_idx) <= merge_close_days:
                continue
        promoted_idx_set.add(w_idx)

    final_idx = sorted(primary_idx_set | promoted_idx_set)
    return (final_idx, promoted_idx_set, near_confirmed_map)


def _build_segments_extended(
    strip_segments: list,
    df,
    *,
    override_cps_idx: list[int] | None = None,
) -> list[dict]:
    """Объединение strip-сегментов с extended-полями (slope_total/intercept_total/
    slope_working/intercept_working/start_idx/end_idx).

    strip — это `_segment_analysis(df)['segments']` (с is_workover/gradual/
    preshutdown_*). Extended — `_segment_trends_extended(...)` (со slopes
    и start_idx/end_idx).

    Берём strip как базу (там уже все post-process поля), добавляем поля
    из extended: start_idx, end_idx, slope_total, intercept_total,
    slope_working, slope_dp. Пересчитываем intercept_working локально
    через _linreg_full (модуль теряет его в _segment_trends_extended).

    R1: если передан `override_cps_idx` — extended-сегменты строятся по
    этому списку boundaries (а НЕ через повторный детектор). Strip-сегменты
    при этом используются только как источник post-process метаданных
    (cause/gradual/preshutdown_verdict) для тех extended-сегментов, чьи
    start-даты совпадают со старыми primary-сегментами. Новые подсегменты
    (созданные промоутом) останутся без causes/preshutdown — это
    приемлемо по контракту (поля optional).
    """
    import numpy as np
    if not strip_segments and override_cps_idx is None:
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

    # R1: если задан override_cps_idx — используем его (post-process fusion);
    # иначе — запускаем детектор повторно (legacy путь).
    if override_cps_idx is not None:
        cps_idx = sorted({int(i) for i in override_cps_idx if 0 < int(i) < len(q_total)})
    else:
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

    # Сопоставление extended ↔ strip по start_date (DD.MM.YYYY).
    # При R1-promote часть extended-сегментов НЕ имеет соответствия в strip
    # (они появились после промоута only_working CP) — для таких post-process
    # поля (cause/preshutdown_verdict/gradual/is_workover) остаются None,
    # это соответствует контракту (поля optional).
    strip_by_start = {s.get("start"): s for s in (strip_segments or [])}

    out: list[dict] = []
    prev_seg_strip = None
    for i, ext in enumerate(extended):
        start_dm = pd.Timestamp(ext["start_date"]).strftime("%d.%m.%Y")
        s_strip = strip_by_start.get(start_dm)

        if s_strip is not None:
            # Перенесём только post-process метаданные strip (cause,
            # preshutdown_*, gradual_*, is_workover, is_shutdown_cluster).
            # ВСЁ ОСТАЛЬНОЕ (start/end/days/mean_q/change_*/…) перезаписывается
            # ниже из extended, потому что при override_cps_idx boundaries
            # сегмента в strip могут не совпадать с новыми extended.
            merged = {
                "is_workover":           bool(s_strip.get("is_workover")),
                "is_shutdown_cluster":   bool(s_strip.get("is_shutdown_cluster")),
                "gradual_trend":         s_strip.get("gradual_trend"),
                "gradual_drift_pct":     s_strip.get("gradual_drift_pct"),
                "preshutdown_q":         s_strip.get("preshutdown_q"),
                "preshutdown_delta_pct": s_strip.get("preshutdown_delta_pct"),
                "preshutdown_verdict":   s_strip.get("preshutdown_verdict"),
                "cause":                 s_strip.get("cause"),
            }
        else:
            # Neutral strip-stub для нового сегмента (promoted by R1).
            merged = {
                "is_workover":           False,
                "is_shutdown_cluster":   False,
                "gradual_trend":         None,
                "gradual_drift_pct":     None,
                "preshutdown_q":         None,
                "preshutdown_delta_pct": None,
                "preshutdown_verdict":   None,
                "cause":                 None,
            }

        # Boundary fields — ВСЕГДА из extended (источник правды).
        merged["start"] = start_dm
        merged["end"]   = pd.Timestamp(ext["end_date"]).strftime("%d.%m.%Y")
        merged["days"]  = int(ext.get("days") or 0)
        merged["start_idx"] = int(ext.get("start_idx"))
        merged["end_idx"]   = int(ext.get("end_idx"))
        merged["slope_total"]     = ext.get("slope")
        merged["intercept_total"] = ext.get("intercept")
        merged["slope_working"]   = ext.get("slope_working")
        merged["slope_dp"]        = ext.get("slope_dp")
        # intercept_working — модуль не сохраняет; пересчитываем
        si = int(ext["start_idx"]); ei = int(ext["end_idx"])
        seg_qw = q_working[si:ei]
        x_days = np.arange(ei - si, dtype=float)
        slope_w, intercept_w = sam._linreg_full(x_days, seg_qw)
        merged["intercept_working"] = float(intercept_w) \
            if np.isfinite(intercept_w) else None

        # Перекрываем числовые поля значениями extended (extended авторитетнее
        # по boundaries при override_cps_idx). _segment_trends_extended
        # называет общий дебит `mean_q_total`, в snapshot контракте — `mean_q`,
        # поэтому делаем явный mapping.
        if "mean_q_total" in ext:
            merged["mean_q"] = ext.get("mean_q_total")
        elif "mean_q" in ext:
            merged["mean_q"] = ext.get("mean_q")
        for ext_field in ("mean_q_working", "mean_dp",
                          "mean_shutdown", "working_pct",
                          "mean_p_wellhead", "mean_p_flowline", "mean_choke_mm",
                          "change_pct", "change_q_working_pct",
                          "change_dp_pct", "change_shutdown", "change_choke"):
            if ext_field in ext:
                merged[ext_field] = ext.get(ext_field)
        # numbering — пересоберём по новой нумерации (1-based)
        merged["num"] = i + 1

        # ISO даты
        merged["start_date"] = _ddmmyyyy_to_iso(merged.get("start"))
        merged["end_date"]   = _ddmmyyyy_to_iso(merged.get("end"))

        # Цвет: кластер простоев → красный; остальные — палитра
        if merged.get("is_shutdown_cluster"):
            merged["color"] = SHUTDOWN_CLUSTER_COLOR
        else:
            merged["color"] = SEGMENT_COLORS[i % len(SEGMENT_COLORS)]

        # Тип режима по §6.1 — относительно предыдущего strip-сегмента
        # (для promoted-сегментов prev может быть None — это OK).
        merged["type"] = _classify_segment_type(merged, prev_seg_strip)

        out.append(merged)
        # prev_seg_strip — для классификации следующего; используем merged
        # (он несёт all необходимые поля независимо от того, был ли strip-match).
        prev_seg_strip = merged

    return out


_PROMOTED_TAG_SUFFIX = " (только в Q рабочем — фактический сдвиг режима)"


def _build_cp_marks(
    strip_changepoints: list,
    ext_segments: list,
    dual: dict,
    *,
    promoted_idx_set: set[int] | None = None,
    near_confirmed_map: dict[int, str] | None = None,
) -> list[dict]:
    """Точки перелома + классификация (confirmed/only_total/only_working)
    + короткий тег причины (§6.3).

    Используются:
      • _changepoint_short_tag(curr, prev) из модуля — для tag
      • dual.common_dates / only_total / only_working — для source

    R1:
      • promoted_idx_set — индексы, продвинутые из only_working до boundaries:
        для них синтезируется отдельная запись cp_marks (source="only_working"),
        даже если такой даты не было в strip_changepoints. Tag получает
        пояснительный суффикс «только в Q рабочем — фактический сдвиг режима».
      • near_confirmed_map — {primary_idx: working_date_dd_mm_yyyy}:
        primary CP, рядом с которым нашёлся working CP, переклассифицируется
        на source="confirmed" (даже если dual не отметил его в common_dates).
    """
    promoted_idx_set = set(promoted_idx_set or [])
    near_confirmed_map = dict(near_confirmed_map or {})

    # dual.* — даты в формате DD.MM.YYYY
    common_set = set((dual or {}).get("common_dates") or [])
    only_total_set = set((dual or {}).get("only_total") or [])
    only_working_set = set((dual or {}).get("only_working") or [])

    out: list[dict] = []
    seen_idx: set[int] = set()

    # ── 1. Существующие primary changepoints (как было) ─────────────
    for i, cp in enumerate(strip_changepoints or []):
        cp_date_dm = cp.get("date")
        cp_idx    = cp.get("idx")
        # Найти сегмент curr (начинается в cp_idx) и prev (заканчивается в cp_idx)
        curr_seg = None
        prev_seg = None
        for s in ext_segments:
            if s.get("start_idx") == cp_idx:
                curr_seg = s
            if s.get("end_idx") == cp_idx:
                prev_seg = s
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

        # Source classification
        if cp_idx is not None and int(cp_idx) in near_confirmed_map:
            # R1 near-confirmed: primary CP подтверждён близким working CP.
            source = "confirmed"
        elif cp_date_dm in common_set:
            source = "confirmed"
        elif cp_date_dm in only_total_set:
            source = "only_total"
        elif cp_date_dm in only_working_set:
            source = "only_working"
        else:
            source = "only_total"

        if cp_idx is not None:
            seen_idx.add(int(cp_idx))

        out.append({
            "idx":    int(cp_idx) if cp_idx is not None else None,
            "date":   _ddmmyyyy_to_iso(cp_date_dm),
            "tag":    tag,
            "source": source,
        })

    # ── 2. R1: promoted only_working boundaries ─────────────────────
    # Для каждого promoted_idx — синтезируем запись cp_marks. Сегменты
    # curr/prev определяются через ext_segments по start_idx/end_idx.
    for p_idx in sorted(promoted_idx_set):
        if p_idx in seen_idx:
            continue
        curr_seg = None
        prev_seg = None
        for s in ext_segments:
            if s.get("start_idx") == p_idx:
                curr_seg = s
            if s.get("end_idx") == p_idx:
                prev_seg = s
        if curr_seg is None or prev_seg is None:
            # Не нашли соответствие в extended — пропускаем (граница
            # отфильтрована, например из-за shutdown_cluster).
            continue
        try:
            tag_core = sam._changepoint_short_tag(curr_seg, prev_seg) or ""
        except Exception:
            tag_core = ""
        # Дата = start_date нового сегмента, формат DD.MM.YYYY → ISO
        cp_date_dm = pd.Timestamp(curr_seg["start_date"]).strftime("%d.%m.%Y")
        tag_full = (tag_core + _PROMOTED_TAG_SUFFIX).strip()
        out.append({
            "idx":    int(p_idx),
            "date":   _ddmmyyyy_to_iso(cp_date_dm),
            "tag":    tag_full,
            "source": "only_working",
        })
        seen_idx.add(p_idx)

    # Финальная сортировка по idx (если есть), иначе по date
    out.sort(key=lambda m: (m.get("idx") is None, m.get("idx") or 0, m.get("date") or ""))
    return out


# ───────────────────────────────────────────────────────────────────────
#  FIX-A + FIX-C: достроить descriptions/cp_descriptions для R1-promoted
#  extended-сегментов и only_working CPs, которые отсутствуют в strip.
#
#  Принципы:
#   • Используем существующие формулировки и существующие поля snapshot.
#   • НЕ создаём новый interpretation engine.
#   • Текст для only_working — осторожный, с фразой «требуется сверка
#     с журналом технологических операций».
#   • Запрещены пластовые формулировки (приток / продуктивность / истощение).
# ───────────────────────────────────────────────────────────────────────

def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    if not (x == x):  # NaN
        return "—"
    sign = "+" if x > 0 else ""
    return f"{sign}{x:.1f}%"


def _fmt_num1(v) -> str:
    if v is None:
        return "—"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    if not (x == x):
        return "—"
    return f"{x:.1f}"


def _augment_descriptions_for_r1(
    primary_descriptions: list,
    primary_cp_descriptions: list,
    segments_extended: list,
    cp_marks: list,
    strip_segments: list,
    promoted_idx_set: set | None,
) -> tuple[list, list]:
    """FIX-A + FIX-C — синхронизировать descriptions/cp_descriptions с
    segments_extended/cp_marks после R1 promotion.

    Возвращает (descriptions_full, cp_descriptions_full).

    Логика:
      • Сначала копируем primary descriptions/cp_descriptions как есть
        (не трогаем работающие формулировки strip-pipeline — FIX-B/AC5).
      • Для extended-сегментов, чья start-дата отсутствует в strip
        (т.е. R1-promoted), синтезируем строку description через шаблон.
      • Для cp_marks, чья дата отсутствует в strip changepoints
        (т.е. only_working promoted), синтезируем строку cp_description.
    """
    primary_descriptions = list(primary_descriptions or [])
    primary_cp_descriptions = list(primary_cp_descriptions or [])
    promoted_idx_set = set(promoted_idx_set or [])

    strip_start_dates = {s.get("start") for s in (strip_segments or [])}

    # ─── FIX-A: descriptions для R1-promoted ext-сегментов ────────────
    # Для определения «куда вставить» строим новый список, идущий по
    # порядку segments_extended; primary descriptions переносим по
    # совпадению start-даты.
    primary_desc_by_start: dict[str, str] = {}
    for s, d in zip(strip_segments or [], primary_descriptions):
        if isinstance(s, dict) and s.get("start"):
            primary_desc_by_start[s["start"]] = d

    descriptions_full: list[str] = []
    prev_ext = None
    for ext in segments_extended:
        start_dm = ext.get("start")
        existing = primary_desc_by_start.get(start_dm)
        if existing:
            descriptions_full.append(existing)
        else:
            # R1-promoted сегмент — синтезируем осторожную строку.
            descriptions_full.append(_synthesize_promoted_description(ext, prev_ext))
        prev_ext = ext

    # ─── FIX-C: cp_descriptions для only_working promoted CPs ─────────
    # Сохраняем все primary cp_descriptions «как есть» (порядок и состав).
    # Для каждого cp_mark с source='only_working' и idx ∈ promoted_idx_set —
    # добавляем синтезированную осторожную строку в конец.
    cp_descriptions_full: list[str] = list(primary_cp_descriptions)
    for cp in cp_marks:
        idx = cp.get("idx")
        src = cp.get("source")
        if src != "only_working":
            continue
        if idx is None or int(idx) not in promoted_idx_set:
            continue
        # Найдём ext-сегмент, начинающийся в этом idx
        curr_ext = next(
            (e for e in segments_extended if e.get("start_idx") == int(idx)),
            None,
        )
        prev_ext = next(
            (e for e in segments_extended if e.get("end_idx") == int(idx)),
            None,
        )
        cp_descriptions_full.append(
            _synthesize_only_working_cp_description(cp, curr_ext, prev_ext)
        )
    return descriptions_full, cp_descriptions_full


def _synthesize_promoted_description(ext: dict, prev_ext: dict | None) -> str:
    """FIX-A: осторожный текст для R1-promoted ext-сегмента.

    Использует только числовые поля snapshot. Не вызывает analytics.
    Содержит фразу «требуется сверка с журналом технологических операций»
    для соответствия diagnostic interpretation policy.
    """
    start = ext.get("start", "—")
    end = ext.get("end", "—")
    days = ext.get("days", "—")
    q_t = _fmt_num1(ext.get("mean_q"))
    q_w = _fmt_num1(ext.get("mean_q_working"))
    dp  = _fmt_num1(ext.get("mean_dp"))
    sd  = _fmt_num1(ext.get("mean_shutdown"))

    lines: list[str] = []
    lines.append(
        f"**{start}–{end}** ({days} дн.): Фактический сдвиг режима эксплуатации, "
        f"выявленный по Q рабочему. "
        f"Q общий={q_t}, Q рабочий={q_w} тыс.м³/сут, ΔP={dp} кгс/см², "
        f"простой ≈{sd} мин/сут."
    )
    # Изменения относительно предыдущего ext-сегмента (если есть)
    if prev_ext is not None:
        chg_q  = ext.get("change_pct")
        chg_qw = ext.get("change_q_working_pct")
        chg_dp = ext.get("change_dp_pct")
        chg_sd = ext.get("change_shutdown")
        change_parts: list[str] = []
        if chg_q is not None:
            change_parts.append(f"Q общий {_fmt_pct(chg_q)}")
        if chg_qw is not None:
            change_parts.append(f"Q рабочий {_fmt_pct(chg_qw)}")
        if chg_dp is not None:
            change_parts.append(f"ΔP {_fmt_pct(chg_dp)}")
        if chg_sd is not None and abs(float(chg_sd)) >= 10:
            sign = "+" if float(chg_sd) > 0 else ""
            change_parts.append(f"простой {sign}{float(chg_sd):.0f} мин/сут")
        if change_parts:
            lines.append(
                "Изменение vs предыдущего сегмента: " + ", ".join(change_parts) + "."
            )
    lines.append(
        "Событие не полностью выражено в Q общем. Требуется сверка с журналом "
        "технологических операций (изменение штуцера, простои, продувки, "
        "оперативные переключения режима работы скважины)."
    )
    return " ".join(lines)


def _synthesize_only_working_cp_description(
    cp: dict, curr_ext: dict | None, prev_ext: dict | None,
) -> str:
    """FIX-C: осторожный текст для only_working CP, продвинутого R1.

    Совпадает по тону с _synthesize_promoted_description. Использует только
    данные cp_marks/segments_extended. Не вызывает analytics.
    """
    cp_date_iso = cp.get("date") or "—"
    # Преобразуем ISO → DD.MM.YYYY для консистентности с primary cp_descriptions.
    try:
        from datetime import datetime as _dt
        cp_date = _dt.fromisoformat(str(cp_date_iso)[:10]).strftime("%d.%m.%Y")
    except Exception:
        cp_date = cp_date_iso

    prev_q  = _fmt_num1(prev_ext.get("mean_q")) if prev_ext else "—"
    curr_q  = _fmt_num1(curr_ext.get("mean_q")) if curr_ext else "—"
    prev_qw = _fmt_num1(prev_ext.get("mean_q_working")) if prev_ext else "—"
    curr_qw = _fmt_num1(curr_ext.get("mean_q_working")) if curr_ext else "—"
    chg_q   = curr_ext.get("change_pct") if curr_ext else None
    chg_qw  = curr_ext.get("change_q_working_pct") if curr_ext else None

    parts: list[str] = []
    parts.append(
        f"↘ **{cp_date}**: Фактический сдвиг режима эксплуатации, "
        f"выявленный по Q рабочему."
    )
    parts.append(
        f"Q общий: {prev_q} → {curr_q} тыс.м³/сут ({_fmt_pct(chg_q)}). "
        f"Q рабочий: {prev_qw} → {curr_qw} тыс.м³/сут ({_fmt_pct(chg_qw)})."
    )
    parts.append(
        "Событие не полностью выражено в Q общем. Для уточнения причины "
        "требуется сверка с журналом технологических операций, изменением "
        "штуцера, простоями, продувками и оперативными переключениями "
        "режима работы скважины."
    )
    return " ".join(parts)


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
    promote_only_working: bool | None = None,
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

        # R1: fusion слой — определяем эффективное значение promote.
        # promote_only_working=None → используется PROMOTE_ONLY_WORKING_DEFAULT
        # (по умолчанию False = legacy behavior).
        _promote = (
            PROMOTE_ONLY_WORKING_DEFAULT
            if promote_only_working is None
            else bool(promote_only_working)
        )
        try:
            final_idx, promoted_idx_set, near_confirmed_map = (
                _compute_final_boundaries(
                    dual, df,
                    promote_only_working=_promote,
                )
            )
        except Exception:
            log.exception("_compute_final_boundaries failed for well=%s", well)
            final_idx, promoted_idx_set, near_confirmed_map = (
                [cp.get("idx") for cp in strip_cps if cp.get("idx") is not None],
                set(),
                {},
            )

        try:
            segments_extended = _build_segments_extended(
                strip_segments, df,
                override_cps_idx=final_idx if _promote else None,
            )
        except Exception:
            log.exception("_build_segments_extended failed for well=%s", well)
            segments_extended = []

        try:
            cp_marks = _build_cp_marks(
                strip_cps, segments_extended, dual,
                promoted_idx_set=promoted_idx_set if _promote else None,
                near_confirmed_map=near_confirmed_map if _promote else None,
            )
        except Exception:
            log.exception("_build_cp_marks failed for well=%s", well)
            cp_marks = []

        try:
            shutdown_clusters = _build_shutdown_clusters(df)
        except Exception:
            log.exception("_build_shutdown_clusters failed for well=%s", well)
            shutdown_clusters = []

        # FIX-A + FIX-C: достроить descriptions/cp_descriptions для R1-promoted
        # сегментов и only_working CPs (которых нет в strip-pipeline).
        # При promote_only_working=False примarie совпадают с segments_extended
        # 1-в-1 и augment ничего не добавляет — legacy поведение сохраняется.
        try:
            descriptions_full, cp_descriptions_full = _augment_descriptions_for_r1(
                primary_descriptions   = primary.get("descriptions") or [],
                primary_cp_descriptions = primary.get("cp_descriptions") or [],
                segments_extended      = segments_extended,
                cp_marks               = cp_marks,
                strip_segments         = strip_segments,
                promoted_idx_set       = promoted_idx_set if _promote else set(),
            )
        except Exception:
            log.exception("_augment_descriptions_for_r1 failed for well=%s", well)
            descriptions_full    = list(primary.get("descriptions") or [])
            cp_descriptions_full = list(primary.get("cp_descriptions") or [])

        dual_summary = {
            "primary_cp_count":   len(strip_cps),
            "working_cp_count":   len((dual.get("working") or {}).get("changepoints", [])
                                      if dual.get("working") else []),
            "common_count":       len(dual.get("common_dates") or []),
            "common_dates":       list(dual.get("common_dates") or []),
            "only_total_dates":   list(dual.get("only_total") or []),
            "only_working_dates": list(dual.get("only_working") or []),
            # R1 trace (новые optional поля; legacy clients их игнорируют)
            "r1_promote_enabled":  bool(_promote),
            "r1_promoted_count":   int(len(promoted_idx_set or set())),
            "r1_near_confirmed_count": int(len(near_confirmed_map or {})),
            "r1_final_boundary_count": int(len(final_idx or [])),
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
        "descriptions":      descriptions_full,
        "cp_descriptions":   cp_descriptions_full,

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
