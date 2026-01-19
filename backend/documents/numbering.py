from __future__ import annotations

from sqlalchemy.orm import Session
import sqlalchemy as sa

def build_doc_number(db: Session, doc, doc_type) -> str:
    """
    Короткий номер: <PREFIX>-<WELL>-<YYYY>-<MM>-<SEQ>
    Пример: RE-043-2026-01-02
    """

    # prefix
    prefix = (doc_type.auto_number_prefix or doc_type.code or "DOC").upper()

    # well number
    well_no = "000"
    if getattr(doc, "well", None) and getattr(doc.well, "number", None):
        well_no = str(doc.well.number).zfill(3)
    elif getattr(doc, "well_id", None):
        # если well не загружен relationship’ом
        # можно не усложнять: оставь 000
        pass

    # period
    year = getattr(doc, "period_year", None)
    month = getattr(doc, "period_month", None)

    # если период не задан — ставим 00 (для разовых актов)
    yy = f"{year:04d}" if year else "0000"
    mm = f"{month:02d}" if month else "00"

    # seq внутри (doc_type + well + year + month)
    seq = _next_seq(db, doc_type_id=doc.doc_type_id, well_id=doc.well_id, year=year, month=month)
    return f"{prefix}-{well_no}-{yy}-{mm}-{seq:02d}"


def _next_seq(db: Session, doc_type_id: int, well_id: int | None, year: int | None, month: int | None) -> int:
    q = sa.select(sa.func.count()).select_from(sa.text("documents"))
    # используем текстовую таблицу, чтобы не тянуть импорт модели и избежать циклов
    # но можно и через ORM, если удобно

    # безопаснее и проще через ORM:
    from backend.documents.models import Document  # локальный импорт, чтобы не было циклов

    stmt = sa.select(sa.func.count(Document.id)).where(Document.doc_type_id == doc_type_id)

    if well_id is not None:
        stmt = stmt.where(Document.well_id == well_id)

    if year is not None:
        stmt = stmt.where(Document.period_year == year)
    else:
        stmt = stmt.where(Document.period_year.is_(None))

    if month is not None:
        stmt = stmt.where(Document.period_month == month)
    else:
        stmt = stmt.where(Document.period_month.is_(None))

    # не считаем soft-deleted
    if hasattr(Document, "deleted_at"):
        stmt = stmt.where(Document.deleted_at.is_(None))

    n = db.execute(stmt).scalar_one()
    return int(n) + 1