# backend/documents/service.py
"""
Сервис для работы с документами (бизнес-логика)
"""

from typing import Optional, List, Dict, Any
from datetime import datetime, date
from calendar import monthrange
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func

from .models import Document, DocumentType, DocumentItem, DocumentSignature
from backend.db import get_db

from backend.models import Event, Well
from datetime import datetime, time
from sqlalchemy import and_

from backend.models.well_status import WellStatus

class DocumentService:
    """Сервис для управления документами"""

    def __init__(self, db: Session):
        self.db = db

    # ==========================================
    # СОЗДАНИЕ ДОКУМЕНТОВ
    # ==========================================

    def create_document(
            self,
            doc_type_code: str,
            well_id: Optional[int] = None,
            period_start: Optional[date] = None,
            period_end: Optional[date] = None,
            created_by_name: Optional[str] = None,
            created_by_user_id: Optional[int] = None,
            metadata: Optional[Dict[str, Any]] = None
    ) -> Document:
        """
        Создать новый документ (черновик)

        Args:
            doc_type_code: Код типа документа ('reagent_expense', и т.д.)
            well_id: ID скважины (если требуется)
            period_start, period_end: Период (для периодических актов)
            created_by_name: ФИО создателя
            created_by_user_id: ID пользователя
            metadata: Дополнительные данные

        Returns:
            Document: Созданный документ
        """
        # Получаем тип документа
        doc_type = self.db.query(DocumentType).filter(
            DocumentType.code == doc_type_code
        ).first()

        if not doc_type:
            raise ValueError(f"Тип документа '{doc_type_code}' не найден")

        # Проверяем требования
        if doc_type.requires_well and not well_id:
            raise ValueError(f"Для документа '{doc_type.name_ru}' требуется указать скважину")

        if doc_type.requires_period and not (period_start and period_end):
            raise ValueError(f"Для документа '{doc_type.name_ru}' требуется указать период")

        # Генерируем номер документа
        doc_number = self._generate_doc_number(doc_type, period_start)

        # Создаём документ
        document = Document(
            doc_type_id=doc_type.id,
            doc_number=doc_number,
            well_id=well_id,
            period_start=period_start,
            period_end=period_end,
            created_by_name=created_by_name,
            created_by_user_id=created_by_user_id,
            status='draft',
            meta=metadata or {}
        )

        # Если есть период, вычисляем month и year
        if period_start:
            document.period_month = period_start.month
            document.period_year = period_start.year

        self.db.add(document)
        self.db.commit()
        self.db.refresh(document)

        return document

    def _naive(self, dt: datetime | None) -> datetime | None:
        """Убираем tzinfo, чтобы сравнивать с Event.event_time (timestamp без TZ)."""
        if dt is None:
            return None
        return dt.replace(tzinfo=None)

    def _dt_end_or_max_naive(self, dt_end: datetime | None) -> datetime:
        end = dt_end if dt_end else datetime.max
        end = self._naive(end)
        return end if end else datetime.max

    def _intersect(self, a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime):
        start = max(a_start, b_start)
        end = min(a_end, b_end)
        if start <= end:
            return start, end
        return None

    def _get_status_intervals(self, well_id: int, status_label: str) -> list[tuple[datetime, datetime]]:
        rows = (
            self.db.query(WellStatus)
            .filter(
                WellStatus.well_id == well_id,
                WellStatus.status == status_label
            )
            .order_by(WellStatus.dt_start.asc())
            .all()
        )

        intervals: list[tuple[datetime, datetime]] = []
        for r in rows:
            s = self._naive(r.dt_start)
            e = self._dt_end_or_max_naive(r.dt_end)
            if s:
                intervals.append((s, e))
        return intervals

    def create_reagent_expense_act(
            self,
            well_id: int,
            month: int,
            year: int,
            created_by_name: str = "Администратор",
            status_label: Optional[str] = None
    ) -> Document:

        _, last_day = monthrange(year, month)
        period_start = date(year, month, 1)
        period_end = date(year, month, last_day)

        well = self.db.query(Well).filter(Well.id == well_id).first()
        if not well:
            raise ValueError(f"Скважина с ID {well_id} не найдена")

        month_names_ru = [
            '', 'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
            'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'
        ]

        metadata = {
            "company_executor": "ООО «UNITOOL»",
            "company_client": "СП ООО «Uz-Kor Gas Chemical»",
            "field_name": "Сургил",
            "act_month_name_ru": month_names_ru[month],
            "well_number": str(well.number) if well.number is not None else str(well.id),
        }

        # ✅ ВОТ ЭТОГО НЕ ХВАТАЛО
        if status_label:
            metadata["status_label"] = status_label

        document = self.create_document(
            doc_type_code='reagent_expense',
            well_id=well_id,
            period_start=period_start,
            period_end=period_end,
            created_by_name=created_by_name,
            metadata=metadata
        )

        self._load_reagent_events_to_document(document)
        return document

    def _load_reagent_events_to_document(self, document: Document):
        # 1) well_id -> wells.number -> events.well (TEXT)
        well = self.db.query(Well).filter(Well.id == document.well_id).first()
        if not well or well.number is None:
            raise ValueError(f"Не найдена скважина или пустой wells.number для well_id={document.well_id}")

        well_number_text = str(well.number)

        # 2) Период документа -> datetime границы
        if not (document.period_start and document.period_end):
            raise ValueError("Для reagent_expense требуется period_start/period_end")

        period_start_dt = datetime.combine(document.period_start, time.min)
        period_end_dt = datetime.combine(document.period_end, time.max)

        # 3) Фильтр по статусу (как ты хочешь)
        # передаём в metadata["status_label"] = "Оптимизация" / "Наблюдение" ...
        md = document.meta or {}
        status_label = md.get("status_label")  # None если фильтра нет

        # 4) Сегменты выборки
        segments: list[tuple[datetime, datetime]] = []

        if status_label:
            status_intervals = self._get_status_intervals(document.well_id, status_label)
            for (s, e) in status_intervals:
                inter = self._intersect(s, e, period_start_dt, period_end_dt)
                if inter:
                    segments.append(inter)
        else:
            segments = [(period_start_dt, period_end_dt)]

        # сохраним для аудита/шаблона
        md["segments"] = [{"start": s.isoformat(), "end": e.isoformat()} for s, e in segments]
        md["well_number"] = str(well.number)
        document.meta = md

        # 5) Очистим старые items (если пересоздаём)
        self.db.query(DocumentItem).filter(DocumentItem.document_id == document.id).delete()

        # 6) Выборка events и создание items
        line_number = 1
        total_qty = 0.0
        summary_by_reagent: dict[str, float] = {}

        for (seg_start, seg_end) in segments:
            evs = (
                self.db.query(Event)
                .filter(
                    and_(
                        Event.well == well_number_text,
                        Event.event_time >= seg_start,
                        Event.event_time <= seg_end,

                        # надёжный критерий "вброс реагента":
                        Event.event_type == "reagent",
                        Event.reagent.isnot(None),
                        Event.qty.isnot(None),
                        Event.qty > 0,
                    )
                )
                .order_by(Event.event_time.asc())
                .all()
            )

            for ev in evs:
                reagent = (ev.reagent or "").strip()
                qty = float(ev.qty or 0)

                total_qty += qty
                if reagent:
                    summary_by_reagent[reagent] = summary_by_reagent.get(reagent, 0.0) + qty

                item = DocumentItem(
                    document_id=document.id,
                    line_number=line_number,
                    work_type="Дозирование реагентов",
                    event_time=ev.event_time,
                    event_time_str=ev.event_time.strftime("%d.%m.%Y %H:%M") if ev.event_time else "",
                    quantity=qty,
                    reagent_name=reagent,
                    stage=status_label or "",  # если фильтр есть — заполняем, если нет — пока пусто
                    event_id=ev.id,
                )
                self.db.add(item)
                line_number += 1

        # 7) Итоги в metadata
        document.meta["total_injections"] = line_number - 1
        document.meta["total_qty"] = total_qty
        document.meta["summary_by_reagent"] = summary_by_reagent

        self.db.commit()

    # ==========================================
    # ГЕНЕРАЦИЯ НОМЕРА ДОКУМЕНТА
    # ==========================================

    def _generate_doc_number(
            self,
            doc_type: DocumentType,
            period_start: Optional[date] = None
    ) -> str:
        """
        Генерировать номер документа

        Args:
            doc_type: Тип документа
            period_start: Дата начала периода (для года)

        Returns:
            str: Номер документа (например: 'АРР-2024-001')
        """
        year = period_start.year if period_start else datetime.now().year
        prefix = doc_type.auto_number_prefix or "DOC"

        # Находим максимальный номер за год
        max_doc = self.db.query(func.max(Document.doc_number)).filter(
            and_(
                Document.doc_type_id == doc_type.id,
                Document.doc_number.like(f"{prefix}-{year}-%")
            )
        ).scalar()

        if max_doc:
            # Извлекаем последовательный номер
            parts = max_doc.split('-')
            if len(parts) >= 3:
                try:
                    last_seq = int(parts[2])
                    seq = last_seq + 1
                except ValueError:
                    seq = 1
            else:
                seq = 1
        else:
            seq = 1

        # Форматируем номер
        return f"{prefix}-{year}-{seq:03d}"

    # ==========================================
    # ПОЛУЧЕНИЕ ДОКУМЕНТОВ
    # ==========================================

    def get_document(self, document_id: int) -> Optional[Document]:
        """Получить документ по ID"""
        return self.db.query(Document).filter(Document.id == document_id).first()

    def get_documents_by_well(self, well_id: int) -> List[Document]:
        """Получить все документы по скважине"""
        return self.db.query(Document).filter(
            Document.well_id == well_id
        ).order_by(Document.created_at.desc()).all()

    def get_documents_by_status(self, status: str) -> List[Document]:
        """Получить документы по статусу"""
        return self.db.query(Document).filter(
            Document.status == status
        ).order_by(Document.created_at.desc()).all()

    def get_all_documents(
            self,
            doc_type_code: Optional[str] = None,
            well_id: Optional[int] = None,
            status: Optional[str] = None,
            year: Optional[int] = None,
            month: Optional[int] = None,
            limit: int = 100,
            offset: int = 0
    ) -> List[Document]:
        """
        Получить список документов с фильтрами

        Args:
            doc_type_code: Фильтр по типу документа
            well_id: Фильтр по скважине
            status: Фильтр по статусу
            year, month: Фильтр по периоду
            limit, offset: Пагинация

        Returns:
            List[Document]: Список документов
        """
        query = self.db.query(Document)

        # Применяем фильтры
        if doc_type_code:
            query = query.join(DocumentType).filter(DocumentType.code == doc_type_code)

        if well_id:
            query = query.filter(Document.well_id == well_id)

        if status:
            query = query.filter(Document.status == status)

        if year:
            query = query.filter(Document.period_year == year)

        if month:
            query = query.filter(Document.period_month == month)

        # Сортировка и пагинация
        return query.order_by(Document.created_at.desc()).limit(limit).offset(offset).all()

    # ==========================================
    # ИЗМЕНЕНИЕ СТАТУСА
    # ==========================================

    def update_status(self, document_id: int, new_status: str) -> Document:
        """Изменить статус документа"""
        document = self.get_document(document_id)
        if not document:
            raise ValueError(f"Документ с ID {document_id} не найден")

        valid_statuses = ['draft', 'generated', 'signed', 'sent', 'archived', 'cancelled']
        if new_status not in valid_statuses:
            raise ValueError(f"Недопустимый статус: {new_status}")

        document.status = new_status
        document.updated_at = datetime.now()

        self.db.commit()
        self.db.refresh(document)

        return document

    def sign_document(
            self,
            document_id: int,
            signed_by_name: str,
            signed_by_position: str = ""
    ) -> Document:
        """Подписать документ"""
        document = self.get_document(document_id)
        if not document:
            raise ValueError(f"Документ с ID {document_id} не найден")

        if document.status != 'generated':
            raise ValueError(f"Документ должен быть в статусе 'generated' для подписания")

        document.status = 'signed'
        document.signed_at = datetime.now()
        document.signed_by_name = signed_by_name
        document.signed_by_position = signed_by_position

        self.db.commit()
        self.db.refresh(document)

        return document

    # ==========================================
    # УДАЛЕНИЕ
    # ==========================================

    def delete_document(self, document_id: int):
        """Удалить документ (только черновики)"""
        document = self.get_document(document_id)
        if not document:
            raise ValueError(f"Документ с ID {document_id} не найден")

        if document.status != 'draft':
            raise ValueError("Можно удалять только черновики")

        self.db.delete(document)
        self.db.commit()