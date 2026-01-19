# scripts/fill_reagent_catalog.py
from sqlalchemy.orm import Session
from backend.db import SessionLocal
from backend.models.reagent_catalog import ReagentCatalog
from backend.models.reagents import ReagentSupply
from backend.models.reagent_inventory import ReagentInventorySnapshot


def fill_catalog_from_existing_data():
    """Заполняет каталог реагентов из существующих таблиц"""
    db = SessionLocal()

    try:
        # Собираем уникальные реагенты из всех источников
        reagents_set = set()

        # Из поставок
        supplies = db.query(ReagentSupply.reagent).distinct().all()
        for s in supplies:
            if s.reagent:
                reagents_set.add(s.reagent.strip())

        # Из инвентаризации
        inventories = db.query(ReagentInventorySnapshot.reagent).distinct().all()
        for i in inventories:
            if i.reagent:
                reagents_set.add(i.reagent.strip())

        print(f"Найдено {len(reagents_set)} уникальных реагентов")

        # Создаем записи в каталоге
        for reagent_name in sorted(reagents_set):
            # Проверяем, нет ли уже в каталоге
            existing = db.query(ReagentCatalog).filter(
                ReagentCatalog.name == reagent_name
            ).first()

            if not existing:
                # Определяем единицу измерения по умолчанию
                # Ищем в поставках
                supply = db.query(ReagentSupply).filter(
                    ReagentSupply.reagent == reagent_name
                ).first()

                default_unit = "шт"
                if supply and supply.unit:
                    default_unit = supply.unit
                else:
                    # Ищем в инвентаризации
                    inventory = db.query(ReagentInventorySnapshot).filter(
                        ReagentInventorySnapshot.reagent == reagent_name
                    ).first()
                    if inventory and inventory.unit:
                        default_unit = inventory.unit

                catalog_item = ReagentCatalog(
                    name=reagent_name,
                    default_unit=default_unit,
                    is_active=True
                )
                db.add(catalog_item)
                print(f"Добавлен: {reagent_name} ({default_unit})")

        db.commit()
        print("✅ Каталог реагентов успешно заполнен")

    except Exception as e:
        db.rollback()
        print(f"❌ Ошибка: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    fill_catalog_from_existing_data()