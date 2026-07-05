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


ADAPT_MIN_DAYS = 1    # адаптация < 1 дня = ошибка (исключаем)
ADAPT_WARN_DAYS = 5   # адаптация < 5 дней = предупреждение (ориентир ~10)

DOSING_CFG = [
    ("foam", "foam_dosing", "Дозирование пенных реагентов / Foam reagent dosing"),
    ("inhibitor", "inhibitor_dosing", "Дозирование ингибирующих реагентов / Inhibitor reagent dosing"),
]


def _adaptation_by_well(db: Session, dt_from, dt_to) -> dict:
    """Завершённые в периоде адаптации по скважине + валидация длительности."""
    completed = (
        db.query(WellStatus, Well.number)
        .join(Well, Well.id == WellStatus.well_id)
        .filter(WellStatus.status == "Адаптация")
        .filter(WellStatus.dt_end.isnot(None))
        .filter(WellStatus.dt_end >= dt_from, WellStatus.dt_end <= dt_to)
        .order_by(Well.number.asc(), WellStatus.dt_end.asc())
        .all()
    )
    out: dict[str, dict] = {}
    for st, well_no in completed:
        d = out.setdefault(str(well_no), {
            "well_id": st.well_id, "count": 0,
            "start": st.dt_start.date(), "end": st.dt_end.date(),
        })
        d["count"] += 1
        d["start"] = min(d["start"], st.dt_start.date())
        d["end"] = max(d["end"], st.dt_end.date())
    for d in out.values():
        d["days"] = (d["end"] - d["start"]).days
        d["valid"] = d["days"] >= ADAPT_MIN_DAYS
        d["warn_short"] = d["valid"] and d["days"] < ADAPT_WARN_DAYS
    return out


def _opt_by_well(db: Session, p_start, p_end, dt_from, dt_to) -> dict:
    """Интервалы оптимизации по скважине (клип по месяцу)."""
    out: dict[str, dict] = {}
    for st, well_no in _wells_by_status(db, "Оптимизация", dt_from, dt_to):
        s = max(st.dt_start.date(), p_start)
        e = min(st.dt_end.date() if st.dt_end else p_end, p_end)
        if e >= s:
            o = out.setdefault(str(well_no), {"well_id": st.well_id, "intervals": []})
            o["intervals"].append((s, e))
    return out


