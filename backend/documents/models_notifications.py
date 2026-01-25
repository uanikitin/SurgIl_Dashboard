# backend/documents/models_notifications.py
"""
Модели для системы уведомлений и автоматизации документов
"""

from sqlalchemy import (
    Column, Integer, String, Text, Boolean,
    DateTime, ForeignKey, Index
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
from backend.db import Base


class NotificationConfig(Base):
    """
    Настройки уведомлений для отправки документов.
    Может быть привязана к типу документа или быть глобальной.
    """
    __tablename__ = "notification_configs"

    id = Column(Integer, primary_key=True)

    # Привязка (опционально)
    doc_type_id = Column(Integer, ForeignKey('document_types.id'), nullable=True, index=True)
    well_id = Column(Integer, ForeignKey('wells.id'), nullable=True, index=True)

    # Название конфига
    name = Column(String(200), nullable=False)
    description = Column(Text)

    # Telegram
    telegram_enabled = Column(Boolean, default=False)
    telegram_chat_id = Column(String(100))  # может быть несколько через запятую
    telegram_template = Column(Text)  # шаблон сообщения (Jinja2)

    # Email
    email_enabled = Column(Boolean, default=False)
    email_to = Column(Text)  # адреса через запятую или JSON array
    email_cc = Column(Text)
    email_subject_template = Column(String(500))  # шаблон темы
    email_body_template = Column(Text)  # шаблон тела (HTML)

    # Активность
    is_active = Column(Boolean, default=True, index=True)
    is_default = Column(Boolean, default=False)  # дефолтный конфиг для типа документа

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def __repr__(self):
        return f"<NotificationConfig(id={self.id}, name='{self.name}')>"


class DocumentSendLog(Base):
    """
    Лог отправки документа через различные каналы.
    """
    __tablename__ = "document_send_logs"

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey('documents.id'), nullable=False, index=True)

    # Канал отправки
    channel = Column(String(50), nullable=False, index=True)  # 'telegram', 'email'

    # Получатель
    recipient = Column(String(500))  # chat_id или email

    # Статус
    status = Column(String(50), nullable=False, index=True)  # 'pending', 'sent', 'failed'

    # Детали
    sent_at = Column(DateTime)
    error_message = Column(Text)
    response_data = Column(JSONB)  # ответ от API (message_id и т.д.)

    # Кто инициировал
    triggered_by = Column(String(100))  # 'manual', 'auto', 'batch'
    triggered_by_user_id = Column(Integer)

    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index('idx_send_log_doc_channel', 'document_id', 'channel'),
    )

    def __repr__(self):
        return f"<DocumentSendLog(id={self.id}, doc={self.document_id}, channel='{self.channel}', status='{self.status}')>"


class JobExecutionLog(Base):
    """
    Лог выполнения фоновых задач (автосоздание актов, массовая отправка и т.д.)
    """
    __tablename__ = "job_execution_logs"

    id = Column(Integer, primary_key=True)

    # Тип задачи
    job_type = Column(String(100), nullable=False, index=True)  # 'reagent_expense_auto_create', 'batch_send'

    # Параметры запуска
    params = Column(JSONB)  # {"year": 2026, "month": 1, "well_ids": [...]}

    # Время выполнения
    started_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
    finished_at = Column(DateTime)

    # Результат
    status = Column(String(50), nullable=False, default='running', index=True)  # 'running', 'success', 'partial', 'failed'

    # Статистика результата
    result_summary = Column(JSONB)  # {"created": 5, "skipped": 2, "errors": 1, "details": [...]}
    error_message = Column(Text)

    # Источник запуска
    triggered_by = Column(String(100))  # 'cron', 'manual', 'api'
    triggered_by_user_id = Column(Integer)

    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index('idx_job_log_type_started', 'job_type', 'started_at'),
    )

    def __repr__(self):
        return f"<JobExecutionLog(id={self.id}, job='{self.job_type}', status='{self.status}')>"
