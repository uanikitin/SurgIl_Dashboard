"""
SqliteImportState — состояние импорта из CODESYS Tracing SQLite (локальный SQLite pressure.db).

Для каждого Trend (1-6) хранится last_ts — последний импортированный timestamp.
При следующем sync берутся только новые записи (TS > last_ts).
"""

from sqlalchemy import Column, Integer, BigInteger, String, DateTime

from backend.db_pressure import PressureBase


class SqliteImportState(PressureBase):
    __tablename__ = "tracing_import_state"

    trend_name = Column(String(64), primary_key=True)   # "Trend1" .. "Trend6"
    last_ts = Column(BigInteger, default=0)              # последний TS (μs Unix epoch)
    rows_imported_total = Column(Integer, default=0)
    updated_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<SqliteImportState {self.trend_name} last_ts={self.last_ts}>"
