"""Роутер «Анализ данных заказчика».

Источник — суточные сводки УзКорГаз (xlsx). Всё хранится в таблице `well_daily`.
Открывается в отдельном окне из отчёта об адаптации.

Эндпоинты:
    GET  /customer-daily               — HTML-страница анализа
    GET  /api/customer-daily/meta      — общая статистика хранилища
    GET  /api/customer-daily/wells     — список скважин с диапазонами дат
    GET  /api/customer-daily/well/{w}  — данные + аналитика по одной скважине
    GET  /api/customer-daily/coverage  — какие даты периода уже есть
    POST /api/customer-daily/upload-check — парсит xlsx, возвращает дубликаты
                                            (ничего не пишет в БД)
    POST /api/customer-daily/upload    — парсит xlsx и пишет в БД
                                          (overwrite=true|false)
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import time as time_module
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile,
)
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from backend.db import SessionLocal
from backend.deps import get_current_user
from backend.services import customer_daily_service as svc

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/customer-daily", tags=["customer-daily"])
pages_router = APIRouter(tags=["customer-daily-pages"])

templates = Jinja2Templates(directory="backend/templates")
templates.env.globals["time"] = lambda: int(time_module.time())


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_with_table():
    """Зависимость FastAPI: сессия + гарантия наличия таблицы well_daily.

    Если миграция alembic не применена, создаст таблицу через CREATE TABLE
    IF NOT EXISTS (см. customer_daily_service.ensure_table).
    """
    db = SessionLocal()
    try:
        svc.ensure_table(db)
        yield db
    finally:
        db.close()


def get_db_with_blocks():
    """Сессия + гарантия таблицы customer_report_block."""
    db = SessionLocal()
    try:
        svc.ensure_table(db)
        svc.ensure_blocks_table(db)
        yield db
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════════════════
#  HTML-страница
# ═══════════════════════════════════════════════════════════════════════


@pages_router.get("/customer-daily", response_class=HTMLResponse)
def customer_daily_page(
    request: Request,
    well: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    embedded: int = 0,
    mode: str = "customer",  # 'customer' | 'observation'
    current_user: str = Depends(get_current_user),
):
    """Открывается в отдельном окне с предзаполненными скважиной/периодом.

    embedded=1 — режим встраивания в iframe: убирает шапку, nav и плавающие
    кнопки (см. base.html:if not embedded).

    mode='observation' — переключает страницу в режим B2 (этап наблюдения):
    меняет заголовки, акцент на UniTool как первичный источник, кнопка
    «Утвердить как B1» становится «Утвердить как B2». Используется
    в визарде на шаге 3.
    """
    if mode not in {"customer", "observation"}:
        mode = "customer"
    return templates.TemplateResponse(
        "customer_daily.html",
        {
            "request": request,
            "current_user": current_user,
            "is_admin": request.session.get("is_admin", False),
            "embedded": bool(embedded),
            "page_mode": mode,
            "preset_well": well,
            "preset_from": date_from,
            "preset_to": date_to,
        },
    )


# ═══════════════════════════════════════════════════════════════════════
#  API: чтение
# ═══════════════════════════════════════════════════════════════════════


@router.get("/meta")
def api_meta(db: Session = Depends(get_db_with_table)):
    return svc.get_dataset_meta(db)


@router.get("/wells")
def api_wells(db: Session = Depends(get_db_with_table)):
    return {"wells": svc.get_wells(db)}


@router.get("/well/{well}")
def api_well(
    well: str,
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    db: Session = Depends(get_db_with_table),
):
    """Данные + аналитика по одной скважине за период."""
    d_from = date.fromisoformat(date_from) if date_from else None
    d_to = date.fromisoformat(date_to) if date_to else None

    df = svc.load_for_well(db, well, d_from=d_from, d_to=d_to)
    if df.empty:
        return {
            "ok": True, "well": well, "ggu": None, "rows": 0,
            "records": [], "chart": svc.well_chart_payload(df),
            "describe": [], "monthly": [], "monthly_desc": [],
        }
    ggu = str(df["ggu"].iloc[0])
    months = svc.monthly_stats(df)
    return {
        "ok": True,
        "well": str(well),
        "ggu": ggu,
        "rows": int(len(df)),
        "date_min": df["date"].min().date().isoformat(),
        "date_max": df["date"].max().date().isoformat(),
        "records": svc.df_to_records(df),
        "chart": svc.well_chart_payload(df),
        "describe": svc.describe_well_period(df),
        "monthly": months,
        "monthly_desc": svc.monthly_description(months),
    }


@router.get("/coverage")
def api_coverage(
    well: str = Query(...),
    date_from: str = Query(...),
    date_to: str = Query(...),
    db: Session = Depends(get_db_with_table),
):
    d_from = date.fromisoformat(date_from)
    d_to = date.fromisoformat(date_to)
    if d_from > d_to:
        raise HTTPException(400, "date_from > date_to")
    return svc.coverage(db, well, d_from, d_to)


@router.get("/availability/{well}")
def api_availability(
    well: str,
    db: Session = Depends(get_db_with_table),
):
    """Что есть в БД по этой скважине (любой период, во всём хранилище)."""
    return svc.well_availability(db, well)


@router.get("/our-data/{well}")
def api_our_data(
    well: str,
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    db: Session = Depends(get_db_with_table),
):
    """Наши суточные данные (pressure_hourly + flow_result) для сравнения.

    Параметр `well` — НОМЕР скважины (как в well_daily). Сами наши таблицы
    используют wells.id, поиск делает svc.find_well (строгий match).
    """
    w = svc.find_well(db, well)
    if w is None:
        return {
            "ok": False, "error": f"Скв. №{well} не найдена в таблице wells",
            "well_id": None, "well": None,
            "pressure": {}, "flow": {},
            "has_pressure": False, "has_flow": False,
        }
    d_from = date.fromisoformat(date_from) if date_from else None
    d_to = date.fromisoformat(date_to) if date_to else None
    data = svc.our_daily_data(db, w["id"], d_from=d_from, d_to=d_to)
    data["ok"] = True
    return data


@router.get("/resolve/{well}")
def api_resolve(well: str, db: Session = Depends(get_db_with_table)):
    """Какая `wells`-запись сопоставилась номеру `well` из well_daily.

    Используется фронтом для вывода «Сопоставлено: id=X, name=Y»
    (чтобы пользователь мог визуально проверить, что наш well_id ≠ чужой).
    """
    w = svc.find_well(db, well)
    return {"ok": w is not None, "well": w, "input": well}


@router.get("/series")
def api_series(
    source: str = Query(..., description="customer|our_pressure|our_flow"),
    well: str = Query(...),
    metric: str = Query(..., description="dp|q_total|q_working|p_wellhead|p_flowline"),
    date_from: str = Query(...),
    date_to: str = Query(...),
    db: Session = Depends(get_db_with_table),
):
    """Единая точка для получения временного ряда (любой источник, любая метрика).

    Используется UI «Сравнение периодов»: А-период и B-период строятся
    двумя независимыми вызовами.
    """
    d_from = date.fromisoformat(date_from)
    d_to = date.fromisoformat(date_to)
    if d_from > d_to:
        raise HTTPException(400, "date_from > date_to")
    return svc.time_series(
        db,
        source=source, well=well, metric=metric,
        d_from=d_from, d_to=d_to,
    )


# ═══════════════════════════════════════════════════════════════════════
#  API: блоки отчёта (customer_report_block)
# ═══════════════════════════════════════════════════════════════════════


from pydantic import BaseModel as _BM
from typing import Any as _Any


class BlockCreate(_BM):
    well_id: int
    kind: str  # 'baseline' | 'period_analysis' | 'comparison'
    title: str
    params: dict[str, _Any] | None = None
    data_snapshot: dict[str, _Any] | None = None
    comment: str | None = None
    in_report: bool = True


class BlockUpdate(_BM):
    title: str | None = None
    params: dict[str, _Any] | None = None
    comment: str | None = None
    in_report: bool | None = None
    sort_order: int | None = None
    data_snapshot: dict[str, _Any] | None = None


@router.get("/blocks")
def api_list_blocks(
    well_id: int = Query(...),
    db: Session = Depends(get_db_with_blocks),
):
    """Все блоки скважины для отчёта об адаптации."""
    return {"blocks": svc.list_blocks(db, well_id)}


@router.get("/blocks/count")
def api_blocks_count(
    well_id: int = Query(...),
    db: Session = Depends(get_db_with_blocks),
):
    """Сколько блоков с in_report=True (для индикатора)."""
    return {"well_id": well_id, "in_report": svc.count_blocks_in_report(db, well_id)}


@router.post("/blocks")
def api_create_block(
    body: BlockCreate,
    db: Session = Depends(get_db_with_blocks),
):
    if body.kind not in svc.VALID_BLOCK_KINDS:
        raise HTTPException(
            400, f"kind должен быть one of {sorted(svc.VALID_BLOCK_KINDS)}",
        )
    try:
        return svc.create_block(
            db,
            well_id=body.well_id, kind=body.kind, title=body.title,
            params=body.params, comment=body.comment, in_report=body.in_report,
            data_snapshot=body.data_snapshot,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/blocks/{block_id}")
def api_update_block(
    block_id: int,
    body: BlockUpdate,
    db: Session = Depends(get_db_with_blocks),
):
    if svc.get_block(db, block_id) is None:
        raise HTTPException(404, f"Block {block_id} not found")
    return svc.update_block(
        db, block_id,
        title=body.title, params=body.params, comment=body.comment,
        in_report=body.in_report, sort_order=body.sort_order,
        data_snapshot=body.data_snapshot,
    )


@router.delete("/blocks/{block_id}")
def api_delete_block(
    block_id: int,
    db: Session = Depends(get_db_with_blocks),
):
    if not svc.delete_block(db, block_id):
        raise HTTPException(404, f"Block {block_id} not found")
    return {"ok": True, "deleted_id": block_id}


# ═══════════════════════════════════════════════════════════════════════
#  API: загрузка xlsx
# ═══════════════════════════════════════════════════════════════════════


def _save_temp(upload: UploadFile) -> Path:
    """Сохранить UploadFile во временный файл и вернуть путь."""
    if not upload.filename:
        raise HTTPException(400, "Файл не передан")
    suffix = Path(upload.filename).suffix or ".xlsx"
    fd = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="uzkor_")
    try:
        shutil.copyfileobj(upload.file, fd)
    finally:
        fd.close()
    return Path(fd.name)


@router.post("/upload-check")
def api_upload_check(
    file: UploadFile = File(...),
    db: Session = Depends(get_db_with_table),
):
    """Распарсить файл и вернуть список (date, ggu, well), уже существующих в БД.

    Сам upsert НЕ выполняется — фронт должен показать диалог
    «перезаписать / добавить только новые / отмена», после чего вызвать /upload.
    """
    tmp = _save_temp(file)
    try:
        df = svc.parse_xlsx(tmp)
        if df.empty:
            raise HTTPException(400, "Парсер не извлёк ни одной строки")
        dups = svc.find_duplicates(db, df)
        return {
            "ok": True,
            "filename": file.filename,
            "rows": int(len(df)),
            "wells": int(df["well"].nunique()),
            "ggus": int(df["ggu"].nunique()),
            "date_min": str(df["date"].min()),
            "date_max": str(df["date"].max()),
            "duplicates_count": len(dups),
            "duplicates_sample": dups[:30],
        }
    except HTTPException:
        raise
    except Exception as e:
        log.exception("upload-check failed")
        raise HTTPException(500, f"Ошибка обработки файла: {e}") from e
    finally:
        tmp.unlink(missing_ok=True)


@router.post("/upload")
def api_upload(
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
    db: Session = Depends(get_db_with_table),
):
    """Парсит xlsx и пишет в БД.

    overwrite=true  → существующие строки (date, ggu, well) перезаписываются;
    overwrite=false → дубликаты пропускаются (DO NOTHING).
    """
    tmp = _save_temp(file)
    try:
        result = svc.ingest_xlsx(
            db, tmp,
            overwrite_duplicates=overwrite,
            source_file=file.filename,
        )
        return {
            "ok": True,
            "filename": file.filename,
            "overwrite": overwrite,
            "rows": result.rows,
            "wells": result.wells,
            "ggus": result.ggus,
            "date_min": result.date_min,
            "date_max": result.date_max,
            "inserted": result.inserted,
            "updated": result.updated,
            "skipped": result.skipped,
            "duplicates_count": len(result.duplicates),
            "warnings": result.warnings,
        }
    except HTTPException:
        raise
    except Exception as e:
        log.exception("upload failed")
        raise HTTPException(500, f"Ошибка загрузки: {e}") from e
    finally:
        tmp.unlink(missing_ok=True)
