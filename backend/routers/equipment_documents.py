"""
ИСПРАВЛЕННЫЙ РОУТЕР v4

Исправления:
1. Демонтаж: ищем по equipment.current_location вместо equipment_installation
2. Давления: сначала equip, потом ближайшее любое событие с давлениями
3. Показываем источник давлений (тип события + дата)
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from backend.db import get_db
from backend.web.templates import templates
from backend.models.wells import Well
from backend.models.equipment import Equipment, EquipmentInstallation
from backend.documents.models import Document, DocumentType
from backend.models.events import Event

router = APIRouter(tags=["equipment-documents"])


# ======================================================================================
# Helpers
# ======================================================================================

def _translit(text: str) -> str:
    """Транслитерация кириллицы в латиницу для имён файлов"""
    translit_map = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
        'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'Yo',
        'Ж': 'Zh', 'З': 'Z', 'И': 'I', 'Й': 'Y', 'К': 'K', 'Л': 'L', 'М': 'M',
        'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U',
        'Ф': 'F', 'Х': 'H', 'Ц': 'Ts', 'Ч': 'Ch', 'Ш': 'Sh', 'Щ': 'Sch',
        'Ъ': '', 'Ы': 'Y', 'Ь': '', 'Э': 'E', 'Ю': 'Yu', 'Я': 'Ya',
    }
    result = []
    for char in text:
        result.append(translit_map.get(char, char))
    return ''.join(result)


def _safe_filename(s: str) -> str:
    """Безопасное имя файла (транслит + очистка)"""
    s = _translit(s)
    s = re.sub(r"[^0-9A-Za-z_\-\.]+", "_", s)
    return s.strip("_") or "doc"


def _parse_dt(dt_str: str, field_name: str) -> datetime:
    try:
        return datetime.fromisoformat(dt_str)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Bad {field_name} формат")


def _to_float_or_none(x) -> Optional[float]:
    """Convert to float or None"""
    if x is None:
        return None
    x = str(x).strip().replace(",", ".")
    if not x:
        return None
    try:
        return float(x)
    except:
        return None


def _get_pressure_smart(db: Session, well_number: str, target_dt: datetime) -> dict:
    """
    Умный поиск давлений:
    1. Сначала ищем event_type='equip' с давлениями
    2. Если нет — ищем ближайшее событие с давлениями (любого типа)

    Возвращает:
    {
        "tube_pressure": float | None,
        "line_pressure": float | None,
        "source_type": str | None,  # тип события
        "source_time": datetime | None,  # время события
        "source_desc": str | None,  # описание для UI
    }
    """
    result = {
        "tube_pressure": None,
        "line_pressure": None,
        "source_type": None,
        "source_time": None,
        "source_desc": None,
    }

    # 1. Сначала пробуем найти event_type='equip' с давлениями
    equip_event = (
        db.query(Event)
        .filter(Event.well == str(well_number))
        .filter(Event.event_type == "equip")
        .filter(sa.or_(Event.p_tube.isnot(None), Event.p_line.isnot(None)))
        .filter(Event.event_time <= target_dt)
        .order_by(Event.event_time.desc())
        .first()
    )

    if equip_event and (equip_event.p_tube is not None or equip_event.p_line is not None):
        result["tube_pressure"] = equip_event.p_tube
        result["line_pressure"] = equip_event.p_line
        result["source_type"] = "equip"
        result["source_time"] = equip_event.event_time
        result["source_desc"] = f"equip от {equip_event.event_time.strftime('%d.%m.%Y %H:%M')}"
        return result

    # 2. Ищем ближайшее событие с давлениями (до или после target_dt)
    # Сначала ищем ДО целевой даты
    before_event = (
        db.query(Event)
        .filter(Event.well == str(well_number))
        .filter(sa.or_(Event.p_tube.isnot(None), Event.p_line.isnot(None)))
        .filter(Event.event_time <= target_dt)
        .order_by(Event.event_time.desc())
        .first()
    )

    # Потом ищем ПОСЛЕ целевой даты
    after_event = (
        db.query(Event)
        .filter(Event.well == str(well_number))
        .filter(sa.or_(Event.p_tube.isnot(None), Event.p_line.isnot(None)))
        .filter(Event.event_time > target_dt)
        .order_by(Event.event_time.asc())
        .first()
    )

    # Выбираем ближайшее
    best_event = None

    if before_event and after_event:
        delta_before = target_dt - before_event.event_time
        delta_after = after_event.event_time - target_dt
        best_event = before_event if delta_before <= delta_after else after_event
    elif before_event:
        best_event = before_event
    elif after_event:
        best_event = after_event

    if best_event:
        result["tube_pressure"] = best_event.p_tube
        result["line_pressure"] = best_event.p_line
        result["source_type"] = best_event.event_type
        result["source_time"] = best_event.event_time

        # Вычисляем разницу во времени
        delta = abs((target_dt - best_event.event_time).total_seconds())
        if delta < 3600:
            time_diff = f"{int(delta/60)} мин"
        elif delta < 86400:
            time_diff = f"{int(delta/3600)} ч"
        else:
            time_diff = f"{int(delta/86400)} дн"

        direction = "до" if best_event.event_time < target_dt else "после"
        result["source_desc"] = f"{best_event.event_type} от {best_event.event_time.strftime('%d.%m.%Y %H:%M')} ({time_diff} {direction})"

    return result


def _next_doc_number(db: Session, doc_type_code: str, well_number: str) -> str:
    """Генерация номера: АУО-51-202601-001 или АДО-51-202601-001"""
    prefix_map = {
        "equipment_install": "АУО",
        "equipment_remove": "АДО",
    }
    prefix = prefix_map.get(doc_type_code, "ОБ")

    now = datetime.now()
    year_month = now.strftime("%Y%m")

    pattern = f"{prefix}-{well_number}-{year_month}-%"

    last_doc = (
        db.query(Document)
        .filter(Document.doc_number.like(pattern))
        .order_by(Document.doc_number.desc())
        .first()
    )

    if last_doc and last_doc.doc_number:
        parts = last_doc.doc_number.split("-")
        if len(parts) == 4:
            try:
                last_seq = int(parts[3])
                next_seq = last_seq + 1
            except:
                next_seq = 1
        else:
            next_seq = 1
    else:
        next_seq = 1

    return f"{prefix}-{well_number}-{year_month}-{next_seq:03d}"


def _tex_escape(s) -> str:
    """Escape special LaTeX characters"""
    if s is None:
        return "—"
    s = str(s)
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


# ======================================================================================
# API: Получение давлений
# ======================================================================================

@router.get("/api/equipment/pressure")
def get_pressure_api(
    well_id: int = Query(...),
    event_dt: str = Query(...),
    db: Session = Depends(get_db),
):
    """API для получения давлений с умным поиском"""
    try:
        dt = datetime.fromisoformat(event_dt)
    except:
        return JSONResponse({
            "tube_pressure": None,
            "line_pressure": None,
            "source_desc": None
        })

    # Получаем скважину
    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        return JSONResponse({
            "tube_pressure": None,
            "line_pressure": None,
            "source_desc": "Скважина не найдена"
        })

    # Умный поиск давлений
    pressure_data = _get_pressure_smart(db, str(well.number), dt)

    return JSONResponse({
        "tube_pressure": pressure_data["tube_pressure"],
        "line_pressure": pressure_data["line_pressure"],
        "source_type": pressure_data["source_type"],
        "source_time": pressure_data["source_time"].isoformat() if pressure_data["source_time"] else None,
        "source_desc": pressure_data["source_desc"],
    })


# ======================================================================================
# GET: Форма создания акта
# ======================================================================================

@router.get("/documents/equipment/new", response_class=HTMLResponse)
def equipment_doc_new(
    request: Request,
    db: Session = Depends(get_db),
    well_id: int | None = None,
    kind: str | None = None,
):
    """Форма создания акта"""
    wells = db.query(Well).order_by(Well.number.asc()).all()

    dt_install = db.query(DocumentType).filter(DocumentType.code == "equipment_install").first()
    dt_removal = db.query(DocumentType).filter(DocumentType.code == "equipment_remove").first()

    # Автосоздание типов если нет
    if not dt_install:
        dt_install = DocumentType(
            code="equipment_install",
            name_ru="Акт монтажа оборудования",
            category="operational",
            is_periodic=False,
        )
        db.add(dt_install)
        db.commit()
        db.refresh(dt_install)

    if not dt_removal:
        dt_removal = DocumentType(
            code="equipment_remove",
            name_ru="Акт демонтажа оборудования",
            category="operational",
            is_periodic=False,
        )
        db.add(dt_removal)
        db.commit()
        db.refresh(dt_removal)

    equipment_classes = []
    pressure_data = {
        "tube_pressure": None,
        "line_pressure": None,
        "source_desc": None,
    }

    if well_id and kind:
        from collections import defaultdict

        well = db.query(Well).filter(Well.id == well_id).first()
        if not well:
            raise HTTPException(status_code=404, detail="Well not found")

        # Получаем давления
        pressure_data = _get_pressure_smart(db, str(well.number), datetime.now())

        if kind == "install":
            # Для монтажа: ВСЁ оборудование
            all_equipment = (
                db.query(Equipment)
                .filter(Equipment.deleted_at.is_(None))
                .order_by(Equipment.name.asc(), Equipment.serial_number.asc())
                .all()
            )
        else:
            # ИСПРАВЛЕНО: Для демонтажа ищем по current_location
            # Ищем оборудование где current_location содержит номер скважины
            location_pattern = f"%{well.number}%"

            all_equipment = (
                db.query(Equipment)
                .filter(Equipment.deleted_at.is_(None))
                .filter(Equipment.status == "installed")
                .filter(Equipment.current_location.ilike(location_pattern))
                .order_by(Equipment.name.asc(), Equipment.serial_number.asc())
                .all()
            )

        # Группируем по названию
        grouped = defaultdict(list)
        for eq in all_equipment:
            grouped[eq.name].append(eq)

        for class_name, items in sorted(grouped.items()):
            available_count = sum(1 for eq in items if eq.status == 'available')
            equipment_classes.append({
                "name": class_name,
                "items": items,
                "total": len(items),
                "available": available_count
            })

    return templates.TemplateResponse(
        "documents/equipment_new.html",
        {
            "request": request,
            "wells": wells,
            "equipment_classes": equipment_classes,
            "dt_install": dt_install,
            "dt_removal": dt_removal,
            "well_id": well_id,
            "kind": kind or "install",
            "auto_tube_pressure": pressure_data.get("tube_pressure"),
            "auto_line_pressure": pressure_data.get("line_pressure"),
            "pressure_source_desc": pressure_data.get("source_desc"),
        },
    )


# ======================================================================================
# POST: Создание акта
# ======================================================================================

@router.post("/documents/equipment/create")
def equipment_doc_create(
    request: Request,
    db: Session = Depends(get_db),
    doc_type_id: int = Form(...),
    well_id: int = Form(...),
    kind: str = Form(...),
    event_dt: str = Form(...),
    equipment_ids: List[int] = Form(...),
    tube_pressure: str = Form(None),
    line_pressure: str = Form(None),
    contract_number: str = Form(...),
    territory_state: str = Form(""),
    equipment_condition: str = Form("working"),  # "working" или "not_working"
    note: str = Form(None),
):
    """Создание акта монтажа/демонтажа"""
    doc_type = db.query(DocumentType).filter(DocumentType.id == doc_type_id).first()
    if not doc_type:
        raise HTTPException(status_code=404, detail="Document type not found")

    if doc_type.code not in ["equipment_install", "equipment_remove"]:
        raise HTTPException(status_code=400, detail="Invalid doc_type for equipment document")

    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        raise HTTPException(status_code=404, detail="Well not found")

    event_datetime = _parse_dt(event_dt, "event_dt")
    doc_number = _next_doc_number(db, doc_type.code, str(well.number))

    # Давления из формы
    tube_p = _to_float_or_none(tube_pressure)
    line_p = _to_float_or_none(line_pressure)

    # Оборудование
    equipment_items = []
    for eq_id in equipment_ids:
        eq = db.query(Equipment).filter(Equipment.id == eq_id).first()
        if eq:
            equipment_items.append({
                "equipment_id": eq.id,
                "name": eq.name,
                "serial_number": eq.serial_number,
                "manufacturer": eq.manufacturer,
                "quantity": 1
            })

    # Метаданные
    meta = {
        "act_date": event_datetime.strftime("%Y-%m-%d"),
        "event_dt": event_datetime.isoformat(),
        "contract_number": contract_number,
        "territory_state": territory_state,
        "tube_pressure": tube_p,
        "line_pressure": line_p,
        "note": note,
        "equipment_items": equipment_items,
        "kind": kind,
        "equipment_condition": equipment_condition  # "working" или "not_working"
    }

    # Создание документа
    doc = Document(
        doc_type_id=doc_type_id,
        doc_number=doc_number,
        well_id=well_id,
        status="draft",
        created_by_name="web",
        meta=meta,
    )

    db.add(doc)
    db.commit()
    db.refresh(doc)

    # Обновление статуса оборудования
    for eq_id in equipment_ids:
        eq = db.query(Equipment).filter(Equipment.id == eq_id).first()
        if not eq:
            continue

        if kind == "install":
            eq.status = "installed"
            eq.current_location = f"Скважина {well.number}"

            installation = EquipmentInstallation(
                equipment_id=eq.id,
                well_id=well_id,
                document_id=doc.id,
                installed_at=event_datetime,
                tube_pressure_install=tube_p,
                line_pressure_install=line_p,
            )
            db.add(installation)
        else:
            # Демонтаж
            eq.status = "available"
            eq.current_location = "Склад"

            # Обновляем запись установки если есть
            installation = (
                db.query(EquipmentInstallation)
                .filter(
                    EquipmentInstallation.equipment_id == eq.id,
                    EquipmentInstallation.well_id == well_id,
                    EquipmentInstallation.removed_at.is_(None)
                )
                .first()
            )

            if installation:
                installation.removed_at = event_datetime
                installation.tube_pressure_remove = tube_p
                installation.line_pressure_remove = line_p

    db.commit()

    return RedirectResponse(url=f"/documents/equipment/{doc.id}", status_code=303)


# ======================================================================================
# GET: Просмотр акта
# ======================================================================================

@router.get("/documents/equipment/{doc_id}", response_class=HTMLResponse)
def equipment_doc_detail(
    doc_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Страница просмотра акта оборудования"""
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    equipment_items = []
    if doc.meta and "equipment_items" in doc.meta:
        for item in doc.meta["equipment_items"]:
            eq = db.query(Equipment).filter(Equipment.id == item.get("equipment_id")).first()
            equipment_items.append({
                "name": item.get("name"),
                "serial_number": item.get("serial_number"),
                "quantity": item.get("quantity", 1),
                "equipment": eq
            })

    return templates.TemplateResponse(
        "documents/equipment_detail.html",
        {
            "request": request,
            "doc": doc,
            "equipment_items": equipment_items,
        },
    )


