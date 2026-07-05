"""Однократно: строит docxtpl-шаблон Счёта-фактуры из файла клиента.

- Берёт ГОТОВУЮ таблицу работ из шаблона акта (цикл {%tr%}, итог, шрифт/ширины/
  выравнивание уже настроены) и вставляет её в файл СФ вместо разбитой на две.
- Тегирует номер/дату в шапке. Реквизиты банка и подписи остаются статичными.

Вход:  financial_act_template.docx (должен быть уже собран) + файл СФ клиента.
Выход: backend/documents/templates/docx/financial_invoice_template.docx
"""
import copy
from pathlib import Path
from docx import Document as Dx

ACT_TPL = "backend/documents/templates/docx/financial_act_template.docx"
SRC = "/Users/volodymyrnikitin/Downloads/CФ 6-с апрель_3.docx"
OUT = Path("backend/documents/templates/docx/financial_invoice_template.docx")


def set_para(paragraph, text: str):
    runs = paragraph.runs
    if not runs:
        paragraph.add_run(text)
        return
    runs[0].text = text
    for r in runs[1:]:
        r.text = ""


def main():
    act = Dx(ACT_TPL)
    works_tbl = copy.deepcopy(act.tables[1]._tbl)  # готовая таблица работ

    d = Dx(SRC)
    # заголовок + номер/дата
    for p in d.paragraphs:
        t = p.text.strip()
        if t.startswith("СЧЕТ-ФАКТУРА") or t.startswith("СЧЁТ-ФАКТУРА"):
            set_para(p, "СЧЁТ-ФАКТУРА / INVOICE")
        elif t.startswith("№"):
            set_para(p, "№ {{ invoice_no }} от {{ act_date }}")

    # убрать ДУБЛИ описания услуги (в исходнике повторяется 4 раза) — оставить один раз
    kept = 0
    for p in list(d.paragraphs):
        t = p.text.strip()
        if t.startswith("на оказание услуг") or t.startswith("по оптимизации"):
            kept += 1
            if kept > 2:  # первые два абзаца (описание услуги) оставляем, остальные — дубли
                p._p.getparent().remove(p._p)

    # заменить разбитую таблицу работ (T1 + T2) на одну готовую
    t1 = d.tables[1]._tbl
    t2 = d.tables[2]._tbl
    t1.addprevious(works_tbl)
    t1.getparent().remove(t1)
    t2.getparent().remove(t2)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(OUT))
    print("Saved invoice template:", OUT)


if __name__ == "__main__":
    main()
