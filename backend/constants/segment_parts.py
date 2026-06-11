"""segment_analysis parts contract — shared между PDF formatter и self-check.

Фиксирует соответствие HTML PART_LABELS.segment_analysis ↔ PDF render parts.

ИСТОЧНИК ПРАВДЫ для PART_LABELS — HTML
(`backend/templates/customer_daily.html`, объект `PART_LABELS.segment_analysis`).
Этот модуль НЕ дублирует HTML — он перечисляет, какие из этих ключей
рендерятся PDF (включая PAV-исключения и always-on dual_comparison).

Полная спецификация контракта: docs/contracts/segment_snapshot_contract.md §5.

ВАЖНО: любое добавление нового checkbox-ключа должно одновременно:
  1) появиться в HTML PART_LABELS.segment_analysis;
  2) появиться в SEGMENT_ANALYSIS_RENDER_PARTS;
  3) обрабатываться в `_format_segment_block`
     (backend/services/adaptation_report_service.py) и LaTeX-подсекции
     `adaptation_report.tex`.
"""

# Чекбоксы, которые ВЫВОДЯТ соответствующую секцию в PDF.
# Порядок не имеет смыслового значения, но соответствует визуальной
# последовательности рендера в HTML и LaTeX.
SEGMENT_ANALYSIS_RENDER_PARTS: tuple[str, ...] = (
    "prefix_note",
    "q_segment_chart",
    "segments_table",
    "descriptions",
    "cp_descriptions",
    "description",
    "suffix_note",
)

# Чекбоксы, присутствующие в HTML, но НАМЕРЕННО не рендерящиеся в PDF.
# Причина: scope PB-V2 запрещает PAV-секции (карточка/балл/обоснование) в PDF.
# Поля snapshot `pav` / `include_pav_recommendation` могут существовать —
# но рендерятся только в HTML.
SEGMENT_ANALYSIS_PDF_EXCLUDED_PARTS: tuple[str, ...] = (
    "pav_card",
    "signs_table",
)

# Разделы, которые НЕ управляются чекбоксом и рендерятся ВСЕГДА
# (при наличии источника данных). Не входят в SEGMENT_ANALYSIS_RENDER_PARTS.
# В HTML — рендерится автоматически; в PDF — безусловно.
SEGMENT_ANALYSIS_ALWAYS_ON_PARTS: tuple[str, ...] = (
    "dual_comparison",
)

# ── Schema-version поддержка ─────────────────────────────────────────
# Текущая поддерживаемая версия schema. Snapshot без `schema_version`
# трактуется как v=1 (legacy compatibility). Версия > SUPPORTED не падает
# — рендерится best-effort с log.warning (см. _format_segment_block).
SEGMENT_SNAPSHOT_SCHEMA_VERSION_SUPPORTED: int = 1
