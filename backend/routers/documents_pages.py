from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

import sqlalchemy as sa
import json
import re
from datetime import datetime, date
from calendar import monthrange

from backend.db import get_db
from backend.web.templates import templates

from backend.models.wells import Well
from backend.documents.models import Document, DocumentType, DocumentItem
from backend.models.well_status import WellStatus
from pathlib import Path
import subprocess
from backend.documents.numbering import build_doc_number

router = APIRouter(tags=["documents-pages"])


def _get_status_css_class(status_name: str | None) -> str:
    """
    Преобразует название статуса из базы данных в CSS-класс.
    ВАЖНО: маппинг берётся динамически из названия статуса.
    """
    if not status_name:
        return ""

    status_lower = status_name.lower()

    # Маппинг на CSS-классы (должно совпадать с вашими CSS-переменными)
    if "наблюдение" in status_lower or "watch" in status_lower:
        return "status-watch"
    elif "адаптация" in status_lower or "adapt" in status_lower:
        return "status-adapt"
    elif "оптимизация" in status_lower or "opt" in status_lower:
        return "status-opt"
    elif "освоение" in status_lower or "dev" in status_lower:
        return "status-dev"
    elif "не обслуживается" in status_lower or "off" in status_lower:
        return "status-off"
    elif "простой" in status_lower or "idle" in status_lower:
        return "status-idle"
    else:
        return "status-other"


# ===============================================================================
# ОБНОВЛЕННАЯ версия функции documents_index с МУЛЬТИВЫБОРОМ статусов
# ===============================================================================

from datetime import datetime, timezone
from typing import List


