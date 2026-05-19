"""Сервис для работы с суточными сводками заказчика (УзКорГаз).

Источник данных:
    Excel-файл «Суточная сводка <МЕСЯЦ ГОД>г.xlsx», парсится модулем
    `backend/utils/parsing_day_report_UZKOR.py`.

Хранилище:
    Таблица `well_daily` (PRIMARY KEY (date, ggu, well)).

Функциональность:
    * `parse_xlsx`            — парсинг файла → DataFrame.
    * `find_duplicates`       — определить, какие из (date, ggu, well) уже есть в БД.
    * `upsert_records`        — INSERT … ON CONFLICT DO UPDATE / DO NOTHING.
    * `load_for_well`         — выборка ряда по скважине за период.
    * `get_wells`             — список доступных скважин.
    * `get_dataset_meta`      — общая статистика хранилища.
    * `monthly_stats`         — помесячная агрегация по скважине.
    * `monthly_description`   — текстовое описание помесячной динамики.
    * `describe_well_period`  — описательная статистика за период.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.utils.parsing_day_report_UZKOR import (
    COLUMN_ORDER,
    parse_workbook,
)

log = logging.getLogger(__name__)


# ─────────────────── Lazy fallback: создание таблицы ───────────────────
# Идеально — применить alembic-миграцию a8b9c0d1e2f3. Но если по каким-то
# причинам её не запустили (или висит другая миграция в цепочке), создадим
# таблицу при первом обращении из кода. Безопасно: IF NOT EXISTS.

_TABLE_INITIALIZED: bool = False

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS well_daily (
        date           DATE         NOT NULL,
        ggu            VARCHAR(16)  NOT NULL,
        well           VARCHAR(32)  NOT NULL,
        choke_mm       DOUBLE PRECISION,
        p_wellhead     DOUBLE PRECISION,
        p_annular      DOUBLE PRECISION,
        annular_packer BOOLEAN      NOT NULL DEFAULT FALSE,
        p_flowline     DOUBLE PRECISION,
        q_gas_total    DOUBLE PRECISION,
        q_gas_working  DOUBLE PRECISION,
        shutdown_min   DOUBLE PRECISION,
        p_static       DOUBLE PRECISION,
        source_sheet   VARCHAR(128),
        source_file    VARCHAR(255),
        loaded_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (date, ggu, well)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_well_daily_well_date ON well_daily(well, date)",
    "CREATE INDEX IF NOT EXISTS ix_well_daily_ggu_date  ON well_daily(ggu, date)",
    "CREATE INDEX IF NOT EXISTS ix_well_daily_date      ON well_daily(date)",
]


def ensure_table(db: Session) -> None:
    """Гарантировать наличие well_daily в БД (idempotent, IF NOT EXISTS).

    Вызывается перед любой операцией с таблицей. Кешируется глобально:
    первый запрос делает SQL, последующие — no-op.
    """
    global _TABLE_INITIALIZED
    if _TABLE_INITIALIZED:
        return
    try:
        for ddl in _DDL:
            db.execute(text(ddl))
        db.commit()
        _TABLE_INITIALIZED = True
    except Exception:
        db.rollback()
        log.exception("ensure_table failed (well_daily)")
        raise


# ─── customer_report_block — блоки анализа для отчёта об адаптации ───
# 3 типа: 'baseline' | 'period_analysis' | 'comparison'.
# Хранит params (для воспроизводимого live-расчёта) и data_snapshot
# (для воспроизведения исторического отчёта).

_BLOCKS_INITIALIZED: bool = False
_BLOCKS_DDL = [
    """
    CREATE TABLE IF NOT EXISTS customer_report_block (
        id            SERIAL PRIMARY KEY,
        well_id       INTEGER NOT NULL REFERENCES wells(id),
        kind          VARCHAR(32) NOT NULL,
        title         VARCHAR(200) NOT NULL,
        params        JSONB NOT NULL DEFAULT '{}'::jsonb,
        data_snapshot JSONB,
        comment       TEXT,
        in_report     BOOLEAN NOT NULL DEFAULT TRUE,
        sort_order    INTEGER NOT NULL DEFAULT 0,
        created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_crb_well_inreport_sort
        ON customer_report_block(well_id, in_report, sort_order)
    """,
]


def ensure_blocks_table(db: Session) -> None:
    """Гарантировать наличие customer_report_block (IF NOT EXISTS)."""
    global _BLOCKS_INITIALIZED
    if _BLOCKS_INITIALIZED:
        return
    try:
        for ddl in _BLOCKS_DDL:
            db.execute(text(ddl))
        db.commit()
        _BLOCKS_INITIALIZED = True
    except Exception:
        db.rollback()
        log.exception("ensure_blocks_table failed")
        raise


VALID_BLOCK_KINDS = {
    # Главы заказчика (исторические)
    "baseline", "period_analysis", "comparison",
    # Главы адаптации (Шаг 5 wizard'a): анализ периода адаптации с метриками
    # против B1/B2, сохранённое окно R★ или сводка эффективности реагентов.
    "adaptation_period_analysis",
    "optimal_window",
    "reagent_irv_summary",
    # Сравнение участков из шага 5 (тот же инструмент cmp, но контекст
    # «Адаптация»; попадает в главу адаптации в PDF). Шаг 3 пишет в 'comparison'.
    "adaptation_comparison",
    # Анализ периода НАБЛЮДЕНИЯ из шага 3 wizard'а (Q/ΔP/КИВ + графики
    # на наших данных UniTool). Попадает в главу «Наблюдение» PDF.
    # Создаётся кнопкой «➕ Сохранить как блок наблюдения». Отдельно от
    # customer_baseline (source='observation') — baseline это «утверждённые
    # значения для сравнения», блок — «отчётный материал главы».
    "observation_analysis",
    # Роза критериев — диагностика отклонения текущего периода от собственной
    # истории скважины на текущем штуцере. Попадает в главу «Анализ исходных
    # данных» (заказчик). См. customer_rose_service.
    "criteria_rose",
    # Общий текст-вступление главы 2 (раздел 1 главы).
    # Автосоздаётся при первом заходе на скважину с дефолтным текстом
    # «Анализ исходных работ скважины №N на основании данных УзКорГаз».
    # params: { text: "..." }. Без графиков и snapshot.
    "chapter_intro",
    # Сегментный анализ Q (PLAN A — Core Segment Analysis).
    # Разбиение Q-кривой на интервалы по точкам перелома, реальные линейные
    # тренды per-segment, классификация cp (confirmed/only_total/only_working),
    # короткие теги причин (P шл ↑, ΔP ↓, …), таблица с типами режимов,
    # развёрнутые описания, сравнение Q общ ↔ Q раб.
    # snapshot формата segment_v1 (см. segment_analysis_service.compute_segment_block).
    # PDF-интеграция запланирована в PLAN B (сейчас тихо пропускается).
    "segment_analysis",
}


def list_blocks(db: Session, well_id: int) -> list[dict[str, Any]]:
    """Все блоки скважины, отсортированы по sort_order, потом по created_at."""
    rows = db.execute(text("""
        SELECT id, well_id, kind, title, params, data_snapshot, comment,
               in_report, sort_order, created_at, updated_at
        FROM customer_report_block
        WHERE well_id = :wid
        ORDER BY sort_order, created_at
    """), {"wid": well_id}).mappings().fetchall()
    return [dict(r) for r in rows]


def create_block(
    db: Session, *,
    well_id: int, kind: str, title: str,
    params: dict[str, Any] | None = None,
    comment: str | None = None,
    in_report: bool = True,
    sort_order: int | None = None,
    data_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if kind not in VALID_BLOCK_KINDS:
        raise ValueError(f"Invalid block kind: {kind}")
    params = dict(params or {})
    if sort_order is None:
        row = db.execute(text("""
            SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order
            FROM customer_report_block WHERE well_id = :wid
        """), {"wid": well_id}).fetchone()
        sort_order = int(row[0])

    # Для baseline-блока сразу зафиксируем метрики в customer_baseline
    # (Q, ΔP, простой, давления) — единый источник истины для отчёта.
    # Связь через params['customer_baseline_id'].
    if kind == "baseline":
        try:
            from datetime import date as _date
            from backend.services import customer_baseline_service as bsvc
            df = params.get("date_from"); dt = params.get("date_to")
            if df and dt:
                bl = bsvc.save_baseline(
                    db, well_id=well_id,
                    name=title,
                    period_from=_date.fromisoformat(str(df)),
                    period_to=_date.fromisoformat(str(dt)),
                    source="customer",
                    notes=comment, is_pinned=in_report,
                )
                params["customer_baseline_id"] = bl["id"]
        except Exception as exc:
            log.warning("baseline metrics fixation failed: %s", exc)

    import json as _json
    new_id = db.execute(text("""
        INSERT INTO customer_report_block
            (well_id, kind, title, params, data_snapshot, comment, in_report, sort_order)
        VALUES
            (:wid, :kind, :title,
             CAST(:params AS JSONB),
             CAST(:data_snapshot AS JSONB),
             :comment, :in_report, :sort_order)
        RETURNING id
    """), {
        "wid": well_id, "kind": kind, "title": title,
        "params": _json.dumps(params, default=str),
        "data_snapshot": _json.dumps(data_snapshot, default=str) if data_snapshot is not None else None,
        "comment": comment, "in_report": in_report,
        "sort_order": sort_order,
    }).fetchone()[0]
    db.commit()
    return get_block(db, int(new_id))


def get_block(db: Session, block_id: int) -> dict[str, Any] | None:
    row = db.execute(text("""
        SELECT id, well_id, kind, title, params, data_snapshot, comment,
               in_report, sort_order, created_at, updated_at
        FROM customer_report_block WHERE id = :bid
    """), {"bid": block_id}).mappings().fetchone()
    return dict(row) if row else None


def update_block(
    db: Session, block_id: int, *,
    title: str | None = None,
    params: dict[str, Any] | None = None,
    comment: str | None = None,
    in_report: bool | None = None,
    sort_order: int | None = None,
    data_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    sets: list[str] = []
    p: dict[str, Any] = {"bid": block_id}
    import json as _json
    if title is not None:
        sets.append("title = :title"); p["title"] = title
    if params is not None:
        sets.append("params = CAST(:params AS JSONB)")
        p["params"] = _json.dumps(params, default=str)
    if comment is not None:
        sets.append("comment = :comment"); p["comment"] = comment
    if in_report is not None:
        sets.append("in_report = :in_report"); p["in_report"] = in_report
    if sort_order is not None:
        sets.append("sort_order = :sort_order"); p["sort_order"] = sort_order
    if data_snapshot is not None:
        sets.append("data_snapshot = CAST(:data_snapshot AS JSONB)")
        p["data_snapshot"] = _json.dumps(data_snapshot, default=str)
    if not sets:
        return get_block(db, block_id)
    sets.append("updated_at = CURRENT_TIMESTAMP")
    db.execute(
        text(f"UPDATE customer_report_block SET {', '.join(sets)} WHERE id = :bid"),
        p,
    )
    db.commit()
    return get_block(db, block_id)


def delete_block(db: Session, block_id: int) -> bool:
    block = get_block(db, block_id)
    if not block:
        return False
    # Для baseline-блока — удалить связанный customer_baseline
    if block.get("kind") == "baseline":
        bid = (block.get("params") or {}).get("customer_baseline_id")
        if bid:
            try:
                from backend.services import customer_baseline_service as bsvc
                bsvc.delete_baseline(db, int(bid))
            except Exception as exc:
                log.warning("delete linked customer_baseline failed: %s", exc)
    res = db.execute(
        text("DELETE FROM customer_report_block WHERE id = :bid"),
        {"bid": block_id},
    )
    db.commit()
    return res.rowcount > 0


def count_blocks_in_report(db: Session, well_id: int) -> int:
    """Сколько блоков с in_report=True у этой скважины — для индикатора."""
    row = db.execute(text("""
        SELECT COUNT(*) FROM customer_report_block
        WHERE well_id = :wid AND in_report = TRUE
    """), {"wid": well_id}).fetchone()
    return int(row[0] if row else 0)


def get_blocks_for_report(db: Session, well_id: int) -> list[dict[str, Any]]:
    """Только активные блоки (in_report=True), отсортированы для PDF.

    Используется в adaptation_report → LaTeX-генерации (Этап 5).
    Возвращает: id, kind, title, params, data_snapshot, comment,
    sort_order, created_at.

    `data_snapshot` отдаётся, потому что сборщик отчёта рендерит блоки
    из снапшота (UI его уже наполнил), а не из live-расчёта по params.
    """
    # Сортировка ТОЛЬКО по sort_order (определяется оператором drag-reorder'ом).
    # Глава = чистый конструктор — оператор сам решает порядок блоков.
    # Жёсткая сортировка по kind убрана (ТЗ §11 этап 2 финальной структуры главы).
    rows = db.execute(text("""
        SELECT id, well_id, kind, title, params, data_snapshot, comment,
               sort_order, created_at
        FROM customer_report_block
        WHERE well_id = :wid AND in_report = TRUE
        ORDER BY sort_order, created_at
    """), {"wid": well_id}).mappings().fetchall()
    return [dict(r) for r in rows]


PARAM_LABELS: dict[str, str] = {
    "p_wellhead":    "Устье, кгс/см²",
    "p_annular":     "Затрубное, кгс/см²",
    "p_flowline":    "Шлейф, кгс/см²",
    "p_static":      "Статическое, кгс/см²",
    "q_gas_total":   "Дебит общий, тыс.м³/сут",
    "q_gas_working": "Дебит рабочий, тыс.м³/сут",
    "shutdown_min":  "Простой, мин/сут",
    "choke_mm":      "Штуцер, мм",
}

NUMERIC_FIELDS = (
    "choke_mm", "p_wellhead", "p_annular", "p_flowline",
    "q_gas_total", "q_gas_working", "shutdown_min", "p_static",
)

RUS_MONTHS = {
    1: "Январь",  2: "Февраль", 3: "Март",    4: "Апрель",
    5: "Май",     6: "Июнь",    7: "Июль",    8: "Август",
    9: "Сентябрь",10: "Октябрь",11: "Ноябрь", 12: "Декабрь",
}


# ──────────────────────────── Парсинг и UPSERT ─────────────────────────


@dataclass
class IngestResult:
    rows: int
    wells: int
    ggus: int
    date_min: str | None
    date_max: str | None
    inserted: int
    updated: int
    skipped: int
    duplicates: list[dict[str, Any]]
    warnings: list[str]


def parse_xlsx(xlsx_path: Path) -> pd.DataFrame:
    """Распарсить .xlsx в DataFrame со схемой, совместимой с `well_daily`."""
    df = parse_workbook(Path(xlsx_path))
    if df.empty:
        return df
    # Унификация типов перед записью в Postgres
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["annular_packer"] = df["annular_packer"].fillna(False).astype(bool)
    for col in NUMERIC_FIELDS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["ggu"] = df["ggu"].astype(str).str.strip()
    df["well"] = df["well"].astype(str).str.strip()
    return df


def find_duplicates(db: Session, df: pd.DataFrame) -> list[dict[str, Any]]:
    """Вернуть список (date, ggu, well), которые уже есть в well_daily.

    Используется для UI-подтверждения перед перезаписью.
    """
    if df.empty:
        return []
    keys = list({(d, str(g), str(w)) for d, g, w in
                 zip(df["date"], df["ggu"], df["well"])})
    if not keys:
        return []

    # Группируем по date чтобы делать один запрос; ANY с массивами составных ключей
    dates = sorted({k[0] for k in keys})
    rows = db.execute(
        text("""
            SELECT date, ggu, well
            FROM well_daily
            WHERE date = ANY(:dates)
        """),
        {"dates": dates},
    ).fetchall()
    existing = {(r[0], r[1], r[2]) for r in rows}
    incoming = {(d, g, w) for d, g, w in keys}
    inter = existing & incoming
    return [{"date": d.isoformat(), "ggu": g, "well": w} for d, g, w in sorted(inter)]


def upsert_records(
    db: Session,
    df: pd.DataFrame,
    *,
    overwrite: bool,
    source_file: str | None = None,
) -> tuple[int, int, int]:
    """Batch UPSERT/INSERT-IGNORE строк well_daily.

    Возвращает (inserted, updated, skipped).

    overwrite=True  → ON CONFLICT DO UPDATE   (перезапись существующих).
    overwrite=False → ON CONFLICT DO NOTHING  (новые добавляются, дубликаты пропускаются).

    Использует один `executemany()` вместо цикла per-row INSERT ... RETURNING
    (иначе на файле ~4000 строк получаются десятки секунд round-trip).
    """
    if df.empty:
        return 0, 0, 0

    cols = [
        "date", "ggu", "well", "choke_mm", "p_wellhead", "p_annular",
        "annular_packer", "p_flowline", "q_gas_total", "q_gas_working",
        "shutdown_min", "p_static", "source_sheet",
    ]
    df = df[[c for c in cols if c in df.columns]].copy()
    df["source_file"] = source_file

    # Сколько из входных уже есть в БД — один запрос до INSERT.
    # Нужно чтобы корректно поделить batch-результат на inserted/updated/skipped
    # без построчного RETURNING.
    existing = set()
    keys = list({(d, str(g), str(w))
                 for d, g, w in zip(df["date"], df["ggu"], df["well"])})
    if keys:
        dates = sorted({k[0] for k in keys})
        rows = db.execute(text("""
            SELECT date, ggu, well FROM well_daily
            WHERE date = ANY(:dates)
        """), {"dates": dates}).fetchall()
        for r in rows:
            existing.add((r[0], r[1], r[2]))

    total = len(df)
    dup_count = sum(
        1 for d, g, w in zip(df["date"], df["ggu"], df["well"])
        if (d, str(g), str(w)) in existing
    )

    update_cols = [c for c in cols if c not in ("date", "ggu", "well")] + ["source_file", "loaded_at"]

    if overwrite:
        sets = ", ".join(
            f"{c} = EXCLUDED.{c}" if c != "loaded_at" else "loaded_at = CURRENT_TIMESTAMP"
            for c in update_cols
        )
        on_conflict = f"DO UPDATE SET {sets}"
    else:
        on_conflict = "DO NOTHING"

    insert_cols = cols + ["source_file"]
    placeholders = ", ".join(f":{c}" for c in insert_cols)
    sql = text(f"""
        INSERT INTO well_daily ({", ".join(insert_cols)})
        VALUES ({placeholders})
        ON CONFLICT (date, ggu, well) {on_conflict}
    """)

    # NaN/Inf → None для совместимости с psycopg
    records = df.to_dict(orient="records")
    for rec in records:
        for k, v in rec.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                rec[k] = None

    try:
        db.execute(sql, records)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("Ошибка batch UPSERT well_daily (rows=%d)", total)
        raise

    if overwrite:
        inserted = total - dup_count
        updated = dup_count
        skipped = 0
    else:
        inserted = total - dup_count
        updated = 0
        skipped = dup_count
    return inserted, updated, skipped


def ingest_xlsx(
    db: Session,
    xlsx_path: Path,
    *,
    overwrite_duplicates: bool,
    source_file: str | None = None,
) -> IngestResult:
    """Полный цикл: парсинг → определение дубликатов → upsert."""
    df = parse_xlsx(xlsx_path)
    if df.empty:
        return IngestResult(
            rows=0, wells=0, ggus=0,
            date_min=None, date_max=None,
            inserted=0, updated=0, skipped=0,
            duplicates=[], warnings=["Парсер не извлёк ни одной строки."],
        )

    dups = find_duplicates(db, df)
    inserted, updated, skipped = upsert_records(
        db, df, overwrite=overwrite_duplicates, source_file=source_file,
    )

    return IngestResult(
        rows=len(df),
        wells=int(df["well"].nunique()),
        ggus=int(df["ggu"].nunique()),
        date_min=str(df["date"].min()),
        date_max=str(df["date"].max()),
        inserted=inserted,
        updated=updated,
        skipped=skipped,
        duplicates=dups,
        warnings=[],
    )


# ─── Helper: 3 описательных блока из snapshot для LaTeX ────────────────
# Зеркалит JS-функцию `_buildOverlapDescription` со страницы. Возвращает
# структуру [{title, color, lines: [{label, value}]}, ...] для рендера в
# отчёте. Использует поля snapshot которые УЖЕ есть (создаются фронтом
# в _buildPeriodSnapshot).

def _fmt_n(v: Any, d: int = 2) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return "—"
        return f"{f:.{d}f}"
    except (TypeError, ValueError):
        return "—"


def _trend_arrow(slope: float | None) -> str:
    if slope is None:
        return "→"
    if slope > 0.05:
        return "↑"
    if slope < -0.05:
        return "↓"
    return "→"


def build_overlap_blocks(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """3 текстовых блока «Анализ UzKorGaz / UniTool / Совместный период».

    Возвращает [{title, color, lines: [{label, value}]}, ...].
    Если данных нет (snapshot пуст или нет полей) — возвращает [].
    Источник: поля snapshot — те же что использует JS `_buildOverlapDescription`.
    """
    if not snapshot:
        return []
    blocks: list[dict[str, Any]] = []

    # ─── Блок 1: Анализ UzKorGaz ───
    cust_lines: list[dict[str, str]] = []
    if snapshot.get("date_min") or snapshot.get("date_max"):
        cust_lines.append({
            "label": "Период",
            "value": f"{snapshot.get('date_min') or '—'} — "
                     f"{snapshot.get('date_max') or '—'} "
                     f"({snapshot.get('days') or 0} сут)",
        })
    if snapshot.get("q_total_avg") is not None or snapshot.get("q_total_median") is not None:
        cust_lines.append({
            "label": "Q общий",
            "value": (f"среднее {_fmt_n(snapshot.get('q_total_avg'))}, "
                      f"медиана {_fmt_n(snapshot.get('q_total_median'))} тыс.м³/сут"),
        })
    if snapshot.get("q_working_avg") is not None or snapshot.get("q_working_median") is not None:
        cust_lines.append({
            "label": "Q рабочий",
            "value": (f"среднее {_fmt_n(snapshot.get('q_working_avg'))}, "
                      f"медиана {_fmt_n(snapshot.get('q_working_median'))} тыс.м³/сут"),
        })
    if snapshot.get("dp_avg") is not None or snapshot.get("dp_median") is not None:
        cust_lines.append({
            "label": "ΔP",
            "value": (f"среднее {_fmt_n(snapshot.get('dp_avg'))}, "
                      f"медиана {_fmt_n(snapshot.get('dp_median'))} кгс/см²"),
        })
    if snapshot.get("p_wellhead_median") is not None or snapshot.get("p_flowline_median") is not None:
        cust_lines.append({
            "label": "Давления (медиана)",
            "value": (f"устье {_fmt_n(snapshot.get('p_wellhead_median'))}, "
                      f"шлейф {_fmt_n(snapshot.get('p_flowline_median'))} кгс/см²"),
        })
    sd_min = snapshot.get("shutdown_min_total")
    if sd_min is not None:
        sd_h = (float(sd_min) / 60) if isinstance(sd_min, (int, float)) else 0
        cust_lines.append({
            "label": "Простой",
            "value": (f"всего {_fmt_n(sd_min, 0)} мин ({sd_h:.1f} ч), "
                      f"дней с простоем — {snapshot.get('shutdown_days_count') or 0}"),
        })
    q_trend = snapshot.get("q_trend") or {}
    if q_trend.get("slope_per_day") is not None:
        cust_lines.append({
            "label": "Тренд Q",
            "value": (f"{_trend_arrow(q_trend.get('slope_per_day'))} "
                      f"{_fmt_n(q_trend.get('slope_per_day'), 3)} тыс.м³/сут (за весь период)"),
        })
    if cust_lines:
        blocks.append({
            "title": "Анализ данных UzKorGaz (well_daily)",
            "color": "cust",
            "lines": cust_lines,
        })

    # ─── Блок 2: Анализ UniTool (если был наложен) ───
    unitool = snapshot.get("unitool") or None
    if unitool:
        our_lines: list[dict[str, str]] = []
        if unitool.get("equip_dt"):
            v = f"{str(unitool['equip_dt'])[:10]}"
            if unitool.get("dropped_pre_equip"):
                v += f" (отброшено {unitool['dropped_pre_equip']} точек до установки)"
            our_lines.append({"label": "Установка датчика", "value": v})
        our_lines.append({
            "label": "Период измерений",
            "value": (f"{unitool.get('first_date') or '—'} — "
                      f"{unitool.get('last_date') or '—'} "
                      f"({unitool.get('days') or 0} сут)"),
        })
        dmap = unitool.get("describe_map") or {}
        q_our = dmap.get("Дебит общий, тыс.м³/сут") or {}
        dp_our = dmap.get("ΔP, кгс/см²") or {}
        pt_our = dmap.get("Устье, кгс/см²") or {}
        pl_our = dmap.get("Шлейф, кгс/см²") or {}
        if unitool.get("has_flow"):
            our_lines.append({
                "label": f"Q (расчёт по штуцеру {unitool.get('choke_mm') or '—'} мм)",
                "value": (f"среднее {_fmt_n(q_our.get('mean'))}, "
                          f"медиана {_fmt_n(q_our.get('median'))} тыс.м³/сут"),
            })
        else:
            our_lines.append({
                "label": "Q",
                "value": "не рассчитан — нет данных по штуцеру в well_construction",
            })
        if dp_our.get("mean") is not None or dp_our.get("median") is not None:
            our_lines.append({
                "label": "ΔP",
                "value": (f"среднее {_fmt_n(dp_our.get('mean'))}, "
                          f"медиана {_fmt_n(dp_our.get('median'))} кгс/см²"),
            })
        if pt_our.get("median") is not None or pl_our.get("median") is not None:
            our_lines.append({
                "label": "Давления (медиана)",
                "value": (f"устье {_fmt_n(pt_our.get('median'))}, "
                          f"шлейф {_fmt_n(pl_our.get('median'))} кгс/см²"),
            })
        blocks.append({
            "title": "Анализ данных UniTool (LoRa-датчики)",
            "color": "our",
            "lines": our_lines,
        })

        # ─── Блок 3: Совместный период ───
        c_first = snapshot.get("date_min")
        c_last = snapshot.get("date_max")
        o_first = unitool.get("first_date")
        o_last = unitool.get("last_date")
        if c_first and c_last and o_first and o_last:
            inter_start = max(c_first, o_first)
            inter_end = min(c_last, o_last)
            if inter_start <= inter_end:
                try:
                    from datetime import date as _date
                    d1 = _date.fromisoformat(str(inter_start)[:10])
                    d2 = _date.fromisoformat(str(inter_end)[:10])
                    days = (d2 - d1).days + 1
                except Exception:
                    days = 0
                cust_days = int(snapshot.get("days") or 0)
                our_days = int(unitool.get("days") or 0)
                ratio = (abs(cust_days - our_days) / max(cust_days, our_days) * 100
                         if (cust_days and our_days) else 0)
                compat_note = (
                    "Периоды сопоставимы (≤ 10% разницы) — "
                    "сравнение в основной таблице корректно."
                    if ratio <= 10 else
                    "Периоды несопоставимы (> 10% разницы) — "
                    "корректное сравнение только в таблице «Строгое сравнение» ниже."
                )
                blocks.append({
                    "title": "Совместный период UzKorGaz ∩ UniTool",
                    "color": "overlap",
                    "lines": [
                        {"label": "Совместных суток",
                         "value": f"{days} ({inter_start} — {inter_end})"},
                        {"label": "Полные периоды",
                         "value": (f"UzKorGaz {cust_days} сут, UniTool "
                                   f"{our_days} сут (разница {ratio:.1f}%)")},
                        {"label": "Сопоставимость", "value": compat_note},
                    ],
                })
            else:
                blocks.append({
                    "title": "Совместный период UzKorGaz ∩ UniTool",
                    "color": "overlap",
                    "lines": [{
                        "label": "Внимание",
                        "value": "Совместный период отсутствует — "
                                 "UzKorGaz и UniTool не пересекаются по времени.",
                    }],
                })
    return blocks


# ──────────────────────────── Чтение из БД ─────────────────────────────


def get_dataset_meta(db: Session) -> dict[str, Any]:
    row = db.execute(text("""
        SELECT COUNT(*)                    AS rows,
               COUNT(DISTINCT well)        AS wells,
               COUNT(DISTINCT ggu)         AS ggus,
               MIN(date)                   AS date_min,
               MAX(date)                   AS date_max,
               MAX(loaded_at)              AS last_loaded
        FROM well_daily
    """)).fetchone()
    if not row or not row[0]:
        return {"rows": 0, "wells": 0, "ggus": 0,
                "date_min": None, "date_max": None, "last_loaded": None}
    return {
        "rows": int(row[0] or 0),
        "wells": int(row[1] or 0),
        "ggus": int(row[2] or 0),
        "date_min": row[3].isoformat() if row[3] else None,
        "date_max": row[4].isoformat() if row[4] else None,
        "last_loaded": row[5].isoformat(timespec="seconds") if row[5] else None,
    }


def get_wells(db: Session) -> list[dict[str, Any]]:
    """Список скважин в well_daily с диапазоном дат для каждой."""
    rows = db.execute(text("""
        SELECT well,
               MAX(ggu)            AS ggu,
               COUNT(*)            AS days,
               MIN(date)           AS date_min,
               MAX(date)           AS date_max
        FROM well_daily
        GROUP BY well
        ORDER BY
            (well ~ '^\\d+(\\.\\d+)?$') DESC,
            CASE WHEN well ~ '^\\d+$' THEN CAST(well AS INTEGER) ELSE NULL END,
            well
    """)).fetchall()
    return [
        {
            "well": r[0],
            "ggu": r[1],
            "days": int(r[2]),
            "date_min": r[3].isoformat() if r[3] else None,
            "date_max": r[4].isoformat() if r[4] else None,
        }
        for r in rows
    ]


def load_for_well(
    db: Session,
    well: str,
    d_from: date | None = None,
    d_to: date | None = None,
) -> pd.DataFrame:
    """Получить ряд по одной скважине (отсортирован по дате)."""
    sql = """
        SELECT date, ggu, well, choke_mm, p_wellhead, p_annular,
               annular_packer, p_flowline, q_gas_total, q_gas_working,
               shutdown_min, p_static, source_sheet, source_file, loaded_at
        FROM well_daily
        WHERE well = :well
    """
    params: dict[str, Any] = {"well": str(well)}
    if d_from:
        sql += " AND date >= :d_from"
        params["d_from"] = d_from
    if d_to:
        sql += " AND date <= :d_to"
        params["d_to"] = d_to
    sql += " ORDER BY date"

    rows = db.execute(text(sql), params).mappings().fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    if "annular_packer" in df.columns:
        df["annular_packer"] = df["annular_packer"].astype(bool)
    return df


def well_availability(db: Session, well: str) -> dict[str, Any]:
    """Доступность данных по скважине во ВСЁМ хранилище well_daily.

    Возвращает {has_data, ggu, days, date_min, date_max, gaps_count}.
    Используется для подсказки пользователю — какой период доступен,
    прежде чем он выберет окно анализа.
    """
    row = db.execute(text("""
        SELECT MAX(ggu)        AS ggu,
               COUNT(*)        AS days,
               MIN(date)       AS date_min,
               MAX(date)       AS date_max
        FROM well_daily
        WHERE well = :well
    """), {"well": str(well)}).fetchone()

    if not row or not row[1]:
        return {
            "has_data": False, "well": str(well),
            "ggu": None, "days": 0, "date_min": None, "date_max": None,
            "gaps_count": 0, "expected_days": 0,
        }
    days = int(row[1] or 0)
    d_min = row[2]
    d_max = row[3]
    expected = (d_max - d_min).days + 1 if (d_min and d_max) else 0
    return {
        "has_data": True,
        "well": str(well),
        "ggu": row[0],
        "days": days,
        "date_min": d_min.isoformat() if d_min else None,
        "date_max": d_max.isoformat() if d_max else None,
        "expected_days": expected,
        "gaps_count": max(0, expected - days),
    }


def find_well(db: Session, well_number: str) -> dict[str, Any] | None:
    """Найти скважину в `wells` по строковому номеру из well_daily.

    Возвращает {id, number, name} или None.

    Алгоритм (строгий, без ILIKE '%N%' — иначе '128' ложно совпадёт с '1280'):
      1) Если well_number — целое: WHERE number = :n (самый надёжный ключ).
      2) Точное совпадение по name: 'Скв <N>' / 'Скв.<N>' / 'Скважина <N>' / '<N>'.
      3) Ничего не возвращаем (пусть UI скажет «не найдено» и не даст overlay).

    При нескольких совпадениях №1 — берём МЕНЬШИЙ id (обычно это более старая
    запись, не тест). Этот же критерий — детерминированный, не зависит от
    порядка вставки.
    """
    s = str(well_number).strip()
    if not s:
        return None

    # 1) По номеру (точное совпадение integer)
    try:
        n = int(float(s.replace(",", ".")))
        row = db.execute(text("""
            SELECT id, number, name FROM wells
            WHERE number = :n
            ORDER BY id
            LIMIT 1
        """), {"n": n}).fetchone()
        if row:
            return {"id": int(row[0]), "number": row[1], "name": row[2]}
    except (ValueError, TypeError):
        pass

    # 2) Точное совпадение по имени
    row = db.execute(text("""
        SELECT id, number, name FROM wells
        WHERE name IN (:s, :skv, :skv2, :skv3, :skvazh)
        ORDER BY id
        LIMIT 1
    """), {
        "s":       s,
        "skv":     f"Скв {s}",
        "skv2":    f"Скв. {s}",
        "skv3":    f"Скв.{s}",
        "skvazh":  f"Скважина {s}",
    }).fetchone()
    if row:
        return {"id": int(row[0]), "number": row[1], "name": row[2]}

    return None


def find_well_id_by_number(db: Session, well_number: str) -> int | None:
    """Back-compat обёртка: только id."""
    w = find_well(db, well_number)
    return w["id"] if w else None


# ───── Наши давления через pressure_raw + verified masks ─────
#
# Конвейер тот же, что в daily_report_service._load_masked_hourly:
#   pressure_raw → clean_pressure → load_active_masks(verified_only=True)
#                → apply_masks → false-zeros filter → resample.
#
# Различие: ресэмпл по СУТКАМ (1d), не по часам, и сразу считаем
# три ряда — p_tube, p_line, dp_working (ΔP с фильтром рабочих часов).


def _live_flow_daily(
    db: Session,
    well_id: int,
    d_from: date,
    d_to: date,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Живой pipeline-расчёт для диапазона дат — ОДИН для всех мест.

    Pipeline ТОЧНО как в /api/flow-rate/calculate (страница скважины):
        get_pressure_data → clean_pressure → load_active_masks + apply_masks
            → df.index += 5h (UTC→Kungrad) → smooth_pressure
            → calculate_flow_rate → calculate_purge_loss
            → calculate_cumulative → aggregate_to_daily

    Returns
    -------
    (daily_rows, meta)
        daily_rows: список {result_date, avg_p_tube, avg_p_line, avg_dp,
                            avg_flow_rate, cumulative_flow, downtime_minutes, ...}
        meta: {choke_mm, choke_source, mask_count, sensor_first_date}
    """
    from datetime import datetime as _dt, time as _time, timedelta as _td

    meta: dict[str, Any] = {
        "choke_mm": None, "choke_source": None,
        "mask_count": 0, "sensor_first_date": None,
    }
    try:
        from backend.services.flow_rate.data_access import (
            get_pressure_data, get_choke_mm,
        )
        from backend.services.flow_rate.cleaning import (
            clean_pressure, smooth_pressure,
        )
        from backend.services.flow_rate.calculator import (
            calculate_flow_rate, calculate_cumulative, calculate_purge_loss,
        )
        from backend.services.flow_rate.config import FlowRateConfig
        from backend.services.flow_rate.scenario_service import aggregate_to_daily
        from backend.services.pressure_mask_service import (
            load_active_masks, apply_masks as _apply_masks,
        )
    except Exception as exc:
        log.warning("[_live_flow_daily] flow_rate/masks unavailable: %s", exc)
        return [], meta

    # Дата первого измерения — ограничивает нижнюю границу
    row = db.execute(text("""
        SELECT MIN(measured_at)::date
        FROM pressure_raw
        WHERE well_id = :wid
          AND (p_tube IS NOT NULL OR p_line IS NOT NULL)
    """), {"wid": well_id}).fetchone()
    sensor_first_date = row[0] if row and row[0] else None
    meta["sensor_first_date"] = (
        sensor_first_date.isoformat() if sensor_first_date else None
    )
    if sensor_first_date is None:
        return [], meta

    # Эффективный период в Kungrad-local
    d_from_eff = d_from
    if d_from_eff is None or d_from_eff < sensor_first_date:
        d_from_eff = sensor_first_date
    d_to_eff = d_to or date.today()
    if d_to_eff < d_from_eff:
        return [], meta

    KUNGRAD_OFFSET = _td(hours=5)
    utc_start = _dt.combine(d_from_eff, _time(0, 0)) - KUNGRAD_OFFSET
    utc_end = _dt.combine(d_to_eff, _time(23, 59, 59)) - KUNGRAD_OFFSET

    # 1) Сырьё
    df_raw = get_pressure_data(well_id, utc_start.isoformat(), utc_end.isoformat())
    if df_raw is None or df_raw.empty:
        return [], meta

    # 2) Чистка
    df = clean_pressure(df_raw)
    if df.empty:
        return [], meta

    # 3) Маски (все active — как /api/flow-rate/calculate, без verified_only=True)
    try:
        masks = load_active_masks(well_id, utc_start, utc_end)
        if masks:
            df, mc = _apply_masks(df, masks)
            meta["mask_count"] = int(mc)
    except Exception as exc:
        log.warning("[_live_flow_daily] mask apply error: %s", exc)

    # 4) UTC → Kungrad
    df.index = df.index + KUNGRAD_OFFSET

    # 5) Сглаживание
    try:
        df = smooth_pressure(df)
    except Exception as exc:
        log.warning("[_live_flow_daily] smooth error: %s", exc)

    # 6) Штуцер. Приоритет:
    #    (1) get_choke_mm() — единая точка для всей системы (well, daily_report,
    #        adaptation_report, наш конвейер). Чинено в data_access.py.
    #    (2) Мода choke_mm из well_daily (сводка заказчика) за период —
    #        используется только если в well_construction штуцер вообще
    #        отсутствует (например, скважина не паспортизована).
    try:
        choke_mm = get_choke_mm(well_id)
    except Exception:
        choke_mm = None
    meta["choke_source"] = "well_construction" if choke_mm else None

    w_row = db.execute(
        text("SELECT number FROM wells WHERE id = :wid"), {"wid": well_id},
    ).fetchone()
    well_number = w_row[0] if w_row else None

    if (not choke_mm or choke_mm <= 0) and well_number is not None:
        r = db.execute(text("""
            SELECT choke_mm FROM well_daily
            WHERE well = :well AND choke_mm IS NOT NULL AND choke_mm > 0
              AND date >= :d_from AND date <= :d_to
            GROUP BY choke_mm
            ORDER BY COUNT(*) DESC, MAX(date) DESC
            LIMIT 1
        """), {
            "well": str(well_number),
            "d_from": d_from_eff, "d_to": d_to_eff,
        }).fetchone()
        if r and r[0]:
            choke_mm = float(r[0])
            meta["choke_source"] = "well_daily (сводка заказчика)"

    meta["choke_mm"] = float(choke_mm) if choke_mm else None

    # 7) Дебит → накопленный → суточная агрегация (тот же aggregate_to_daily)
    if choke_mm and choke_mm > 0:
        df_flow = calculate_flow_rate(df, float(choke_mm), FlowRateConfig())
        if df_flow.empty or "flow_rate" not in df_flow.columns:
            return [], meta
        try:
            df_flow = calculate_purge_loss(df_flow)
        except Exception as exc:
            log.warning("[_live_flow_daily] purge_loss error: %s", exc)
        df_flow = calculate_cumulative(df_flow)
        daily_rows = aggregate_to_daily(df_flow)
        return daily_rows, meta

    # 8) Без штуцера — только давления (суточная группировка по Kungrad-date)
    tmp = df.copy()
    tmp["_d"] = tmp.index.date
    grouped = (
        tmp.groupby("_d")
           .agg(p_tube=("p_tube", "mean"),
                p_line=("p_line", "mean"))
           .reset_index()
    )
    rows = []
    for _, r in grouped.iterrows():
        pt = float(r["p_tube"]) if pd.notna(r["p_tube"]) else None
        pl = float(r["p_line"]) if pd.notna(r["p_line"]) else None
        dp = (pt - pl) if (pt is not None and pl is not None) else None
        rows.append({
            "result_date": r["_d"],
            "avg_p_tube": pt, "avg_p_line": pl, "avg_dp": dp,
            "avg_flow_rate": None, "cumulative_flow": None,
            "downtime_minutes": None,
        })
    return rows, meta