# ======================================================================================
# POST: Генерация PDF
# ======================================================================================

@router.post("/documents/equipment/{doc_id}/generate-pdf")
def equipment_doc_generate_pdf(
    doc_id: int,
    db: Session = Depends(get_db),
):
    """Генерация PDF для акта оборудования"""
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.status != "draft":
        raise HTTPException(status_code=400, detail="Only drafts can be generated")

    meta = doc.meta or {}
    kind = meta.get("kind", "install")

    # Единый шаблон
    template_path = Path("backend/templates/latex/equipment_act.tex")
    if not template_path.exists():
        raise HTTPException(status_code=404, detail="Template equipment_act.tex not found")

    with open(template_path, "r", encoding="utf-8") as f:
        latex_source = f.read()

    well = doc.well
    event_dt = datetime.fromisoformat(meta.get("event_dt", datetime.now().isoformat()))

    # Условные переменные
    if kind == "install":
        act_title = "монтажа оборудования"
        action_verb = "установлено"
        action_context = "для проведения работ в рамках выполнения условий"

        # Текст о состоянии для монтажа
        condition_text = "Переданное оборудование находится в рабочем состоянии."
    else:
        act_title = "демонтажа оборудования"
        action_verb = "демонтировано"
        action_context = "использовавшееся для проведения работ в рамках выполнения условий"

        # Текст о состоянии для демонтажа
        equipment_condition = meta.get("equipment_condition", "working")
        condition_status = "рабочем" if equipment_condition == "working" else "нерабочем"

        condition_text = f"Демонтированное оборудование передано Подрядчику.\n\n"
        condition_text += f"На момент передачи оборудование находится в {condition_status} состоянии."

    # Замены
    replacements = {
        r"\VAR{act_number}": doc.doc_number or "—",
        r"\VAR{act_title}": act_title,
        r"\VAR{act_date}": event_dt.strftime("%d.%m.%Y"),
        r"\VAR{event_date}": event_dt.strftime("%d.%m.%Y"),
        r"\VAR{event_time}": event_dt.strftime("%H:%M"),
        r"\VAR{well_number}": str(well.number) if well else "—",
        r"\VAR{action_verb}": action_verb,
        r"\VAR{action_context}": action_context,
        r"\VAR{contract_number}": _tex_escape(meta.get("contract_number", "—")),
        r"\VAR{tube_pressure}": str(meta.get("tube_pressure") or "—"),
        r"\VAR{line_pressure}": str(meta.get("line_pressure") or "—"),
        r"\VAR{note}": _tex_escape(meta.get("note") or "—"),
    }

    for old, new in replacements.items():
        latex_source = latex_source.replace(old, new)

    # Таблица оборудования
    equipment_rows = []
    for idx, item in enumerate(meta.get("equipment_items", []), 1):
        name = _tex_escape(item.get('name', '—'))
        serial = _tex_escape(item.get('serial_number', '—'))
        qty = item.get('quantity', 1)
        row = f"{idx} & {name} & {serial} & {qty} \\\\\n\\hline"
        equipment_rows.append(row)

    equipment_table = "\n".join(equipment_rows) if equipment_rows else "— & — & — & — \\\\\n\\hline"

    # Заменяем всю секцию между маркерами
    # Разбиваем по началу, находим конец, и вставляем таблицу
    start_marker = "%%% EQUIPMENT_START"
    end_marker = "%%% EQUIPMENT_END"

    if start_marker in latex_source and end_marker in latex_source:
        before = latex_source.split(start_marker)[0]
        after = latex_source.split(end_marker)[1]
        latex_source = f"{before}{start_marker}\n{equipment_table}\n{end_marker}{after}"
    else:
        raise HTTPException(status_code=500, detail="EQUIPMENT markers not found in template")

    # Заменяем секцию с текстом о состоянии оборудования
    condition_start = "%%% CONDITION_TEXT_START"
    condition_end = "%%% CONDITION_TEXT_END"

    if condition_start in latex_source and condition_end in latex_source:
        before = latex_source.split(condition_start)[0]
        after = latex_source.split(condition_end)[1]
        latex_source = f"{before}{condition_start}\n{condition_text}\n{condition_end}{after}"
    else:
        raise HTTPException(status_code=500, detail="CONDITION_TEXT markers not found in template")

    # Директория
    output_dir = Path("backend/static/generated/pdf")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Транслитерация имени
    safe_name = _safe_filename(doc.doc_number or f"doc_{doc.id}")
    tex_file = output_dir / f"{safe_name}.tex"

    with open(tex_file, "w", encoding="utf-8") as f:
        f.write(latex_source)

    # Компиляция
    try:
        result = subprocess.run(
            [
                "xelatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                f"{safe_name}.tex",  # Только имя файла, так как cwd=output_dir
            ],
            cwd=str(output_dir),
            capture_output=True,
            timeout=60,
        )

        if result.returncode != 0:
            # Читаем лог-файл для детальной информации
            log_file = output_dir / f"{safe_name}.log"
            log_content = ""
            if log_file.exists():
                log_content = log_file.read_text(encoding="utf-8", errors="ignore")

            # Также выводим в консоль для отладки
            print("="*80)
            print("LaTeX compilation FAILED")
            print("="*80)
            print("STDOUT:")
            print(result.stdout)
            print("-"*80)
            print("STDERR:")
            print(result.stderr)
            print("-"*80)
            if log_content:
                print("LOG FILE (last 3000 chars):")
                print(log_content[-3000:])
            print("="*80)

            raise HTTPException(
                status_code=500,
                detail=f"LaTeX compilation failed.\n\nSTDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}\n\nLOG:\n{log_content[-3000:]}"
            )

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="LaTeX compilation timeout")

    doc.pdf_filename = f"generated/pdf/{safe_name}.pdf"
    doc.latex_source = latex_source
    doc.status = "generated"
    db.commit()

    return RedirectResponse(url=f"/documents/equipment/{doc.id}", status_code=303)