@router.get("/documents", response_class=HTMLResponse)
def documents_index(
        request: Request,
        db: Session = Depends(get_db),
        status: List[str] | None = Query(None)  # <-- Изменено: теперь принимаем список
):
    """
    Главная страница актов с канбаном и списком скважин.

    ОБНОВЛЕНО:
    - Мультивыбор статусов через чекбоксы
    - Сортировка скважин по разным критериям
    - Сохранение выбора в localStorage (на клиенте)
    """

    # ========== Канбан-доска ==========
    board_titles = {
        "draft": "📝 Черновик",
        "generated": "📄 Создан",
        "signed": "✅ Подписан",
        "sent": "📧 Отправлен",
        "archived": "📦 Архив",
        "cancelled": "❌ Отменён",
    }
    statuses = list(board_titles.keys())
    board = {s: [] for s in statuses}

    docs = (
        db.query(Document)
        .filter(Document.deleted_at.is_(None))
        .order_by(Document.created_at.desc())
        .limit(300)
        .all()
    )
    for d in docs:
        board.setdefault(d.status, []).append(d)

    # ========== Типы документов ==========
    doc_types = (
        db.query(DocumentType)
        .order_by(DocumentType.category.asc(), DocumentType.sort_order.asc(), DocumentType.id.asc())
        .all()
    )

    doc_types_one_time = [dt for dt in doc_types if not dt.is_periodic and dt.category == "operational"]
    doc_types_periodic = [dt for dt in doc_types if dt.is_periodic]
    doc_types_finance = [dt for dt in doc_types if dt.category == "financial"]

    # ========== Получаем ВСЕ скважины (фильтрация будет на клиенте) ==========
    wells_query = db.query(Well).order_by(Well.number.asc())

    all_wells = []
    wells_without_status_count = 0

    for well in wells_query.all():
        # Получаем текущий статус (dt_end IS NULL)
        current_status_row = (
            db.query(WellStatus)
            .filter(
                WellStatus.well_id == well.id,
                WellStatus.dt_end.is_(None)
            )
            .order_by(WellStatus.dt_start.desc())
            .first()
        )

        # Добавляем информацию о статусе к объекту скважины
        well.current_status = current_status_row.status if current_status_row else None
        well.current_status_start = current_status_row.dt_start if current_status_row else None
        well.current_status_css = _get_status_css_class(well.current_status)

        if not well.current_status:
            wells_without_status_count += 1

        # Вычисляем дни в статусе с учётом timezone
        if well.current_status_start:
            now = datetime.now()
            start = well.current_status_start

            if start.tzinfo is not None:
                now = datetime.now(timezone.utc)
            else:
                now = datetime.now()

            delta = now - start
            well.current_status_days = delta.total_seconds() / 86400
        else:
            well.current_status_days = None

        all_wells.append(well)

    # ========== Статистика актов по скважинам ==========
    well_ids = [w.id for w in all_wells]
    dtype_ids = [dt.id for dt in doc_types]

    well_stats: dict[tuple[int, int], dict] = {}
    well_total_stats: dict[int, dict] = {}

    if well_ids and dtype_ids:
        all_docs = (
            db.query(Document)
            .filter(Document.deleted_at.is_(None))
            .filter(Document.well_id.in_(well_ids))
            .order_by(Document.created_at.desc())
            .all()
        )

        # Статистика по (скважина, тип документа)
        for d in all_docs:
            if d.well_id is None or d.doc_type_id is None:
                continue
            key = (d.well_id, d.doc_type_id)

            if key not in well_stats:
                well_stats[key] = {"count": 0, "docs": []}

            well_stats[key]["count"] += 1

            if len(well_stats[key]["docs"]) < 5:
                well_stats[key]["docs"].append(
                    {"id": d.id, "status": d.status, "doc_number": d.doc_number}
                )

        # Общая статистика по скважине
        for d in all_docs:
            if d.well_id is None:
                continue

            if d.well_id not in well_total_stats:
                well_total_stats[d.well_id] = {
                    "total": 0,
                    "by_category": {},
                    "by_group": {}
                }

            well_total_stats[d.well_id]["total"] += 1

            if d.doc_type:
                category = d.doc_type.category
                if category not in well_total_stats[d.well_id]["by_category"]:
                    well_total_stats[d.well_id]["by_category"][category] = 0
                well_total_stats[d.well_id]["by_category"][category] += 1

                doc_code = d.doc_type.code

                if doc_code in ["well_acceptance", "well_transfer"]:
                    group = "handover"
                elif doc_code == "reagent_expense":
                    group = "reagents"
                elif category == "operational":
                    group = "operational_other"
                elif category == "financial":
                    group = "financial"
                else:
                    group = "other"

                if group not in well_total_stats[d.well_id]["by_group"]:
                    well_total_stats[d.well_id]["by_group"][group] = 0
                well_total_stats[d.well_id]["by_group"][group] += 1

    # ========== Список уникальных статусов для фильтра ==========
    unique_statuses_dict = {}

    for w in all_wells:
        if w.current_status:
            css = w.current_status_css
            if css not in unique_statuses_dict:
                unique_statuses_dict[css] = w.current_status

    # Сортируем в правильном порядке
    status_order = [
        "status-watch",
        "status-adapt",
        "status-opt",
        "status-dev",
        "status-off",
        "status-idle",
        "status-other"
    ]

    available_statuses = []
    for css in status_order:
        if css in unique_statuses_dict:
            available_statuses.append((css, unique_statuses_dict[css]))

    # ========== Определяем выбранные статусы ==========
    # По умолчанию показываем все
    selected_statuses = set()
    if status and len(status) > 0:
        selected_statuses = set(status)
    else:
        # Если ничего не выбрано, выбираем все доступные статусы
        selected_statuses = set(css for css, _ in available_statuses)
        if wells_without_status_count > 0:
            selected_statuses.add('no-status')

    return templates.TemplateResponse(
        "documents/index.html",
        {
            "request": request,
            "board": board,
            "board_titles": board_titles,
            "wells": all_wells,  # <-- Передаём ВСЕ скважины
            "total_wells": len(all_wells),
            "wells_without_status": wells_without_status_count,
            "doc_types": doc_types,
            "doc_types_one_time": doc_types_one_time,
            "doc_types_periodic": doc_types_periodic,
            "doc_types_finance": doc_types_finance,
            "well_stats": well_stats,
            "well_total_stats": well_total_stats,
            "available_statuses": available_statuses,
            "selected_statuses": selected_statuses,
        },
    )


# ===============================================================================
# HELPER FUNCTION - без изменений
# ===============================================================================

def _get_status_css_class(status_name: str | None) -> str:
    """
    Преобразует название статуса из базы данных в CSS-класс.
    """
    if not status_name:
        return ""

    status_lower = status_name.lower()

    if "наблюдение" in status_lower or "watch" in status_lower:
        return "status-watch"
    elif "адаптация" in status_lower or "adapt" in status_lower:
        return "status-adapt"
    elif "оптимизация" in status_lower or "opt" in status_lower:
        return "status-opt"
    elif "освоение" in status_lower or "dev" in status_lower:
        return "status-dev"
    elif "не обслуживается" in status_lower or "off" in status_lower:
        return "status-off"
    elif "простой" in status_lower or "idle" in status_lower:
        return "status-idle"
    else:
        return "status-other"


