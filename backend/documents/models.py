# backend/documents/models.py
"""
Модели SQLAlchemy для системы управления актами
"""

from sqlalchemy import (
    Column, Integer, String, Text, Boolean,
    DateTime, Date, ForeignKey, CheckConstraint,
    UniqueConstraint, Index
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
from backend.db import Base
from sqlalchemy import DateTime
from datetime import datetime

import sqlalchemy as sa

class DocumentType(Base):
    """Тип документа (акта)"""
    __tablename__ = "document_types"

    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, nullable=False, index=True)
    name_ru = Column(String(200), nullable=False)
    name_en = Column(String(200))
    category = Column(String(20), nullable=False, default='operational', index=True)

    # Шаблоны
    latex_template_name = Column(String(100))
    excel_template_name = Column(String(100))

    # Настройки периодичности
    is_periodic = Column(Boolean, default=False)
    period_type = Column(String(20))  # 'monthly', 'quarterly', 'yearly'

    # Требования
    requires_well = Column(Boolean, default=False)
    requires_period = Column(Boolean, default=False)

    # Автономерация
    auto_number_prefix = Column(String(20))
    auto_number_format = Column(String(50), default='{prefix}-{year}-{seq:03d}')

    # Дополнительно
    description = Column(Text)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True, index=True)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


    # Отношения
    documents = relationship("Document", back_populates="doc_type")

    def __repr__(self):
        return f"<DocumentType(code='{self.code}', name_ru='{self.name_ru}')>"


class Document(Base):
    """Документ (акт)"""
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)

    # Классификация
    doc_type_id = Column(Integer, ForeignKey('document_types.id'), nullable=False, index=True)
    doc_number = Column(String(100), unique=True, index=True)

    # Связи
    well_id = Column(Integer, ForeignKey('wells.id'), index=True)

    # Период
    period_start = Column(Date)
    period_end = Column(Date)
    period_month = Column(Integer)  # 1-12
    period_year = Column(Integer)

    # Создание
    created_at = Column(DateTime, default=datetime.now, index=True)
    created_by_user_id = Column(Integer)
    created_by_name = Column(String(200))

    # Статус
    status = Column(
        String(50),
        default='draft',
        nullable=False,
        index=True
    )

    # Подписание
    signed_at = Column(DateTime)
    signed_by_name = Column(String(200))
    signed_by_position = Column(String(200))

    # Файлы
    pdf_filename = Column(String(500))
    excel_filename = Column(String(500))
    latex_source = Column(Text)

    # Метаданные (JSON)
    # NB: имя атрибута `metadata` зарезервировано в SQLAlchemy Declarative API,
    # поэтому используем `meta`, а имя колонки в БД оставляем `metadata`.
    meta = Column("metadata", JSONB, nullable=False, default=dict)

    # Заметки
    notes = Column(Text)

    # Версионирование
    version = Column(Integer, default=1)
    parent_id = Column(Integer, ForeignKey('documents.id'))

    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    deleted_at = Column(DateTime, nullable=True, index=True)
    # Ограничения
    __table_args__ = (
        CheckConstraint(
            status.in_(['draft', 'generated', 'signed', 'sent', 'archived', 'cancelled']),
            name='valid_status'
        ),
        CheckConstraint(
            '(period_start IS NULL AND period_end IS NULL) OR '
            '(period_start IS NOT NULL AND period_end IS NOT NULL)',
            name='valid_period'
        ),
        Index('idx_documents_period', 'period_year', 'period_month'),
        Index('idx_documents_period_dates', 'period_start', 'period_end'),
    )

    # Отношения
    doc_type = relationship("DocumentType", back_populates="documents")
    well = relationship("Well")
    items = relationship("DocumentItem", back_populates="document", cascade="all, delete-orphan")
    signatures = relationship("DocumentSignature", back_populates="document", cascade="all, delete-orphan")
    children = relationship("Document", backref="parent", remote_side=[id])

    def __repr__(self):
        return f"<Document(id={self.id}, number='{self.doc_number}', status='{self.status}')>"

    @property
    def is_editable(self):
        """Можно ли редактировать документ"""
        return self.status == 'draft'

    @property
    def is_signable(self):
        """Можно ли подписывать документ"""
        return self.status == 'generated'

    @property
    def status_display(self):
        """Отображение статуса"""
        status_map = {
            'draft': '📝 Черновик',
            'generated': '📄 Создан',
            'signed': '✅ Подписан',
            'sent': '📧 Отправлен',
            'archived': '📦 Архив',
            'cancelled': '❌ Отменён'
        }
        return status_map.get(self.status, self.status)




class DocumentItem(Base):
    """Строка документа (запись о вбросе, работе и т.д.)"""
    __tablename__ = "document_items"

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey('documents.id'), nullable=False, index=True)

    # Порядок
    line_number = Column(Integer, nullable=False)

    # Данные строки
    work_type = Column(String(200))  # 'Дозирование пенных реагентов'
    event_time = Column(DateTime)
    event_time_str = Column(String(50))  # '01.04.2025 12:19'

    quantity = Column(Integer, default=1)
    reagent_name = Column(String(200))
    stage = Column(String(100))  # 'Оптимизация Optimization'

    # Связь с событием
    event_id = Column(Integer, index=True)  # bigint в БД

    notes = Column(Text)

    __table_args__ = (
        UniqueConstraint('document_id', 'line_number', name='unique_line_per_document'),
        Index('idx_document_items_line', 'document_id', 'line_number'),
    )

    # Отношения
    document = relationship("Document", back_populates="items")

    def __repr__(self):
        return f"<DocumentItem(id={self.id}, line={self.line_number}, reagent='{self.reagent_name}')>"


class DocumentSignature(Base):
    """Подпись на документе"""
    __tablename__ = "document_signatures"

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey('documents.id'), nullable=False, index=True)

    # Роль
    role = Column(String(100), nullable=False, index=True)  # 'executor', 'client', 'geologist'
    role_title_ru = Column(String(200))  # 'Представитель Исполнителя'

    # Данные подписанта
    signer_name = Column(String(200))
    signer_position = Column(String(200))
    company_name = Column(String(300))

    # Подписание
    signed_at = Column(DateTime)
    signature_image_path = Column(String(500))

    # Порядок
    sort_order = Column(Integer, default=0)

    notes = Column(Text)

    # Отношения
    document = relationship("Document", back_populates="signatures")

    def __repr__(self):
        return f"<DocumentSignature(id={self.id}, role='{self.role}', signer='{self.signer_name}')>"