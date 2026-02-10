# backend/models/well_sub_status.py

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from ..db import Base


class WellSubStatus(Base):
    """Подстатус скважины (оперативное состояние), независимый от основного статуса."""
    __tablename__ = "well_sub_status"

    id = Column(Integer, primary_key=True)
    well_id = Column(
        Integer,
        ForeignKey("wells.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sub_status = Column(String(100), nullable=False)

    dt_start = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    dt_end = Column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    note = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    well = relationship("Well", lazy="joined")

    def __repr__(self) -> str:
        return (
            f"<WellSubStatus well_id={self.well_id} "
            f"sub_status={self.sub_status!r} start={self.dt_start} end={self.dt_end}>"
        )

    @property
    def is_active(self) -> bool:
        return self.dt_end is None

    def duration_days(self, now=None) -> float:
        from datetime import datetime
        if now is None:
            now = datetime.now(self.dt_start.tzinfo)
        end = self.dt_end or now
        delta = end - self.dt_start
        return delta.total_seconds() / 86400.0
