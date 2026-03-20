from __future__ import annotations
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Text, DateTime
from backend.db import Base


class MapObject(Base):
    __tablename__ = "map_object"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    description = Column(Text, nullable=True)
    icon_color = Column(String(20), nullable=False, server_default="#e74c3c")
    icon_type = Column(String(30), nullable=False, server_default="default")
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
