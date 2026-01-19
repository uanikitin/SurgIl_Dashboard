"""
Admin Panel для діагностики обладнання
Показує всі дані, дозволяє тестувати API
"""

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.db import get_db
from backend.web.templates import templates
from backend.models.equipment import Equipment, EquipmentInstallation, EquipmentMaintenance

router = APIRouter(tags=["equipment-admin"])


@router.get("/admin/equipment", response_class=HTMLResponse)
async def equipment_admin_panel(
    request: Request,
    db: Session = Depends(get_db),
):
    """Admin panel для діагностики"""

    # Отримати всі обладнання
    equipment_list = db.query(Equipment).filter(
        Equipment.deleted_at.is_(None)
    ).all()

    # Статистика по статусах
    status_stats = {}
    for eq in equipment_list:
        status = eq.status or 'unknown'
        status_stats[status] = status_stats.get(status, 0) + 1

    # Перевірка наявності поля installation_location
    has_installation_location = False
    try:
        result = db.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'equipment_installation' 
            AND column_name = 'installation_location'
        """))
        has_installation_location = result.fetchone() is not None
    except:
        pass

    # Перевірка наявності поля condition
    has_condition = False
    try:
        result = db.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'equipment' 
            AND column_name = 'condition'
        """))
        has_condition = result.fetchone() is not None
    except:
        pass

    # Перевірка наявності поля no_document_confirmed
    has_no_document_confirmed = False
    try:
        result = db.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'equipment_installation' 
            AND column_name = 'no_document_confirmed'
        """))
        has_no_document_confirmed = result.fetchone() is not None
    except:
        pass

    context = {
        "request": request,
        "equipment_list": equipment_list,
        "status_stats": status_stats,
        "has_installation_location": has_installation_location,
        "has_condition": has_condition,
        "has_no_document_confirmed": has_no_document_confirmed,
    }

    return templates.TemplateResponse("equipment_admin.html", context)


@router.get("/admin/equipment/{equipment_id}/raw", response_class=JSONResponse)
async def equipment_raw_data(
    equipment_id: int,
    db: Session = Depends(get_db),
):
    """Повні RAW дані про обладнання"""

    # Equipment
    equipment = db.query(Equipment).filter(Equipment.id == equipment_id).first()
    if not equipment:
        return {"error": "Not found"}

    # Installations
    installations = db.query(EquipmentInstallation).filter(
        EquipmentInstallation.equipment_id == equipment_id
    ).all()

    # Maintenance
    maintenance = db.query(EquipmentMaintenance).filter(
        EquipmentMaintenance.equipment_id == equipment_id
    ).all()

    return {
        "equipment": {
            "id": equipment.id,
            "name": equipment.name,
            "equipment_type": equipment.equipment_type,
            "serial_number": equipment.serial_number,
            "status": equipment.status,
            "condition": getattr(equipment, 'condition', 'NO FIELD'),
            "current_location": equipment.current_location,
            "deleted_at": str(equipment.deleted_at) if equipment.deleted_at else None,
        },
        "installations": [
            {
                "id": inst.id,
                "well_id": inst.well_id,
                "installed_at": str(inst.installed_at),
                "removed_at": str(inst.removed_at) if inst.removed_at else None,
                "installed_by": inst.installed_by,
                "removed_by": inst.removed_by,
                "installation_location": getattr(inst, 'installation_location', 'NO FIELD'),
                "document_id": inst.document_id,
                "no_document_confirmed": getattr(inst, 'no_document_confirmed', 'NO FIELD'),
            }
            for inst in installations
        ],
        "maintenance": [
            {
                "id": m.id,
                "maintenance_type": m.maintenance_type,
                "maintenance_date": str(m.maintenance_date),
                "description": m.description,
                "cost": float(m.cost) if m.cost else None,
            }
            for m in maintenance
        ]
    }


@router.post("/admin/equipment/test-move")
async def test_move(
    equipment_id: int = Form(...),
    action: str = Form(...),
    well_id: int = Form(None),
    installation_location: str = Form(None),
    db: Session = Depends(get_db),
):
    """Тестування API move"""

    from datetime import datetime

    equipment = db.query(Equipment).filter(Equipment.id == equipment_id).first()
    if not equipment:
        return JSONResponse({"error": "Equipment not found", "equipment_id": equipment_id})

    try:
        if action == "install":
            # Перевірка активної установки
            active = db.query(EquipmentInstallation).filter(
                EquipmentInstallation.equipment_id == equipment_id,
                EquipmentInstallation.removed_at.is_(None)
            ).first()

            if active:
                return JSONResponse({
                    "error": "Already installed",
                    "active_installation": {
                        "id": active.id,
                        "well_id": active.well_id,
                        "installed_at": str(active.installed_at),
                    }
                })

            # Створення
            installation = EquipmentInstallation(
                equipment_id=equipment_id,
                well_id=well_id,
                installed_at=datetime.now(),
                removed_at=None,
                installed_by="1",  # test
                installation_location=installation_location,
                no_document_confirmed=True,
                notes="TEST from admin panel",
            )
            db.add(installation)

            equipment.status = 'installed'
            equipment.current_location = f"Свердловина {well_id}"

            db.commit()

            return JSONResponse({
                "success": True,
                "message": "Installed",
                "installation_id": installation.id,
            })

        elif action == "remove":
            active = db.query(EquipmentInstallation).filter(
                EquipmentInstallation.equipment_id == equipment_id,
                EquipmentInstallation.removed_at.is_(None)
            ).first()

            if not active:
                return JSONResponse({"error": "Not installed"})

            active.removed_at = datetime.now()
            active.removed_by = "1"  # test

            equipment.status = 'available'
            equipment.current_location = "Склад"

            db.commit()

            return JSONResponse({
                "success": True,
                "message": "Removed",
                "installation_id": active.id,
            })

        else:
            return JSONResponse({"error": f"Unknown action: {action}"})

    except Exception as e:
        db.rollback()
        return JSONResponse({"error": str(e), "type": type(e).__name__})


@router.post("/admin/equipment/test-status")
async def test_status(
    equipment_id: int = Form(...),
    new_status: str = Form(...),
    new_condition: str = Form(None),
    new_location: str = Form(None),
    db: Session = Depends(get_db),
):
    """Тестування зміни статусу"""

    from datetime import datetime

    equipment = db.query(Equipment).filter(Equipment.id == equipment_id).first()
    if not equipment:
        return JSONResponse({"error": "Equipment not found"})

    try:
        old_status = equipment.status
        equipment.status = new_status

        # Логіка локації
        if new_status == 'available' and not new_location:
            equipment.current_location = "Склад"
        elif new_status == 'maintenance' and not new_location:
            equipment.current_location = "Майстерня"
        elif new_location:
            equipment.current_location = new_location

        # Стан
        if new_condition:
            if hasattr(equipment, 'condition'):
                equipment.condition = new_condition
            else:
                return JSONResponse({"error": "Field 'condition' does not exist in database"})

        # Закрити активну установку
        if new_status in ['available', 'maintenance', 'broken', 'lost']:
            active = db.query(EquipmentInstallation).filter(
                EquipmentInstallation.equipment_id == equipment_id,
                EquipmentInstallation.removed_at.is_(None)
            ).first()

            if active:
                active.removed_at = datetime.now()
                active.removed_by = "1"  # test

        equipment.updated_at = datetime.now()
        db.commit()

        return JSONResponse({
            "success": True,
            "message": f"Status changed: {old_status} → {new_status}",
            "location": equipment.current_location,
        })

    except Exception as e:
        db.rollback()
        return JSONResponse({"error": str(e), "type": type(e).__name__})