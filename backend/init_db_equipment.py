# backend/init_db_equipment.py

from backend.db import Base, engine
# важно: просто импортируем модели, чтобы они зарегистрировались в Base.metadata
from backend.models import wells, events, users, well_status, well_equipment  # noqa: F401


def main():
    print("Создаю недостающие таблицы в БД...")
    Base.metadata.create_all(bind=engine)
    print("Готово.")


if __name__ == "__main__":
    main()