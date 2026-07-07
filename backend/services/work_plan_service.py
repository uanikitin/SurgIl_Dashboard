"""Сервис «План работ» (сценарий ОПТИМИЗАЦИЯ).

Единственная ответственность модуля — собрать префилл Таблицы 1.1 из БД по
НОМЕРУ скважины (строка). Ключ — номер, а не `wells.id`: конструкция/сводки
покрывают ВСЕ скважины месторождения (`well_construction`, `well_daily`), тогда
как таблица `wells` — лишь подмножество LoRa-скважин (напр. скв. 74 в `wells`
отсутствует, но есть в `well_construction`/`well_daily`).

Источники:
- статическая геометрия + перфорация — `well_construction` / `well_perforation_interval`;
- операционные параметры (давления, дебит, штуцер, продувка) — последняя строка
  `well_daily` (переиспользуем `customer_daily_service.load_for_well`).

Пластовое давление и периодичность продувки в БД отсутствуют — остаются пустыми,
оператор дозаполняет в форме перед генерацией.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.services import customer_daily_service as csvc


DEFAULT_SIGS = {
    "sog_position": "Заместитель Председателя Правления",
    "sog_org": 'JV "Uz-Kor Gas Chemical" LLC',
    "sog_name": "Шарапов Ж. М.",
    "sog_date": "2025 г",
    "utv_position": "Главный инженер",
    "utv_org": "ТОО «UNITOOL»",
    "utv_name": "Верба А.Ю.",
    "utv_date": "2025 г",
}

# Порядок ключей Таблицы 1.1 (совпадает с шаблоном work_plan_template.docx).
TABLE11_KEYS = [
    "well_no", "prod_casing_diam", "prod_casing_depth", "bottomhole", "horizon",
    "perforation", "tubing_diam", "tubing_shoe", "packer", "adapter",
    "pattern_stuck", "choke", "p_tube", "p_annulus", "p_flowline", "q_gas",
    "p_static", "p_reservoir", "purge_freq", "purge_dur", "gdi",
]


def _num(v: Any) -> str:
    """Число → строка без лишнего '.0' ('140', '41.1'). None/'' → '-'."""
    if v is None or v == "":
        return "-"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf
        return "-"
    if f == int(f):
        return str(int(f))
    return f"{f:g}"


def _fmt_perforation(intervals: list[dict]) -> str:
    """[{top,bottom}] → '2276,5-2285,5 2303-2310' (запятая-десятичная, пробел между). Нет → '-'."""
    parts = []
    for it in intervals or []:
        top = _num(it.get("top_depth_m")).replace(".", ",")
        bot = _num(it.get("bottom_depth_m")).replace(".", ",")
        if top != "-" and bot != "-":
            parts.append(f"{top}-{bot}")
        elif top != "-":
            parts.append(top)
    return " ".join(parts) or "-"


def available_wells(db: Session) -> list[str]:
    """Номера скважин, у которых есть конструкция (для выбора в форме)."""
    rows = db.execute(text(
        "SELECT DISTINCT TRIM(well_no) AS n FROM well_construction "
        "WHERE well_no IS NOT NULL ORDER BY 1"
    )).fetchall()
    return sorted({r[0] for r in rows if r[0]}, key=lambda x: (len(x), x))


def build_table11_prefill(db: Session, well_no: str) -> dict[str, str]:
    """Собрать значения Таблицы 1.1 из БД по номеру скважины (все — строки)."""
    wno = str(well_no).strip()
    t = {k: "-" for k in TABLE11_KEYS}  # нет данных → "-"
    t["well_no"] = wno

    # 1) Конструкция (последняя запись) + перфорация
    row = db.execute(text("""
        SELECT id, prod_casing_diam_mm, prod_casing_depth_m, current_bottomhole_m,
               horizon, tubing_diam_mm, tubing_shoe_depth_m, packer_depth_m,
               adapter_depth_m, pattern_stuck_depth_m, choke_diam_mm
        FROM well_construction
        WHERE TRIM(well_no) = :wno
        ORDER BY data_as_of DESC NULLS LAST, id DESC
        LIMIT 1
    """), {"wno": wno}).fetchone()
    if row:
        t["prod_casing_diam"] = _num(row[1])
        t["prod_casing_depth"] = _num(row[2])
        t["bottomhole"] = _num(row[3])
        t["horizon"] = (row[4] or "").strip() or "-"
        t["tubing_diam"] = _num(row[5])
        t["tubing_shoe"] = _num(row[6])
        t["packer"] = _num(row[7])
        t["adapter"] = _num(row[8])
        t["pattern_stuck"] = _num(row[9])
        t["choke"] = _num(row[10])
        perf = db.execute(text("""
            SELECT interval_index, top_depth_m, bottom_depth_m
            FROM well_perforation_interval
            WHERE well_construction_id = :cid
            ORDER BY interval_index, top_depth_m
        """), {"cid": row[0]}).fetchall()
        t["perforation"] = _fmt_perforation(
            [{"top_depth_m": p[1], "bottom_depth_m": p[2]} for p in perf])

    # 2) Операционные параметры — последняя суточная сводка well_daily
    df = csvc.load_for_well(db, wno)
    if not df.empty:
        r = df.iloc[-1]
        # choke из well_daily переопределяет well_construction (если есть)
        if r.get("choke_mm") is not None:
            t["choke"] = _num(r.get("choke_mm"))
        t["p_tube"] = _num(r.get("p_wellhead"))
        t["p_annulus"] = "пак" if bool(r.get("annular_packer")) else _num(r.get("p_annular"))
        t["p_flowline"] = _num(r.get("p_flowline"))
        t["q_gas"] = _num(r.get("q_gas_total"))
        t["p_static"] = _num(r.get("p_static"))
        t["purge_dur"] = _num(r.get("shutdown_min"))

    # 3) Нет в БД — оператор заполнит вручную
    t["gdi"] = "ГДИ/ГКИ/ОПП"
    return t
