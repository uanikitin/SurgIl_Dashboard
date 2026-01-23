"""
Роутер для управління обладнанням
Сторінка перегляду, фільтрації, переміщення обладнання
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc, func
from starlette.responses import RedirectResponse

from backend.db import get_db
from backend.web.templates import templates
from backend.deps import get_current_admin
from backend.models.wells import Well
from backend.models.equipment import Equipment, EquipmentInstallation, EquipmentMaintenance
from backend.models.users import User
from backend.documents.models import Document

router = APIRouter(tags=["equipment-management"])


# ======================================================================================
# API: Встановлення обладнання на свердловину
# ======================================================================================

@router.post("/api/wells/{well_id}/install-equipment")
async def install_equipment_on_well(
        well_id: int,
        equipment_id: int = Form(...),
        installation_location: str = Form(...),  # Устье, НКТ, Шлейф, Затруб, Інше
        installed_at: Optional[str] = Form(None),
        notes: Optional[str] = Form(None),
        create_document: bool = Form(False),
        db: Session = Depends(get_db),
        current_admin: str = Depends(get_current_admin),  # Изменили тип на str
):
    """
    API для встановлення обладнання на свердловину
    """

    # Отримуємо свердловину
    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        raise HTTPException(status_code=404, detail="Свердловина не знайдена")

    # Отримуємо обладнання
    equipment = db.query(Equipment).filter(Equipment.id == equipment_id).first()
    if not equipment:
        raise HTTPException(status_code=404, detail="Обладнання не знайдено")

    # Перевіряємо чи не встановлено вже це обладнання
    active_installation = db.query(EquipmentInstallation).filter(
        EquipmentInstallation.equipment_id == equipment_id,
        EquipmentInstallation.removed_at.is_(None)
    ).first()

    if active_installation:
        raise HTTPException(
            status_code=400,
            detail=f"Обладнання вже встановлено на свердловині {active_installation.well_id}"
        )

    # Перевіряємо що обладнання доступне
    if equipment.status != 'available':
        raise HTTPException(
            status_code=400,
            detail=f"Обладнання не доступне для встановлення. Поточний статус: {equipment.status}"
        )

    # Парсимо дату встановлення
    if installed_at:
        try:
            # Формат: "13.12.2025, 20:31" или "2025-12-13T20:31"
            if "," in installed_at:
                # Формат с запятой
                install_dt = datetime.strptime(installed_at, "%d.%m.%Y, %H:%M")
            else:
                # Формат datetime-local
                install_dt = datetime.fromisoformat(installed_at.replace("T", " "))
        except ValueError:
            try:
                # Другие форматы
                install_dt = datetime.fromisoformat(installed_at)
            except:
                install_dt = datetime.now()
    else:
        install_dt = datetime.now()

    # Получаем ID текущего пользователя
    # current_admin может быть строкой с ID или username
    user_id_str = current_admin
    if hasattr(current_admin, 'id'):
        user_id_str = str(current_admin.id)
    elif hasattr(current_admin, '__str__'):
        user_id_str = str(current_admin)

    # Створюємо запис установки
    installation = EquipmentInstallation(
        equipment_id=equipment_id,
        well_id=well_id,
        installed_at=install_dt,
        removed_at=None,
        installed_by=user_id_str,  # Используем строку
        installation_location=installation_location,
        notes=notes,
        document_id=None,
        no_document_confirmed=not create_document,  # Якщо не створюємо акт - підтверджуємо
    )
    db.add(installation)

    # Оновлюємо обладнання
    equipment.status = 'installed'
    equipment.current_location = f"Свердловина {well.number}"
    equipment.updated_at = datetime.now()

    db.commit()
    db.refresh(installation)

    return JSONResponse({
        "success": True,
        "message": f"✅ Обладнання '{equipment.name}' встановлено на свердловині {well.number}",
        "installation_id": installation.id,
        "requires_document": create_document,
        "equipment": {
            "id": equipment.id,
            "name": equipment.name,
            "serial_number": equipment.serial_number,
            "type": equipment.equipment_type,
        }
    })
# ======================================================================================

def get_well_equipment_sync(well_id: int, db: Session):
    """
    Синхронная версия: Отримати список обладнання, встановленого на конкретній свердловині
    """
    try:
        # Перевіряємо, чи існує свердловина
        well = db.query(Well).filter(Well.id == well_id).first()
        if not well:
            return {"error": f"Свердловина з ID {well_id} не знайдена"}

        # Отримуємо активні встановлення обладнання на цій свердловині
        active_installations = db.query(
            EquipmentInstallation,
            Equipment
        ).join(
            Equipment, EquipmentInstallation.equipment_id == Equipment.id
        ).filter(
            EquipmentInstallation.well_id == well_id,
            EquipmentInstallation.removed_at.is_(None)
        ).all()

        # Формуємо результат
        equipment_list = []
        for installation, equipment in active_installations:
            equipment_list.append({
                "equipment_id": equipment.id,
                "equipment_name": equipment.name,
                "equipment_type": equipment.equipment_type,
                "serial_number": equipment.serial_number,
                "condition": equipment.condition,
                "installation_date": installation.installed_at,
                "installation_location": getattr(installation, 'installation_location', None),
                "notes": installation.notes,
            })

        return {
            "well_id": well_id,
            "well_number": well.number,
            "equipment_count": len(equipment_list),
            "equipment": equipment_list
        }

    except Exception as e:
        return {"error": str(e), "type": type(e).__name__}


def get_available_equipment_sync(db: Session):
    """
    Синхронна версія: Отримати список доступного обладнання (статус = 'available')
    """
    try:
        # Базовий запит
        query = db.query(Equipment).filter(
            Equipment.status == 'available',
            or_(Equipment.deleted_at.is_(None), Equipment.deleted_at == None)
        )

        # Сортування
        equipment_list = query.order_by(Equipment.name).all()

        # Формуємо результат
        result = []
        for equipment in equipment_list:
            result.append({
                "id": equipment.id,
                "name": equipment.name,
                "type": equipment.equipment_type,
                "serial_number": equipment.serial_number,
                "condition": equipment.condition,
                "location": equipment.current_location,
                "manufacturer": equipment.manufacturer,
                "description": equipment.description,
            })

        return {
            "count": len(result),
            "equipment": result
        }

    except Exception as e:
        return {"error": str(e), "type": type(e).__name__}
# ======================================================================================
# ДОДАНО: Функції для інтеграції зі свердловинами
# ======================================================================================

@router.get("/api/wells/{well_id}/equipment")
async def get_well_equipment(
    well_id: int,
    db: Session = Depends(get_db),
):
    """
    Отримати список обладнання, встановленого на конкретній свердловині
    """
    try:
        # Перевіряємо, чи існує свердловина
        well = db.query(Well).filter(Well.id == well_id).first()
        if not well:
            return JSONResponse(
                {"error": f"Свердловина з ID {well_id} не знайдена"},
                status_code=404
            )

        # Отримуємо активні встановлення обладнання на цій свердловині
        active_installations = db.query(
            EquipmentInstallation,
            Equipment
        ).join(
            Equipment, EquipmentInstallation.equipment_id == Equipment.id
        ).filter(
            EquipmentInstallation.well_id == well_id,
            EquipmentInstallation.removed_at.is_(None)
        ).all()

        # Формуємо результат
        equipment_list = []
        for installation, equipment in active_installations:
            equipment_list.append({
                "equipment_id": equipment.id,
                "equipment_name": equipment.name,
                "equipment_type": equipment.equipment_type,
                "serial_number": equipment.serial_number,
                "condition": equipment.condition,
                "installation_date": installation.installed_at,
                "installation_location": getattr(installation, 'installation_location', None),
                "notes": installation.notes,
            })

        return JSONResponse({
            "well_id": well_id,
            "well_number": well.number,
            "equipment_count": len(equipment_list),
            "equipment": equipment_list
        })

    except Exception as e:
        return JSONResponse(
            {"error": str(e), "type": type(e).__name__},
            status_code=500
        )


@router.get("/api/equipment/available")
async def get_available_equipment(
    equipment_type: Optional[str] = Query(None),
    condition: Optional[str] = Query("working"),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Отримати список доступного обладнання (статус = 'available')
    """
    try:
        # Базовий запит
        query = db.query(Equipment).filter(
            Equipment.status == 'available',
            or_(Equipment.deleted_at.is_(None), Equipment.deleted_at == None)
        )

        # Фільтр по типу
        if equipment_type:
            query = query.filter(Equipment.equipment_type == equipment_type)

        # Фільтр по стану
        if condition:
            query = query.filter(Equipment.condition == condition)

        # Пошук
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    Equipment.name.ilike(search_pattern),
                    Equipment.serial_number.ilike(search_pattern),
                )
            )

        # Сортування
        equipment_list = query.order_by(Equipment.name).all()

        # Формуємо результат
        result = []
        for equipment in equipment_list:
            result.append({
                "id": equipment.id,
                "name": equipment.name,
                "type": equipment.equipment_type,
                "serial_number": equipment.serial_number,
                "condition": equipment.condition,
                "location": equipment.current_location,
                "manufacturer": equipment.manufacturer,
                "description": equipment.description,
            })

        return JSONResponse({
            "count": len(result),
            "equipment": result
        })

    except Exception as e:
        return JSONResponse(
            {"error": str(e), "type": type(e).__name__},
            status_code=500
        )


