"""
Управление LoRa-манометрами (прошивка CSV).

Страница: /admin/lora-sensors
- Прошивка датчиков (указание откуда читать данные CSV)
- Просмотр текущей установки (через equipment_installation)
- История перемещений (через equipment_installation)

Установка/снятие/перенос датчиков — через equipment_management.py.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.db import get_db
from backend.web.templates import templates

router = APIRouter(tags=["lora-sensors"])


@router.get("/admin/lora-sensors", response_class=HTMLResponse)
async def lora_sensors_page(
    request: Request,
    db: Session = Depends(get_db),
):
    """Страница управления LoRa-датчиками."""

    # Все датчики с текущей установкой (через equipment_installation)
    sensors = db.execute(text("""
        SELECT
            s.id,
            s.serial_number,
            s.csv_group,
            s.csv_channel,
            s.csv_column,
            s.label,
            ei.id as installation_id,
            ei.well_id,
            CASE WHEN s.csv_column = 'Ptr' THEN 'tube' ELSE 'line' END as position,
            w.name as well_name,
            ei.installed_at,
            ei.notes as installation_notes
        FROM lora_sensors s
        LEFT JOIN equipment e ON e.serial_number = s.serial_number
        LEFT JOIN equipment_installation ei ON ei.equipment_id = e.id AND ei.removed_at IS NULL
        LEFT JOIN wells w ON w.id = ei.well_id
        ORDER BY s.csv_group, s.csv_channel, s.csv_column
    """)).fetchall()

    # Все скважины для выпадающего списка
    wells = db.execute(text("""
        SELECT id, name FROM wells ORDER BY name
    """)).fetchall()

    # Статистика
    total_sensors = len(sensors)
    installed_count = sum(1 for s in sensors if s.well_id is not None)
    uninstalled_count = total_sensors - installed_count

    return templates.TemplateResponse("lora_sensors.html", {
        "request": request,
        "sensors": sensors,
        "wells": wells,
        "total_sensors": total_sensors,
        "installed_count": installed_count,
        "uninstalled_count": uninstalled_count,
    })


@router.post("/admin/lora-sensors/add")
async def add_sensor(
    request: Request,
    serial_number: str = Form(...),
    csv_group: int = Form(...),
    csv_channel: int = Form(...),
    csv_column: str = Form(...),
    label: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Прошить новый датчик."""

    # Проверяем уникальность серийника
    existing = db.execute(text("""
        SELECT id FROM lora_sensors WHERE serial_number = :serial_number
    """), {"serial_number": serial_number}).fetchone()

    if existing:
        raise HTTPException(400, f"Датчик с серийным номером {serial_number} уже существует")

    # Создаём датчик
    db.execute(text("""
        INSERT INTO lora_sensors (serial_number, csv_group, csv_channel, csv_column, label)
        VALUES (:serial_number, :csv_group, :csv_channel, :csv_column, :label)
    """), {
        "serial_number": serial_number,
        "csv_group": csv_group,
        "csv_channel": csv_channel,
        "csv_column": csv_column,
        "label": label,
    })
    db.commit()

    return RedirectResponse("/admin/lora-sensors", status_code=303)


@router.post("/admin/lora-sensors/edit")
async def edit_sensor(
    request: Request,
    sensor_id: int = Form(...),
    csv_group: int = Form(...),
    csv_channel: int = Form(...),
    csv_column: str = Form(...),
    label: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Изменить прошивку датчика."""

    db.execute(text("""
        UPDATE lora_sensors
        SET csv_group = :csv_group, csv_channel = :csv_channel, csv_column = :csv_column, label = :label, updated_at = NOW()
        WHERE id = :sensor_id
    """), {
        "sensor_id": sensor_id,
        "csv_group": csv_group,
        "csv_channel": csv_channel,
        "csv_column": csv_column,
        "label": label,
    })
    db.commit()

    return RedirectResponse("/admin/lora-sensors", status_code=303)


@router.get("/admin/lora-sensors/{sensor_id}/history", response_class=HTMLResponse)
async def sensor_history(
    request: Request,
    sensor_id: int,
    db: Session = Depends(get_db),
):
    """История установок датчика."""

    sensor = db.execute(text("""
        SELECT id, serial_number, csv_group, csv_channel, csv_column, label
        FROM lora_sensors WHERE id = :sensor_id
    """), {"sensor_id": sensor_id}).fetchone()

    if not sensor:
        raise HTTPException(404, "Датчик не найден")

    history = db.execute(text("""
        SELECT
            ei.id,
            ei.well_id,
            w.name as well_name,
            CASE WHEN s.csv_column = 'Ptr' THEN 'tube' ELSE 'line' END as position,
            ei.installed_at,
            ei.removed_at,
            ei.notes
        FROM lora_sensors s
        JOIN equipment e ON e.serial_number = s.serial_number
        JOIN equipment_installation ei ON ei.equipment_id = e.id
        JOIN wells w ON w.id = ei.well_id
        WHERE s.id = :sensor_id
        ORDER BY ei.installed_at DESC
    """), {"sensor_id": sensor_id}).fetchall()

    return templates.TemplateResponse("lora_sensor_history.html", {
        "request": request,
        "sensor": sensor,
        "history": history,
    })


@router.get("/api/lora-sensors", response_class=HTMLResponse)
async def api_lora_sensors(
    db: Session = Depends(get_db),
):
    """API: Получить все датчики с текущими установками."""
    from fastapi.responses import JSONResponse

    sensors = db.execute(text("""
        SELECT
            s.id,
            s.serial_number,
            s.csv_group,
            s.csv_channel,
            s.csv_column,
            ei.well_id,
            w.name as well_name,
            CASE WHEN s.csv_column = 'Ptr' THEN 'tube' ELSE 'line' END as position,
            ei.installed_at
        FROM lora_sensors s
        LEFT JOIN equipment e ON e.serial_number = s.serial_number
        LEFT JOIN equipment_installation ei ON ei.equipment_id = e.id AND ei.removed_at IS NULL
        LEFT JOIN wells w ON w.id = ei.well_id
        ORDER BY s.csv_group, s.csv_channel
    """)).fetchall()

    result = []
    for s in sensors:
        result.append({
            "id": s.id,
            "serial_number": s.serial_number,
            "csv_group": s.csv_group,
            "csv_channel": s.csv_channel,
            "csv_column": s.csv_column,
            "well_id": s.well_id,
            "well_name": s.well_name,
            "position": s.position,
            "installed_at": s.installed_at.isoformat() if s.installed_at else None,
        })

    return JSONResponse(result)