def _load_masked_daily_pressure(
    well_id: int,
    d_from: date,
    d_to: date,
) -> pd.DataFrame:
    """Суточные средние давлений с применёнными verified-масками.

    Returns
    -------
    DataFrame с колонками:
        d        : date
        p_tube   : суточное среднее p_tube (после масок и NULLIF(0))
        p_line   : суточное среднее p_line
        dp       : суточное среднее ΔP — только из «рабочих часов»
                   (p_tube > p_line, 0.1 < ΔP < 30)
    Пустой DataFrame, если по скважине нет данных в pressure_raw.
    """
    from datetime import datetime as _dt, time as _time, timedelta as _td

    # Период по UTC-границам суток (pressure_raw хранит timestamps как
    # «Кунградское время в naive datetime», но get_pressure_data
    # принимает ISO-строки и фильтрует по measured_at — так же,
    # как делает daily_report_service).
    utc_start = _dt.combine(d_from, _time.min)
    utc_end = _dt.combine(d_to, _time.max)

    try:
        from backend.services.flow_rate.data_access import get_pressure_data
        from backend.services.flow_rate.cleaning import clean_pressure
        from backend.services.pressure_mask_service import (
            load_active_masks, apply_masks as _apply_masks,
        )
    except Exception as exc:
        log.warning("[masked_daily] модули flow_rate/pressure_mask недоступны: %s", exc)
        return pd.DataFrame()

    df_raw = get_pressure_data(well_id, utc_start.isoformat(), utc_end.isoformat())
    if df_raw is None or df_raw.empty:
        return pd.DataFrame()

    df = clean_pressure(df_raw)
    if df.empty:
        return pd.DataFrame()

    try:
        masks = load_active_masks(well_id, utc_start, utc_end, verified_only=True)
        if masks:
            df, _ = _apply_masks(df, masks)
    except Exception as exc:
        log.warning("[masked_daily] mask apply error: %s", exc)

    # False-zeros защита (LoRa SMOD-PT-60 даёт 0.0 ~4% времени).
    df["p_tube"] = df["p_tube"].where(df["p_tube"] > 0)
    df["p_line"] = df["p_line"].where(df["p_line"] > 0)

    # ΔP считаем построчно с фильтром рабочих часов.
    valid = (
        df["p_tube"].notna() & df["p_line"].notna() &
        (df["p_tube"] > df["p_line"]) &
        ((df["p_tube"] - df["p_line"]) > 0.1) &
        ((df["p_tube"] - df["p_line"]) < 30)
    )
    df["dp_working"] = (df["p_tube"] - df["p_line"]).where(valid)

    # Группируем по календарной дате (в той TZ, в которой лежит index).
    daily = (
        df.assign(_d=df.index.date)
          .groupby("_d", as_index=True)
          .agg(p_tube=("p_tube", "mean"),
               p_line=("p_line", "mean"),
               dp=("dp_working", "mean"))
          .reset_index()
          .rename(columns={"_d": "d"})
    )
    daily = daily.sort_values("d").reset_index(drop=True)
    return daily


