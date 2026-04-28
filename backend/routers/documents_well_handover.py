from __future__ import annotations

import re
import subprocess
from datetime import datetime, date as _date
from pathlib import Path

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from starlette.responses import RedirectResponse

from backend.db import get_db
from backend.utils.latex import find_xelatex as _find_xelatex
from backend.web.templates import templates, base_context
from backend.models.wells import Well
from backend.documents.models import Document, DocumentType
from backend.models.events import Event
from backend.models.well_status import WellStatus, ALLOWED_STATUS
from backend.services.pressure_lookup import (
    get_nearest_from_pressure_raw,
    get_raw_candidates_around,
)

router = APIRouter(tags=["documents-well-handover"])

# Все допустимые коды типов документов для актов приёма/передачи
HANDOVER_DOC_CODES = (
    "well_acceptance",      # Общий акт приёма
    "well_transfer",        # Общий акт передачи/возврата
    "well_stage_accept",    # Этапный акт приёма
    "well_stage_return",    # Этапный акт возврата
)

# Префиксы для номеров документов
DOC_NUMBER_PREFIXES = {
    "well_acceptance": "АПС",
    "well_transfer": "АВС",
    "well_stage_accept": "АПЭ",
    "well_stage_return": "АВЭ",
}


# ======================================================================================
# Helpers
# ======================================================================================

def _safe_filename(s: str) -> str:
    s = re.sub(r"[^0-9A-Za-zА-Яа-я_\-\.]+", "_", s)
    return s.strip("_") or "doc"


def _parse_dt(dt_str: str, field_name: str) -> datetime:
    try:
        return datetime.fromisoformat(dt_str)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Bad {field_name} формат. Ожидаю YYYY-MM-DDTHH:MM")


def _parse_date(date_str: str, field_name: str) -> str:
    try:
        _ = _date.fromisoformat(date_str)
        return date_str
    except Exception:
        raise HTTPException(status_code=400, detail=f"Bad {field_name} формат. Ожидаю YYYY-MM-DD")


def _to_float_or_none(x) -> float | None:
    """
    Безопасное преобразование в float.
    Поддерживает запятую как десятичный разделитель.
    Возвращает None при невалидном вводе (вместо исключения).
    """
    if x is None:
        return None
    x = str(x).strip().replace(",", ".")
    if not x or x == "—" or x.lower() in ("none", "null", ""):
        return None
    try:
        return float(x)
    except (ValueError, TypeError):
        return None  # Невалидный ввод → None вместо crash


