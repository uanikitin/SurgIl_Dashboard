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
    APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile,
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
    current_user: str = Depends(get_current_user),
):
    """Открывается в отдельном окне с предзаполненными скважиной/периодом.

    embedded=1 — режим встраивания в iframe: убирает шапку, nav и плавающие
    кнопки (см. base.html:if not embedded).
    """
    return templates.TemplateResponse(
        "customer_daily.html",
        {
            "request": request,
            "current_user": current_user,
            "is_admin": request.session.get("is_admin", False),
            "embedded": bool(embedded),
            "preset_well": well,
            "preset_from": date_from,
            "preset_to": date_to,
        },
    )


@pages_router.get("/customer-daily/upload", response_class=HTMLResponse)
def customer_daily_upload_page(
    request: Request,
    back_well: str | None = None,
    current_user: str = Depends(get_current_user),
):
    """Отдельная страница для загрузки XLSX-файлов сводок заказчика.

    Вынесена с главной /customer-daily для разгрузки UI (см. TZ §11 этап 2).
    После успешной загрузки — ссылка-кнопка «← К анализу» возвращает на
    /customer-daily?well={back_well}.
    """
    return templates.TemplateResponse(
        "customer_upload.html",
        {
            "request": request,
            "current_user": current_user,
            "is_admin": request.session.get("is_admin", False),
            "back_well": back_well,
        },
    )


