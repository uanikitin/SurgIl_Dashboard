# scripts/sync_reagent_catalog.py
from decimal import Decimal
from sqlalchemy.orm import Session
from backend.db import SessionLocal
from backend.models.reagent_catalog import ReagentCatalog
from backend.models.reagents import ReagentSupply
from backend.models.reagent_inventory import ReagentInventorySnapshot


def sync_reagent_catalog():
    """Синхронизирует каталог реагентов с существующими данными"""
    db = SessionLocal()

    try:
        print("🔍 Сбор уникальных реагентов из существующих данных...")

        # Собираем уникальные реагенты
        all_reagents = set()

        # Из поставок
        for supply in db.query(ReagentSupply).all():
            if supply.reagent and supply.reagent.strip():
                all_reagents.add(supply.reagent.strip())

        # Из инвентаризации
        for inv in db.query(ReagentInventorySnapshot).all():
            if inv.reagent and inv.reagent.strip():
                all_reagents.add(inv.reagent.strip())

        print(f"📊 Найдено {len(all_reagents)} уникальных реагентов")

        # Создаем записи в каталоге
        for reagent_name in sorted(all_reagents):
            existing = db.query(ReagentCatalog).filter(
                ReagentCatalog.name == reagent_name
            ).first()

            if not existing:
                # Определяем единицу измерения по умолчанию
                default_unit = "шт"

                supply = db.query(ReagentSupply).filter(
                    ReagentSupply.reagent == reagent_name
                ).first()

                if supply and supply.unit:
                    default_unit = supply.unit
                else:
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
                print(f"✅ Добавлен в каталог: {reagent_name} ({default_unit})")

        db.commit()
        print("📚 Каталог реагентов создан")

        # Устанавливаем связи
        print("\n🔗 Установка связей для поставок...")
        for supply in db.query(ReagentSupply).all():
            if supply.reagent:
                catalog_item = db.query(ReagentCatalog).filter(
                    ReagentCatalog.name == supply.reagent.strip()
                ).first()

                if catalog_item:
                    supply.reagent_id = catalog_item.id

        print("\n🔗 Установка связей для инвентаризаций...")
        for inv in db.query(ReagentInventorySnapshot).all():
            if inv.reagent:
                catalog_item = db.query(ReagentCatalog).filter(
                    ReagentCatalog.name == inv.reagent.strip()
                ).first()

                if catalog_item:
                    inv.reagent_id = catalog_item.id

        db.commit()
        print("\n🎉 Синхронизация завершена успешно!")

    except Exception as e:
        db.rollback()
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()