def our_daily_data(
    db: Session,
    well_id: int,
    d_from: date | None = None,
    d_to: date | None = None,
) -> dict[str, Any]:
    """Наши суточные данные для overlay. Использует _live_flow_daily ─
    единый pipeline (как /api/flow-rate/calculate)."""
    # Инфо о скважине
    w_row = db.execute(
        text("SELECT id, number, name FROM wells WHERE id = :wid"),
        {"wid": well_id},
    ).fetchone()
    well_info = (
        {"id": int(w_row[0]), "number": w_row[1], "name": w_row[2]}
        if w_row else None
    )

    daily_rows, meta = _live_flow_daily(db, well_id, d_from, d_to)

    dates  = [r["result_date"].isoformat() for r in daily_rows]
    p_tube = [_fmt_float(r.get("avg_p_tube")) for r in daily_rows]
    p_line = [_fmt_float(r.get("avg_p_line")) for r in daily_rows]
    dp     = [_fmt_float(r.get("avg_dp")) for r in daily_rows]
    q_avg  = [_fmt_float(r.get("avg_flow_rate")) for r in daily_rows]
    q_cum  = [_fmt_float(r.get("cumulative_flow")) for r in daily_rows]
    dntm   = [_fmt_float(r.get("downtime_minutes")) for r in daily_rows]

    return {
        "well_id": well_id,
        "well": well_info,
        "scenario_id": None,
        "scenario_ids": [],
        "sensor_first_date": meta.get("sensor_first_date"),
        "choke_mm": meta.get("choke_mm"),
        "choke_source": meta.get("choke_source"),
        "mask_count": meta.get("mask_count", 0),
        "pressure": {
            "dates":  dates,
            "p_tube": p_tube,
            "p_line": p_line,
            "dp":     dp,
        },
        "flow": {
            "dates":           dates,
            "avg_flow_rate":   q_avg,
            "cumulative_flow": q_cum,
            "downtime":        dntm,
        },
        "has_pressure": any(v is not None for v in p_tube),
        "has_flow": any(v is not None for v in q_avg),
    }