@pages_router.get("/customer-daily/preview-popout", response_class=HTMLResponse)
def customer_daily_preview_popout_page(
    request: Request,
    well: str | None = None,
    current_user: str = Depends(get_current_user),
):
    """Pop-out окно для просмотра live-превью главы (ТЗ §11 этап 9).

    Открывается из основной /customer-daily кнопкой «↗ В окно».
    Только для просмотра (HTML + PDF), без редактирования блоков.
    Синхронизируется с основной страницей через BroadcastChannel.
    """
    return templates.TemplateResponse(
        "customer_popout.html",
        {
            "request": request,
            "current_user": current_user,
            "is_admin": request.session.get("is_admin", False),
            "well": well,
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
#  API: роза критериев (preview-расчёт без сохранения)
# ═══════════════════════════════════════════════════════════════════════


from pydantic import BaseModel as _RoseBM


class RosePreviewRequest(_RoseBM):
    well: str
    period_from: date
    period_to: date
    mode: str = "balanced"  # liquid|gsp|purge_cycles|balanced|custom
    weights: dict[str, float] | None = None
    history_step_days: int = 1


@router.post("/rose/preview")
def api_rose_preview(
    req: RosePreviewRequest,
    db: Session = Depends(get_db_with_table),
):
    """Рассчитать розу критериев для (скважина, период, режим).

    Возвращает dict с current/history/ranks/contributions/score.
    Если расчёт невозможен (мало истории, бесштуцерные данные и т.п.) —
    `{"ok": False, "error": "..."}`. Если упало непредвиденно —
    `{"ok": False, "error": "..."}` (НЕ 500, чтобы UI показал текст).
    """
    from backend.services import customer_rose_service as rsvc
    try:
        if req.period_from > req.period_to:
            return {"ok": False, "error": "period_from > period_to"}
        if not str(req.well).strip():
            return {"ok": False, "error": "Не выбрана скважина"}
        return rsvc.compute_rose(
            db,
            well_number=str(req.well).strip(),
            period_from=req.period_from,
            period_to=req.period_to,
            mode=req.mode,
            weights=req.weights,
            history_step_days=req.history_step_days,
        )
    except Exception as e:
        log.exception(
            "rose/preview failed for well=%s period=%s..%s mode=%s",
            req.well, req.period_from, req.period_to, req.mode,
        )
        # Не бросаем 500 — возвращаем структуру с error, чтобы UI показал
        # пользователю текст ошибки, а не безликий HTTP 500.
        return {"ok": False, "error": f"Внутренняя ошибка: {e}"}


# ═══════════════════════════════════════════════════════════════════════
#  API: роза НЕСТАБИЛЬНОСТИ (новая — кандидаты на обводнение)
#  Период = якорная дата + скользящие окна назад. Источник:
#  backend/services/stability_rose_service.py
# ═══════════════════════════════════════════════════════════════════════


class StabilityRoseRequest(_RoseBM):
    well: str
    anchor: date | None = None        # момент оценки; None → последняя дата
    source: str = "well_daily"        # well_daily | lora (этап 2)
    window_days: int | None = None           # ручное окно динамики; None → авто (30)
    downtime_window_days: int | None = None  # ручное окно простоев; None → авто (90)


@router.post("/stability-rose/preview")
def api_stability_rose_preview(
    req: StabilityRoseRequest,
    db: Session = Depends(get_db_with_table),
):
    """Рассчитать розу нестабильности для (скважина, якорная дата, источник).

    Возвращает snapshot (petals/raw/contributions/L_star/index_I/descriptions).
    При невозможности расчёта — {ok: False, error: "..."} (НЕ 500).
    """
    from backend.services import stability_rose_service as srs
    try:
        if not str(req.well).strip():
            return {"ok": False, "error": "Не выбрана скважина"}
        return srs.compute_stability_rose(
            db, well_number=str(req.well).strip(),
            anchor=req.anchor, source=req.source, window_days=req.window_days,
            downtime_window_days=req.downtime_window_days,
        )
    except Exception as e:
        log.exception("stability-rose/preview failed for well=%s", req.well)
        return {"ok": False, "error": f"Внутренняя ошибка: {e}"}


# ═══════════════════════════════════════════════════════════════════════
#  API: анализ стабильности в период работ (before/during/after)
#  Источник: backend/services/works_stability_service.py
# ═══════════════════════════════════════════════════════════════════════


class WorksAnalysisRequest(_RoseBM):
    well: str
    work_from: date
    work_to: date
    baseline_days: int = 14
    work_type: str = "tpav"
    weight_profile: str = "tpav"
    custom_weights: dict | None = None
    ref_from: date | None = None
    ref_to: date | None = None
    source: str = "well_daily"


@router.post("/works-analysis/preview")
def api_works_analysis_preview(
    req: WorksAnalysisRequest,
    db: Session = Depends(get_db_with_table),
):
    """Рассчитать анализ стабильности в период работ (before/during/after).

    Возвращает snapshot с метриками трёх окон, интерпретацией и scores.
    При невозможности расчёта — {ok: False, error: "..."} (НЕ 500).
    """
    from backend.services.works_stability_service import compute_works_analysis
    try:
        if not str(req.well).strip():
            return {"ok": False, "error": "Не выбрана скважина"}
        if req.work_from > req.work_to:
            return {"ok": False, "error": "work_from > work_to"}
        return compute_works_analysis(
            db,
            well_number=str(req.well).strip(),
            work_from=req.work_from,
            work_to=req.work_to,
            baseline_days=req.baseline_days,
            work_type=req.work_type,
            weight_profile=req.weight_profile,
            custom_weights=req.custom_weights,
            ref_from=req.ref_from,
            ref_to=req.ref_to,
            source=req.source,
        )
    except Exception as e:
        log.exception("works-analysis/preview failed for well=%s", req.well)
        return {"ok": False, "error": f"Внутренняя ошибка: {e}"}


# ═══════════════════════════════════════════════════════════════════════
#  API: анализ эффективности работ v2 (роза за период + опц. сравнение)
#  Источник: backend/services/works_effectiveness_service.py
#  Поминутный пайплайн давления (compute_full_flow), НЕ well_daily.
# ═══════════════════════════════════════════════════════════════════════


class WorksEffectivenessRequest(_RoseBM):
    well: str
    period_from: date | None = None   # None → первый вброс
    period_to: date | None = None     # None → сейчас
    ref_from: date | None = None      # None → авто «до работ»
    ref_to: date | None = None
    compare: bool | None = None       # False=single; None/True=compare
    work_type: str = "tpav"


@router.post("/works-effectiveness/preview")
def api_works_effectiveness_preview(
    req: WorksEffectivenessRequest,
    db: Session = Depends(get_db_with_table),
):
    """Анализ эффективности работ за период (роза + описание; опц. сравнение).

    mode='single' → роза+описание за период (без Δ/Балла).
    mode='compare' → overlay-роза + Δ-таблица + Балл БЭР (ref по умолчанию = до работ).
    При невозможности — {ok: False, error: "..."} (НЕ 500).
    """
    from backend.services.works_effectiveness_service import compute_works_effectiveness
    try:
        if not str(req.well).strip():
            return {"ok": False, "error": "Не выбрана скважина"}
        if req.period_from and req.period_to and req.period_from > req.period_to:
            return {"ok": False, "error": "period_from > period_to"}
        return compute_works_effectiveness(
            db,
            well_number=str(req.well).strip(),
            period_from=req.period_from,
            period_to=req.period_to,
            ref_from=req.ref_from,
            ref_to=req.ref_to,
            compare=req.compare,
            work_type=req.work_type,
        )
    except Exception as e:
        log.exception("works-effectiveness/preview failed for well=%s", req.well)
        return {"ok": False, "error": f"Внутренняя ошибка: {e}"}


# ═══════════════════════════════════════════════════════════════════════
#  API: сегментный анализ + ПАВ-балл
#  ──────────────────────────────────
#  /segment-analysis/preview        — рассчитать без сохранения
#  /segment-thresholds              — GET текущие эффективные пороги
#  /segment-thresholds              — PUT user-overrides
#  /segment-thresholds/reset        — POST сбросить к дефолтам
#  Источник: backend/services/segment_analysis_service.py
#  Хранение настроек: backend/data/segment_thresholds.json (persist volume)
# ═══════════════════════════════════════════════════════════════════════

class SegmentPreviewRequest(_RoseBM):
    well: str
    period_from: date
    period_to: date
    # PLAN A: PAV layer optional. По умолчанию ОТКЛЮЧЁН для главы
    # «Анализ данных заказчика» (ядро = тренды/переломы/описания).
    # При True — будет вычислен _compute_pav_score и snapshot.pav заполнен.
    include_pav: bool = False
    # R1: experimental opt-in флаг — продвижение only_working CPs до boundaries.
    # Default OFF. Не применяется внутри shutdown_cluster и у краёв периода.
    # См. docs/contracts/segment_snapshot_contract.md и R1-VISUAL отчёт.
    promote_only_working: bool = False


@router.post("/segment-analysis/preview")
def api_segment_preview(
    req: SegmentPreviewRequest,
    db: Session = Depends(get_db_with_table),
):
    """Запустить Core Segment Analysis для (скважина, период).

    PLAN A: ПАВ-балл по умолчанию ОТКЛЮЧЁН (include_pav=False).
    Чтобы включить ПАВ-карточку — передать `include_pav: true` в теле POST.

    R1 (experimental): promote_only_working (default False). При True
    only_working changepoints (Q_working) промоутятся до boundaries сегментов
    при условии, что они не лежат внутри shutdown_cluster и не в edge-зоне.
    Trace сохраняется в dual_summary.r1_*.

    Не сохраняет результат — только возвращает snapshot. Сохранение
    происходит через POST /api/customer-daily/blocks с kind='segment_analysis'
    и передачей этого snapshot в data_snapshot.

    Возвращает структуру segment_v1 (см. compute_segment_block).
    На любой ошибке — {"ok": False, "error": "..."} (НЕ 500).
    """
    from backend.services.segment_analysis_service import compute_segment_block
    try:
        if req.period_from > req.period_to:
            return {"ok": False, "error": "period_from > period_to"}
        if not str(req.well).strip():
            return {"ok": False, "error": "Не выбрана скважина"}
        return compute_segment_block(
            db,
            well=str(req.well).strip(),
            d_from=req.period_from,
            d_to=req.period_to,
            include_pav=bool(req.include_pav),
            promote_only_working=bool(req.promote_only_working),
        )
    except Exception as e:
        log.exception(
            "segment-analysis/preview failed for well=%s period=%s..%s "
            "include_pav=%s promote_only_working=%s",
            req.well, req.period_from, req.period_to,
            req.include_pav, req.promote_only_working,
        )
        return {"ok": False, "error": f"Внутренняя ошибка: {e}"}


@router.post("/segment-analysis/describe")
def api_segment_describe(snapshot: dict = Body(..., embed=True),
                         db: Session = Depends(get_db_with_blocks)):
    """Фактические описания сегментов для живого «Текстового отчёта».

    Принимает снимок segment_analysis_v2 (как его строит клиентский виджет) и
    возвращает {"descriptions": [...]} — тем же генератором, что для отчёта/PDF.
    Единый источник текста: живая страница и отчёт не расходятся.
    """
    from backend.services.segment_descriptions import (
        build_rich_descriptions, fetch_ops_events,
    )
    try:
        snap = snapshot or {}
        try:
            snap["events_ops"] = fetch_ops_events(
                db, snap.get("well_number"),
                snap.get("date_from"), snap.get("date_to"))
        except Exception:
            log.exception("fetch_ops_events failed in describe")
        return {"descriptions": build_rich_descriptions(snap)}
    except Exception as e:
        log.exception("segment-analysis/describe failed")
        return {"descriptions": [], "error": str(e)}


@router.get("/segment-thresholds")
def api_segment_thresholds_get():
    """Возвращает текущие пороги сегментного анализа.

    Структура:
        {
            "defaults":   {key: number, ...},   # из Python-константы
            "overrides":  {key: number, ...},   # из JSON, может быть пустым
            "effective":  {key: number, ...},   # merged — что реально использует анализ
            "metadata":   {has_overrides, updated_at, overrides_count}
        }
    UI использует defaults для placeholder, effective для текущего значения,
    overrides для индикации «переопределено».
    """
    from backend.services import segment_settings_service as sset
    return {
        "ok": True,
        "defaults":  sset.get_defaults(),
        "overrides": sset.get_user_overrides(),
        "effective": sset.get_effective(),
        "metadata":  sset.get_metadata(),
    }


from typing import Optional as _Opt


class SegmentThresholdsUpdate(_RoseBM):
    overrides: dict[str, float]
    merge: bool = True  # True — частичное обновление; False — полная замена


@router.put("/segment-thresholds")
def api_segment_thresholds_set(req: SegmentThresholdsUpdate):
    """Сохранить пользовательские overrides порогов.

    Только известные ключи (из SEGMENT_THRESHOLDS) принимаются;
    остальные возвращаются в `skipped_unknown` (для UI: показать warning).
    Запись атомарна (os.replace).
    """
    from backend.services import segment_settings_service as sset
    try:
        result = sset.set_overrides(req.overrides or {}, merge=req.merge)
        return result
    except Exception as e:
        log.exception("segment-thresholds set failed")
        return {"ok": False, "error": str(e)}


@router.post("/segment-thresholds/reset")
def api_segment_thresholds_reset():
    """Сбросить все user-overrides → анализ снова использует дефолты."""
    from backend.services import segment_settings_service as sset
    try:
        return sset.reset_to_defaults()
    except Exception as e:
        log.exception("segment-thresholds reset failed")
        return {"ok": False, "error": str(e)}


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
    # Разрешены к изменению: метаданные карточки + частичное обновление params
    # по whitelist (parts / prefix_note / suffix_note). data_snapshot и прочие
    # ключи params (kind/source/chapter/date_*) НЕ изменяются через PUT
    # (защита снапшота). Для пересоздания блока — DELETE + POST.
    title: str | None = None
    comment: str | None = None
    in_report: bool | None = None
    sort_order: int | None = None
    params: dict[str, _Any] | None = None


# Единственные ключи params, разрешённые к изменению через PUT (whitelist).
# Зеркало backend/routers/observation.py::_ALLOWED_PARAM_KEYS.
_ALLOWED_PARAM_KEYS = frozenset({"parts", "prefix_note", "suffix_note", "recommendation"})


def _merge_block_params(existing: dict | None, incoming: dict) -> dict:
    """Частичное обновление params по whitelist. parts сливается по ключам
    (per-key), prefix/suffix — заменяются. Прочие ключи incoming игнорируются
    (защита kind/source/chapter/date_*). data_snapshot не входит в params."""
    merged = dict(existing) if isinstance(existing, dict) else {}
    for key in ("prefix_note", "suffix_note", "recommendation"):
        if key in incoming:
            val = incoming[key]
            if val is not None and not isinstance(val, str):
                raise HTTPException(400, f"params.{key} должен быть строкой")
            merged[key] = val or ""
    if "parts" in incoming:
        parts_in = incoming["parts"]
        if not isinstance(parts_in, dict):
            raise HTTPException(400, "params.parts должен быть объектом {part: bool}")
        for k, v in parts_in.items():
            if not isinstance(v, bool):
                raise HTTPException(400, f"params.parts.{k} должен быть boolean")
        cur_parts = merged.get("parts") if isinstance(merged.get("parts"), dict) else {}
        merged["parts"] = {**cur_parts, **parts_in}
    return merged


def _list_blocks_payload(db: Session, well_id: int, chapter: str | None = None,
                         kinds: list[str] | None = None) -> dict:
    """Возвращает payload блоков БЕЗ HTTP-вызова (для plotly_png_service).

    То же, что api_list_blocks, но напрямую через сервис.
    """
    from backend.services.segment_descriptions import enrich_block_descriptions
    blocks = svc.list_blocks(db, well_id, chapter=chapter, kinds=kinds)
    for _b in blocks:
        enrich_block_descriptions(_b, db=db)
    return {"blocks": blocks}


@router.get("/blocks")
def api_list_blocks(
    well_id: int = Query(...),
    chapter: str | None = Query(None, description="Фильтр по главе (например, 'observation')"),
    kinds: str | None = Query(None, description="Фильтр по типам блоков, через запятую (например, 'period_analysis,baseline')"),
    db: Session = Depends(get_db_with_blocks),
):
    """Все блоки скважины для отчёта об адаптации.

    Фильтры (можно комбинировать):
    - chapter: фильтрует по params.chapter
    - kinds: фильтрует по типу блока (kind), через запятую
    """
    kinds_list = [k.strip() for k in kinds.split(",")] if kinds else None
    blocks = svc.list_blocks(db, well_id, chapter=chapter, kinds=kinds_list)
    # Подробные описания сегментов (с реакцией на вброс) — генерируем на лету,
    # если в снимке только заглушки. Не пишем в БД; покрывает старые блоки.
    from backend.services.segment_descriptions import enrich_block_descriptions
    for _b in blocks:
        enrich_block_descriptions(_b, db=db)
    return {"blocks": blocks}


@router.get("/blocks/count")
def api_blocks_count(
    well_id: int = Query(...),
    db: Session = Depends(get_db_with_blocks),
):
    """Сколько блоков с in_report=True (для индикатора)."""
    return {"well_id": well_id, "in_report": svc.count_blocks_in_report(db, well_id)}


@router.get("/blocks/chapter-preview")
def api_chapter_preview(
    well_id: int = Query(...),
    db: Session = Depends(get_db_with_blocks),
):
    """JSON-превью главы «Анализ данных заказчика» для правой панели.

    Возвращает список блоков с краткими summary и флагами is_corrupted/warnings.
    Corrupted-блок = data_snapshot отсутствует или не прошёл минимальную
    валидацию (нет ключа '_v'). UI отображает badge «corrupted» для таких блоков
    и warnings в тексте карточки.

    Гарантия: только READ, никакого INSERT/UPDATE/DELETE.
    """
    return svc.build_chapter_preview(db, well_id)


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
        # create_block принудительно устанавливает source/chapter для
        # observation_analysis (см. customer_daily_service.create_block).
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
    """Обновить метаданные блока (title / comment / in_report / sort_order)
    и частично params по whitelist (parts / prefix_note / suffix_note).

    data_snapshot и прочие ключи params (kind/source/chapter/date_*) НЕ
    изменяются — защита снапшота. Это позволяет галочкам «что попадёт в отчёт»
    (params.parts) персиститься, не перезаписывая сам снапшот.
    """
    existing = svc.get_block(db, block_id)
    if existing is None:
        raise HTTPException(404, f"Block {block_id} not found")
    merged_params = None
    if body.params is not None:
        merged_params = _merge_block_params(existing.get("params"), body.params)
    return svc.update_block(
        db, block_id,
        title=body.title,
        comment=body.comment,
        in_report=body.in_report,
        sort_order=body.sort_order,
        params=merged_params,  # только whitelist-merge; data_snapshot не трогаем
    )


@router.delete("/blocks/{block_id}")
def api_delete_block(
    block_id: int,
    db: Session = Depends(get_db_with_blocks),
):
    if not svc.delete_block(db, block_id):
        raise HTTPException(404, f"Block {block_id} not found")
    return {"ok": True, "deleted_id": block_id}


class BlocksReorder(_BM):
    block_ids: list[int]


@router.patch("/blocks/reorder")
def api_reorder_blocks(
    body: BlocksReorder,
    db: Session = Depends(get_db_with_blocks),
):
    """Массовое обновление порядка блоков (drag-and-drop).

    Принимает список block_ids в нужном порядке. Присваивает sort_order
    = 0, 1, 2, ... в соответствии с позицией в списке.
    """
    if not body.block_ids:
        return {"ok": True, "updated": 0}
    return svc.reorder_blocks(db, body.block_ids)


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
