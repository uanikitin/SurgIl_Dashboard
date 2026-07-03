"""Сервис финансового акта («Акт приёма-передачи выполненных работ»).

Мульти-скважинный ежемесячный акт: собирает 3 группы работ за календарный месяц
из БД (WellStatus + Event), считает стоимость/НДС/итоги, создаёт Document +
DocumentItems (well_id=NULL, скважина хранится построчно в DocumentItem.well_number).

Правила (согласованы с пользователем):
- Адаптация     — фикс. цена за скважино-операцию (кол-во справочно, на сумму не влияет).
- Оптимизация   — цена_за_месяц / дней_в_месяце × отработанных суток (с разбивкой по ценам).
- Дозирование   — счёт Event по реагентам группы 'foam' × цена/операцию.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_EVEN

import sqlalchemy as sa
from sqlalchemy.orm import Session

from num2words import num2words

from backend.documents.models import Document, DocumentItem, DocumentType
from backend.models.wells import Well
from backend.models.well_status import WellStatus
from backend.models.events import Event
from backend.models.reagent_catalog import ReagentCatalog

VAT_RATE = Decimal("0.12")
CONTRACT_REF = "2/24-09 от 24.09.2024"
Q2 = Decimal("0.01")


def _money(x) -> Decimal:
    return Decimal(str(x)).quantize(Q2, rounding=ROUND_HALF_EVEN)


def _period(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def _fmt_range(d1: date, d2: date) -> str:
    """Компактный диапазон дат: '22-30.04.2026' (в одном месяце),
    '09.03-21.04.2026' (в одном году), иначе полные даты."""
    if d1 > d2:
        d1, d2 = d2, d1
    if (d1.year, d1.month) == (d2.year, d2.month):
        return f"{d1.day:02d}-{d2.day:02d}.{d1.month:02d}.{d1.year}"
    if d1.year == d2.year:
        return f"{d1.day:02d}.{d1.month:02d}-{d2.day:02d}.{d2.month:02d}.{d1.year}"
    return f"{d1.strftime('%d.%m.%Y')}-{d2.strftime('%d.%m.%Y')}"


# ─────────────────────────── пропись (сум/тийин) ───────────────────────────

def _tiyin_word(n: int) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return "тийинов"
    d = n % 10
    if d == 1:
        return "тийин"
    if d in (2, 3, 4):
        return "тийна"
    return "тийнов"


def amount_in_words_ru(amount: Decimal) -> str:
    amount = _money(amount)
    whole = int(amount)
    kop = int((amount - whole) * 100)
    words = num2words(whole, lang="ru")
    words = words[:1].upper() + words[1:]
    return f"{words} сум {kop:02d} {_tiyin_word(kop)}"


def amount_in_words_en(amount: Decimal) -> str:
    amount = _money(amount)
    whole = int(amount)
    kop = int((amount - whole) * 100)
    words = num2words(whole, lang="en").replace(" and ", " ").replace(",", "")
    words = words[:1].upper() + words[1:]
    return f"{words} UZS and {kop:02d} tiyins"


# ─────────────────────────── цены ───────────────────────────

def _price_for(db: Session, work_type: str, well_id: int | None, on_date: date) -> Decimal | None:
    """Цена на дату: приоритет строки по скважине над общей (well_id IS NULL)."""
    row = db.execute(sa.text("""
        SELECT price_per_unit FROM contract_price
        WHERE work_type = :wt AND effective_from <= :d
          AND (well_id = :wid OR well_id IS NULL)
        ORDER BY (well_id IS NULL) ASC, effective_from DESC
        LIMIT 1
    """), {"wt": work_type, "d": on_date, "wid": well_id}).fetchone()
    return Decimal(str(row[0])) if row else None


def _opt_monthly_on(db: Session, well_id: int | None, on_date: date) -> Decimal | None:
    return _price_for(db, "optimization", well_id, on_date)


# ─────────────────────────── сборка строк ───────────────────────────

def _clip(status: WellStatus, p_start: date, p_end: date) -> tuple[date, date] | None:
    s = max(status.dt_start.date(), p_start)
    e = min(status.dt_end.date() if status.dt_end else p_end, p_end)
    if e < s:
        return None
    return s, e


def _wells_by_status(db: Session, status_name: str, dt_from: datetime, dt_to: datetime):
    """WellStatus нужного статуса, пересекающиеся с периодом, с номером скважины."""
    return (
        db.query(WellStatus, Well.number)
        .join(Well, Well.id == WellStatus.well_id)
        .filter(WellStatus.status == status_name)
        .filter(WellStatus.dt_start <= dt_to)
        .filter(sa.or_(WellStatus.dt_end.is_(None), WellStatus.dt_end >= dt_from))
        .order_by(Well.number.asc(), WellStatus.dt_start.asc())
        .all()
    )


def _build_rows(db: Session, year: int, month: int) -> list[dict]:
    p_start, p_end = _period(year, month)
    dim = monthrange(year, month)[1]
    dt_from = datetime.combine(p_start, time.min)
    dt_to = datetime.combine(p_end, time.max)
    rows: list[dict] = []

    # 1) Адаптация — скважино-операция (непрерывный период). Привязка к акту ПО МЕСЯЦУ
    #    ЗАВЕРШЕНИЯ (dt_end в этом месяце). Незавершённые (dt_end IS NULL) НЕ включаются.
    #    Цена фиксирована за операцию; amount = цена × число завершённых операций.
    completed = (
        db.query(WellStatus, Well.number)
        .join(Well, Well.id == WellStatus.well_id)
        .filter(WellStatus.status == "Адаптация")
        .filter(WellStatus.dt_end.isnot(None))
        .filter(WellStatus.dt_end >= dt_from, WellStatus.dt_end <= dt_to)
        .order_by(Well.number.asc(), WellStatus.dt_end.asc())
        .all()
    )
    adapt: dict[str, dict] = {}
    for st, well_no in completed:
        d = adapt.setdefault(str(well_no), {
            "well_id": st.well_id, "count": 0,
            "start": st.dt_start.date(), "end": st.dt_end.date(),
        })
        d["count"] += 1
        d["start"] = min(d["start"], st.dt_start.date())
        d["end"] = max(d["end"], st.dt_end.date())
    for well_no, d in adapt.items():
        price = _price_for(db, "adaptation", d["well_id"], p_start) or Decimal("0")
        rows.append(_row(
            "adaptation", "Адаптация / Adaptation", well_no, _fmt_range(d["start"], d["end"]),
            "скв операция", d["count"], price, amount=price * Decimal(d["count"]),
        ))

    # 2) Оптимизация — (месячная цена / дней в месяце) × сутки, с разбивкой по ценам
    for st, well_no in _wells_by_status(db, "Оптимизация", dt_from, dt_to):
        clip = _clip(st, p_start, p_end)
        if not clip:
            continue
        s, e = clip
        worked = (e - s).days
        if worked <= 0:
            continue
        # Дневная цена = round(месячная / дней_в_месяце, 2), суммируем по дням
        # (при смене цены в середине месяца каждый день берёт свою цену).
        amount = Decimal("0")
        for i in range(worked):
            day = s + timedelta(days=i)
            monthly = _opt_monthly_on(db, st.well_id, day) or Decimal("0")
            amount += _money(monthly / Decimal(dim))
        # цена за сутки для колонки «цена за ед» — на дату начала интервала
        daily = (_opt_monthly_on(db, st.well_id, s) or Decimal("0")) / Decimal(dim)
        label = _fmt_range(s, e)
        rows.append(_row(
            "optimization",
            "Оптимизация (по формуле дни/дней в месяце × ежемесячный платёж) / Optimization",
            str(well_no), label, "сут", worked, _money(daily), amount=amount,
        ))

    # 3) Возмещение реагентов — ТОЛЬКО за вбросы В ПЕРИОД ОПТИМИЗАЦИИ.
    #    Две категории по группе реагента (пенные / ингибирующие), РАЗНЫЕ цены.
    #    (Вбросы во время адаптации НЕ возмещаются — химия в цене операции.)
    opt_intervals: dict[str, list] = {}
    for st, well_no in _wells_by_status(db, "Оптимизация", dt_from, dt_to):
        s = max(st.dt_start.date(), p_start)
        e = min(st.dt_end.date() if st.dt_end else p_end, p_end)
        if e >= s:
            opt_intervals.setdefault(str(well_no), []).append((s, e))

    def _in_opt(wkey: str, d: date) -> bool:
        return any(s <= d <= e for s, e in opt_intervals.get(wkey, []))

    grp_of = dict(db.query(ReagentCatalog.name, ReagentCatalog.act_group)
                  .filter(ReagentCatalog.act_group.in_(["foam", "inhibitor"])).all())
    if grp_of and opt_intervals:
        events = (db.query(Event.well, Event.reagent, Event.event_time)
                  .filter(Event.event_time >= dt_from, Event.event_time <= dt_to)
                  .filter(Event.reagent.in_(list(grp_of.keys())))
                  .filter(Event.qty.isnot(None), Event.qty > 0).all())
        agg: dict[tuple, dict] = {}
        for well, reagent, et in events:
            wkey = str(well).strip()
            if not wkey or not _in_opt(wkey, et.date()):
                continue
            key = (wkey, grp_of.get(reagent))
            a = agg.setdefault(key, {"count": 0, "min": et, "max": et})
            a["count"] += 1
            a["min"] = min(a["min"], et)
            a["max"] = max(a["max"], et)

        cfg = [
            ("foam", "foam_dosing", "Дозирование пенных реагентов / Foam reagent dosing"),
            ("inhibitor", "inhibitor_dosing", "Дозирование ингибирующих реагентов / Inhibitor reagent dosing"),
        ]
        for grp, work_group, title in cfg:
            items = sorted(((k, a) for k, a in agg.items() if k[1] == grp),
                           key=lambda kv: (len(kv[0][0]), kv[0][0]))
            for (wkey, _), a in items:
                w = db.query(Well).filter(sa.cast(Well.number, sa.String) == wkey).first()
                price = _price_for(db, work_group, w.id if w else None, p_start) or Decimal("0")
                rows.append(_row(
                    work_group, title, wkey, _fmt_range(a["min"].date(), a["max"].date()),
                    "операция", a["count"], price, amount=price * Decimal(a["count"]),
                ))

    return rows


def _row(work_group, work_type, well_number, period_label, unit, qty, price, amount):
    amount = _money(amount)
    vat = _money(amount * VAT_RATE)
    return {
        "work_group": work_group,
        "work_type": work_type,
        "well_number": well_number,
        "period_label": period_label,
        "unit": unit,
        "quantity": int(qty),
        "price_per_unit": _money(price),
        "amount": amount,
        "vat_amount": vat,
        "amount_with_vat": _money(amount + vat),
    }


# ─────────────────────────── публичное API ───────────────────────────

_DEFAULT_SIGS = [
    {"side": "customer", "position_ru": "Председатель Правления",
     "position_en": "Chairman of the Board", "name_ru": "Исраилов У. Т.", "name_en": "Israilov U. T."},
    {"side": "customer", "position_ru": "Первый Заместитель Председателя Правления",
     "position_en": "First Deputy Chairman of the Board", "name_ru": "Cho Eun Sang", "name_en": "Cho Eun Sang"},
    {"side": "contractor", "position_ru": "Директор",
     "position_en": "Director", "name_ru": "Яцкив А. П.", "name_en": "Yatskiv A. P."},
]


def build_financial_act(db: Session, year: int, month: int,
                        created_by_name: str | None = None,
                        header_sigs: list | None = None,
                        sign_sigs: list | None = None,
                        excluded_wells: list | None = None,
                        continue_clause: str = "3.9",
                        stop_clause: str = "3.17") -> Document:
    """Создаёт (или пересобирает) черновик финансового акта за месяц.

    header_sigs / sign_sigs: списки подписантов (dict) для ШАПКИ и ПОДПИСЕЙ — независимо.
    excluded_wells: номера скважин, по которым «прекратить работы» (остальные — «продолжить»).
    continue_clause / stop_clause: пункты Договора для решений."""
    dt = db.query(DocumentType).filter(DocumentType.code == "financial_act").first()
    if not dt:
        raise ValueError("DocumentType 'financial_act' не найден (нужен сидинг)")

    p_start, p_end = _period(year, month)

    # rebuild: удалить ВСЕ прежние акты за этот период (draft/иные) — освобождаем номер,
    # чтобы вставка не конфликтовала по unique(doc_number). Номер акта (act_no) сохраняем.
    existing = (db.query(Document)
                .filter(Document.doc_type_id == dt.id,
                        Document.period_year == year, Document.period_month == month)
                .all())
    old_act_no = next((e.meta.get("act_no") for e in existing if e.meta and e.meta.get("act_no")), None)
    if existing:
        ids = [e.id for e in existing]
        db.query(DocumentItem).filter(DocumentItem.document_id.in_(ids)).delete(synchronize_session=False)
        db.query(Document).filter(Document.id.in_(ids)).delete(synchronize_session=False)
        db.flush()

    # сквозной номер акта: при пересборке периода — прежний; иначе следующий глобальный
    if old_act_no:
        seq = int(old_act_no)
    else:
        max_no = db.query(
            sa.func.max(sa.cast(Document.meta["act_no"].astext, sa.Integer))
        ).filter(Document.doc_type_id == dt.id).scalar() or 0
        seq = int(max_no) + 1

    rows = _build_rows(db, year, month)
    total = sum((r["amount"] for r in rows), Decimal("0"))
    total_vat = sum((r["vat_amount"] for r in rows), Decimal("0"))
    total_with_vat = sum((r["amount_with_vat"] for r in rows), Decimal("0"))
    # порядок скважин — как в таблице (по группам, порядок первого появления)
    wells = list(dict.fromkeys(r["well_number"] for r in rows if r["well_number"]))
    # решения: по умолчанию все «продолжить», выбранные — «прекратить»
    excl = {str(w).strip() for w in (excluded_wells or [])}
    stop_wells = [w for w in wells if w in excl]
    continue_wells = [w for w in wells if w not in excl]

    doc = Document(
        doc_type_id=dt.id,
        doc_number=f"FA-{year}-{month:02d}",  # уникален по периоду; act_no (№) — в meta
        well_id=None,
        period_start=p_start, period_end=p_end,
        period_month=month, period_year=year,
        status="draft",
        created_by_name=created_by_name,
        meta={
            "act_no": seq,
            "contract_ref": CONTRACT_REF,
            "total_amount": str(_money(total)),
            "total_vat": str(_money(total_vat)),
            "total_with_vat": str(_money(total_with_vat)),
            "total_with_vat_words_ru": amount_in_words_ru(total_with_vat),
            "total_with_vat_words_en": amount_in_words_en(total_with_vat),
            "total_vat_words_ru": amount_in_words_ru(total_vat),
            "total_vat_words_en": amount_in_words_en(total_vat),
            "wells": wells,
            "continue_wells": continue_wells,
            "stop_wells": stop_wells,
            "continue_clause": continue_clause,
            "stop_clause": stop_clause,
            "header_sigs": header_sigs if header_sigs is not None else _DEFAULT_SIGS,
            "sign_sigs": sign_sigs if sign_sigs is not None else _DEFAULT_SIGS,
        },
    )
    db.add(doc)
    db.flush()

    for i, r in enumerate(rows, start=1):
        db.add(DocumentItem(
            document_id=doc.id, line_number=i,
            work_type=r["work_type"], work_group=r["work_group"],
            well_number=r["well_number"], period_label=r["period_label"],
            unit=r["unit"], quantity=r["quantity"],
            price_per_unit=r["price_per_unit"], amount=r["amount"],
            vat_amount=r["vat_amount"], amount_with_vat=r["amount_with_vat"],
        ))
    db.flush()
    return doc