# ======================================================================================
# POST: Подписание
# ======================================================================================

@router.post("/documents/equipment/{doc_id}/sign")
def equipment_doc_sign(
    doc_id: int,
    db: Session = Depends(get_db),
    signer_name: str = Form(""),
    signer_position: str = Form(""),
):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.status != "generated":
        raise HTTPException(status_code=400, detail="Only generated documents can be signed")

    doc.status = "signed"
    doc.signed_at = datetime.now()
    doc.signed_by_name = signer_name.strip() or "Представитель Заказчика"
    doc.signed_by_position = signer_position.strip() or None
    db.commit()

    return RedirectResponse(url=f"/documents/equipment/{doc.id}", status_code=303)


# ======================================================================================
# POST: Изменение статуса
# ======================================================================================

@router.post("/documents/equipment/{doc_id}/change-status")
def equipment_doc_change_status(
    doc_id: int,
    db: Session = Depends(get_db),
    new_status: str = Form(...),
):
    """Изменение статуса документа (для канбана)"""
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Разрешённые переходы
    allowed_transitions = {
        "draft": ["generated"],
        "generated": ["signed"],
        "signed": ["sent", "archived"],
        "sent": ["archived"],
        "archived": ["signed"],
    }

    current_status = doc.status
    if new_status not in allowed_transitions.get(current_status, []):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot change status from {current_status} to {new_status}"
        )

    # Обновляем статус
    doc.status = new_status

    # Если возвращаем из архива - убираем дату архивации
    if new_status == "signed" and current_status == "archived":
        if hasattr(doc, 'archived_at'):
            doc.archived_at = None

    # Если архивируем - ставим дату
    if new_status == "archived":
        if hasattr(doc, 'archived_at'):
            doc.archived_at = datetime.now()

    db.commit()

    return RedirectResponse(url=f"/documents/equipment/{doc.id}", status_code=303)


