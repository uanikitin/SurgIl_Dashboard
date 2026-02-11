"""
CsvImportLog — журнал импорта CSV файлов давлений (локальный SQLite pressure.db).

Для каждого CSV файла хранится sha256 хеш — если файл не изменился, повторный
импорт пропускается. Это делает импорт идемпотентным.
"""

from sqlalchemy import Column, Integer, String, DateTime

from backend.db_pressure import PressureBase


class CsvImportLog(PressureBase):
    __tablename__ = "csv_import_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(128), unique=True, nullable=False)
    file_sha256 = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="pending")  # pending/imported/failed
    rows_imported = Column(Integer, default=0)
    rows_skipped = Column(Integer, default=0)
    first_timestamp = Column(DateTime, nullable=True)   # UTC
    last_timestamp = Column(DateTime, nullable=True)    # UTC
    imported_at = Column(DateTime, nullable=True)
    error_message = Column(String(500), nullable=True)
    rows_in_file = Column(Integer, nullable=True)  # total rows in CSV at import time

    def __repr__(self):
        return f"<CsvImportLog {self.filename} status={self.status}>"
