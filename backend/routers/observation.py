"""
Observation Router — Phase C4.

Prefix: /api/observation. Tags: ["observation"].

9 эндпоинтов:
  POST /preview/baseline        — preview baseline snapshot
  POST /preview/period          — preview period snapshot
  POST /preview/segment         — preview segment snapshot
  POST /chapter-preview         — HTML preview главы
  GET  /blocks                  — список блоков (без data_snapshot)
  GET  /blocks/{block_id}       — блок по id (с sanitized snapshot)
  POST /blocks                  — создать блок
  PUT  /blocks/{block_id}       — обновить title/comment/in_report/sort_order
  DELETE /blocks/{block_id}     — удалить блок

ОГРАНИЧЕНИЯ:
  - Router НЕ содержит расчётов
  - Router НЕ генерирует HTML — только renderer
  - Router НЕ импортирует matplotlib/plotly
  - CRUD только для kind ∈ {observation_baseline/period/segment}
  - params['source'] = 'observation' автоматически в POST /blocks
  - Фильтр params->>'source' = 'observation' во всех CRUD
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.constants.observation_parts import OBSERVATION_PARTS
from backend.db import get_db
from backend.models.customer_report_block import CustomerReportBlock
from backend.services.observation_baseline_service import compute_baseline_preview
from backend.services.observation_chapter_renderer import (
    render_observation_chapter,
    render_wz3obs_v1_html,
)
from backend.services.observation_period_service import compute_period_preview
from backend.services.observation_segment_service import compute_segment_preview
from backend.services.observation_snapshot_service import (
    classify_block_status,
    load_and_validate_snapshot,
    validate_snapshot_structure,
)

router = APIRouter(prefix="/api/observation", tags=["observation"])
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class PeriodRequest(BaseModel):
    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")
    model_config = ConfigDict(populate_by_name=True)

    @field_validator("to_date")
    @classmethod
    def check_order(cls, v: date, info: Any) -> date:
        if info.data.get("from_date") and v < info.data["from_date"]:
            raise ValueError("period.from must be <= period.to")
        return v


class CustomerPeriodRequest(BaseModel):
    from_date: Optional[date] = Field(None, alias="from")
    to_date: Optional[date] = Field(None, alias="to")
    use_same_as_analysis: bool = True
    model_config = ConfigDict(populate_by_name=True)


class BaselinePreviewRequest(BaseModel):
    well_id: int = Field(gt=0)
    period: PeriodRequest
    sensor_source: Literal["lora"] = "lora"
    comment: Optional[str] = None
    include_raw_chart: bool = False


class PeriodPreviewRequest(BaseModel):
    well_id: int = Field(gt=0)
    period: PeriodRequest
    baseline_block_id: Optional[int] = None
    customer_period: Optional[CustomerPeriodRequest] = None
    include_raw_chart: bool = True


class SegmentPreviewRequest(BaseModel):
    well_id: int = Field(gt=0)
    period: PeriodRequest
    aggregation: Literal["daily", "12h", "6h", "hourly"] = "daily"
    sensitivity: Literal["low", "medium", "high", "custom"] = "medium"
    min_segment_days: Optional[int] = Field(None, ge=2)
    min_change_pct: Optional[float] = Field(None, ge=1.0)
    smoothing_window: Optional[int] = Field(None, ge=1)
    ignore_shutdown_days: bool = True
    ignore_purge_window_hours: int = Field(default=24, ge=0)
    include_raw_chart: bool = True


class ChapterPreviewRequest(BaseModel):
    well_id: int = Field(gt=0)


class CreateBlockRequest(BaseModel):
    well_id: int = Field(gt=0)
    kind: Literal["observation_baseline", "observation_period", "observation_segment"]
    title: str
    params: dict
    data_snapshot: dict
    comment: Optional[str] = None
    in_report: bool = True
    sort_order: int = 0


class UpdateBlockRequest(BaseModel):
    title: Optional[str] = None
    comment: Optional[str] = None
    in_report: Optional[bool] = None
    sort_order: Optional[int] = None
    # Phase O1: частичное обновление params. Применяются ТОЛЬКО ключи
    # parts / prefix_note / suffix_note (whitelist-merge в update_block).
    # source / chapter / kind / data_snapshot защищены от перезаписи.
    params: Optional[dict] = None


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------


def _error_payload(
    code: str,
    message: str,
    details: Optional[dict] = None,
) -> dict:
    return {"ok": False, "error_code": code, "message": message, "details": details}


def _raise_http(
    status_code: int,
    code: str,
    message: str,
    details: Optional[dict] = None,
) -> None:
    raise HTTPException(
        status_code=status_code,
        detail=_error_payload(code, message, details),
    )


# ---------------------------------------------------------------------------
# Phase O1 — частичное обновление params (whitelist-merge)
# ---------------------------------------------------------------------------

# Единственные ключи params, которые разрешено менять через PUT /blocks/{id}.
_ALLOWED_PARAM_KEYS = frozenset({"parts", "prefix_note", "suffix_note"})


def _merge_observation_params(
    existing: dict,
    kind: str,
    incoming: dict,
    block_id: int,
) -> dict:
    """Сливает частичное обновление params блока.

    Разрешено менять ТОЛЬКО parts / prefix_note / suffix_note. Остальные
    ключи `incoming` (source / chapter / kind / period / well_id / …)
    игнорируются — они защищены от перезаписи. data_snapshot через PUT
    не трогается вовсе (отдельная колонка, не входит в params).

    parts фильтруется по каталогу OBSERVATION_PARTS[kind]: неизвестные
    для данного kind part-ключи отбрасываются (forward-compat, без 400).

    Raises:
        HTTPException 400 — неверные типы parts / prefix_note / suffix_note.
    """
    merged = dict(existing) if isinstance(existing, dict) else {}

    extra = set(incoming.keys()) - _ALLOWED_PARAM_KEYS
    if extra:
        log.warning(
            "update_block %s: PUT params содержит защищённые/неизвестные "
            "ключи %s — проигнорированы", block_id, sorted(extra),
        )

    for key in ("prefix_note", "suffix_note"):
        if key in incoming:
            val = incoming[key]
            if val is not None and not isinstance(val, str):
                _raise_http(
                    400, "invalid_params",
                    f"params.{key} должен быть строкой",
                )
            merged[key] = val or ""

    if "parts" in incoming:
        parts_in = incoming["parts"]
        if not isinstance(parts_in, dict):
            _raise_http(
                400, "invalid_params",
                "params.parts должен быть объектом {part: bool}",
            )
        catalog = set(OBSERVATION_PARTS.get(kind, ()))
        clean: dict = {}
        for k, v in parts_in.items():
            if not isinstance(v, bool):
                _raise_http(
                    400, "invalid_params",
                    f"params.parts.{k} должен быть boolean",
                )
            if catalog and k not in catalog:
                log.warning(
                    "update_block %s: неизвестный part-ключ %r для kind %s "
                    "— отброшен", block_id, k, kind,
                )
                continue
            clean[k] = v
        merged["parts"] = clean

    return merged


# ---------------------------------------------------------------------------
# Endpoints: preview
# ---------------------------------------------------------------------------


@router.post("/preview/baseline")
def preview_baseline(req: BaselinePreviewRequest, db: Session = Depends(get_db)):
    """Вычисляет preview для блока observation_baseline. Не пишет в БД."""
    try:
        snapshot = compute_baseline_preview(
            db=db,
            well_id=req.well_id,
            d_from=req.period.from_date,
            d_to=req.period.to_date,
            sensor_source=req.sensor_source,
            comment=req.comment,
            include_raw_chart=req.include_raw_chart,
        )
        return {"ok": True, "snapshot": snapshot}
    except ValueError as e:
        _raise_http(400, "invalid_params", str(e))
    except HTTPException:
        raise
    except Exception:
        log.exception("preview/baseline failed for well=%s", req.well_id)
        _raise_http(500, "internal_error", "internal server error")


@router.post("/preview/period")
def preview_period(req: PeriodPreviewRequest, db: Session = Depends(get_db)):
    """Вычисляет preview для блока observation_period. Не пишет в БД."""
    # Конвертируем CustomerPeriodRequest → dict для C1
    customer_period_dict: dict | None = None
    if req.customer_period is not None:
        cp = req.customer_period
        customer_period_dict = {
            "use_same_as_analysis": cp.use_same_as_analysis,
        }
        if cp.from_date is not None:
            customer_period_dict["from"] = cp.from_date.isoformat()
        if cp.to_date is not None:
            customer_period_dict["to"] = cp.to_date.isoformat()

    try:
        snapshot = compute_period_preview(
            db=db,
            well_id=req.well_id,
            d_from=req.period.from_date,
            d_to=req.period.to_date,
            baseline_block_id=req.baseline_block_id,
            customer_period=customer_period_dict,
            include_raw_chart=req.include_raw_chart,
        )
        return {"ok": True, "snapshot": snapshot}
    except ValueError as e:
        _raise_http(400, "invalid_params", str(e))
    except HTTPException:
        raise
    except Exception:
        log.exception("preview/period failed for well=%s", req.well_id)
        _raise_http(500, "internal_error", "internal server error")


@router.post("/preview/segment")
def preview_segment(req: SegmentPreviewRequest, db: Session = Depends(get_db)):
    """Вычисляет preview для блока observation_segment. Не пишет в БД."""
    # Собираем kwargs для C2 — только не-None кастомные параметры
    segment_kwargs: dict = {
        "aggregation":               req.aggregation,
        "sensitivity":               req.sensitivity,
        "ignore_shutdown_days":      req.ignore_shutdown_days,
        "ignore_purge_window_hours": req.ignore_purge_window_hours,
        "include_raw_chart":         req.include_raw_chart,
    }
    if req.min_segment_days is not None:
        segment_kwargs["min_segment_days"] = req.min_segment_days
    if req.min_change_pct is not None:
        segment_kwargs["min_change_pct"] = req.min_change_pct
    if req.smoothing_window is not None:
        segment_kwargs["smoothing_window"] = req.smoothing_window

    try:
        snapshot = compute_segment_preview(
            db=db,
            well_id=req.well_id,
            d_from=req.period.from_date,
            d_to=req.period.to_date,
            **segment_kwargs,
        )
        return {"ok": True, "snapshot": snapshot}
    except ValueError as e:
        _raise_http(400, "invalid_params", str(e))
    except HTTPException:
        raise
    except Exception:
        log.exception("preview/segment failed for well=%s", req.well_id)
        _raise_http(500, "internal_error", "internal server error")


# ---------------------------------------------------------------------------
# Endpoints: chapter-preview
# ---------------------------------------------------------------------------


@router.post("/chapter-preview")
def chapter_preview(req: ChapterPreviewRequest, db: Session = Depends(get_db)):
    """
    Собирает HTML preview главы «Наблюдение» для скважины.

    Загружает все observation-блоки, валидирует, передаёт в renderer.
    Corrupted блоки НЕ вызывают 409 — добавляются warnings, renderer
    показывает badge corrupted.
    """
    try:
        rows = db.execute(
            text("""
                SELECT id, kind, title, data_snapshot, params, comment
                FROM customer_report_block
                WHERE well_id = :wid
                  AND (params->>'source' = 'observation' OR params->>'chapter' = 'observation')
                  AND in_report = true
                ORDER BY sort_order, created_at
            """),
            {"wid": req.well_id},
        ).fetchall()

        # RFC-блоки (observation_baseline/period/segment) рендерятся через
        # render_observation_chapter. Legacy-блоки observation_analysis
        # (плоская схема wz3obs_v1) — напрямую через render_wz3obs_v1_html.
        # Блоки segment_analysis/segment_comparison (из customer_daily виджета)
        # проходят без observation-специфичной валидации.
        rfc_blocks: list[dict] = []
        legacy_html: dict[int, str] = {}
        order: list[int] = []
        warnings: list[str] = []

        # Kinds, которые рендерятся напрямую без observation-валидации
        PASSTHROUGH_KINDS = {"segment_analysis", "segment_comparison"}

        for row in rows:
            block_id, kind, title, snapshot = row[0], row[1], row[2], row[3]
            params = row[4] if isinstance(row[4], dict) else {}
            comment = row[5] or ""
            order.append(block_id)
            is_legacy = (
                kind == "observation_analysis"
                or (isinstance(snapshot, dict)
                    and snapshot.get("_v") == "wz3obs_v1")
            )
            if is_legacy:
                legacy_html[block_id] = render_wz3obs_v1_html(
                    snapshot or {}, title or ""
                )
                continue

            # segment_analysis / segment_comparison — passthrough без валидации
            if kind in PASSTHROUGH_KINDS:
                rfc_blocks.append({
                    "block_id":     block_id,
                    "kind":         kind,
                    "title":        title or "",
                    "snapshot":     snapshot if isinstance(snapshot, dict) else {},
                    "block_status": "ok",  # предполагаем OK, renderer обработает ошибки
                    "sort_order":   0,
                    "params":       params,
                    "comment":      comment,
                })
                continue

            try:
                result = load_and_validate_snapshot(db, block_id)
                rfc_blocks.append({
                    "block_id":     block_id,
                    "kind":         result.expected_kind,
                    "title":        title or "",
                    "snapshot":     result.snapshot,
                    "block_status": result.block_status,
                    "sort_order":   0,
                    # Phase O1 — parts/notes/comment для гейтинга в renderer.
                    "params":       params,
                    "comment":      comment,
                })
                if result.validation_warnings:
                    warnings.extend(
                        [f"block {block_id}: {w}" for w in result.validation_warnings]
                    )
            except ValueError as e:
                warnings.append(f"block {block_id} skipped: {e}")

        # Собираем HTML в исходном порядке блоков.
        rfc_by_id = {b["block_id"]: b for b in rfc_blocks}
        parts: list[str] = ['<section class="obs-chapter">']
        for block_id in order:
            if block_id in legacy_html:
                parts.append(legacy_html[block_id])
            elif block_id in rfc_by_id:
                # render_observation_chapter оборачивает в <section> —
                # рендерим по одному блоку и срезаем внешнюю обёртку.
                inner = render_observation_chapter([rfc_by_id[block_id]])
                inner = inner.removeprefix('<section class="obs-chapter">')
                inner = inner.removesuffix("</section>")
                parts.append(inner.strip())
        parts.append("</section>")

        blocks_count = len(legacy_html) + len(rfc_blocks)
        return {
            "ok":           True,
            "html":         "\n".join(parts) if blocks_count else "",
            "blocks_count": blocks_count,
            "warnings":     warnings,
        }
    except HTTPException:
        raise
    except Exception:
        log.exception("chapter-preview failed for well=%s", req.well_id)
        _raise_http(500, "internal_error", "internal server error")


# ---------------------------------------------------------------------------
# Endpoints: CRUD — list
# ---------------------------------------------------------------------------


@router.get("/blocks")
def list_blocks(
    well_id: int = Query(..., gt=0),
    db: Session = Depends(get_db),
):
    """Список observation-блоков для скважины.

    params включён в ответ (parts / prefix_note / suffix_note) — нужен UI
    конструктора, чтобы отразить сохранённое состояние карточек блоков.
    data_snapshot включён для отображения метрик в карточках.
    """
    rows = db.execute(
        text("""
            SELECT id, kind, title, comment, in_report, sort_order,
                   created_at, updated_at, params, data_snapshot
            FROM customer_report_block
            WHERE well_id = :wid
              AND (params->>'source' = 'observation' OR params->>'chapter' = 'observation')
            ORDER BY sort_order, created_at
        """),
        {"wid": well_id},
    ).fetchall()

    items = []
    for r in rows:
        items.append({
            "block_id":      r[0],
            "kind":          r[1],
            "title":         r[2],
            "comment":       r[3],
            "in_report":     r[4],
            "sort_order":    r[5],
            "created_at":    r[6].isoformat() if r[6] else None,
            "updated_at":    r[7].isoformat() if r[7] else None,
            "params":        r[8] if isinstance(r[8], dict) else {},
            "data_snapshot": r[9] if isinstance(r[9], dict) else {},
        })
    return {"ok": True, "items": items, "total": len(items)}


# ---------------------------------------------------------------------------
# Endpoints: CRUD — get by id
# ---------------------------------------------------------------------------


@router.get("/blocks/{block_id}")
def get_block(block_id: int, db: Session = Depends(get_db)):
    """
    Загружает и валидирует observation-блок по id.

    Returns:
        200 — sanitized snapshot + metadata
        404 — блок не найден или не observation
        409 — блок corrupted (block_status='corrupted')
    """
    try:
        result = load_and_validate_snapshot(db, block_id)
    except ValueError as e:
        _raise_http(404, "not_found", str(e))

    if result.block_status == "corrupted":
        _raise_http(
            409,
            "block_corrupted",
            f"Block {block_id} has corrupted data snapshot",
            details={"validation_warnings": result.validation_warnings},
        )

    return {
        "ok":                    True,
        "block_id":              result.block_id,
        "kind":                  result.expected_kind,
        "block_status":          result.block_status,
        "can_recompute":         result.can_recompute,
        "recompute_params":      result.recompute_params,
        "snapshot":              result.snapshot,
        "validation_warnings":   result.validation_warnings,
    }


# ---------------------------------------------------------------------------
# Endpoints: CRUD — create
# ---------------------------------------------------------------------------


@router.post("/blocks", status_code=http_status.HTTP_201_CREATED)
def create_block(req: CreateBlockRequest, db: Session = Depends(get_db)):
    """
    Создаёт новый observation-блок.

    Валидирует data_snapshot через C3.validate_snapshot_structure.
    400 — invalid_schema, 409 — corrupted.
    Автоматически устанавливает params['source'] = 'observation'.
    """
    # Валидация структуры snapshot
    validation = validate_snapshot_structure(req.data_snapshot, expected_kind=req.kind)

    if validation.has_corrupted:
        _raise_http(
            409,
            "snapshot_corrupted",
            "data_snapshot contains corrupted values (NaN/Inf/invalid)",
            details={
                "issues": [
                    {"code": i.code, "path": i.path, "message": i.message}
                    for i in validation.issues
                    if i.severity == "corrupted"
                ]
            },
        )

    if validation.has_invalid_schema:
        _raise_http(
            400,
            "invalid_snapshot_schema",
            "data_snapshot has invalid schema (missing required fields or wrong types)",
            details={
                "issues": [
                    {"code": i.code, "path": i.path, "message": i.message}
                    for i in validation.issues
                    if i.severity == "invalid_schema"
                ]
            },
        )

    # Override source и chapter
    params = dict(req.params)
    params["source"] = "observation"
    params["chapter"] = "observation"

    try:
        row = db.execute(
            text("""
                INSERT INTO customer_report_block
                    (well_id, kind, title, params, data_snapshot, comment, in_report, sort_order)
                VALUES
                    (:wid, :kind, :title,
                     CAST(:params_json AS jsonb),
                     CAST(:snap_json AS jsonb),
                     :comment, :in_report, :sort_order)
                RETURNING id
            """),
            {
                "wid":        req.well_id,
                "kind":       req.kind,
                "title":      req.title,
                "params_json": json.dumps(params),
                "snap_json":  json.dumps(req.data_snapshot),
                "comment":    req.comment,
                "in_report":  req.in_report,
                "sort_order": req.sort_order,
            },
        ).fetchone()
        db.commit()
    except Exception:
        db.rollback()
        log.exception("create_block failed for well=%s kind=%s", req.well_id, req.kind)
        _raise_http(500, "internal_error", "failed to create block")

    return {"ok": True, "block_id": row[0]}


# ---------------------------------------------------------------------------
# Endpoints: CRUD — update
# ---------------------------------------------------------------------------


@router.put("/blocks/{block_id}")
def update_block(
    block_id: int,
    req: UpdateBlockRequest,
    db: Session = Depends(get_db),
):
    """
    Обновляет title / comment / in_report / sort_order блока.

    Phase O1: дополнительно принимает частичный `params` — применяются
    ТОЛЬКО ключи parts / prefix_note / suffix_note (whitelist-merge).
    data_snapshot и защищённые ключи params (source/chapter/kind/…)
    через PUT не изменяются.

    404 — блок не найден или не observation.
    400 — неверный тип params.parts / prefix_note / suffix_note.
    """
    # Проверяем что блок существует и является observation.
    # kind + params нужны для whitelist-merge параметров.
    existing = db.execute(
        text("""
            SELECT id, kind, params FROM customer_report_block
            WHERE id = :id AND params->>'source' = 'observation'
        """),
        {"id": block_id},
    ).fetchone()

    if not existing:
        _raise_http(404, "not_found", f"Observation block {block_id} not found")

    existing_kind = existing[1]
    existing_params = existing[2] if isinstance(existing[2], dict) else {}

    # Строим SET clause только для не-None полей
    set_parts: list[str] = ["updated_at = NOW()"]
    bind_params: dict = {"id": block_id}

    if req.title is not None:
        set_parts.append("title = :title")
        bind_params["title"] = req.title
    if req.comment is not None:
        set_parts.append("comment = :comment")
        bind_params["comment"] = req.comment
    if req.in_report is not None:
        set_parts.append("in_report = :in_report")
        bind_params["in_report"] = req.in_report
    if req.sort_order is not None:
        set_parts.append("sort_order = :sort_order")
        bind_params["sort_order"] = req.sort_order
    if req.params is not None:
        merged_params = _merge_observation_params(
            existing_params, existing_kind, req.params, block_id,
        )
        set_parts.append("params = CAST(:params_json AS jsonb)")
        bind_params["params_json"] = json.dumps(merged_params)

    if len(set_parts) == 1:
        # Только updated_at — ничего не менялось, но всё равно выполняем
        pass

    try:
        db.execute(
            text(f"UPDATE customer_report_block SET {', '.join(set_parts)} WHERE id = :id"),
            bind_params,
        )
        db.commit()
    except Exception:
        db.rollback()
        log.exception("update_block failed for block_id=%s", block_id)
        _raise_http(500, "internal_error", "failed to update block")

    return {"ok": True, "block_id": block_id}


# ---------------------------------------------------------------------------
# Endpoints: CRUD — delete
# ---------------------------------------------------------------------------


@router.delete("/blocks/{block_id}")
def delete_block(block_id: int, db: Session = Depends(get_db)):
    """
    Удаляет observation-блок.

    404 — блок не найден или не observation.
    """
    # Проверяем: source='observation' ИЛИ chapter='observation' (segment_analysis/comparison)
    existing = db.execute(
        text("""
            SELECT id FROM customer_report_block
            WHERE id = :id AND (
                params->>'source' = 'observation'
                OR params->>'chapter' = 'observation'
            )
        """),
        {"id": block_id},
    ).fetchone()

    if not existing:
        _raise_http(404, "not_found", f"Observation block {block_id} not found")

    try:
        db.execute(
            text("DELETE FROM customer_report_block WHERE id = :id"),
            {"id": block_id},
        )
        db.commit()
    except Exception:
        db.rollback()
        log.exception("delete_block failed for block_id=%s", block_id)
        _raise_http(500, "internal_error", "failed to delete block")

    return {"ok": True, "block_id": block_id, "deleted": True}