def _empty_our_data(db: Session, well_id: int) -> dict[str, Any]:
    w_row = db.execute(
        text("SELECT id, number, name FROM wells WHERE id = :wid"),
        {"wid": well_id},
    ).fetchone()
    return {
        "well_id": well_id,
        "well": (
            {"id": int(w_row[0]), "number": w_row[1], "name": w_row[2]}
            if w_row else None
        ),
        "scenario_id": None, "scenario_ids": [],
        "sensor_first_date": None, "choke_mm": None, "mask_count": 0,
        "pressure": {"dates": [], "p_tube": [], "p_line": [], "dp": []},
        "flow": {"dates": [], "avg_flow_rate": [], "cumulative_flow": [], "downtime": []},
        "has_pressure": False, "has_flow": False,
    }


# ─────────────── Универсальный time series (для compare) ───────────────

# source: 'customer' (well_daily),
#         'our_pressure' (pressure_hourly → суточные средние),
#         'our_flow' (flow_result → суточный avg_flow_rate)
# metric: 'dp' | 'q_total' | 'q_working' | 'p_wellhead' | 'p_flowline'


SOURCE_LABELS = {
    "customer":     "Заказчик (суточная сводка)",
    "our_pressure": "Наши датчики (live: pressure_raw + маски + агрегация по суткам)",
    "our_flow":     "Наш расчёт (live flow_rate — как /well)",
}
METRIC_LABELS = {
    "dp":         "ΔP = P_устье − P_шлейф, кгс/см²",
    "q_total":    "Q общий, тыс.м³/сут",
    "q_working":  "Q рабочий, тыс.м³/сут",
    "p_wellhead": "P устье, кгс/см²",
    "p_flowline": "P шлейф, кгс/см²",
}


