"""Роутер «План работ» — подвкладка на странице «Акты» (/documents).

Только для админа. Сценарий — оптимизация (по эталону «План работ скважина 74»).
Данные скважины (конструкция + операционные параметры) префилятся из БД, затем
редактируются оператором; подписанты (СОГЛАСОВАНО/УТВЕРЖДАЮ) и пути к печати/подписи
гибкие. .docx/PDF генерируются из meta документа.

ВАЖНО: регистрировать в app.py ДО documents_pages (иначе /documents/{doc_id}
перехватит /documents/work-plans).
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse, HTMLResponse
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.web.templates import templates, base_context
from backend.documents.models import Document, DocumentType
from backend.documents.generator import DocumentGenerator
from backend.services.work_plan_service import (
    build_table11_prefill, available_wells, TABLE11_KEYS, DEFAULT_SIGS,
)

router = APIRouter()

SIG_KEYS = list(DEFAULT_SIGS.keys())  # sog_position/org/name/date, utv_*


def _require_admin(request: Request):
    if not request.session.get("is_admin", False):
        raise HTTPException(status_code=403, detail="Только для администратора")


def _redirect(msg: str, msg_type: str = "success"):
    return RedirectResponse(
        url=f"/documents/work-plans?msg={quote(msg)}&msg_type={msg_type}",
        status_code=303,
    )


@router.get("/documents/work-plans")
def work_plans_page(request: Request, db: Session = Depends(get_db),
                    msg: str | None = None, msg_type: str | None = None):
    _require_admin(request)
    dt = db.query(DocumentType).filter(DocumentType.code == "work_plan").first()
    docs = []
    if dt:
        docs = (db.query(Document)
                .filter(Document.doc_type_id == dt.id, Document.deleted_at.is_(None))
                .order_by(Document.created_at.desc()).all())
    ctx = base_context(request)
    ctx.update({
        "request": request, "docs": docs, "msg": msg, "msg_type": msg_type,
        "wells": available_wells(db), "defaults": DEFAULT_SIGS,
        "cur_year": date.today().year,
    })
    return templates.TemplateResponse("documents/work_plan.html", ctx)


@router.post("/documents/work-plans/create")
def work_plan_create(request: Request, well_no: str = Form(...),
                     year: int = Form(...), db: Session = Depends(get_db)):
    _require_admin(request)
    dt = db.query(DocumentType).filter(DocumentType.code == "work_plan").first()
    if not dt:
        return _redirect("Тип 'work_plan' не найден — запустите seed_work_plan_type.py", "error")
    wno = well_no.strip()
    if not wno:
        return _redirect("Укажите номер скважины", "error")

    doc_number = f"ПР-{wno}-{year}"
    if db.query(Document).filter(Document.doc_number == doc_number,
                                 Document.deleted_at.is_(None)).first():
        return _redirect(f"План {doc_number} уже существует — откройте его для правки", "error")

    try:
        table11 = build_table11_prefill(db, wno)
        meta = {"well_no": wno, "table11": table11, **DEFAULT_SIGS,
                "include_seal": True, "include_signature": True}
        doc = Document(doc_type_id=dt.id, doc_number=doc_number, period_year=year,
                       status="draft", created_by_name=request.session.get("username"),
                       meta=meta)
        db.add(doc)
        db.commit()
    except Exception as e:
        db.rollback()
        import traceback; traceback.print_exc()
        return _redirect(f"Ошибка создания: {type(e).__name__}: {e}", "error")
    return _redirect(f"План {doc_number} создан — проверьте параметры и подписантов")


@router.get("/documents/work-plans/{doc_id}/prefill")
def work_plan_prefill(doc_id: int, request: Request, db: Session = Depends(get_db)):
    """Текущие значения документа (Таблица 1.1 + подписанты + пути картинок) для правки."""
    _require_admin(request)
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(404, "План не найден")
    m = doc.meta or {}
    table11 = {k: "" for k in TABLE11_KEYS}
    table11.update(m.get("table11", {}) or {})
    sigs = {k: (m.get(k) or DEFAULT_SIGS[k]) for k in SIG_KEYS}
    return JSONResponse({
        "table11": table11, "table11_keys": TABLE11_KEYS, "sigs": sigs,
        "seal_path": m.get("seal_path", ""), "signature_path": m.get("signature_path", ""),
        "include_seal": m.get("include_seal", True),
        "include_signature": m.get("include_signature", True),
    })


@router.post("/documents/work-plans/{doc_id}/rebuild")
async def work_plan_rebuild(doc_id: int, request: Request, db: Session = Depends(get_db)):
    """Сохранить отредактированные значения (Таблица 1.1 + подписанты + картинки)."""
    _require_admin(request)
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(404, "План не найден")
    form = await request.form()
    m = dict(doc.meta or {})

    table11 = {k: (form.get(f"t_{k}") or "").strip() for k in TABLE11_KEYS}
    m["table11"] = table11
    m["well_no"] = table11.get("well_no", m.get("well_no", ""))
    for k in SIG_KEYS:
        m[k] = (form.get(k) or "").strip() or DEFAULT_SIGS[k]
    m["seal_path"] = (form.get("seal_path") or "").strip()
    m["signature_path"] = (form.get("signature_path") or "").strip()
    m["include_seal"] = form.get("include_seal") is not None
    m["include_signature"] = form.get("include_signature") is not None

    doc.meta = m
    db.commit()
    return _redirect(f"План {doc.doc_number} обновлён")


@router.post("/documents/work-plans/{doc_id}/status")
def work_plan_status(doc_id: int, request: Request, action: str = Form(...),
                     db: Session = Depends(get_db)):
    _require_admin(request)
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(404, "План не найден")
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    m = dict(doc.meta or {})
    if action == "sent":
        m["sent_at"] = now
    elif action == "accepted":
        m["accepted_at"] = now; m.setdefault("sent_at", now)
    elif action == "reset":
        m.pop("sent_at", None); m.pop("accepted_at", None)
    doc.meta = m
    db.commit()
    return _redirect("Статус обновлён")


@router.get("/documents/work-plans/{doc_id}/download.docx")
def work_plan_docx(doc_id: int, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(404, "План не найден")
    rel = DocumentGenerator().generate_docx(doc)
    path = Path("backend/static") / rel
    fn = f"{doc.doc_number}.docx"
    return FileResponse(
        str(path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fn)}"},
    )


@router.get("/documents/work-plans/{doc_id}/download.pdf")
def work_plan_pdf(doc_id: int, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(404, "План не найден")
    try:
        rel = DocumentGenerator().generate_pdf_from_docx(doc)
        db.commit()
    except Exception as e:
        import traceback; traceback.print_exc()
        return HTMLResponse(
            f"<div style='font:14px sans-serif;padding:16px;color:#b91c1c;'>"
            f"Не удалось сгенерировать PDF: {type(e).__name__}: {e}<br><br>"
            f"Проверьте, что установлен LibreOffice (soffice) и он не заблокирован.</div>",
            status_code=200,
        )
    path = Path("backend/static") / rel
    fn = f"{doc.doc_number}.pdf"
    return FileResponse(str(path), media_type="application/pdf",
                        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(fn)}"})


@router.post("/documents/work-plans/{doc_id}/delete")
def work_plan_delete(doc_id: int, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    db.query(Document).filter(Document.id == doc_id).delete()
    db.commit()
    return _redirect("План удалён")