def _get_pressure_smart(
        db: Session,
        well_number: str,
        ref_dt: datetime,
        mode: str = "nearest",
        *,
        well_id: int | None = None,
        window_hours: int = 24,
) -> dict:
    """
    Умный поиск давлений из pressure_raw и Events **независимо по каналам**.

    Каналы p_tube и p_line ломаются независимо (SMOD-PT-60 false-zeros),
    поэтому для каждого канала выбирается свой ближайший источник.

    mode: 'nearest' | 'before' | 'first' | 'last'
    window_hours: окно поиска в pressure_raw (по умолч. 24ч)

    Возвращает dict с tube_pressure/line_pressure и описанием источника.
    """
    # --- 1. Собираем кандидатов из Events ---
    base_q = (
        db.query(Event)
        .filter(sa.cast(Event.well, sa.String) == str(well_number))
        .filter(sa.or_(Event.p_tube.isnot(None), Event.p_line.isnot(None)))
    )

    if mode in ("before", "last"):
        before_ev = (
            base_q.filter(Event.event_time <= ref_dt)
            .order_by(Event.event_time.desc()).first()
        )
        after_ev = None
    elif mode == "first":
        before_ev = (
            base_q.filter(Event.event_time <= ref_dt)
            .order_by(Event.event_time.asc()).first()
        )
        after_ev = None
    else:
        before_ev = (
            base_q.filter(Event.event_time <= ref_dt)
            .order_by(Event.event_time.desc()).first()
        )
        after_ev = (
            base_q.filter(Event.event_time > ref_dt)
            .order_by(Event.event_time.asc()).first()
        )

    def _ev_channel(attr: str):
        """Выбрать лучшего Event-кандидата по конкретному каналу."""
        cands = []
        for ev in (before_ev, after_ev):
            if ev is None:
                continue
            v = getattr(ev, attr)
            if v is not None:
                cands.append((abs((ref_dt - ev.event_time).total_seconds()), ev, v))
        if not cands:
            return None
        cands.sort(key=lambda x: x[0])
        return cands[0]  # (delta, event, value)

    ev_tube = _ev_channel("p_tube")
    ev_line = _ev_channel("p_line")

    # --- 2. Собираем кандидатов из pressure_raw (per channel) ---
    _wid = well_id
    if _wid is None:
        w = db.query(Well).filter(
            sa.cast(Well.number, sa.String) == str(well_number)
        ).first()
        _wid = w.id if w else None

    raw = None
    if _wid is not None:
        raw_mode = "before" if mode in ("before", "last") else "nearest"
        raw = get_nearest_from_pressure_raw(
            _wid, ref_dt, mode=raw_mode, max_gap_hours=window_hours,
        )

    # --- 3. Выбираем лучший источник для каждого канала ---
    def _pick_channel(
        ev_best, raw_value, raw_at,
    ) -> tuple[float | None, str | None, datetime | None]:
        """
        Возвращает (value, source_type, source_time) для канала.
        Приоритет отдаётся источнику с меньшей дельтой от ref_dt.
        """
        ev_delta = ev_best[0] if ev_best else float("inf")
        raw_delta = (
            abs((ref_dt - raw_at).total_seconds())
            if raw_value is not None and raw_at is not None else float("inf")
        )
        if ev_delta == float("inf") and raw_delta == float("inf"):
            return None, None, None
        if raw_delta < ev_delta:
            return float(raw_value), "sensor", raw_at
        ev = ev_best[1]
        return float(ev_best[2]), ev.event_type, ev.event_time

    raw_tube_v = raw["p_tube"] if raw else None
    raw_tube_at = raw["p_tube_at_local"] if raw else None
    raw_line_v = raw["p_line"] if raw else None
    raw_line_at = raw["p_line_at_local"] if raw else None

    tube_v, tube_src, tube_at = _pick_channel(ev_tube, raw_tube_v, raw_tube_at)
    line_v, line_src, line_at = _pick_channel(ev_line, raw_line_v, raw_line_at)

    # --- 4. Сводим описание источника ---
    parts = []
    if tube_at is not None:
        label = "датчик" if tube_src == "sensor" else (tube_src or "событие")
        parts.append(f"трубн.: {_format_source_desc(label, tube_at, ref_dt)}")
    if line_at is not None:
        label = "датчик" if line_src == "sensor" else (line_src or "событие")
        parts.append(f"шлейф.: {_format_source_desc(label, line_at, ref_dt)}")
    source_desc = " · ".join(parts) if parts else None

    # Для обратной совместимости (audit): общий type/time выбираем
    # как «самый свежий» из двух каналов
    src_type, src_time = None, None
    for candidate_time, candidate_type in (
        (tube_at, tube_src), (line_at, line_src),
    ):
        if candidate_time is None:
            continue
        if src_time is None or abs((ref_dt - candidate_time).total_seconds()) \
                < abs((ref_dt - src_time).total_seconds()):
            src_time = candidate_time
            src_type = candidate_type

    return {
        "tube_pressure": tube_v,
        "line_pressure": line_v,
        "source_type": src_type,
        "source_time": src_time,
        "source_desc": source_desc,
    }


def _format_source_desc(
    source_label: str, source_time: datetime, target_dt: datetime,
) -> str:
    delta = abs((target_dt - source_time).total_seconds())
    if delta < 3600:
        time_diff = f"{int(delta / 60)} мин"
    elif delta < 86400:
        time_diff = f"{int(delta / 3600)} ч"
    else:
        time_diff = f"{int(delta / 86400)} дн"
    direction = "до" if source_time < target_dt else "после"
    return (
        f"{source_label} от "
        f"{source_time.strftime('%d.%m.%Y %H:%M')} "
        f"({time_diff} {direction})"
    )


def _get_pressure_first_last(
        db: Session,
        well_number: str,
        mode: str,
        ref_dt: datetime,
) -> tuple[float | None, float | None]:
    """
    Legacy wrapper для обратной совместимости.
    Использует новую _get_pressure_smart.
    """
    smart_mode = "before" if mode == "last" else "first"
    result = _get_pressure_smart(db, well_number, ref_dt, smart_mode)
    return (result["tube_pressure"], result["line_pressure"])


def _next_doc_number_compact(db: Session, prefix: str, dt: datetime, well_number: str) -> str:
    """
    Формат: PREFIX-WELL-YYYYMM-SEQ
    пример: АПС-43-202601-006
    """
    yyyymm = dt.strftime("%Y%m")
    like = f"{prefix}-{well_number}-{yyyymm}-%"

    # Берём максимум уже существующего seq (последние 3 цифры)
    max_seq = (
        db.query(
            sa.func.max(
                sa.cast(sa.func.split_part(Document.doc_number, "-", 4), sa.Integer)
            )
        )
        .filter(Document.doc_number.ilike(like))
        .scalar()
    )

    seq = (max_seq or 0) + 1
    return f"{prefix}-{well_number}-{yyyymm}-{seq:03d}"