# ===============================================================================
# ВАЖНО: ОБНОВИТЕ ИМПОРТЫ В НАЧАЛЕ ФАЙЛА:
# ===============================================================================
# from datetime import datetime, date, timezone
# from typing import List  # <-- добавьте это
# from fastapi import APIRouter, Request, Depends, Form, HTTPException, Query


# ===============================================================================
# HELPER FUNCTION - добавьте в начало файла после импортов
# ===============================================================================

def _get_status_css_class(status_name: str | None) -> str:
    """
    Преобразует название статуса из базы данных в CSS-класс.
    ВАЖНО: маппинг берётся динамически из названия статуса.
    """
    if not status_name:
        return ""

    status_lower = status_name.lower()

    # Маппинг на CSS-классы (должно совпадать с вашими CSS-переменными)
    if "наблюдение" in status_lower or "watch" in status_lower:
        return "status-watch"
    elif "адаптация" in status_lower or "adapt" in status_lower:
        return "status-adapt"
    elif "оптимизация" in status_lower or "opt" in status_lower:
        return "status-opt"
    elif "освоение" in status_lower or "dev" in status_lower:
        return "status-dev"
    elif "не обслуживается" in status_lower or "off" in status_lower:
        return "status-off"
    elif "простой" in status_lower or "idle" in status_lower:
        return "status-idle"
    else:
        return "status-other"


# ===============================================================================
# ТАКЖЕ ДОБАВЬТЕ В ИМПОРТЫ В НАЧАЛЕ ФАЙЛА:
# ===============================================================================
# from fastapi import APIRouter, Request, Depends, Form, HTTPException, Query  # <-- добавьте Query