# ======================================================================================
# GET: Редактирование
# ======================================================================================

@router.get("/documents/equipment/{doc_id}/edit", response_class=HTMLResponse)
def equipment_doc_edit(
    doc_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.status != "draft":
        raise HTTPException(status_code=400, detail="Only drafts can be edited")

    meta = doc.meta or {}
    kind = meta.get('kind', 'install')
    return RedirectResponse(url=f"/documents/equipment/new?well_id={doc.well_id}&kind={kind}")


# ======================================================================================
# POST: Удаление
# ======================================================================================

@router.post("/documents/equipment/{doc_id}/delete")
def equipment_doc_delete(
    doc_id: int,
    db: Session = Depends(get_db),
):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Для черновиков - возвращаем оборудование в статус "available"
    if doc.status == "draft" and doc.meta and "equipment_items" in doc.meta:
        kind = doc.meta.get("kind", "install")

        for item in doc.meta["equipment_items"]:
            eq_id = item.get("equipment_id")
            if not eq_id:
                continue

            eq = db.query(Equipment).filter(Equipment.id == eq_id).first()
            if not eq:
                continue

            if kind == "install":
                eq.status = "available"
                eq.current_location = "Склад"

                db.query(EquipmentInstallation).filter(
                    EquipmentInstallation.document_id == doc.id
                ).delete()

    db.delete(doc)
    db.commit()

    return RedirectResponse(url="/documents", status_code=303)


# ======================================================================================
# POST: API добавления оборудования
# ======================================================================================

@router.post("/api/equipment/add")
async def add_equipment_api(
    request: Request,
    db: Session = Depends(get_db),
):
    data = await request.json()

    name = data.get("name")
    serial_number = data.get("serial_number")
    manufacturer = data.get("manufacturer", "")

    if not name or not serial_number:
        raise HTTPException(status_code=400, detail="Name and serial required")

    existing = db.query(Equipment).filter(Equipment.serial_number == serial_number).first()
    if existing:
        raise HTTPException(status_code=400, detail="Serial number already exists")

    equipment = Equipment(
        name=name,
        serial_number=serial_number,
        manufacturer=manufacturer,
        status="available",
        current_location="Склад"
    )

    db.add(equipment)
    db.commit()
    db.refresh(equipment)

    return {"success": True, "id": equipment.id}