def _tex_escape(s: str | None) -> str:
    if s is None:
        return ""
    s = str(s)
    s = s.replace("\\", r"\textbackslash{}")
    s = s.replace("&", r"\&").replace("%", r"\%").replace("$", r"\$")
    s = s.replace("#", r"\#").replace("_", r"\_").replace("{", r"\{").replace("}", r"\}")
    s = s.replace("~", r"\textasciitilde{}").replace("^", r"\textasciicircum{}")
    return s


def _apply_vars_or_fail(latex_tpl: str, mapping: dict[str, str]) -> str:
    for k, v in mapping.items():
        latex_tpl = latex_tpl.replace(r"\VAR{" + k + "}", str(v))

    # если осталось что-то незамененное — падаем понятной ошибкой
    if r"\VAR{" in latex_tpl:
        left = re.findall(r"\\VAR\{([^}]+)\}", latex_tpl)
        left = list(dict.fromkeys(left))[:15]
        raise HTTPException(status_code=500, detail=f"LaTeX template has unresolved VAR keys: {left}")

    return latex_tpl


def _defaults_for_doc_type(dt_code: str) -> dict[str, str]:
    default_contract = "2/24-09 от 24.09.2024"
    default_territory = "Территория вокруг скважины – чистая."
    return {
        "contract_number": default_contract,
        "territory_state": default_territory,
        "note": "",
        "dt_code": dt_code,
    }


# ======================================================================================
# New document form
# ======================================================================================