@router.get("/documents/{doc_id}", response_class=HTMLResponse)
def document_detail(doc_id: int, request: Request, db: Session = Depends(get_db)):
    doc = (
        db.query(Document)
        .filter(
            Document.id == doc_id,
            Document.deleted_at.is_(None)  # ← ВАЖНО
        )
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    items = (
        db.query(DocumentItem)
        .filter(DocumentItem.document_id == doc.id)
        .order_by(DocumentItem.line_number.asc())
        .all()
    )

    return templates.TemplateResponse(
        "documents/detail.html",
        {
            "request": request,
            "doc": doc,
            "items": items,
        },
    )

@router.post("/documents/{doc_id}/update")
def document_update(
    doc_id: int,
    db: Session = Depends(get_db),
    notes: str = Form(""),
    metadata_json: str = Form("{}"),
):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    _require_status(doc, {"draft"}, "edit-items")
    # notes
    doc.notes = notes

    # metadata JSON
    try:
        meta = json.loads(metadata_json.strip() or "{}")
        if not isinstance(meta, dict):
            raise ValueError("metadata must be JSON object")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Bad metadata JSON: {e}")

    # ⚠️ если у тебя в модели поле называется НЕ metadata, а например meta / meta_json — поменяй тут
    doc.meta = meta

    doc.updated_at = datetime.utcnow()

    db.add(doc)
    db.commit()

    return RedirectResponse(url=f"/documents/{doc_id}", status_code=303)
@router.post("/documents/{doc_id}/items/add")
def document_item_add(
    doc_id: int,
    db: Session = Depends(get_db),
    work_type: str = Form(""),
    reagent_name: str = Form(""),
    quantity: int = Form(1),
    stage: str = Form(""),
    event_time_str: str = Form(""),
    notes: str = Form(""),
):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # следующий номер строки
    last_ln = (
        db.query(sa.func.max(DocumentItem.line_number))
        .filter(DocumentItem.document_id == doc_id)
        .scalar()
    ) or 0

    item = DocumentItem(
        document_id=doc_id,
        line_number=last_ln + 1,
        work_type=work_type or None,
        reagent_name=reagent_name or None,
        quantity=quantity,
        stage=stage or None,
        event_time_str=event_time_str or None,
        notes=notes or None,
    )
    db.add(item)
    db.commit()

    return RedirectResponse(url=f"/documents/{doc_id}", status_code=303)

@router.post("/documents/items/{item_id}/delete")
def document_item_delete(item_id: int, db: Session = Depends(get_db)):
    item = db.query(DocumentItem).filter(DocumentItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    doc_id = item.document_id

    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    _require_status(doc, {"draft"}, "edit-items")

    db.delete(item)
    db.commit()

    return RedirectResponse(url=f"/documents/{doc_id}", status_code=303)

def _next_doc_number(db: Session, prefix: str, year: int, month: int, well_number: str) -> str:
    """
    Формат: {prefix}-W{well}-{YYYY}-{MM}-{seq:03d}
    Пример: АРР-W89-2026-01-002
    seq считается внутри (prefix + well + year + month)
    """
    base = f"{prefix}-W{well_number}-{year:02d}-{month:02d}-"
    like = f"{base}%"

    # вытаскиваем последние 3 цифры после последнего '-'
    max_seq = (
        db.query(
            sa.func.max(
                sa.cast(sa.func.regexp_replace(Document.doc_number, r"^.*-", ""), sa.Integer)
            )
        )
        .filter(Document.doc_number.ilike(like))
        .scalar()
    )

    seq = (max_seq or 0) + 1
    return f"{base}{seq:03d}"
def _safe_filename(s: str) -> str:
    s = re.sub(r"[^0-9A-Za-zА-Яа-я_\-\.]+", "_", s)
    return s.strip("_") or "doc"

def _require_status(doc: Document, allowed: set[str], action: str) -> None:
    if doc.status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Action '{action}' is not allowed for status '{doc.status}'. Allowed: {sorted(allowed)}"
        )

def _work_type_by_reagent(reagent_name: str | None) -> str:
    r = (reagent_name or "").strip().lower()
    # условие из ТЗ:
    # Super Foam -> ингибирующие
    # иначе -> пенные
    if r == "super foam".lower():
        return "Дозирование ингибирующих реагентов"
    return "Дозирование пенных реагентов"


def _stage_from_status(status: str | None) -> str | None:
    # если хочешь: оставляем как есть (русское название статуса)
    s = (status or "").strip()
    return s or None

@router.get("/documents/reagent-expense/new", response_class=HTMLResponse)
def reagent_expense_new(request: Request, db: Session = Depends(get_db)):
    dt = db.query(DocumentType).filter(DocumentType.code == "reagent_expense").first()
    if not dt:
        raise HTTPException(status_code=500, detail="DocumentType reagent_expense not found")

    wells = db.query(Well).order_by(Well.number.asc()).all()
    # 1) список уникальных названий статусов из БД
    status_names = [
        r[0] for r in (
            db.query(WellStatus.status)
            .distinct()
            .order_by(WellStatus.status.asc())
            .all()
        )
        if r and r[0]
    ]
    today = date.today()
    return templates.TemplateResponse(
        "documents/reagent_expense_new.html",
        {
            "request": request,
            "dt": dt,
            "wells": wells,
            "status_names": status_names,
            "default_year": today.year,
            "default_month": today.month,
        },
    )


@router.post("/documents/reagent-expense/create")
def reagent_expense_create(
    db: Session = Depends(get_db),
    year: int = Form(...),
    month: int = Form(...),
    act_date: str = Form(""),          # YYYY-MM-DD
    numbering_mode: str = Form("auto"),# auto|manual
    manual_number: str = Form(""),
    well_ids: list[int] = Form([]),
):
    dt = db.query(DocumentType).filter(DocumentType.code == "reagent_expense").first()
    if not dt:
        raise HTTPException(status_code=500, detail="DocumentType reagent_expense not found")

    if not act_date:
        last_day = monthrange(year, month)[1]
        act_date = f"{year:04d}-{month:02d}-{last_day:02d}"

    prefix = dt.auto_number_prefix or "АРР"
    if numbering_mode == "manual" and manual_number.strip() and len(well_ids) != 1:
        raise HTTPException(status_code=400, detail="Manual number allowed only when one well selected")
    created = 0
    for wid in well_ids:
        doc = Document(
            doc_type_id=dt.id,
            well_id=wid,
            period_year=year,
            period_month=month,
            status="draft",
            created_by_name="web",

        )
        # период как даты (1-е число и последний день месяца)
        last_day = monthrange(year, month)[1]
        doc.period_start = date(year, month, 1)
        doc.period_end = date(year, month, last_day)
        # IMPORTANT: у тебя в модели поле называется meta, а колонка в БД metadata
        meta = doc.meta or {}
        meta["act_date"] = act_date
        doc.meta = meta

        if numbering_mode == "manual" and manual_number.strip():
            doc.doc_number = manual_number.strip()
        else:
            well_number = str(db.query(Well).filter(Well.id == wid).one().number)
            doc.doc_number = _next_doc_number(db, prefix, year, month, well_number)

        db.add(doc)
        db.flush()
        # ---------- АВТОЗАПОЛНЕНИЕ СТРОК АКТА ИЗ events ----------
        # 1) период месяца
        last_day = monthrange(year, month)[1]
        period_start = date(year, month, 1)
        period_end = date(year, month, last_day)

        # 2) берем периоды статусов из БД (well_status) и НЕ хардкодим allowed
        #    далее ты уже сможешь в UI выбрать какие статусы учитывать,
        #    а сейчас просто используем ВСЕ статусы для скважины в пределах месяца.
        from backend.models.well_status import WellStatus  # подстрой импорт под свой путь
        from backend.models.events import Event  # подстрой импорт под свой путь

        ws_periods = (
            db.query(WellStatus)
            .filter(WellStatus.well_id == wid)
            .filter(sa.func.date(WellStatus.dt_start) <= period_end)
            .filter(
                sa.or_(
                    WellStatus.dt_end.is_(None),
                    sa.func.date(WellStatus.dt_end) >= period_start,
                )
            )
            .order_by(WellStatus.dt_start.asc())
            .all()
        )

        # если периодов нет — можно либо не заполнять строки, либо заполнять по всему месяцу.
        # Я делаю fallback: месяц целиком.
        if not ws_periods:
            ws_periods = [type("Tmp", (), {"status": None, "dt_start": period_start, "dt_end": period_end})]

        # 3) собираем события дозирования реагентов из events в пересечении с периодами статусов
        #    !!! ВАЖНО: подстрой фильтр event_type под твоё реальное значение.
        #    Если в events event_type = 'reagent' (или 'reagent_injection') — поставь это.
        items_to_add = []
        line_no = 0
        summary_foam = 0
        summary_inhibitor = 0

        for p in ws_periods:
            p_start = p.dt_start.date() if hasattr(p.dt_start, "date") else p.dt_start
            p_end_raw = p.dt_end
            p_end = (p_end_raw.date() if p_end_raw and hasattr(p_end_raw, "date") else p_end_raw) or period_end

            # пересечение с месяцем
            start = max(period_start, p_start)
            end = min(period_end, p_end)
            if start > end:
                continue

            events = (
                db.query(Event)
                .filter(Event.well == str(doc.well.number))  # или Event.well_id == wid (если есть)
                .filter(sa.func.date(Event.event_time) >= start)
                .filter(sa.func.date(Event.event_time) <= end)
                .filter(Event.reagent.isnot(None))
                # .filter(Event.event_type == "reagent")         # <- раскомментируй и подставь реальное
                .order_by(Event.event_time.asc())
                .all()
            )

            for ev in events:
                reagent_name = (ev.reagent or "").strip()
                qty = int(ev.qty) if ev.qty is not None else 1

                work_type = _work_type_by_reagent(reagent_name)
                if work_type == "Дозирование ингибирующих реагентов":
                    summary_inhibitor += qty
                else:
                    summary_foam += qty

                line_no += 1
                items_to_add.append(
                    DocumentItem(
                        document_id=doc.id,
                        line_number=line_no,
                        work_type=work_type,
                        event_time=ev.event_time,
                        event_time_str=ev.event_time.strftime("%d.%m.%Y %H:%M") if ev.event_time else None,
                        quantity=qty,
                        reagent_name=reagent_name or None,
                        stage=_stage_from_status(getattr(p, "status", None)),
                        event_id=getattr(ev, "id", None),
                    )
                )

        if items_to_add:
            db.add_all(items_to_add)

        # 4) записываем агрегаты в meta (их LaTeX потом возьмет как summary_foam/summary_inhibitor)
        meta = doc.meta or {}
        meta["period_start"] = str(period_start)
        meta["period_end"] = str(period_end)
        meta["summary_foam"] = summary_foam
        meta["summary_inhibitor"] = summary_inhibitor
        meta["total_injections"] = line_no
        doc.meta = meta
        created += 1

    db.commit()
    return RedirectResponse(url="/documents", status_code=303)

@router.post("/documents/create")
def documents_create(
    db: Session = Depends(get_db),
    doc_type_id: int = Form(...),
    well_id: int | None = Form(None),
    period_month: int | None = Form(None),
    period_year: int | None = Form(None),
):
    dt = db.query(DocumentType).filter(DocumentType.id == doc_type_id).one()

    doc = Document(
        doc_type_id=dt.id,
        status="draft",
        created_by_name="web",
    )

    if getattr(dt, "requires_well", False):
        doc.well_id = well_id

    if getattr(dt, "requires_period", False):
        doc.period_month = period_month
        doc.period_year = period_year

    # номер генерируем сразу
    doc.doc_number = build_doc_number(db, doc, dt)

    db.add(doc)
    db.commit()
    db.refresh(doc)

    return RedirectResponse(url=f"/documents/{doc.id}", status_code=303)

@router.post("/documents/{doc_id}/delete")
def documents_delete(doc_id: int, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Безопасно: удаляем только черновики
    if doc.status != "draft":
        raise HTTPException(status_code=400, detail="Only draft documents can be deleted")

    from datetime import datetime

    doc.deleted_at = datetime.utcnow()
    doc.status = "cancelled"  # опционально, чтобы визуально было видно
    db.add(doc)
    db.commit()

    return RedirectResponse(url="/documents", status_code=303)
@router.post("/documents/{doc_id}/soft-delete")
def documents_soft_delete(doc_id: int, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc.deleted_at = datetime.utcnow()
    db.add(doc)
    db.commit()
    return RedirectResponse(url="/documents", status_code=303)

from backend.documents.services.reagent_expense import refill_reagent_expense_items

@router.post("/documents/{doc_id}/reagent-expense/refill")
def reagent_expense_refill(doc_id: int, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # пример: какие статусы учитывать (можешь менять)
    allowed = ["Оптимизация", "Адаптация", "Освоение", "Наблюдение"]

    n = refill_reagent_expense_items(db, doc_id, allowed_statuses=allowed)
    db.commit()

    return RedirectResponse(url=f"/documents/{doc_id}", status_code=303)

@router.post("/documents/{doc_id}/generate-pdf")
def document_generate_pdf(
    doc_id: int,
    db: Session = Depends(get_db),
    split_tables: str = Form("0"),
):
    use_split = (split_tables == "1")
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # пока делаем только для reagent_expense
    if not doc.doc_type or doc.doc_type.code != "reagent_expense":
        raise HTTPException(status_code=400, detail="PDF generator is implemented only for reagent_expense for now")

    items = (
        db.query(DocumentItem)
        .filter(DocumentItem.document_id == doc.id)
        .order_by(DocumentItem.line_number.asc())
        .all()
    )

    meta = doc.meta or {}

    # --- значения для LaTeX шаблона ---
    theactnum = doc.doc_number or f"ID{doc.id}"
    theactmonth = f"{doc.period_month:02d}.{doc.period_year}" if doc.period_month and doc.period_year else ""
    theactwell = doc.well.number if doc.well else ""
    theactdate = meta.get("act_date") or ""

    from datetime import date as _date

    def _fmt_ru(d: _date | None) -> str:
        return d.strftime("%d.%m.%Y") if d else ""

    # 1) пытаемся взять из meta
    ps = meta.get("period_start")
    pe = meta.get("period_end")

    # 2) fallback: из doc.period_start / doc.period_end
    if not ps and getattr(doc, "period_start", None):
        ps = str(doc.period_start)
    if not pe and getattr(doc, "period_end", None):
        pe = str(doc.period_end)

    # 3) fallback: из месяца/года документа
    if (not ps or not pe) and doc.period_year and doc.period_month:
        last_day = monthrange(doc.period_year, doc.period_month)[1]
        ps = str(date(doc.period_year, doc.period_month, 1))
        pe = str(date(doc.period_year, doc.period_month, last_day))

    # превращаем YYYY-MM-DD -> dd.mm.yyyy
    def _parse_iso(x: str | None) -> _date | None:
        try:
            return date.fromisoformat(str(x))
        except Exception:
            return None

    period_start_str = _fmt_ru(_parse_iso(ps))
    period_end_str = _fmt_ru(_parse_iso(pe))



    summary_foam = 0
    summary_inhibitor = 0
    total_injections = len(items)

    for it in items:
        qty = int(it.quantity or 0)
        # критерий как в ТЗ: Super Foam -> ингибирующие, иначе пенные
        r = (it.reagent_name or "").strip().lower()
        if r == "super foam":
            summary_inhibitor += qty
        else:
            summary_foam += qty

    # company/field можно хранить в document_types.metadata или в doc.meta
    field_name = meta.get("field_name", "Сургил")
    company_executor = meta.get("company_executor", "ООО «UNITOOL»")
    company_client = meta.get("company_client", "СП ООО «Uz-Kor Gas Chemical»")

    # --- пути ---
    out_dir = Path("backend/static/generated/pdf")
    out_dir.mkdir(parents=True, exist_ok=True)

    # base = _safe_filename(f"{theactnum}")
    # ASCII-safe имена файлов (стабильно на всех ОС/кодировках)
    # период YYYY-MM
    period_tag = ""
    if doc.period_year and doc.period_month:
        period_tag = f"{doc.period_year:04d}-{doc.period_month:02d}"

    well_tag = f"W{doc.well.number}" if doc.well else "WNA"
    num_tag = _safe_filename(doc.doc_number or f"ID{doc.id}")

    base_name = f"akt_rashoda_reagentov_{well_tag}_{period_tag}_{num_tag}"
    base_name = _safe_filename(base_name)

    tex_path = out_dir / f"{base_name}.tex"
    pdf_path = out_dir / f"{base_name}.pdf"

    # --- шаблон LaTeX (пока просто читаем из файла в templates/latex) ---
    tpl_path = Path(
    "backend/templates/latex/reagent_expense_split.tex"
    if use_split else
    "backend/templates/latex/reagent_expense.tex"
)
    if not tpl_path.exists():
        raise HTTPException(status_code=500, detail=f"LaTeX template not found: {tpl_path}")

    latex_tpl = tpl_path.read_text(encoding="utf-8")
    def _tex_escape(s: str | None) -> str:
        if s is None:
            return ""
        s = str(s)
        # порядок важен: сначала backslash
        s = s.replace("\\", r"\textbackslash{}")
        s = s.replace("&", r"\&")
        s = s.replace("%", r"\%")
        s = s.replace("$", r"\$")
        s = s.replace("#", r"\#")
        s = s.replace("_", r"\_")
        s = s.replace("{", r"\{")
        s = s.replace("}", r"\}")
        s = s.replace("~", r"\textasciitilde{}")
        s = s.replace("^", r"\textasciicircum{}")
        return s
    # --- очень простой рендер: подменяем VAR через replace (временно) ---
    # Лучше потом подключим Jinja2 для LaTeX, но сейчас запускаемся быстро.
    def rep(key: str, val: str) -> None:
        nonlocal latex_tpl
        latex_tpl = latex_tpl.replace(r"\VAR{" + key + "}", str(val))

    rep("theactnum", _tex_escape(theactnum))
    rep("theactmonth", _tex_escape(theactmonth))
    rep("theactwell", _tex_escape(str(theactwell)))
    rep("theactdate", _tex_escape(theactdate))
    rep("field_name", _tex_escape(field_name))
    rep("company_executor", _tex_escape(company_executor))
    rep("company_client", _tex_escape(company_client))
    rep("period_start_str", _tex_escape(period_start_str))
    rep("period_end_str", _tex_escape(period_end_str))
    rep("total_injections", total_injections)
    rep("summary_foam", summary_foam)
    rep("summary_inhibitor", summary_inhibitor)

    def _inject_block(tpl: str, start: str, end: str, content: str) -> str:
        if start not in tpl or end not in tpl:
            raise HTTPException(status_code=500, detail=f"Markers not found: {start} / {end}")
        before, rest = tpl.split(start, 1)
        _, after = rest.split(end, 1)
        return before + content + after

    # --- рендер таблицы items (заменим блок %% for item in items ... %% endfor) ---
    if use_split:
        # --- SPLIT режим: 2 таблицы ---
        foam_rows = []
        inh_rows = []
        foam_i = 0
        inh_i = 0

        for it in items:
            qty = int(it.quantity or 1)
            r = (it.reagent_name or "").strip().lower()

            # ингибирующий только Super Foam, всё остальное — пенные
            if r == "super foam":
                inh_i += 1
                inh_rows.append(
                    f"{inh_i} & "
                    f"{_tex_escape(it.event_time_str)} & "
                    f"{qty} & "
                    f"{_tex_escape(it.reagent_name)} & "
                    f"{_tex_escape(it.stage)} "
                    r"\\"
                    "\n\\hline"
                )
            else:
                foam_i += 1
                foam_rows.append(
                    f"{foam_i} & "
                    f"{_tex_escape(it.event_time_str)} & "
                    f"{qty} & "
                    f"{_tex_escape(it.reagent_name)} & "
                    f"{_tex_escape(it.stage)} "
                    r"\\"
                    "\n\\hline"
                )

        # если пусто — прочерки
        if not foam_rows:
            foam_rows = [r"\multicolumn{5}{|c|}{---} \\ \hline"]
        if not inh_rows:
            inh_rows = [r"\multicolumn{5}{|c|}{---} \\ \hline"]

        latex_tpl = _inject_block(latex_tpl, "%%% FOAM_START", "%%% FOAM_END", "\n".join(foam_rows))
        latex_tpl = _inject_block(latex_tpl, "%%% INH_START", "%%% INH_END", "\n".join(inh_rows))

    else:
        # --- OLD режим: одна таблица ---
        rows = []
        for it in items:
            rows.append(
                f"{it.line_number} & "
                f"{_tex_escape(it.work_type)} & "
                f"{_tex_escape(it.event_time_str)} & "
                f"{int(it.quantity or 1)} & "
                f"{_tex_escape(it.reagent_name)} & "
                f"{_tex_escape(it.stage)} "
                r"\\"
                "\n\\hline"
            )

        latex_tpl = _inject_block(latex_tpl, "%%% ITEMS_START", "%%% ITEMS_END", "\n".join(rows) if rows else "")
    # --- sanity-check: шаблон не должен обрезаться ---
    # --- sanity-check: документ должен заканчиваться корректно ---
    if r"\end{document}" not in latex_tpl:
        raise HTTPException(
            status_code=500,
            detail="LaTeX template got truncated: \\end{document} missing. Check marker positions in template."
        )

    if r"\end{document" in latex_tpl and r"\end{document}" not in latex_tpl:
        raise HTTPException(
            status_code=500,
            detail="LaTeX contains '\\end{document' without '}'. Fix template."
        )
    tex_path.write_text(latex_tpl, encoding="utf-8")

    # --- компиляция xelatex ---
    try:
        cmd = [
            "xelatex",
            "-interaction=nonstopmode",
            "-halt-on-error",
            f"-output-directory={str(out_dir)}",
            f"-jobname={base_name}",  # <-- фиксируем имя выходного PDF
            str(tex_path),  # <-- полный путь к .tex (надёжнее)
        ]
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        log = e.stdout if e.stdout else str(e)
        raise HTTPException(status_code=500, detail=f"LaTeX build failed:\n{log[:4000]}")

    # сохраним путь в БД
    doc.pdf_filename = f"generated/pdf/{base_name}.pdf"
    doc.status = "generated" if doc.status == "draft" else doc.status
    db.add(doc)
    db.commit()

    return RedirectResponse(url=f"/documents/{doc_id}", status_code=303)

@router.post("/documents/{doc_id}/sign")
def document_sign(
    doc_id: int,
    db: Session = Depends(get_db),
    signer_name: str = Form(""),
    signer_position: str = Form(""),
):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    _require_status(doc, {"generated"}, "sign")

    doc.signed_at = datetime.utcnow()
    doc.signed_by_name = signer_name.strip() or (doc.signed_by_name or None)
    doc.signed_by_position = signer_position.strip() or (doc.signed_by_position or None)

    doc.status = "signed"
    db.add(doc)
    db.commit()
    return RedirectResponse(url=f"/documents/{doc_id}", status_code=303)


@router.post("/documents/{doc_id}/mark-sent")
def document_mark_sent(
    doc_id: int,
    db: Session = Depends(get_db),
    sent_to: str = Form(""),
    sent_via: str = Form(""),
):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    _require_status(doc, {"signed"}, "mark-sent")

    meta = doc.meta or {}
    meta["sent_at"] = datetime.utcnow().isoformat(timespec="seconds")
    if sent_to.strip():
        meta["sent_to"] = sent_to.strip()
    if sent_via.strip():
        meta["sent_via"] = sent_via.strip()
    doc.meta = meta

    doc.status = "sent"
    db.add(doc)
    db.commit()
    return RedirectResponse(url=f"/documents/{doc_id}", status_code=303)


@router.post("/documents/{doc_id}/archive")
def document_archive(
    doc_id: int,
    db: Session = Depends(get_db),
):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    _require_status(doc, {"sent", "signed"}, "archive")

    doc.status = "archived"
    db.add(doc)
    db.commit()
    return RedirectResponse(url=f"/documents/{doc_id}", status_code=303)

@router.post("/documents/items/{item_id}/update")
def document_item_update(
    item_id: int,
    db: Session = Depends(get_db),
    work_type: str = Form(""),
    reagent_name: str = Form(""),
    quantity: int = Form(1),
    stage: str = Form(""),
    event_time_str: str = Form(""),
    notes: str = Form(""),
):
    item = db.query(DocumentItem).filter(DocumentItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    item.work_type = work_type or None
    item.reagent_name = reagent_name or None
    item.quantity = quantity
    item.stage = stage or None
    item.event_time_str = event_time_str or None
    item.notes = notes or None

    db.add(item)
    db.commit()

    return RedirectResponse(url=f"/documents/{item.document_id}", status_code=303)