def _build_rows(db: Session, year: int, month: int, decisions: dict | None = None):
    """Строит строки акта с учётом ПО-СКВАЖИННЫХ решений.

    decisions: {well_number: {"mode": "adaptation"|"ineffective"|"exclude",
                              "reagents": bool, "time": bool}}
    Возвращает (rows, catalog, warnings). catalog — данные для панели «Скважины и этапы».
    Без decisions вывод = поведению по умолчанию (адаптация оплачивается, реагенты — в оптимизации).
    """
    decisions = decisions or {}
    p_start, p_end = _period(year, month)
    dim = monthrange(year, month)[1]
    dt_from = datetime.combine(p_start, time.min)
    dt_to = datetime.combine(p_end, time.max)

    adapt = _adaptation_by_well(db, dt_from, dt_to)
    opt = _opt_by_well(db, p_start, p_end, dt_from, dt_to)

    # реагентные события за месяц по скважине (для гейтинга по периодам)
    grp_of = dict(db.query(ReagentCatalog.name, ReagentCatalog.act_group)
                  .filter(ReagentCatalog.act_group.in_(["foam", "inhibitor"])).all())
    events_by_well: dict[str, list] = {}
    if grp_of:
        for well, reagent, et in (db.query(Event.well, Event.reagent, Event.event_time)
                .filter(Event.event_time >= dt_from, Event.event_time <= dt_to)
                .filter(Event.reagent.in_(list(grp_of.keys())))
                .filter(Event.qty.isnot(None), Event.qty > 0).all()):
            wkey = str(well).strip()
            if wkey:
                events_by_well.setdefault(wkey, []).append((et, grp_of.get(reagent)))

    def opt_row(well_id, wkey, s, e):
        worked = (e - s).days
        if worked <= 0:
            return None
        amount = Decimal("0")
        for i in range(worked):
            monthly = _opt_monthly_on(db, well_id, s + timedelta(days=i)) or Decimal("0")
            amount += _money(monthly / Decimal(dim))
        daily = (_opt_monthly_on(db, well_id, s) or Decimal("0")) / Decimal(dim)
        return _row("optimization",
                    "Оптимизация (по формуле дни/дней в месяце × ежемесячный платёж) / Optimization",
                    wkey, _fmt_range(s, e), "сут", worked, _money(daily), amount=amount)

    rows: list[dict] = []
    warnings: list[str] = []
    catalog: list[dict] = []
    reagent_intervals: dict[str, list] = {}  # wkey -> интервалы для подсчёта реагентов

    # ── Адаптация: решение по скважине ──
    for wkey in sorted(adapt, key=lambda x: (len(x), x)):
        d = adapt[wkey]
        default_mode = "adaptation" if d["valid"] else "exclude"
        dec = decisions.get(wkey, {})
        mode = dec.get("mode", default_mode)
        reimb_reagents = bool(dec.get("reagents", True))
        reimb_time = bool(dec.get("time", True))
        w_warn = []
        if not d["valid"]:
            w_warn.append(f"адаптация < {ADAPT_MIN_DAYS} дня ({d['days']} дн) — исключена")
            mode = "exclude"
        elif d["warn_short"]:
            w_warn.append(f"адаптация {d['days']} дн (< ориентира {ADAPT_WARN_DAYS}) — проверить")

        if mode == "adaptation":
            price = _price_for(db, "adaptation", d["well_id"], p_start) or Decimal("0")
            rows.append(_row("adaptation", "Адаптация / Adaptation", wkey,
                             _fmt_range(d["start"], d["end"]), "скв операция",
                             d["count"], price, amount=price * Decimal(d["count"])))
        elif mode == "ineffective":
            if reimb_time:
                r = opt_row(d["well_id"], wkey, d["start"], d["end"])
                if r:
                    rows.append(r)
            if reimb_reagents:
                reagent_intervals.setdefault(wkey, []).append((d["start"], d["end"]))
        # mode == "exclude" → ничего
        warnings.extend(f"скв.{wkey}: {w}" for w in w_warn)
        catalog.append({"well": wkey, "stage": "adaptation",
                        "period": _fmt_range(d["start"], d["end"]), "days": d["days"],
                        "valid": d["valid"], "mode": mode,
                        "reagents": reimb_reagents, "time": reimb_time, "warnings": w_warn})

    # ── Оптимизация (реальный статус): всегда billable, реагенты за период оптимизации ──
    for wkey in sorted(opt, key=lambda x: (len(x), x)):
        o = opt[wkey]
        total = 0
        for s, e in o["intervals"]:
            r = opt_row(o["well_id"], wkey, s, e)
            if r:
                rows.append(r); total += (e - s).days
        reagent_intervals.setdefault(wkey, []).extend(o["intervals"])
        per = _fmt_range(o["intervals"][0][0], o["intervals"][-1][1]) if o["intervals"] else ""
        catalog.append({"well": wkey, "stage": "optimization", "period": per,
                        "days": total, "mode": "optimization", "warnings": []})

    # ── Дозирование: подсчёт реагентов по собранным интервалам скважин ──
    dosing = {"foam": [], "inhibitor": []}
    for wkey, intervals in reagent_intervals.items():
        foam, inhib = [], []
        for et, g in events_by_well.get(wkey, []):
            if any(s <= et.date() <= e for s, e in intervals):
                (foam if g == "foam" else inhib).append(et)
        if foam:
            dosing["foam"].append((wkey, foam))
        if inhib:
            dosing["inhibitor"].append((wkey, inhib))
    for grp, work_group, title in DOSING_CFG:
        for wkey, ets in sorted(dosing[grp], key=lambda kv: (len(kv[0]), kv[0])):
            w = db.query(Well).filter(sa.cast(Well.number, sa.String) == wkey).first()
            price = _price_for(db, work_group, w.id if w else None, p_start) or Decimal("0")
            rows.append(_row(work_group, title, wkey,
                             _fmt_range(min(ets).date(), max(ets).date()), "операция",
                             len(ets), price, amount=price * Decimal(len(ets))))

    return rows, catalog, warnings


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