@router.get("/documents/well-handover/new", response_class=HTMLResponse)
def well_handover_new(
        request: Request,
        db: Session = Depends(get_db),
        well_id: int | None = None,
        kind: str | None = None,  # "well_acceptance" | "well_transfer" | "well_stage_accept" | "well_stage_return"
):
    wells = db.query(Well).order_by(Well.number.asc()).all()

    # Получаем или создаём типы документов
    doc_types = {}
    for code in HANDOVER_DOC_CODES:
        dt = db.query(DocumentType).filter(DocumentType.code == code).first()
        if not dt:
            # Автосоздание типа если не существует
            names = {
                "well_acceptance": "Акт приёма скважины",
                "well_transfer": "Акт возврата скважины",
                "well_stage_accept": "Акт приёма скважины на этап",
                "well_stage_return": "Акт возврата скважины после этапа",
            }
            dt = DocumentType(
                code=code,
                name_ru=names.get(code, code),
                category="operational",
                auto_number_prefix=DOC_NUMBER_PREFIXES.get(code, "АКТ"),
            )
            db.add(dt)
            db.commit()
            db.refresh(dt)
        doc_types[code] = dt

    # Получаем активные статусы для выбранной скважины (если есть)
    well_statuses = []
    if well_id:
        well_statuses = (
            db.query(WellStatus)
            .filter(WellStatus.well_id == well_id)
            .order_by(WellStatus.dt_start.desc())
            .all()
        )

    dfl = _defaults_for_doc_type(kind or "")

    # --- Поиск событий-кандидатов на скважине ---
    _event_type_labels = {
        "equip": "Установка оборудования",
        "pressure": "Замер давления",
        "reagent": "Вброс реагента",
        "purge": "Продувка скважины",
        "note": "Заметка",
        "other": "Другое",
    }
    _priority_types = ("equip", "pressure", "reagent", "purge")

    def _collect_events(well_num: str, order: str) -> list[dict]:
        """Собрать до 4 событий: по одному первому/последнему каждого типа."""
        sort = Event.event_time.asc() if order == "asc" else Event.event_time.desc()
        result: list[dict] = []
        seen: set[str] = set()
        for etype in _priority_types:
            evt = (
                db.query(Event)
                .filter(Event.well == well_num, Event.event_type == etype)
                .order_by(sort)
                .first()
            )
            if evt:
                seen.add(etype)
                result.append(_evt_to_dict(evt, etype))
        if len(result) < 4:
            others = (
                db.query(Event)
                .filter(
                    Event.well == well_num,
                    Event.event_type.notin_(list(seen)) if seen else True,
                )
                .order_by(sort)
                .limit(4 - len(result))
                .all()
            )
            for evt in others:
                result.append(_evt_to_dict(evt, evt.event_type or "other"))
        return result

    def _evt_to_dict(evt: Event, etype: str) -> dict:
        return {
            "event_time_iso": evt.event_time.strftime("%Y-%m-%dT%H:%M"),
            "event_time_display": evt.event_time.strftime("%d.%m.%Y %H:%M"),
            "type": etype,
            "type_label": _event_type_labels.get(etype, etype),
            "description": (evt.description or "")[:80],
        }

    # Первые события (для актов приёма) и последние (для актов возврата/демонтажа)
    first_events: list[dict] = []
    last_events: list[dict] = []
    # Даты из well_status (для этапных актов приёма)
    stage_dates: list[dict] = []
    # Даты демонтажа/перемещения оборудования (для актов возврата/передачи)
    equip_movements: list[dict] = []
    # Даты завершения этапов (для well_stage_return)
    stage_ends: list[dict] = []

    if well_id:
        well_obj = db.query(Well).filter(Well.id == well_id).first()
        if well_obj:
            well_num_str = str(well_obj.number)
            first_events = _collect_events(well_num_str, "asc")
            last_events = _collect_events(well_num_str, "desc")

        # Формируем даты из истории статусов
        for ws in well_statuses:
            stage_dates.append({
                "id": ws.id,
                "status": ws.status,
                "dt_start_iso": ws.dt_start.strftime("%Y-%m-%dT%H:%M") if ws.dt_start else "",
                "dt_start_display": ws.dt_start.strftime("%d.%m.%Y %H:%M") if ws.dt_start else "—",
                "dt_end_iso": ws.dt_end.strftime("%Y-%m-%dT%H:%M") if ws.dt_end else "",
                "dt_end_display": ws.dt_end.strftime("%d.%m.%Y %H:%M") if ws.dt_end else "—",
            })

        # --- Перемещения оборудования (removed_at) ---
        # Последние демонтажи оборудования с этой скважины — служат маркером
        # даты фактического прекращения пребывания оборудования.
        rows = db.execute(
            sa.text(
                """
                SELECT ei.removed_at, ei.notes,
                       e.name AS eq_name, e.serial_number, e.equipment_type,
                       (ls.id IS NOT NULL) AS is_lora
                FROM equipment_installation ei
                JOIN equipment e ON e.id = ei.equipment_id
                LEFT JOIN lora_sensors ls ON ls.serial_number = e.serial_number
                WHERE ei.well_id = :wid AND ei.removed_at IS NOT NULL
                ORDER BY ei.removed_at DESC
                LIMIT 15
                """
            ),
            {"wid": well_id},
        ).fetchall()
        for r in rows:
            dt = r.removed_at
            if not dt:
                continue
            type_label = "Датчик" if r.is_lora else (r.equipment_type or "Оборудование")
            label = r.eq_name or type_label
            if r.serial_number:
                label = f"{label} ({r.serial_number})"
            equip_movements.append({
                "dt_iso": dt.strftime("%Y-%m-%dT%H:%M"),
                "dt_display": dt.strftime("%d.%m.%Y %H:%M"),
                "type_label": type_label,
                "label": label,
                "is_lora": bool(r.is_lora),
                "note": (r.notes or "")[:80],
            })

        # --- Завершения этапов (well_status.dt_end) ---
        for ws in well_statuses:
            if not ws.dt_end:
                continue
            stage_ends.append({
                "id": ws.id,
                "status": ws.status,
                "dt_iso": ws.dt_end.strftime("%Y-%m-%dT%H:%M"),
                "dt_display": ws.dt_end.strftime("%d.%m.%Y %H:%M"),
            })

    return templates.TemplateResponse(
        "documents/well_handover_new.html",
        {
            **base_context(request),
            "wells": wells,
            "doc_types": doc_types,
            # Legacy compatibility
            "dt_accept": doc_types.get("well_acceptance"),
            "dt_transfer": doc_types.get("well_transfer"),
            "selected_well_id": well_id,
            "selected_kind": kind,
            "default_contract": dfl["contract_number"],
            "default_territory": dfl["territory_state"],
            # Этапные акты
            "allowed_statuses": ALLOWED_STATUS,
            "well_statuses": well_statuses,
            # События-кандидаты
            "first_events": first_events,
            "last_events": last_events,
            "stage_dates": stage_dates,
            "equip_movements": equip_movements,
            "stage_ends": stage_ends,
        },
    )


# ======================================================================================
# Create
# ======================================================================================

