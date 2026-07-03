"""Роутер «Финансовые акты» — подвкладка на странице «Акты» (/documents).

Только для админа (request.session['is_admin']). Создание ежемесячного
финансового акта из данных БД, скачивание .docx/PDF, настройка прайса и
классификации реагентов.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import quote

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.web.templates import templates, base_context
from backend.documents.models import Document, DocumentItem, DocumentType
from backend.documents.generator import DocumentGenerator
from backend.documents.services.financial_act import build_financial_act, _DEFAULT_SIGS
from backend.models.reagent_catalog import ReagentCatalog
from backend.models.wells import Well
from backend.models.well_status import WellStatus
from backend.models.act_signatory import ActSignatory, SIDE_CONTRACTOR, SIDE_CUSTOMER

router = APIRouter()


def _require_admin(request: Request):
    if not request.session.get("is_admin", False):
        raise HTTPException(status_code=403, detail="Только для администратора")


def _signatory_library(db: Session) -> list:
    """Библиотека подписантов (для чекбоксов шапки/подписи)."""
    return [s.as_dict() for s in db.query(ActSignatory).order_by(
        ActSignatory.side.desc(), ActSignatory.id).all()]


def _split_side(sigs: list, side: str) -> list:
    return [{"position_ru": s.get("position_ru", ""), "name_ru": s.get("name_ru", "")}
            for s in (sigs or []) if s.get("side") == side]


def _sig_defaults(db: Session, dt_id: int | None) -> dict:
    """Строки по умолчанию: из последнего акта (остаются последние значения), иначе из образца."""
    last = None
    if dt_id:
        last = (db.query(Document)
                .filter(Document.doc_type_id == dt_id, Document.deleted_at.is_(None))
                .order_by(Document.created_at.desc()).first())
    hdr = (last.meta.get("header_sigs") if last and last.meta else None) or _DEFAULT_SIGS
    sgn = (last.meta.get("sign_sigs") if last and last.meta else None) or _DEFAULT_SIGS
    return {
        "def_h_customer": _split_side(hdr, "customer"), "def_h_contractor": _split_side(hdr, "contractor"),
        "def_s_customer": _split_side(sgn, "customer"), "def_s_contractor": _split_side(sgn, "contractor"),
    }


def _sig_used(db: Session) -> dict:
    """Использованные значения (для datalist), по сторонам: должности и ФИО."""
    lib = db.query(ActSignatory).all()
    def dedup(side, attr):
        return sorted({getattr(s, attr) for s in lib if s.side == side and getattr(s, attr)})
    return {
        "pos_customer": dedup("customer", "position_ru"), "name_customer": dedup("customer", "name_ru"),
        "pos_contractor": dedup("contractor", "position_ru"), "name_contractor": dedup("contractor", "name_ru"),
    }


def _optimization_wells(db: Session) -> list:
    rows = (db.query(Well.number).join(WellStatus, WellStatus.well_id == Well.id)
            .filter(WellStatus.status == "Оптимизация").distinct().all())
    return sorted({str(r[0]).strip() for r in rows if r[0] is not None}, key=lambda x: (len(x), x))


def _redirect(msg: str, msg_type: str = "success"):
    return RedirectResponse(
        url=f"/documents/financial-acts?msg={quote(msg)}&msg_type={msg_type}",
        status_code=303,
    )


@router.get("/documents/financial-acts")
def financial_acts_page(request: Request, db: Session = Depends(get_db),
                        msg: str | None = None, msg_type: str | None = None):
    _require_admin(request)
    dt = db.query(DocumentType).filter(DocumentType.code == "financial_act").first()
    docs = []
    if dt:
        docs = (db.query(Document)
                .filter(Document.doc_type_id == dt.id, Document.deleted_at.is_(None))
                .order_by(Document.period_year.desc(), Document.period_month.desc())
                .all())
    prices = db.execute(sa.text("""
        SELECT cp.id, cp.work_type, cp.well_id, w.number AS well_number,
               cp.price_per_unit, cp.effective_from, cp.contract_ref
        FROM contract_price cp LEFT JOIN wells w ON w.id = cp.well_id
        ORDER BY cp.work_type, cp.effective_from DESC
    """)).fetchall()
    reagents = (db.query(ReagentCatalog)
                .order_by(ReagentCatalog.act_group.desc().nullsfirst(), ReagentCatalog.name)
                .all())

    ctx = base_context(request)
    ctx.update({
        "request": request, "docs": docs, "prices": prices, "reagents": reagents,
        "msg": msg, "msg_type": msg_type,
        "sig_library": _signatory_library(db),
        "opt_wells": _optimization_wells(db),
        **_sig_defaults(db, dt.id if dt else None), **_sig_used(db),
        "cur_year": date.today().year, "cur_month": date.today().month,
    })
    return templates.TemplateResponse("documents/financial_acts.html", ctx)


@router.post("/documents/financial-acts/create")
def financial_act_create(request: Request, year: int = Form(...), month: int = Form(...),
                         hc_pos: list[str] = Form(default=[]), hc_name: list[str] = Form(default=[]),
                         hi_pos: list[str] = Form(default=[]), hi_name: list[str] = Form(default=[]),
                         sc_pos: list[str] = Form(default=[]), sc_name: list[str] = Form(default=[]),
                         si_pos: list[str] = Form(default=[]), si_name: list[str] = Form(default=[]),
                         stop_wells: list[str] = Form(default=[]),
                         continue_clause: str = Form("3.9"), stop_clause: str = Form("3.17"),
                         db: Session = Depends(get_db)):
    _require_admin(request)
    if not (1 <= month <= 12):
        return _redirect("Некорректный месяц", "error")

    # библиотека для подтягивания EN и сохранения новых значений
    lib_map = {(s.side, s.position_ru, s.name_ru): s for s in db.query(ActSignatory).all()}

    def build(side: str, poss: list, names: list) -> list:
        out = []
        for p, n in zip(poss, names):
            p, n = (p or "").strip(), (n or "").strip()
            if not (p or n):
                continue
            ex = lib_map.get((side, p, n))
            out.append({"side": side, "position_ru": p, "name_ru": n,
                        "position_en": ex.position_en if ex else "",
                        "name_en": ex.name_en if ex else ""})
            if not ex and p and n:  # сохранить новое значение в справочник
                new = ActSignatory(side=side, position_ru=p, name_ru=n)
                db.add(new)
                lib_map[(side, p, n)] = new
        return out

    try:
        header_sigs = build("customer", hc_pos, hc_name) + build("contractor", hi_pos, hi_name)
        sign_sigs = build("customer", sc_pos, sc_name) + build("contractor", si_pos, si_name)
        db.flush()
        excluded = [w.strip() for w in stop_wells if w and w.strip()]

        doc = build_financial_act(
            db, year, month, created_by_name=request.session.get("username"),
            header_sigs=header_sigs or None, sign_sigs=sign_sigs or None,
            excluded_wells=excluded,
            continue_clause=continue_clause.strip() or "3.9",
            stop_clause=stop_clause.strip() or "3.17",
        )
        n = db.query(DocumentItem).filter(DocumentItem.document_id == doc.id).count()
        db.commit()
    except Exception as e:
        db.rollback()
        import traceback
        traceback.print_exc()
        return _redirect(f"Ошибка создания акта: {type(e).__name__}: {e}", "error")
    return _redirect(f"Акт {doc.doc_number} создан ({n} строк)")


@router.post("/documents/financial-acts/signatory/add")
def signatory_add(request: Request, side: str = Form(...), position_ru: str = Form(...),
                  name_ru: str = Form(...), position_en: str = Form(""),
                  name_en: str = Form(""), db: Session = Depends(get_db)):
    _require_admin(request)
    if side not in (SIDE_CONTRACTOR, SIDE_CUSTOMER):
        return _redirect("Неверная сторона", "error")
    db.add(ActSignatory(side=side, position_ru=position_ru.strip(), name_ru=name_ru.strip(),
                        position_en=position_en.strip() or None, name_en=name_en.strip() or None))
    db.commit()
    return _redirect("Подписант добавлен")


@router.post("/documents/financial-acts/signatory/{sig_id}/delete")
def signatory_delete(sig_id: int, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    db.query(ActSignatory).filter(ActSignatory.id == sig_id).delete()
    db.commit()
    return _redirect("Подписант удалён")


@router.get("/documents/financial-acts/{doc_id}/download.docx")
def financial_act_docx(doc_id: int, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(404, "Акт не найден")
    rel = DocumentGenerator().generate_docx(doc)
    path = Path("backend/static") / rel
    fn = f"{doc.doc_number}.docx"
    return FileResponse(
        str(path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{quote(fn)}"},
    )


@router.get("/documents/financial-acts/{doc_id}/download.pdf")
def financial_act_pdf(doc_id: int, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(404, "Акт не найден")
    try:
        rel = DocumentGenerator().generate_pdf_from_docx(doc)
        db.commit()
    except Exception as e:
        import traceback
        traceback.print_exc()
        # читаемое сообщение в iframe вместо generic-500
        from fastapi.responses import HTMLResponse
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


@router.post("/documents/financial-acts/{doc_id}/delete")
def financial_act_delete(doc_id: int, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    db.query(DocumentItem).filter(DocumentItem.document_id == doc_id).delete()
    db.query(Document).filter(Document.id == doc_id).delete()
    db.commit()
    return _redirect("Акт удалён")


# ─────────────────── Прайс контракта ───────────────────

@router.post("/documents/financial-acts/prices/add")
def price_add(request: Request, work_type: str = Form(...), price_per_unit: float = Form(...),
              effective_from: str = Form(...), well_number: str = Form(""),
              contract_ref: str = Form(""), db: Session = Depends(get_db)):
    _require_admin(request)
    well_id = None
    if well_number.strip():
        w = db.query(Well).filter(sa.cast(Well.number, sa.String) == well_number.strip()).first()
        if not w:
            return _redirect(f"Скважина {well_number} не найдена", "error")
        well_id = w.id
    db.execute(sa.text("""
        INSERT INTO contract_price (work_type, well_id, price_per_unit, effective_from, contract_ref, created_at)
        VALUES (:wt, :wid, :p, :ef, :cr, now())
    """), {"wt": work_type, "wid": well_id, "p": price_per_unit,
           "ef": effective_from, "cr": contract_ref or None})
    db.commit()
    return _redirect("Цена добавлена")


@router.post("/documents/financial-acts/prices/{price_id}/delete")
def price_delete(price_id: int, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    db.execute(sa.text("DELETE FROM contract_price WHERE id=:i"), {"i": price_id})
    db.commit()
    return _redirect("Цена удалена")


# ─────────────────── Классификация реагентов ───────────────────

@router.post("/documents/financial-acts/reagents/{reagent_id}/update")
def reagent_update(reagent_id: int, request: Request, act_group: str = Form(...),
                   unit_cost: str = Form(""), db: Session = Depends(get_db)):
    _require_admin(request)
    r = db.query(ReagentCatalog).filter(ReagentCatalog.id == reagent_id).first()
    if not r:
        return _redirect("Реагент не найден", "error")
    r.act_group = act_group if act_group in ("foam", "inhibitor") else None
    r.unit_cost = float(unit_cost) if unit_cost.strip() else None
    db.commit()
    return _redirect(f"Реагент «{r.name}» обновлён")