# ======================================================================================
# ДУБЛІ ВИДАЛЕНО (використовуйте equipment_management.py):
# - /equipment, /equipment/add, /equipment/{id}
# - /api/equipment/create
# - /api/equipment/{id}/move
# - /api/equipment/{id}/update_status
# - /api/equipment/{id}/add_maintenance
# - DELETE /api/equipment/{id}
# ======================================================================================


# ======================================================================================
# API: Записи обслуговування (УНІКАЛЬНІ - тільки тут)
# ======================================================================================

# 1. Получить запись обслуживания
@router.get("/api/maintenance/{maintenance_id}")
async def get_maintenance_record(
        maintenance_id: int,
        db: Session = Depends(get_db)
):
    maintenance = db.query(EquipmentMaintenance).filter(
        EquipmentMaintenance.id == maintenance_id
    ).first()

    if not maintenance:
        raise HTTPException(status_code=404, detail="Запись не найдена")

    return {
        "id": maintenance.id,
        "maintenance_type": maintenance.maintenance_type,
        "description": maintenance.description,
        "maintenance_date": maintenance.maintenance_date,
        "performed_by": maintenance.performed_by,
        "cost": maintenance.cost,
        "issues_found": getattr(maintenance, 'issues_found', None),
        "actions_taken": getattr(maintenance, 'actions_taken', None),
        "notes": getattr(maintenance, 'notes', None),
        "next_maintenance_date": getattr(maintenance, 'next_maintenance_date', None),
    }