@router.post("/documents/well-handover/create")
def well_handover_create(
        db: Session = Depends(get_db),
        doc_type_id: int = Form(...),
        well_id: int = Form(...),

        # ОСНОВНЫЕ ДАТЫ (обе редактируемы)
        handover_dt: str = Form(...),  # YYYY-MM-DDTHH:MM - дата/время передачи (обязательно)
        act_date: str = Form(""),  # YYYY-MM-DD - дата документа (опционально, default = дата handover_dt)

        # тексты
        contract_number: str = Form("2/24-09 от 24.09.2024"),
        territory_state: str = Form("Территория вокруг скважины – чистая."),
        note: str = Form(""),

        # Для этапных актов
        stage_status: str = Form(""),  # Название этапа (Наблюдение, Адаптация, ...)
        well_status_id: str = Form(""),  # ID записи well_status для трассировки
):
    """
    Создание акта приема/передачи скважины.

    Поддерживает 4 типа актов:
    - well_acceptance: общий акт приёма
    - well_transfer: общий акт возврата
    - well_stage_accept: этапный акт приёма (требует stage_status)
    - well_stage_return: этапный акт возврата (требует stage_status)
    """
    dt = db.query(DocumentType).filter(DocumentType.id == doc_type_id).first()
    if not dt or dt.code not in HANDOVER_DOC_CODES:
        raise HTTPException(status_code=400, detail="Invalid doc_type for well handover")

    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        raise HTTPException(status_code=404, detail="Well not found")

    # Для этапных актов требуется указать статус
    is_stage_doc = dt.code in ("well_stage_accept", "well_stage_return")
    if is_stage_doc and not stage_status.strip():
        raise HTTPException(status_code=400, detail="Для этапного акта необходимо указать этап работ")

    # Парсим дату передачи (обязательно)
    ho_dt = _parse_dt(handover_dt, "handover_dt")

    # Дата документа: если не указана - берем дату из handover_dt
    act_date_iso = (act_date or "").strip()
    if not act_date_iso:
        act_date_iso = ho_dt.date().isoformat()
    else:
        act_date_iso = _parse_date(act_date_iso, "act_date")

    # Получаем давления с улучшенной логикой
    # Для приёма - ближайшее к дате, для возврата - последнее ДО даты
    is_accept = dt.code in ("well_acceptance", "well_stage_accept")
    pressure_mode = "nearest" if is_accept else "before"
    pressure_data = _get_pressure_smart(db, str(well.number), ho_dt, pressure_mode, well_id=well.id)

    # Генерируем номер документа
    prefix = DOC_NUMBER_PREFIXES.get(dt.code, "АКТ")
    doc_number = _next_doc_number_compact(db, prefix, ho_dt, str(well.number))

    # Формируем метаданные
    meta = {
        # ДАТЫ
        "act_date": act_date_iso,
        "handover_dt": ho_dt.isoformat(timespec="seconds"),
        "event_dt": ho_dt.isoformat(timespec="seconds"),

        # ТЕКСТЫ
        "contract_number": contract_number.strip(),
        "territory_state": territory_state.strip(),
        "note": note.strip(),

        # ДАВЛЕНИЯ
        "tube_pressure": pressure_data["tube_pressure"],
        "line_pressure": pressure_data["line_pressure"],
        "pressure_source": pressure_data["source_desc"],
        "pressure_source_type": pressure_data["source_type"],  # тип события для аудита
        "pressure_source_time": pressure_data["source_time"].isoformat() if pressure_data["source_time"] else None,
    }

    # Добавляем данные этапа для этапных актов
    if is_stage_doc:
        meta["stage_status"] = stage_status.strip()
        if well_status_id.strip().isdigit():
            meta["well_status_id"] = int(well_status_id)

    # Создаем документ
    doc = Document(
        doc_type_id=doc_type_id,
        well_id=well_id,
        doc_number=doc_number,
        status="draft",
        meta=meta,
    )

    db.add(doc)
    db.commit()
    db.refresh(doc)

    return RedirectResponse(url=f"/documents/well-handover/{doc.id}", status_code=303)


# ======================================================================================
# Detail page
# ======================================================================================