def create_invoice_from_act(db: Session, act_id: int,
                            created_by_name: str | None = None) -> Document:
    """Счёт-фактура ИЗ акта: копирует строки и итоги (сумма ГАРАНТИРОВАННО = акту),
    свой номер (СФ), привязка parent_id=act. Одна СФ на акт (пересобирается)."""
    act = db.query(Document).filter(Document.id == act_id).first()
    if not act:
        raise ValueError("Акт не найден")
    dt = db.query(DocumentType).filter(DocumentType.code == "financial_invoice").first()
    if not dt:
        raise ValueError("DocumentType 'financial_invoice' не найден (нужен сидинг)")
    year, month = int(act.period_year), int(act.period_month)

    # rebuild: удалить прежние СФ за этот период (сохранив номер)
    existing = (db.query(Document)
                .filter(Document.doc_type_id == dt.id,
                        Document.period_year == year, Document.period_month == month).all())
    old_no = next((e.meta.get("invoice_seq") for e in existing if e.meta and e.meta.get("invoice_seq")), None)
    if existing:
        ids = [e.id for e in existing]
        db.query(DocumentItem).filter(DocumentItem.document_id.in_(ids)).delete(synchronize_session=False)
        db.query(Document).filter(Document.id.in_(ids)).delete(synchronize_session=False)
        db.flush()
    if old_no:
        seq = int(old_no)
    else:
        maxno = db.query(
            sa.func.max(sa.cast(Document.meta["invoice_seq"].astext, sa.Integer))
        ).filter(Document.doc_type_id == dt.id).scalar() or 0
        seq = int(maxno) + 1

    meta = dict(act.meta or {})          # копия итогов/прописи из акта → сумма идентична
    meta["invoice_seq"] = seq
    meta["invoice_no"] = f"{seq}-c"
    meta["act_ref"] = act.doc_number

    inv = Document(
        doc_type_id=dt.id, doc_number=f"СФ-{year}-{month:02d}", well_id=None,
        period_start=act.period_start, period_end=act.period_end,
        period_month=month, period_year=year, status="draft",
        created_by_name=created_by_name, parent_id=act.id, meta=meta,
    )
    db.add(inv)
    db.flush()
    for it in act.items:
        db.add(DocumentItem(
            document_id=inv.id, line_number=it.line_number, work_type=it.work_type,
            work_group=it.work_group, well_number=it.well_number, period_label=it.period_label,
            unit=it.unit, quantity=it.quantity, price_per_unit=it.price_per_unit,
            amount=it.amount, vat_amount=it.vat_amount, amount_with_vat=it.amount_with_vat,
        ))
    db.flush()
    return inv


def get_well_catalog(db: Session, year: int, month: int, decisions: dict | None = None):
    """Каталог «Скважины и этапы» + предупреждения (на лету, для панели ревизии)."""
    _, catalog, warnings = _build_rows(db, year, month, decisions)
    return catalog, warnings


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
                        stop_clause: str = "3.17",
                        well_decisions: dict | None = None) -> Document:
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

    rows, well_catalog, warnings = _build_rows(db, year, month, well_decisions)
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
            "well_catalog": well_catalog,     # для панели «Скважины и этапы»
            "well_decisions": well_decisions or {},
            "warnings": warnings,
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
