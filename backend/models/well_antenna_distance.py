from __future__ import annotations
from datetime import datetime
from sqlalchemy import Column, Integer, Float, DateTime, ForeignKey, UniqueConstraint
from backend.db import Base


class WellAntennaDistance(Base):
    __tablename__ = "well_antenna_distance"

    id = Column(Integer, primary_key=True)
    well_id = Column(Integer, ForeignKey("wells.id"), nullable=False, index=True)
    map_object_id = Column(Integer, ForeignKey("map_object.id"), nullable=False)
    distance_m = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("well_id", "map_object_id", name="uq_well_antenna_dist"),
    )