def time_series(
    db: Session,
    *,
    source: str,
    well: str,
    metric: str,
    d_from: date,
    d_to: date,
) -> dict[str, Any]:
    """Единая точка для графика «период → значения» из любого источника.

    Возвращает:
        {
          ok, error?,
          source, metric, label,
          well: {id, number, name} | None,
          dates: [...iso...],
          values: [...float|None...],
        }
    """
    if source not in SOURCE_LABELS:
        return {"ok": False, "error": f"Неизвестный источник: {source}"}
    if metric not in METRIC_LABELS:
        return {"ok": False, "error": f"Неизвестная метрика: {metric}"}

    # ── Источник: заказчик (well_daily) ──────────────────────
    if source == "customer":
        expr_map = {
            "dp":         "p_wellhead - p_flowline",
            "q_total":    "q_gas_total",
            "q_working":  "q_gas_working",
            "p_wellhead": "p_wellhead",
            "p_flowline": "p_flowline",
        }
        sql = text(f"""
            SELECT date, ({expr_map[metric]}) AS v
            FROM well_daily
            WHERE well = :well
              AND date BETWEEN :d_from AND :d_to
            ORDER BY date
        """)
        rows = db.execute(sql, {
            "well": str(well), "d_from": d_from, "d_to": d_to,
        }).fetchall()
        return {
            "ok": True, "source": source, "metric": metric,
            "label": f"{SOURCE_LABELS[source]} — скв. № {well}",
            "well": None,
            "dates":  [r[0].isoformat() for r in rows],
            "values": [_fmt_float(r[1]) for r in rows],
        }

    # Остальные источники используют wells.id
    w = find_well(db, well)
    if not w:
        return {
            "ok": False,
            "error": f"Скв. № {well} не найдена в таблице wells",
            "source": source, "metric": metric,
        }

    # ── Источник: наши давления — ТОТ ЖЕ live-pipeline ──
    if source == "our_pressure":
        if metric not in ("dp", "p_wellhead", "p_flowline"):
            return {
                "ok": False,
                "error": f"Для источника our_pressure метрика {metric} не поддерживается",
            }

        daily_rows, meta = _live_flow_daily(db, w["id"], d_from, d_to)
        if not daily_rows:
            return {
                "ok": True, "source": source, "metric": metric,
                "label": f"{SOURCE_LABELS[source]} — {w['name'] or ('Скв ' + str(w['number']))}",
                "well": w, "dates": [], "values": [],
            }

        if metric == "dp":
            col = "avg_dp"
        elif metric == "p_wellhead":
            col = "avg_p_tube"
        else:
            col = "avg_p_line"

        return {
            "ok": True, "source": source, "metric": metric,
            "label": f"{SOURCE_LABELS[source]} — {w['name'] or ('Скв ' + str(w['number']))}",
            "well": w,
            "dates":  [r["result_date"].isoformat() for r in daily_rows],
            "values": [_fmt_float(r.get(col)) for r in daily_rows],
        }

    # ── Источник: наш расчёт дебита — ТОТ ЖЕ live-pipeline ──
    if source == "our_flow":
        if metric == "q_total":
            col = "avg_flow_rate"
        elif metric == "dp":
            col = "avg_dp"
        elif metric == "q_working":
            # Q рабочий = avg_flow_rate * (1440 - downtime) / 1440
            col = "__derived_q_working__"
        else:
            return {
                "ok": False,
                "error": f"Для источника our_flow метрика {metric} не поддерживается",
            }

        daily_rows, meta = _live_flow_daily(db, w["id"], d_from, d_to)
        if not daily_rows:
            return {
                "ok": False,
                "error": (
                    f"Нет данных для расчёта (скв. {w['name'] or w['number']}, "
                    f"штуцер: {meta.get('choke_source') or 'не найден'})."
                ),
                "source": source, "metric": metric, "well": w,
                "dates": [], "values": [],
            }

        dates = [r["result_date"].isoformat() for r in daily_rows]
        if col == "__derived_q_working__":
            values: list[Any] = []
            for r in daily_rows:
                q = r.get("avg_flow_rate")
                dm = r.get("downtime_minutes") or 0
                if q is None:
                    values.append(None)
                else:
                    values.append(_fmt_float(q * (1440.0 - dm) / 1440.0))
        else:
            values = [_fmt_float(r.get(col)) for r in daily_rows]

        return {
            "ok": True, "source": source, "metric": metric,
            "label": (
                f"{SOURCE_LABELS[source]} — "
                f"{w['name'] or ('Скв ' + str(w['number']))} "
                f"(live, choke={meta.get('choke_mm')}мм)"
            ),
            "well": w,
            "choke_mm": meta.get("choke_mm"),
            "choke_source": meta.get("choke_source"),
            "mask_count": meta.get("mask_count"),
            "dates": dates,
            "values": values,
        }

    return {"ok": False, "error": "unreachable"}


