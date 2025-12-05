# backend/init_db.py

from backend.db import engine, Base
from backend.models.users import User, DashboardUser  # noqa: F401  (важно, чтобы модели были импортированы)

def init_db():
    # создаёт в базе все таблицы, описанные моделями, которых ещё нет
    Base.metadata.create_all(bind=engine)

if __name__ == "__main__":
    init_db()
    print("✅ Таблицы созданы / обновлены")