@router.get("/documents/well-handover/{doc_id}", response_class=HTMLResponse)
def well_handover_detail(doc_id: int, request: Request, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc or not doc.doc_type or doc.doc_type.code not in HANDOVER_DOC_CODES:
        raise HTTPException(status_code=404, detail="Document not found")

    meta = doc.meta or {}

    # Определяем тип акта для UI
    is_stage_doc = doc.doc_type.code in ("well_stage_accept", "well_stage_return")

    ctx = {
        **base_context(request),
        "doc": doc,
        "act_date": (meta.get("act_date") or ""),
        "tube_pressure": meta.get("tube_pressure"),
        "line_pressure": meta.get("line_pressure"),
        "pressure_source": meta.get("pressure_source"),
        # Для этапных актов
        "is_stage_doc": is_stage_doc,
        "stage_status": meta.get("stage_status", ""),
        "well_status_id": meta.get("well_status_id"),
        "allowed_statuses": ALLOWED_STATUS,
    }
    return templates.TemplateResponse("documents/well_handover_detail.html", ctx)


# ======================================================================================
# Update (edit ALL before PDF)
# ======================================================================================

@router.post("/documents/well-handover/{doc_id}/update")
def well_handover_update(
        doc_id: int,
        db: Session = Depends(get_db),
        act_date: str = Form(""),  # YYYY-MM-DD
        handover_dt: str = Form(""),  # YYYY-MM-DDTHH:MM
        event_dt: str = Form(""),  # YYYY-MM-DDTHH:MM (тех.)
        contract_number: str = Form(""),
        territory_state: str = Form(""),
        tube_pressure: str = Form(""),
        line_pressure: str = Form(""),
        note: str = Form(""),
):
    """
    Обновление полей акта (только в статусе draft).

    ВСЕ даты можно редактировать независимо:
    - act_date: дата в шапке документа
    - handover_dt: дата/время в тексте акта
    - event_dt: техническая дата для выборки из базы
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc or not doc.doc_type or doc.doc_type.code not in HANDOVER_DOC_CODES:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.status not in ("draft", "generated"):
        raise HTTPException(status_code=400, detail="Only draft/generated documents can be edited")

    meta = doc.meta or {}

    # --- ДАТЫ: сохраняем ровно то, что пришло из формы
    if (act_date or "").strip():
        meta["act_date"] = _parse_date(act_date.strip(), "act_date")

    if (handover_dt or "").strip():
        ho_dt = _parse_dt(handover_dt.strip(), "handover_dt")
        meta["handover_dt"] = ho_dt.isoformat(timespec="seconds")

    if (event_dt or "").strip():
        ev_dt = _parse_dt(event_dt.strip(), "event_dt")
        meta["event_dt"] = ev_dt.isoformat(timespec="seconds")

    # --- ТЕКСТЫ
    if (contract_number or "").strip():
        meta["contract_number"] = contract_number.strip()

    if (territory_state or "").strip():
        meta["territory_state"] = territory_state.strip()

    meta["note"] = (note or "").strip()

    # --- ДАВЛЕНИЯ (всегда обновляем, даже если пустые - для возможности очистки)
    meta["tube_pressure"] = _to_float_or_none(tube_pressure)
    meta["line_pressure"] = _to_float_or_none(line_pressure)

    doc.meta = meta
    flag_modified(doc, "meta")
    db.add(doc)
    db.commit()

    return RedirectResponse(url=f"/documents/well-handover/{doc_id}", status_code=303)


# ======================================================================================
# Rebuild from events (refetch pressures and sync dates)
# ======================================================================================

def _resolve_ref_dt(meta: dict) -> datetime:
    """Вернуть техническую дату для выборки давлений: event_dt → handover_dt → now."""
    for key in ("event_dt", "handover_dt"):
        iso = (meta.get(key) or "").strip()
        if iso:
            try:
                return datetime.fromisoformat(iso)
            except Exception:
                continue
    return datetime.now()


@router.post("/documents/well-handover/{doc_id}/rebuild-from-events")
def well_handover_rebuild_from_events(
    doc_id: int,
    db: Session = Depends(get_db),
    window_hours: int = Form(24),
):
    """
    Пересобирает давления из базы событий + pressure_raw (per-channel).

    ВАЖНО:
    - Обновляет ТОЛЬКО давления
    - Использует event_dt для выборки (или handover_dt, если event_dt нет)
    - НЕ меняет даты (act_date, handover_dt, event_dt)
    - window_hours: окно поиска в pressure_raw (по умолч. 24ч)
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc or not doc.doc_type or doc.doc_type.code not in HANDOVER_DOC_CODES:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.status != "draft":
        raise HTTPException(status_code=400, detail="Only draft documents can be rebuilt")

    if not doc.well:
        raise HTTPException(status_code=400, detail="Document has no well")

    meta = doc.meta or {}
    ref_dt = _resolve_ref_dt(meta)

    if window_hours <= 0 or window_hours > 24 * 30:
        window_hours = 24

    is_accept = doc.doc_type.code in ("well_acceptance", "well_stage_accept")
    pressure_mode = "nearest" if is_accept else "before"
    pressure_data = _get_pressure_smart(
        db, str(doc.well.number), ref_dt, pressure_mode,
        well_id=doc.well.id, window_hours=window_hours,
    )

    # ВАЖНО: меняем ТОЛЬКО давления, НЕ трогаем даты!
    meta["tube_pressure"] = pressure_data["tube_pressure"]
    meta["line_pressure"] = pressure_data["line_pressure"]
    meta["pressure_source"] = pressure_data["source_desc"]
    meta["pressure_source_type"] = pressure_data["source_type"]  # тип события для аудита
    meta["pressure_source_time"] = pressure_data["source_time"].isoformat() if pressure_data["source_time"] else None

    doc.meta = meta
    flag_modified(doc, "meta")
    db.add(doc)
    db.commit()

    return RedirectResponse(url=f"/documents/well-handover/{doc_id}", status_code=303)


# ======================================================================================
# Pressure candidates (pairs before/after event)
# ======================================================================================

@router.get("/documents/well-handover/{doc_id}/pressure-candidates")
def well_handover_pressure_candidates(
    doc_id: int,
    db: Session = Depends(get_db),
    window_hours: int = Query(24, ge=1, le=24 * 30),
    limit: int = Query(8, ge=1, le=50),
):
    """
    JSON со списком ближайших точек (pressure_raw) ДО и ПОСЛЕ event_dt.
    Каждая точка — пара (p_tube, p_line), как физически лежит в БД.
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc or not doc.doc_type or doc.doc_type.code not in HANDOVER_DOC_CODES:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc.well:
        raise HTTPException(status_code=400, detail="Document has no well")

    meta = doc.meta or {}
    ref_dt = _resolve_ref_dt(meta)

    data = get_raw_candidates_around(
        doc.well.id, ref_dt,
        window_hours=window_hours, limit_each_side=limit,
    )
    data["event_local"] = ref_dt.strftime("%Y-%m-%dT%H:%M:%S")
    data["event_display"] = ref_dt.strftime("%d.%m.%Y %H:%M")
    return JSONResponse(data)


@router.post("/documents/well-handover/{doc_id}/apply-candidate")
def well_handover_apply_candidate(
    doc_id: int,
    db: Session = Depends(get_db),
    measured_at: str = Form(...),  # YYYY-MM-DDTHH:MM:SS (местное)
    apply_tube: str = Form(""),
    apply_line: str = Form(""),
):
    """
    Применить конкретную точку pressure_raw к акту.

    apply_tube / apply_line — строки: "1" = применить, "" = не трогать.
    Значения берутся из БД по переданному measured_at (local).
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc or not doc.doc_type or doc.doc_type.code not in HANDOVER_DOC_CODES:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status not in ("draft", "generated"):
        raise HTTPException(status_code=400, detail="Only draft/generated can be edited")
    if not doc.well:
        raise HTTPException(status_code=400, detail="Document has no well")

    try:
        at_local = datetime.fromisoformat(measured_at)
    except Exception:
        raise HTTPException(status_code=400, detail="Bad measured_at (ISO expected)")

    want_tube = apply_tube.strip() in ("1", "true", "on", "yes")
    want_line = apply_line.strip() in ("1", "true", "on", "yes")
    if not (want_tube or want_line):
        raise HTTPException(status_code=400, detail="Nothing to apply (tube/line unset)")

    # Читаем точку из pressure_raw (конвертим local → utc)
    at_utc = at_local - _kungrad_td()
    row = db.execute(
        sa.text(
            "SELECT p_tube, p_line FROM pressure_raw "
            "WHERE well_id = :wid AND measured_at = :at"
        ),
        {"wid": doc.well.id, "at": at_utc},
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Point not found in pressure_raw")

    p_tube, p_line = row

    meta = doc.meta or {}
    updated = []
    if want_tube:
        meta["tube_pressure"] = float(p_tube) if p_tube is not None \
            and 0 < float(p_tube) <= 85 else None
        updated.append("трубн.")
    if want_line:
        meta["line_pressure"] = float(p_line) if p_line is not None \
            and 0 < float(p_line) <= 85 else None
        updated.append("шлейф.")

    meta["pressure_source"] = (
        f"выбрано вручную: {', '.join(updated)} из датчика "
        f"{at_local.strftime('%d.%m.%Y %H:%M')}"
    )
    meta["pressure_source_type"] = "sensor_manual"
    meta["pressure_source_time"] = at_local.isoformat(timespec="seconds")

    doc.meta = meta
    flag_modified(doc, "meta")
    db.add(doc)
    db.commit()

    return RedirectResponse(url=f"/documents/well-handover/{doc_id}", status_code=303)


def _kungrad_td():
    from datetime import timedelta
    return timedelta(hours=5)


# ======================================================================================
# Generate PDF
# ======================================================================================

@router.post("/documents/well-handover/{doc_id}/generate-pdf")
def well_handover_generate_pdf(doc_id: int, db: Session = Depends(get_db)):
    """
    Генерирует PDF из LaTeX шаблона.

    Поддерживает все 4 типа актов:
    - well_acceptance / well_transfer (общие)
    - well_stage_accept / well_stage_return (этапные)
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc or not doc.doc_type or doc.doc_type.code not in HANDOVER_DOC_CODES:
        raise HTTPException(status_code=404, detail="Document not found")

    meta = doc.meta or {}

    out_dir = Path("backend/static/generated/pdf")
    out_dir.mkdir(parents=True, exist_ok=True)

    well_tag = f"W{doc.well.number}" if doc.well else "WNA"
    dtype = doc.doc_type.code if doc.doc_type else "well_handover"
    base_name = _safe_filename(f"{dtype}_{well_tag}_{doc.doc_number}")
    tex_path = out_dir / f"{base_name}.tex"

    tpl_path = Path("backend/templates/latex/well_handover.tex")
    if not tpl_path.exists():
        raise HTTPException(status_code=500, detail=f"LaTeX template not found: {tpl_path}")

    latex_tpl = tpl_path.read_text(encoding="utf-8")

    # Определяем тип акта
    is_accept = doc.doc_type.code in ("well_acceptance", "well_stage_accept")
    is_stage_doc = doc.doc_type.code in ("well_stage_accept", "well_stage_return")
    stage_status = meta.get("stage_status", "")

    # Дата документа (в шапке)
    act_iso = (meta.get("act_date") or "").strip()
    act_date_str = ""
    if act_iso:
        try:
            act_date_str = _date.fromisoformat(act_iso).strftime("%d.%m.%Y")
        except Exception:
            act_date_str = ""

    # Дата/время передачи (в тексте)
    ho_iso = (meta.get("handover_dt") or "").strip()
    ho_dt = None
    if ho_iso:
        try:
            ho_dt = datetime.fromisoformat(ho_iso)
        except Exception:
            ho_dt = None

    handover_date_str = ho_dt.strftime("%d.%m.%Y") if ho_dt else ""
    handover_time_str = ho_dt.strftime("%H:%M:%S") if ho_dt else ""

    tube_p = meta.get("tube_pressure")
    line_p = meta.get("line_pressure")

    # Формируем текст для этапных актов
    if is_stage_doc and stage_status:
        if is_accept:
            # Этапный акт приёма
            stage_text = f"для выполнения работ по этапу «{stage_status}»"
        else:
            # Этапный акт возврата
            stage_text = f"после выполнения Подрядчиком работ по этапу «{stage_status}»"
    else:
        stage_text = ""

    mapping = {
        "act_number": _tex_escape(doc.doc_number or f"ID{doc.id}"),
        "well_number": _tex_escape(str(doc.well.number if doc.well else "")),
        "act_date": _tex_escape(act_date_str),
        "handover_date": _tex_escape(handover_date_str),
        "handover_time": _tex_escape(handover_time_str),

        "contract_number": _tex_escape(meta.get("contract_number", "2/24-09 от 24.09.2024")),
        "territory_state": _tex_escape(meta.get("territory_state", "Территория вокруг скважины – чистая.")),
        "note": _tex_escape(meta.get("note", "")),

        "tube_pressure": _tex_escape("" if tube_p is None else str(tube_p)),
        "line_pressure": _tex_escape("" if line_p is None else str(line_p)),

        "role_executor_action": "Принял" if is_accept else "Сдал",
        "role_client_action": "Сдал" if is_accept else "Принял",

        # Новое для этапных актов
        "stage_text": _tex_escape(stage_text),
    }

    latex_tpl = _apply_vars_or_fail(latex_tpl, mapping)
    tex_path.write_text(latex_tpl, encoding="utf-8")

    try:
        cmd = [
            _find_xelatex(),
            "-interaction=nonstopmode",
            "-halt-on-error",
            f"-output-directory={str(out_dir)}",
            f"-jobname={base_name}",
            str(tex_path),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except subprocess.CalledProcessError as e:
        log = e.stdout if e.stdout else str(e)
        raise HTTPException(status_code=500, detail=f"LaTeX build failed:\n{log[:4000]}")

    doc.pdf_filename = f"generated/pdf/{base_name}.pdf"
    doc.status = "generated" if doc.status == "draft" else doc.status
    db.add(doc)
    db.commit()

    return RedirectResponse(url=f"/documents/well-handover/{doc_id}", status_code=303)


# ======================================================================================
# Revert to Draft (for editing after generation)
# ======================================================================================

@router.post("/documents/well-handover/{doc_id}/revert-to-draft")
def well_handover_revert_to_draft(doc_id: int, db: Session = Depends(get_db)):
    """
    Возвращает документ в статус draft для редактирования.
    Работает только для документов со статусом generated.
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc or not doc.doc_type or doc.doc_type.code not in HANDOVER_DOC_CODES:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.status != "generated":
        raise HTTPException(
            status_code=400,
            detail="Only generated documents can be reverted to draft"
        )

    doc.status = "draft"
    db.add(doc)
    db.commit()

    return RedirectResponse(url=f"/documents/well-handover/{doc_id}", status_code=303)