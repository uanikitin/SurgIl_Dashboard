# backend/models/user.py

from backend.db import Base
from datetime import datetime

from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    BigInteger,
    Text,
    ForeignKey,
)

class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True)
    username = Column(Text)
    full_name = Column(Text)



class DashboardUser(Base):
    __tablename__ = "dashboard_users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)

    # новые поля
    email = Column(String(255), nullable=True)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)

    is_admin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)

class DashboardLoginLog(Base):
    __tablename__ = "dashboard_login_log"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("dashboard_users.id"), nullable=False)

    login_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    logout_at = Column(DateTime(timezone=True), nullable=True)

    ip_address = Column(String(64), nullable=True)
    user_agent = Column(Text, nullable=True)

    user = relationship("DashboardUser", backref="login_logs")