def coverage(
    db: Session,
    well: str,
    d_from: date,
    d_to: date,
) -> dict[str, Any]:
    """Какие даты периода уже есть в БД для скважины (для UI/индикации)."""
    rows = db.execute(text("""
        SELECT date FROM well_daily
        WHERE well = :well AND date BETWEEN :d_from AND :d_to
        ORDER BY date
    """), {"well": str(well), "d_from": d_from, "d_to": d_to}).fetchall()
    present = {r[0] for r in rows}
    expected_days = (d_to - d_from).days + 1
    from datetime import timedelta as _td
    all_dates = [d_from + _td(days=i) for i in range(expected_days)]
    missing = [d.isoformat() for d in all_dates if d not in present]
    return {
        "expected_days": expected_days,
        "present_days": len(present),
        "missing_dates": missing[:50],
        "missing_total": expected_days - len(present),
    }


# ──────────────────────────── Аналитика ────────────────────────────────


def _fmt_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Преобразовать DataFrame в JSON-сериализуемый список (NaN → None, date → ISO)."""
    if df.empty:
        return []
    out = []
    for r in df.to_dict(orient="records"):
        clean: dict[str, Any] = {}
        for k, v in r.items():
            if isinstance(v, (pd.Timestamp, datetime)):
                clean[k] = v.date().isoformat() if isinstance(v, pd.Timestamp) else v.isoformat()
            elif isinstance(v, date):
                clean[k] = v.isoformat()
            elif isinstance(v, float):
                clean[k] = None if (math.isnan(v) or math.isinf(v)) else v
            elif isinstance(v, np.integer):
                clean[k] = int(v)
            elif isinstance(v, np.floating):
                f = float(v)
                clean[k] = None if (math.isnan(f) or math.isinf(f)) else f
            elif isinstance(v, np.bool_):
                clean[k] = bool(v)
            else:
                clean[k] = v
        out.append(clean)
    return out


def describe_well_period(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Описательная статистика по числовым параметрам за период."""
    if df.empty:
        return []
    cols = [c for c in PARAM_LABELS.keys() if c in df.columns]
    if not cols:
        return []
    desc = df[cols].describe().T
    desc = desc.rename(columns={
        "count": "N", "mean": "mean", "std": "std",
        "min": "min", "25%": "q25", "50%": "median",
        "75%": "q75", "max": "max",
    })
    out = []
    for k, row in desc.iterrows():
        out.append({
            "param": k,
            "label": PARAM_LABELS.get(k, k),
            "n":      int(row.get("N", 0)),
            "mean":   _fmt_float(row.get("mean")),
            "std":    _fmt_float(row.get("std")),
            "min":    _fmt_float(row.get("min")),
            "q25":    _fmt_float(row.get("q25")),
            "median": _fmt_float(row.get("median")),
            "q75":    _fmt_float(row.get("q75")),
            "max":    _fmt_float(row.get("max")),
        })
    return out


