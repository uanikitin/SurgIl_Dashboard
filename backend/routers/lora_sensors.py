"""
Управление LoRa-манометрами и их установками на скважины.

Страница: /admin/lora-sensors
- Прошивка датчиков (указание откуда читать данные CSV)
- Установка датчиков на скважины с указанием места (затруб/шлейф)
- История перемещений
"""

from datetime import datetime
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

    # Все датчики с текущей установкой
    sensors = db.execute(text("""
        SELECT
            s.id,
            s.serial_number,
            s.csv_group,
            s.csv_channel,
            s.csv_column,
            s.label,
            i.id as installation_id,
            i.well_id,
            i.position,
            w.name as well_name,
            i.installed_at,
            i.notes as installation_notes
        FROM lora_sensors s
        LEFT JOIN sensor_installations i ON i.sensor_id = s.id AND i.removed_at IS NULL
        LEFT JOIN wells w ON w.id = i.well_id
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


@router.post("/admin/lora-sensors/install")
async def install_sensor(
    request: Request,
    sensor_id: int = Form(...),
    well_id: int = Form(...),
    position: str = Form(...),
    installed_at: str = Form(...),
    notes: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Установить датчик на скважину."""

    # Проверяем что датчик не установлен
    existing = db.execute(text("""
        SELECT id FROM sensor_installations
        WHERE sensor_id = :sensor_id AND removed_at IS NULL
    """), {"sensor_id": sensor_id}).fetchone()

    if existing:
        raise HTTPException(400, "Датчик уже установлен. Сначала снимите его.")

    # Парсим дату
    try:
        installed_dt = datetime.fromisoformat(installed_at)
    except ValueError:
        raise HTTPException(400, "Неверный формат даты")

    # Создаём установку
    db.execute(text("""
        INSERT INTO sensor_installations (sensor_id, well_id, position, installed_at, notes)
        VALUES (:sensor_id, :well_id, :position, :installed_at, :notes)
    """), {
        "sensor_id": sensor_id,
        "well_id": well_id,
        "position": position,
        "installed_at": installed_dt,
        "notes": notes,
    })
    db.commit()

    return RedirectResponse("/admin/lora-sensors", status_code=303)


@router.post("/admin/lora-sensors/remove")
async def remove_sensor(
    request: Request,
    installation_id: int = Form(...),
    removed_at: str = Form(...),
    db: Session = Depends(get_db),
):
    """Снять датчик со скважины."""

    # Парсим дату
    try:
        removed_dt = datetime.fromisoformat(removed_at)
    except ValueError:
        raise HTTPException(400, "Неверный формат даты")

    # Обновляем установку
    db.execute(text("""
        UPDATE sensor_installations
        SET removed_at = :removed_at
        WHERE id = :installation_id AND removed_at IS NULL
    """), {
        "installation_id": installation_id,
        "removed_at": removed_dt,
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
            i.id,
            i.well_id,
            w.name as well_name,
            i.position,
            i.installed_at,
            i.removed_at,
            i.notes
        FROM sensor_installations i
        JOIN wells w ON w.id = i.well_id
        WHERE i.sensor_id = :sensor_id
        ORDER BY i.installed_at DESC
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
            i.well_id,
            w.name as well_name,
            i.position,
            i.installed_at
        FROM lora_sensors s
        LEFT JOIN sensor_installations i ON i.sensor_id = s.id AND i.removed_at IS NULL
        LEFT JOIN wells w ON w.id = i.well_id
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
