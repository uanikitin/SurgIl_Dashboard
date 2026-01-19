# backend/documents/generator.py
"""
Генератор PDF и Excel документов
"""

import os
import subprocess
from typing import Optional
from pathlib import Path
from jinja2 import Template, Environment, FileSystemLoader
from datetime import date

from .models import Document


class DocumentGenerator:
    """Генератор документов (PDF через LaTeX, Excel через openpyxl)"""

    def __init__(self, templates_dir: str = "backend/documents/templates"):
        """
        Args:
            templates_dir: Директория с шаблонами
        """
        self.templates_dir = Path(templates_dir)
        self.latex_dir = self.templates_dir / "latex"
        self.excel_dir = self.templates_dir / "excel"
        self.output_dir = Path("backend/static/generated")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Создаём под директории
        (self.output_dir / "pdf").mkdir(exist_ok=True)
        (self.output_dir / "excel").mkdir(exist_ok=True)
        (self.output_dir / "temp").mkdir(exist_ok=True)

        # Jinja2 environment для LaTeX
        self.latex_env = Environment(
            loader=FileSystemLoader(str(self.latex_dir)),
            block_start_string='\\BLOCK{',
            block_end_string='}',
            variable_start_string='\\VAR{',
            variable_end_string='}',
            comment_start_string='\\#{',
            comment_end_string='}',
            line_statement_prefix='%%',
            line_comment_prefix='%#',
            trim_blocks=True,
            autoescape=False,
        )

    # ==========================================
    # ГЕНЕРАЦИЯ PDF
    # ==========================================

    def generate_pdf(self, document: Document) -> str:
        """
        Сгенерировать PDF для документа

        Args:
            document: Объект Document

        Returns:
            str: Путь к сгенерированному PDF (относительно static/)
        """
        # Получаем имя шаблона
        template_name = document.doc_type.latex_template_name
        if not template_name:
            raise ValueError(f"Для типа '{document.doc_type.code}' не указан LaTeX шаблон")

        # Готовим данные для шаблона
        context = self._prepare_context(document)

        # Рендерим LaTeX
        latex_source = self._render_latex_template(template_name, context)

        # Сохраняем исходник в документ
        document.latex_source = latex_source

        # Компилируем в PDF
        pdf_path = self._compile_latex_to_pdf(document, latex_source)

        # Сохраняем путь в документ
        document.pdf_filename = f"generated/pdf/{Path(pdf_path).name}"

        return document.pdf_filename

    def _prepare_context(self, document: Document) -> dict:
        """
        Подготовить контекст для шаблона

        Args:
            document: Объект Document

        Returns:
            dict: Контекст для Jinja2
        """
        # Базовый контекст
        context = {
            "doc": document,
            "doc_number": document.doc_number,
            "doc_type": document.doc_type,
            "well": document.well,
            "items": document.items,
            "period_start": document.period_start,
            "period_end": document.period_end,
            "metadata": document.metadata,
        }

        # Добавляем метаданные
        if document.metadata:
            context.update(document.metadata)

        # Специфичная обработка для акта расхода реагентов
        if document.doc_type.code == 'reagent_expense':
            context.update(self._prepare_reagent_expense_context(document))

        return context

    def _prepare_reagent_expense_context(self, document: Document) -> dict:
        """Подготовить контекст для акта расхода реагентов"""
        # Форматируем даты
        period_start_str = document.period_start.strftime("%d.%m.%Y") if document.period_start else ""
        period_end_str = document.period_end.strftime("%d.%m.%Y") if document.period_end else ""

        # Номер акта (число) и месяц
        doc_num_parts = document.doc_number.split('-')
        act_num = doc_num_parts[-1] if len(doc_num_parts) > 0 else "1"
        act_month = document.metadata.get("act_month_name_ru", "")

        # Номер скважины
        well_number = document.metadata.get("well_number",
                                            str(document.well.number) if document.well and document.well.number else "")

        # Дата акта (последний день периода)
        act_date = period_end_str

        # Подсчёты
        total_injections = len(document.items)
        summary = document.metadata.get("summary_by_type", {})

        return {
            "theactnum": act_num,
            "theactmonth": act_month,
            "theactwell": well_number,
            "theactdate": act_date,
            "period_start_str": period_start_str,
            "period_end_str": period_end_str,
            "total_injections": total_injections,
            "summary_foam": summary.get("foam", 0),
            "summary_inhibitor": summary.get("inhibitor", 0),
            "company_executor": document.metadata.get("company_executor", "ООО «UNITOOL»"),
            "company_client": document.metadata.get("company_client", "СП ООО «Uz-Kor Gas Chemical»"),
            "field_name": document.metadata.get("field_name", "Сургил"),
        }

    def _render_latex_template(self, template_name: str, context: dict) -> str:
        """
        Рендерить LaTeX шаблон

        Args:
            template_name: Имя файла шаблона
            context: Контекст для Jinja2

        Returns:
            str: Рендеренный LaTeX код
        """
        template = self.latex_env.get_template(template_name)
        return template.render(**context)

    def _compile_latex_to_pdf(self, document: Document, latex_source: str) -> str:
        """
        Скомпилировать LaTeX в PDF

        Args:
            document: Объект Document
            latex_source: LaTeX код

        Returns:
            str: Полный путь к PDF файлу
        """
        # Имя файла без расширения
        base_name = f"{document.doc_number.replace('/', '-')}"

        # Временная директория
        temp_dir = self.output_dir / "temp"
        tex_file = temp_dir / f"{base_name}.tex"

        # Сохраняем .tex файл
        with open(tex_file, 'w', encoding='utf-8') as f:
            f.write(latex_source)

        # Компилируем в PDF (xelatex для поддержки русского)
        # Запускаем дважды для правильных ссылок
        for _ in range(2):
            result = subprocess.run(
                ['xelatex', '-interaction=nonstopmode', f'-output-directory={temp_dir}', str(tex_file)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(temp_dir)
            )

            if result.returncode != 0:
                error_msg = result.stderr.decode('utf-8', errors='ignore')
                raise RuntimeError(f"Ошибка компиляции LaTeX: {error_msg}")

        # Перемещаем PDF в output
        temp_pdf = temp_dir / f"{base_name}.pdf"
        final_pdf = self.output_dir / "pdf" / f"{base_name}.pdf"

        if temp_pdf.exists():
            temp_pdf.rename(final_pdf)
        else:
            raise FileNotFoundError(f"PDF файл не создан: {temp_pdf}")

        # Очищаем временные файлы
        for ext in ['.aux', '.log', '.out', '.tex']:
            temp_file = temp_dir / f"{base_name}{ext}"
            if temp_file.exists():
                temp_file.unlink()

        return str(final_pdf)

    # ==========================================
    # ГЕНЕРАЦИЯ EXCEL
    # ==========================================

    def generate_excel(self, document: Document) -> str:
        """
        Сгенерировать Excel для документа

        Args:
            document: Объект Document

        Returns:
            str: Путь к сгенерированному Excel (относительно static/)
        """
        try:
            from openpyxl import Workbook, load_workbook
            from openpyxl.styles import Font, Alignment, Border, Side
        except ImportError:
            raise ImportError("Установите openpyxl: pip install openpyxl")

        # Получаем имя шаблона
        template_name = document.doc_type.excel_template_name

        if template_name and (self.excel_dir / template_name).exists():
            # Используем шаблон
            wb = load_workbook(self.excel_dir / template_name)
            ws = wb.active
        else:
            # Создаём с нуля
            wb = Workbook()
            ws = wb.active
            ws.title = "Акт"

        # Для акта расхода реагентов
        if document.doc_type.code == 'reagent_expense':
            self._fill_reagent_expense_excel(ws, document)

        # Сохраняем
        base_name = f"{document.doc_number.replace('/', '-')}"
        excel_path = self.output_dir / "excel" / f"{base_name}.xlsx"
        wb.save(excel_path)

        # Сохраняем путь в документ
        document.excel_filename = f"generated/excel/{excel_path.name}"

        return document.excel_filename

    def _fill_reagent_expense_excel(self, ws, document: Document):
        """Заполнить Excel для акта расхода реагентов"""
        from openpyxl.styles import Font, Alignment

        # Заголовок
        ws['A1'] = f"Акт №{document.doc_number}"
        ws['A1'].font = Font(bold=True, size=14)

        ws['A2'] = f"дозирования реагентов на скважине №{document.metadata.get('well_number', '')}"
        ws['A3'] = f"месторождения {document.metadata.get('field_name', 'Сургил')}"
        ws[
            'A4'] = f"в период с {document.period_start.strftime('%d.%m.%Y')} по {document.period_end.strftime('%d.%m.%Y')}"

        # Заголовки таблицы
        row = 6
        headers = ['№', 'Вид работ', 'Дата и время вброса', 'К-во', 'Тип реагента', 'Этап']
        for col, header in enumerate(headers, start=1):
            cell = ws.cell(row=row, column=col, value=header)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal='center')

        # Данные
        row += 1
        for item in document.items:
            ws.cell(row=row, column=1, value=item.line_number)
            ws.cell(row=row, column=2, value=item.work_type)
            ws.cell(row=row, column=3, value=item.event_time_str)
            ws.cell(row=row, column=4, value=item.quantity)
            ws.cell(row=row, column=5, value=item.reagent_name)
            ws.cell(row=row, column=6, value=item.stage)
            row += 1

        # Итого
        row += 1
        ws.cell(row=row, column=1, value=f"Всего вбросов — {len(document.items)}")
        ws.cell(row=row, column=1).font = Font(bold=True)

        # Ширина колонок
        ws.column_dimensions['A'].width = 5
        ws.column_dimensions['B'].width = 35
        ws.column_dimensions['C'].width = 20
        ws.column_dimensions['D'].width = 8
        ws.column_dimensions['E'].width = 20
        ws.column_dimensions['F'].width = 25