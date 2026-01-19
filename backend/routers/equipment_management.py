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
# Форма додавання обладнання
# ======================================================================================

@router.get("/equipment/add", response_class=HTMLResponse)
async def equipment_add_page(
    request: Request,
    db: Session = Depends(get_db),
):
    """Форма додавання нового обладнання"""

    context = {
        "request": request,
    }

    return templates.TemplateResponse("equipment_add.html", context)


# ======================================================================================
# API: Створення обладнання
# ======================================================================================

@router.post("/api/equipment/create")
async def create_equipment(
    name: str = Form(...),
    equipment_type: str = Form(...),
    serial_number: str = Form(...),
    manufacturer: Optional[str] = Form(None),
    manufacture_date: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    specifications: Optional[str] = Form(None),
    current_location: str = Form("Склад"),
    condition: str = Form("working"),
    notes: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_admin = Depends(get_current_admin),
):
    """
    Створення нового обладнання
    """

    # Перевірка унікальності серійного номера
    existing = db.query(Equipment).filter(
        Equipment.serial_number == serial_number
    ).first()

    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Обладнання з серійним номером {serial_number} вже існує (ID: {existing.id})"
        )

    # Парсимо дату виробництва
    manufacture_dt = None
    if manufacture_date:
        try:
            from datetime import datetime as dt
            manufacture_dt = dt.fromisoformat(manufacture_date)
        except:
            pass

    # Створюємо обладнання
    equipment = Equipment(
        name=name,
        equipment_type=equipment_type,
        serial_number=serial_number,
        manufacturer=manufacturer,
        manufacture_date=manufacture_dt,
        description=description,
        specifications=specifications,
        status='available',  # Завжди створюється як "на складі"
        condition=condition,
        current_location=current_location,
        notes=notes,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    db.add(equipment)
    db.commit()
    db.refresh(equipment)

    return RedirectResponse(
        url=f"/equipment/{equipment.id}",
        status_code=303
    )


# ======================================================================================
# Список обладнання
# ======================================================================================

@router.get("/equipment", response_class=HTMLResponse)
async def equipment_list_page(
    request: Request,
    db: Session = Depends(get_db),
    equipment_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    condition: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
):
    """
    Сторінка списку обладнання з фільтрами

    Фільтри:
    - equipment_type: тип обладнання (шлюз, манометр, etc)
    - status: available, installed, maintenance, broken, lost
    - condition: working, broken, needs_repair
    - search: пошук по назві або серійному номеру
    """

    # Базовий запит
    query = db.query(Equipment).filter(
        or_(Equipment.deleted_at.is_(None), Equipment.deleted_at == None)
    )

    # Фільтр по типу
    if equipment_type:
        query = query.filter(Equipment.equipment_type == equipment_type)

    # Фільтр по статусу
    if status:
        query = query.filter(Equipment.status == status)

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
    query = query.order_by(Equipment.id.desc())

    # Отримуємо дані
    equipment_list = query.all()

    # Отримуємо унікальні типи для фільтру
    equipment_types = db.query(Equipment.equipment_type).distinct().filter(
        Equipment.equipment_type.isnot(None)
    ).all()
    equipment_types = [t[0] for t in equipment_types if t[0]]

    # Статистика
    total_count = db.query(func.count(Equipment.id)).scalar()
    installed_count = db.query(func.count(Equipment.id)).filter(
        Equipment.status == 'installed'
    ).scalar()
    available_count = db.query(func.count(Equipment.id)).filter(
        Equipment.status == 'available'
    ).scalar()
    maintenance_count = db.query(func.count(Equipment.id)).filter(
        Equipment.status == 'maintenance'
    ).scalar()

    context = {
        "request": request,
        "equipment_list": equipment_list,
        "equipment_types": equipment_types,
        "selected_type": equipment_type,
        "selected_status": status,
        "selected_condition": condition,
        "search_query": search or "",
        "stats": {
            "total": total_count,
            "installed": installed_count,
            "available": available_count,
            "maintenance": maintenance_count,
        }
    }

    return templates.TemplateResponse("equipment_list.html", context)


# ======================================================================================
# Деталі обладнання
# ======================================================================================

@router.get("/equipment/{equipment_id}", response_class=HTMLResponse)
async def equipment_detail_page(
    request: Request,
    equipment_id: int,
    db: Session = Depends(get_db),
):
    """
    Детальна інформація про обладнання:
    - Поточний стан
    - Історія встановлень
    - Історія обслуговування
    - Статистика
    """

    # Отримуємо обладнання
    equipment = db.query(Equipment).filter(Equipment.id == equipment_id).first()
    if not equipment:
        raise HTTPException(status_code=404, detail="Обладнання не знайдено")

    # Історія встановлень (з інформацією про свердловини та користувачів)
    installations_query = db.query(
        EquipmentInstallation,
        Well.number.label('well_number')
    ).outerjoin(
        Well, EquipmentInstallation.well_id == Well.id
    ).filter(
        EquipmentInstallation.equipment_id == equipment_id
    ).order_by(
        EquipmentInstallation.installed_at.desc()
    )

    installations_raw = installations_query.all()

    # Отримуємо унікальні user_id для завантаження
    user_ids = set()
    for inst, _ in installations_raw:
        if inst.installed_by:
            # Перетворюємо в int якщо це строка
            try:
                user_ids.add(int(inst.installed_by))
            except (ValueError, TypeError):
                pass
        if inst.removed_by:
            try:
                user_ids.add(int(inst.removed_by))
            except (ValueError, TypeError):
                pass

    # Завантажуємо users одним запитом
    users_dict = {}
    if user_ids:
        users = db.query(User).filter(User.id.in_(user_ids)).all()
        for user in users:
            users_dict[user.id] = user.username or user.full_name or f"User {user.id}"

    # Обробка історії
    installation_history = []
    for inst, well_num in installations_raw:
        # Розрахунок тривалості
        start_dt = inst.installed_at
        end_dt = inst.removed_at or datetime.now()
        duration_days = (end_dt - start_dt).days

        # Отримуємо username
        installed_by_name = None
        if inst.installed_by:
            try:
                installed_by_name = users_dict.get(int(inst.installed_by))
            except (ValueError, TypeError):
                pass

        removed_by_name = None
        if inst.removed_by:
            try:
                removed_by_name = users_dict.get(int(inst.removed_by))
            except (ValueError, TypeError):
                pass

        # Перевірка наявності акту
        has_document = inst.document_id is not None
        no_document_confirmed = getattr(inst, 'no_document_confirmed', False) or False

        installation_history.append({
            "id": inst.id,
            "well_number": well_num,
            "well_id": inst.well_id,  # Додаємо для посилань
            "installed_at": inst.installed_at,
            "removed_at": inst.removed_at,
            "is_active": inst.removed_at is None,
            "duration_days": duration_days,
            "installed_by": installed_by_name or f"User {inst.installed_by}" if inst.installed_by else None,
            "removed_by": removed_by_name or f"User {inst.removed_by}" if inst.removed_by else None,
            "installation_location": getattr(inst, 'installation_location', None),  # НОВЕ
            "has_document": has_document,
            "no_document_confirmed": no_document_confirmed,
            "document_id": inst.document_id,
            "notes": inst.notes,
        })

    # Історія обслуговування
    maintenance_history = db.query(EquipmentMaintenance).filter(
        EquipmentMaintenance.equipment_id == equipment_id
    ).order_by(
        EquipmentMaintenance.maintenance_date.desc()
    ).all()

    # Статистика
    total_installations = len(installation_history)
    total_days = sum(h['duration_days'] for h in installation_history)
    total_maintenance_cost = sum(m.cost or 0 for m in maintenance_history)

    # Поточна установка
    current_installation = next((h for h in installation_history if h['is_active']), None)

    # Додаємо well_id для створення акту демонтажу
    if current_installation:
        # Знаходимо installation record для отримання well_id
        for inst, well_num in installations_raw:
            if inst.removed_at is None:
                current_installation['well_id'] = inst.well_id
                break

    # Список свердловин для форми встановлення
    wells_list = db.query(Well).order_by(Well.number).all()

    context = {
        "request": request,
        "equipment": equipment,
        "current_installation": current_installation,
        "installation_history": installation_history,
        "maintenance_history": maintenance_history,
        "wells_list": wells_list,
        "stats": {
            "total_installations": total_installations,
            "total_days": total_days,
            "total_maintenance_cost": total_maintenance_cost,
            "avg_days_per_installation": round(total_days / total_installations, 1) if total_installations > 0 else 0,
        }
    }

    return templates.TemplateResponse("equipment_detail.html", context)


# ======================================================================================
# API: Переміщення обладнання
# ======================================================================================

@router.post("/api/equipment/{equipment_id}/move")
async def move_equipment(
    equipment_id: int,
    action: str = Form(...),  # "install" або "remove"
    well_id: Optional[str] = Form(None),  # ЗМІНЕНО: строка, потім конвертуємо
    installation_location: Optional[str] = Form(None),  # НОВЕ: Устье, НКТ, Шлейф, Затруб, Інше
    location: Optional[str] = Form(None),
    condition: str = Form("working"),
    create_document: bool = Form(False),
    no_document_confirmed: bool = Form(False),
    notes: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_admin = Depends(get_current_admin),
):
    """
    API для переміщення обладнання

    Parameters:
    - action: "install" (встановити) або "remove" (зняти)
    - well_id: ID свердловини (обов'язково для install)
    - location: Локація текстом (для remove, якщо не "Склад")
    - condition: "working", "broken", "needs_repair"
    - create_document: чи створювати акт
    - no_document_confirmed: підтвердження що акт не потрібен
    - notes: примітки
    """

    # Отримуємо обладнання
    equipment = db.query(Equipment).filter(Equipment.id == equipment_id).first()
    if not equipment:
        raise HTTPException(status_code=404, detail="Обладнання не знайдено")

    # Конвертуємо well_id з строки в int
    well_id_int = None
    if well_id and well_id.strip():
        try:
            well_id_int = int(well_id)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Невалідний well_id: '{well_id}'")

    # Перевіряємо поточний стан
    active_installation = db.query(EquipmentInstallation).filter(
        EquipmentInstallation.equipment_id == equipment_id,
        EquipmentInstallation.removed_at.is_(None)
    ).first()

    if action == "install":
        # ==== ВСТАНОВЛЕННЯ ====

        # Перевірка: чи не встановлено вже
        if active_installation:
            raise HTTPException(
                status_code=400,
                detail=f"Обладнання вже встановлено на свердловині {active_installation.well_id}"
            )

        # Перевірка: well_id обов'язковий
        if not well_id_int:
            raise HTTPException(status_code=400, detail="Виберіть свердловину для встановлення")

        # Перевіряємо що свердловина існує
        well = db.query(Well).filter(Well.id == well_id_int).first()
        if not well:
            raise HTTPException(status_code=404, detail=f"Свердловина {well_id_int} не знайдена")

        # Створюємо запис установки
        installation = EquipmentInstallation(
            equipment_id=equipment_id,
            well_id=well_id_int,
            installed_at=datetime.now(),
            removed_at=None,
            tube_pressure_install=None,  # Можна додати якщо передається
            line_pressure_install=None,
            installed_by=str(current_admin.id),
            installation_location=installation_location,  # НОВЕ
            document_id=None,  # Буде заповнено якщо створюється акт
            no_document_confirmed=no_document_confirmed,
            notes=notes,
        )
        db.add(installation)

        # Оновлюємо обладнання
        equipment.status = 'installed'
        equipment.current_location = f"Свердловина {well.number}"
        equipment.condition = condition

        db.commit()

        return JSONResponse({
            "success": True,
            "message": f"Обладнання встановлено на свердловині {well.number}",
            "installation_id": installation.id,
            "requires_document": not no_document_confirmed,
        })

    elif action == "remove":
        # ==== ДЕМОНТАЖ ====

        # Перевірка: чи встановлено зараз
        if not active_installation:
            raise HTTPException(
                status_code=400,
                detail="Обладнання не встановлено"
            )

        # Закриваємо установку
        active_installation.removed_at = datetime.now()
        active_installation.removed_by = current_admin.id
        if notes:
            active_installation.notes = (active_installation.notes or "") + f"\nДемонтаж: {notes}"

        # Оновлюємо обладнання
        equipment.status = 'available'
        equipment.current_location = location or "Склад"
        equipment.condition = condition

        db.commit()

        return JSONResponse({
            "success": True,
            "message": f"Обладнання знято. Локація: {equipment.current_location}",
            "installation_id": active_installation.id,
        })

    else:
        raise HTTPException(status_code=400, detail="action має бути 'install' або 'remove'")


# ======================================================================================
# API: Зміна статусу/стану
# ======================================================================================

@router.post("/api/equipment/{equipment_id}/update_status")
async def update_equipment_status(
    equipment_id: int,
    new_status: str = Form(...),  # available, installed, maintenance, broken, lost
    new_condition: Optional[str] = Form(None),  # working, broken, needs_repair
    new_location: Optional[str] = Form(None),  # текстова локація
    notes: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_admin = Depends(get_current_admin),
):
    """
    Оновлення статусу/стану обладнання

    Логіка локацій:
    - available → location = "Склад" (якщо не вказано інше)
    - maintenance → location = "Майстерня" / "Ремонт" / "Консервація"
    - installed → зберігається поточна локація (свердловина)
    """

    equipment = db.query(Equipment).filter(Equipment.id == equipment_id).first()
    if not equipment:
        raise HTTPException(status_code=404, detail="Обладнання не знайдено")

    old_status = equipment.status
    old_location = equipment.current_location

    # Оновлюємо статус
    equipment.status = new_status

    # Логіка автоматичної локації
    if new_status == 'available' and not new_location:
        equipment.current_location = "Склад"
    elif new_status == 'maintenance' and not new_location:
        equipment.current_location = "Майстерня"
    elif new_location:
        equipment.current_location = new_location

    # Оновлюємо стан
    if new_condition:
        equipment.condition = new_condition

    equipment.updated_at = datetime.now()

    # Якщо переведено в статус "не на свердловині" - закриваємо активну установку
    if new_status in ['available', 'maintenance', 'broken', 'lost']:
        active_installation = db.query(EquipmentInstallation).filter(
            EquipmentInstallation.equipment_id == equipment_id,
            EquipmentInstallation.removed_at.is_(None)
        ).first()

        if active_installation:
            active_installation.removed_at = datetime.now()
            active_installation.removed_by = str(current_admin.id)
            if notes:
                active_installation.notes = (active_installation.notes or "") + f"\nЗміна статусу: {notes}"

    db.commit()

    return JSONResponse({
        "success": True,
        "message": f"Статус змінено: {old_status} → {new_status}. Локація: {equipment.current_location}",
        "equipment": {
            "id": equipment.id,
            "status": equipment.status,
            "condition": equipment.condition,
            "location": equipment.current_location,
        }
    })


# ======================================================================================
# API: Додати обслуговування
# ======================================================================================

@router.post("/api/equipment/{equipment_id}/add_maintenance")
async def add_maintenance(
    equipment_id: int,
    maintenance_type: str = Form(...),
    description: str = Form(...),
    cost: Optional[float] = Form(None),
    performed_by: Optional[str] = Form(None),
    maintenance_date: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_admin = Depends(get_current_admin),
):
    """
    Додати запис про обслуговування
    """

    equipment = db.query(Equipment).filter(Equipment.id == equipment_id).first()
    if not equipment:
        raise HTTPException(status_code=404, detail="Обладнання не знайдено")

    # Парсимо дату
    if maintenance_date:
        try:
            maint_dt = datetime.fromisoformat(maintenance_date)
        except:
            maint_dt = datetime.now()
    else:
        maint_dt = datetime.now()

    # Створюємо запис
    maintenance = EquipmentMaintenance(
        equipment_id=equipment_id,
        maintenance_date=maint_dt,
        maintenance_type=maintenance_type,
        description=description,
        performed_by=performed_by or f"User {current_admin.id}",
        cost=cost,
        created_at=datetime.now(),
    )
    db.add(maintenance)

    # Оновлюємо last_maintenance_date в equipment
    equipment.last_maintenance_date = maint_dt

    db.commit()

    return JSONResponse({
        "success": True,
        "message": "Запис про обслуговування додано",
        "maintenance_id": maintenance.id,
    })


# ======================================================================================
# API: Видалення обладнання
# ======================================================================================

@router.delete("/api/equipment/{equipment_id}")
async def delete_equipment(
    equipment_id: int,
    db: Session = Depends(get_db),
    current_admin = Depends(get_current_admin),
):
    """
    Видалення обладнання (soft delete - встановлюємо deleted_at)
    """

    equipment = db.query(Equipment).filter(Equipment.id == equipment_id).first()
    if not equipment:
        raise HTTPException(status_code=404, detail="Обладнання не знайдено")

    # Перевірка: чи не встановлено зараз
    active_installation = db.query(EquipmentInstallation).filter(
        EquipmentInstallation.equipment_id == equipment_id,
        EquipmentInstallation.removed_at.is_(None)
    ).first()

    if active_installation:
        raise HTTPException(
            status_code=400,
            detail=f"Не можна видалити обладнання яке зараз встановлено на свердловині {active_installation.well_id}. Спочатку демонтуйте."
        )

    # Soft delete
    equipment.deleted_at = datetime.now()
    db.commit()

    return JSONResponse({
        "success": True,
        "message": f"Обладнання {equipment.name} видалено",
    })