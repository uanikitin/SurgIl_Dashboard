"""Каталог parts (видимость частей блока в отчёте) для глав observation_*.

SINGLE SOURCE OF TRUTH для набора управляемых частей блоков «Наблюдение».

Используется:
  * observation_chapter_renderer — гейтинг секций HTML и LaTeX;
  * routers/observation.py — валидация PUT params.parts (whitelist ключей);
  * (Фаза O2) static/js/observation_ui.js — чекбоксы «Что включить в PDF»
    (значения зеркалируются вручную; при изменении этого файла —
    синхронизировать JS).

Семантика:
  * part == True или ОТСУТСТВУЕТ в params.parts → секция видима (default-on);
  * part == False → секция скрыта в HTML и PDF.

Это поведение делает все ранее сохранённые блоки (без ключа `parts`)
обратно совместимыми — они рендерятся полностью, как раньше.

ВАЖНО: набор ключей зафиксирован ТЗ Фазы O1. Любое добавление нового
ключа должно одновременно появиться:
  1) в OBSERVATION_PARTS ниже;
  2) в гейтинге соответствующего render_* в observation_chapter_renderer.py;
  3) в чекбоксах observation_ui.js (Фаза O2).

Части, НЕ входящие в каталог (рендерятся всегда, чекбоксом не управляются):
  * h3-заголовок блока;
  * baseline: график метрик (ТЗ не выделяет его в parts);
  * segment: shutdown_clusters (кластеры простоев).
  * comment (аналитический комментарий оператора) — отдельное поле блока,
    не часть parts; рендерится всегда при непустом значении.
"""
from __future__ import annotations

OBSERVATION_PARTS: dict[str, list[str]] = {
    # observation_analysis — блок «Анализ наблюдения» из Step 3 wizard'а.
    # Содержит 4 отдельных графика из wz3RenderCharts.
    "observation_analysis": [
        "prefix_note",
        "intro",
        "metrics_table",
        "chart_pressures",         # wz3-ch-pres: P трубное + P линейное
        "chart_dp",                # wz3-ch-dp: Перепад ΔP
        "chart_q",                 # wz3-ch-flow: Дебит Q(t)
        "chart_utilization",       # wz3-ch-down: Учёт рабочего времени по дням
        "quality",
        "flags",
        "description",
        "suffix_note",
    ],
    "observation_baseline": [
        "prefix_note",
        "intro",
        "metrics_table",
        "chart_metrics",           # график метрик baseline
        "quality",
        "flags",
        "description",
        "suffix_note",
    ],
    "observation_period": [
        "prefix_note",
        "intro",
        "metrics_table",
        "chart_timeseries",        # Q + ΔP time series
        "chart_compare_b1",        # bar chart comparison vs B1
        "comparison_with_b1",      # текстовое/табличное сравнение
        "comparison_with_customer",
        "diagnostics",
        "daily_table",
        "flags",
        "description",
        "suffix_note",
    ],
    "observation_segment": [
        "prefix_note",
        "intro",
        "chart_segments",          # график сегментов
        "segments_table",
        "changepoints",
        "diagnostics",
        "flags",
        "description",
        "suffix_note",
    ],
    # segment_analysis — блок из customer_daily SegmentBlocksWidget,
    # интегрированный в главу «Наблюдение» (схема segment_analysis_v1).
    "segment_analysis": [
        "intro",
        "chart",                   # Plotly-график сегментов
        "segments_table",
        "changepoints",
        "description",
    ],
    # segment_comparison — сравнение сегментов из customer_daily.
    "segment_comparison": [
        "intro",
        "chart",
        "diff_table",
        "description",
    ],
}

# Человекочитаемые подписи для UI (Фаза O2 — чекбоксы конструктора блоков).
OBSERVATION_PART_LABELS: dict[str, str] = {
    "prefix_note":              "Вступительный текст",
    "intro":                    "Период / заголовок",
    "metrics_table":            "Таблица метрик",
    # observation_analysis — 4 графика из wz3RenderCharts
    "chart_pressures":          "📈 График давлений (P трубное, P линейное)",
    "chart_dp":                 "📈 График ΔP",
    "chart_q":                  "📈 График Q(t)",
    "chart_utilization":        "📊 Рабочее время по дням",
    # observation_baseline
    "chart_metrics":            "📈 График метрик (baseline)",
    # observation_period
    "chart_timeseries":         "📈 График Q + ΔP (временной ряд)",
    "chart_compare_b1":         "📊 График сравнения с B1",
    # observation_segment
    "chart_segments":           "📈 График сегментов",
    # Остальные части
    "quality":                  "Качество данных",
    "comparison_with_b1":       "Сравнение с baseline (B1)",
    "comparison_with_customer": "Сравнение с заказчиком",
    "diagnostics":              "Диагностика",
    "daily_table":              "Суточная таблица",
    "segments_table":           "Таблица сегментов",
    "changepoints":             "Точки перелома",
    "flags":                    "Флаги",
    "description":              "Текстовое описание",
    "suffix_note":              "Заключительный текст",
}


def part_visible(parts: dict | None, key: str) -> bool:
    """Возвращает True, если часть `key` должна рендериться.

    Часть скрыта ТОЛЬКО при явном `parts[key] is False`. Отсутствие ключа
    или нечисловой/непереданный `parts` → видима (default-on, backward-compat).
    """
    if not isinstance(parts, dict):
        return True
    return parts.get(key, True) is not False