def _linreg_slope(x: np.ndarray, y: np.ndarray) -> float | None:
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 2:
        return None
    try:
        slope, _ = np.polyfit(x[mask], y[mask], 1)
        f = float(slope)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return None


def monthly_stats(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Помесячные агрегаты для одной скважины (среднее, медиана, тренд)."""
    if df.empty:
        return []
    d = df.copy()
    d["_month"] = d["date"].dt.to_period("M")
    d["_dp"] = d["p_wellhead"] - d["p_flowline"]

    out = []
    for m_period, grp in d.groupby("_month", sort=True):
        x_days = (grp["date"] - grp["date"].min()).dt.total_seconds().to_numpy() / 86400.0
        q_total = grp["q_gas_total"].to_numpy(dtype=float)
        q_work  = grp["q_gas_working"].to_numpy(dtype=float)
        dp      = grp["_dp"].to_numpy(dtype=float)

        out.append({
            "month": m_period.strftime("%Y-%m"),
            "month_label": f"{RUS_MONTHS[m_period.month]} {m_period.year}",
            "days": int(grp["date"].nunique()),
            "mean_q_total":     _fmt_float(np.nanmean(q_total)) if np.isfinite(q_total).any() else None,
            "median_q_total":   _fmt_float(np.nanmedian(q_total)) if np.isfinite(q_total).any() else None,
            "mean_q_working":   _fmt_float(np.nanmean(q_work)) if np.isfinite(q_work).any() else None,
            "median_q_working": _fmt_float(np.nanmedian(q_work)) if np.isfinite(q_work).any() else None,
            "mean_dp":          _fmt_float(np.nanmean(dp)) if np.isfinite(dp).any() else None,
            "median_dp":        _fmt_float(np.nanmedian(dp)) if np.isfinite(dp).any() else None,
            "trend_q_total":    _linreg_slope(x_days, q_total),
            "trend_q_working":  _linreg_slope(x_days, q_work),
            "trend_dp":         _linreg_slope(x_days, dp),
            "mean_p_wellhead":  _fmt_float(np.nanmean(grp["p_wellhead"].to_numpy(dtype=float))),
            "mean_shutdown":    _fmt_float(np.nanmean(grp["shutdown_min"].to_numpy(dtype=float))),
            "date_min": grp["date"].min().date().isoformat(),
            "date_max": grp["date"].max().date().isoformat(),
        })
    return out


def monthly_description(months: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Развёрнутое описание помесячной динамики.

    Для каждого месяца текстом выводим: число суток в выборке, оценочное
    рабочее время (минут/часов с учётом среднего простоя), средние и
    медианные Q общ./раб., ΔP, P уст., тренд Q, простой (мин/сут и всего
    часов), а также абсолютное и относительное изменение ключевых
    показателей к предыдущему месяцу.
    """
    if not months:
        return []

    def _cmp(curr: float | None, prev: float | None, unit: str, name: str) -> str | None:
        if curr is None or prev is None:
            return None
        diff = curr - prev
        if prev:
            pct = diff / prev * 100.0
            return f"{name} {diff:+.1f}{unit} ({pct:+.1f}%)"
        return f"{name} {diff:+.1f}{unit}"

    def _trend_txt(slope: float | None, unit_per_day: str) -> str:
        if slope is None:
            return "тренд: данных мало"
        if abs(slope) < 0.05:
            return f"тренд плоский ({slope:+.2f}{unit_per_day})"
        if slope > 0:
            return f"рост ({slope:+.2f}{unit_per_day})"
        return f"снижение ({slope:+.2f}{unit_per_day})"

    out = []
    prev = None
    for r in months:
        days = int(r.get("days") or 0)
        mean_sd = r.get("mean_shutdown")  # мин/сут
        # Оценка рабочего времени: (1440 − среднее простоя) × дней / 60 → часы
        if mean_sd is not None and days > 0:
            work_min_per_day = max(0.0, 1440.0 - float(mean_sd))
            work_hours_total = work_min_per_day * days / 60.0
            sd_hours_total = float(mean_sd) * days / 60.0
        else:
            work_hours_total = None
            sd_hours_total = None

        parts: list[str] = []
        # Вступительная фраза: «За месяц в выборке 28 суток, около 578 часов
        # рабочего времени.»
        head = f"За месяц в выборке {days} суток"
        if work_hours_total is not None:
            head += f", около {work_hours_total:.0f} часов рабочего времени"
        parts.append(head + ".")

        # Дебиты — общий и рабочий
        if r.get("mean_q_total") is not None and r.get("median_q_total") is not None:
            parts.append(
                f"Общий дебит: среднее {r['mean_q_total']:.1f}, "
                f"медиана {r['median_q_total']:.1f} тыс.м³/сут."
            )
        if r.get("mean_q_working") is not None and r.get("median_q_working") is not None:
            parts.append(
                f"Рабочий дебит: среднее {r['mean_q_working']:.1f}, "
                f"медиана {r['median_q_working']:.1f} тыс.м³/сут."
            )

        # Перепад давления
        if r.get("mean_dp") is not None and r.get("median_dp") is not None:
            parts.append(
                f"Перепад давления: среднее {r['mean_dp']:.2f}, "
                f"медиана {r['median_dp']:.2f} кгс/см²."
            )

        # Устьевое давление (среднее)
        if r.get("mean_p_wellhead") is not None:
            parts.append(
                f"Среднее устьевое давление: {r['mean_p_wellhead']:.1f} кгс/см²."
            )

        # Простой
        if mean_sd is not None:
            sd_hr_txt = (
                f", всего около {sd_hours_total:.1f} часов за месяц"
                if sd_hours_total is not None and sd_hours_total > 0 else ""
            )
            parts.append(
                f"Простой: в среднем {float(mean_sd):.0f} мин/сут{sd_hr_txt}."
            )

        # Тренд общего дебита
        slope = r.get("trend_q_total")
        if slope is None:
            parts.append("Тренд по дебиту определить нельзя — мало данных.")
        elif abs(slope) < 0.05:
            parts.append(
                f"Тренд по общему дебиту плоский ({slope:+.2f} тыс.м³/сут в день)."
            )
        elif slope > 0:
            parts.append(
                f"По общему дебиту наблюдается рост ({slope:+.2f} тыс.м³/сут в день)."
            )
        else:
            parts.append(
                f"По общему дебиту наблюдается снижение ({slope:+.2f} тыс.м³/сут в день)."
            )

        # Сравнение с предыдущим месяцем
        if prev is not None:
            cmp_parts = []
            for nm, key, unit in (
                ("общий дебит",     "mean_q_total",   " тыс.м³/сут"),
                ("рабочий дебит",   "mean_q_working", " тыс.м³/сут"),
                ("перепад давления", "mean_dp",        " кгс/см²"),
                ("простой",         "mean_shutdown",  " мин/сут"),
            ):
                s = _cmp(r.get(key), prev.get(key), unit, nm)
                if s:
                    cmp_parts.append(s)
            if cmp_parts:
                parts.append(
                    "Изменение к предыдущему месяцу: " + "; ".join(cmp_parts) + "."
                )
        else:
            parts.append(
                "Первый месяц в выборке — базовый ориентир для сравнения."
            )

        out.append({"label": r["month_label"], "text": " ".join(parts)})
        prev = r
    return out


def period_summary_text(
    analysis: dict[str, Any], months: list[dict[str, Any]] | None = None,
) -> str:
    """Короткая сводка по периоду (1–2 предложения) с ключевыми числами.

    Используется как вступительный текст к подразделу периода в PDF-отчёте.

    Структура автоописания (ТЗ §3 раздел 2):
    «Анализ периода YYYY-MM-DD … YYYY-MM-DD на основании данных заказчика.
    N сут. Тип данных: суточные сводки. За период доступно N сут. Q общий: ...»
    """
    parts: list[str] = []
    days = analysis.get("days_count") or 0
    pf = analysis.get("period_from")
    pt = analysis.get("period_to")
    # Шапка-описание раздела 2 (формализованный заголовок-блок)
    if pf and pt:
        parts.append(
            f"Анализ периода {pf} … {pt} на основании данных заказчика "
            f"(суточные сводки УзКорГаз, {days} сут.). "
            f"Параметры: устьевое, затрубное, шлейфовое и статическое "
            f"давление; общий и рабочий дебиты газа; простой."
        )
    else:
        parts.append(f"За период доступно {days} сут.")

    if analysis.get("q_total_avg") is not None:
        q_avg = analysis["q_total_avg"]; q_med = analysis.get("q_total_median")
        if q_med is not None:
            parts.append(
                f"Q общий: ср. {q_avg:.1f}, мед. {q_med:.1f} тыс.м³/сут."
            )
        else:
            parts.append(f"Q общий ср.: {q_avg:.1f} тыс.м³/сут.")
    if analysis.get("q_working_avg") is not None:
        qw_avg = analysis["q_working_avg"]; qw_med = analysis.get("q_working_median")
        if qw_med is not None:
            parts.append(
                f"Q рабочий: ср. {qw_avg:.1f}, мед. {qw_med:.1f} тыс.м³/сут."
            )
    if analysis.get("dp_median") is not None:
        dp_med = analysis["dp_median"]; dp_avg = analysis.get("dp_avg")
        if dp_avg is not None:
            parts.append(f"ΔP: ср. {dp_avg:.2f}, мед. {dp_med:.2f} кгс/см².")
        else:
            parts.append(f"ΔP мед.: {dp_med:.2f} кгс/см².")
    if analysis.get("shutdown_min_total") is not None:
        sd_total = float(analysis["shutdown_min_total"])
        sd_days = analysis.get("shutdown_days_count") or 0
        if sd_total > 0:
            parts.append(
                f"Простой: суммарно {sd_total:.0f} мин (~{sd_total/60:.1f} ч) "
                f"за {sd_days} сут."
            )
        else:
            parts.append("Простоев за период не зафиксировано.")

    # Тренд Q (если есть q_trend от compute_period_analysis)
    qt = analysis.get("q_trend") or {}
    slope = qt.get("slope_per_day")
    if slope is not None:
        if abs(slope) < 0.05:
            parts.append(f"Тренд Q рабочий — плоский ({slope:+.2f}/день).")
        elif slope > 0:
            parts.append(f"Тренд Q рабочий — рост ({slope:+.2f} тыс.м³/сут за день).")
        else:
            parts.append(f"Тренд Q рабочий — снижение ({slope:+.2f} тыс.м³/сут за день).")

    # Лучший/худший месяц по Q общему среднему
    if months and len(months) >= 2:
        with_q = [m for m in months if m.get("mean_q_total") is not None]
        if len(with_q) >= 2:
            best = max(with_q, key=lambda m: m["mean_q_total"])
            worst = min(with_q, key=lambda m: m["mean_q_total"])
            if best is not worst:
                parts.append(
                    f"Лучший месяц: {best['month_label']} "
                    f"(Q общ. ср. {best['mean_q_total']:.1f}); "
                    f"худший: {worst['month_label']} "
                    f"({worst['mean_q_total']:.1f} тыс.м³/сут)."
                )

    return " ".join(parts)


def well_chart_payload(df: pd.DataFrame) -> dict[str, Any]:
    """Готовый payload для построения Plotly-графиков на фронтенде."""
    if df.empty:
        return {
            "dates": [], "p_wellhead": [], "p_annular": [], "p_flowline": [],
            "p_static": [], "q_gas_total": [], "q_gas_working": [],
            "shutdown_min": [], "dp": [],
        }
    dates = [d.date().isoformat() if isinstance(d, pd.Timestamp) else str(d)
             for d in df["date"]]

    def _col(name: str) -> list[float | None]:
        if name not in df.columns:
            return [None] * len(df)
        return [_fmt_float(v) for v in df[name].to_list()]

    dp = (df["p_wellhead"] - df["p_flowline"]) if (
        "p_wellhead" in df.columns and "p_flowline" in df.columns
    ) else pd.Series([None] * len(df))
    return {
        "dates":         dates,
        "p_wellhead":    _col("p_wellhead"),
        "p_annular":     _col("p_annular"),
        "p_flowline":    _col("p_flowline"),
        "p_static":      _col("p_static"),
        "q_gas_total":   _col("q_gas_total"),
        "q_gas_working": _col("q_gas_working"),
        "shutdown_min":  _col("shutdown_min"),
        "dp":            [_fmt_float(v) for v in dp.to_list()],
    }