# 2. Обновить запись обслуживания
@router.post("/api/maintenance/{maintenance_id}/update")
async def update_maintenance_record(
        maintenance_id: int,
        maintenance_type: str = Form(...),
        description: str = Form(...),
        cost: Optional[str] = Form(None),
        performed_by: Optional[str] = Form(None),
        maintenance_date: Optional[str] = Form(None),
        issues_found: Optional[str] = Form(None),
        actions_taken: Optional[str] = Form(None),
        notes: Optional[str] = Form(None),
        db: Session = Depends(get_db),
        current_admin=Depends(get_current_admin)
):
    maintenance = db.query(EquipmentMaintenance).filter(
        EquipmentMaintenance.id == maintenance_id
    ).first()

    if not maintenance:
        raise HTTPException(status_code=404, detail="Запись не найдена")

    # Обновляем поля
    maintenance.maintenance_type = maintenance_type
    maintenance.description = description

    # Преобразуем cost
    if cost:
        try:
            maintenance.cost = float(cost)
        except:
            maintenance.cost = 0.0

    maintenance.performed_by = performed_by or f"User {current_admin.id}"

    # Обновляем дату
    if maintenance_date:
        try:
            maintenance.maintenance_date = datetime.fromisoformat(maintenance_date.replace('T', ' '))
        except:
            pass

    # Дополнительные поля
    if hasattr(maintenance, 'issues_found'):
        maintenance.issues_found = issues_found

    if hasattr(maintenance, 'actions_taken'):
        maintenance.actions_taken = actions_taken

    if hasattr(maintenance, 'notes'):
        maintenance.notes = notes

    maintenance.updated_at = datetime.now()

    db.commit()

    return JSONResponse({
        "success": True,
        "message": "Запись обновлена",
        "maintenance_id": maintenance.id
    })


# Удаление записи обслуживания
@router.delete("/api/maintenance/{maintenance_id}/delete")
async def delete_maintenance_record(
        maintenance_id: int,
        db: Session = Depends(get_db),
        current_admin=Depends(get_current_admin)
):
    maintenance = db.query(EquipmentMaintenance).filter(
        EquipmentMaintenance.id == maintenance_id
    ).first()

    if not maintenance:
        raise HTTPException(status_code=404, detail="Запись не найдена")

    # Сохраняем информацию для сообщения
    maintenance_type = maintenance.maintenance_type
    maintenance_date = maintenance.maintenance_date

    # Удаляем запись
    db.delete(maintenance)
    db.commit()

    return JSONResponse({
        "success": True,
        "message": f"Запись об обслуживании '{maintenance_type}' от {maintenance_date.strftime('%d.%m.%Y')} удалена",
        "maintenance_id": maintenance_id
    })

