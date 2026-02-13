"""Logical recipient subgroups for the Telegram chat drawer."""

from datetime import datetime

from sqlalchemy import Column, Integer, BigInteger, String, DateTime, ForeignKey, Table

from backend.db import Base

chat_subgroup_member = Table(
    'chat_subgroup_member',
    Base.metadata,
    Column('subgroup_id', Integer, ForeignKey('chat_subgroup.id', ondelete='CASCADE'),
           primary_key=True),
    Column('user_id', BigInteger, ForeignKey('users.id', ondelete='CASCADE'),
           primary_key=True),
)


class ChatSubgroup(Base):
    __tablename__ = 'chat_subgroup'

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    sort_order = Column(Integer, nullable=False, server_default='0')
    created_at = Column(DateTime, default=datetime.utcnow)
