"""Лог отправленных Telegram-сообщений из дашборда."""

from datetime import datetime

from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, DateTime, ForeignKey,
)

from backend.db import Base


class ChatMessageLog(Base):
    __tablename__ = "chat_message_log"

    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, nullable=False, index=True)
    chat_title = Column(Text)
    message_text = Column(Text, nullable=False)
    parse_mode = Column(String(20), server_default="HTML")
    status = Column(String(20), nullable=False, server_default="pending")
    error_message = Column(Text)
    telegram_message_id = Column(Integer)
    sent_by_user_id = Column(Integer, ForeignKey("dashboard_users.id"))
    sent_by_username = Column(String(100))
    sent_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
