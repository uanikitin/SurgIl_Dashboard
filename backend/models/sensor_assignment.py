"""
Sensor role assignment timeline.

Overrides default role (tube/line) derived from LoRaSensor.csv_column.
Without a record the role is computed as: Ptr→tube, Pshl→line (legacy).
"""
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import relationship

from backend.db import Base


class SensorAssignment(Base):
    __tablename__ = "lora_sensor_assignment"

    id = Column(Integer, primary_key=True)
    sensor_id = Column(
        Integer,
        ForeignKey("lora_sensors.id", ondelete="CASCADE"),
        nullable=False,
    )
    role = Column(String(10), nullable=False)  # 'tube' | 'line'
    valid_from = Column(DateTime(timezone=False), nullable=False)
    valid_to = Column(DateTime(timezone=False), nullable=True)  # NULL = active
    note = Column(String(500), nullable=True)
    created_by = Column(Integer, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    sensor = relationship("LoRaSensor", backref="role_assignments")

    __table_args__ = (
        CheckConstraint("role IN ('tube','line')", name="ck_lsa_role"),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_lsa_valid_range",
        ),
        Index("ix_lsa_sensor_from", "sensor_id", "valid_from"),
    )

    def __repr__(self):
        state = "active" if self.valid_to is None else f"until {self.valid_to}"
        return f"<SensorAssignment s={self.sensor_id} {self.role} from {self.valid_from} {state}>"
