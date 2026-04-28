"""
Управление LoRa-манометрами (прошивка CSV).

Страница: /admin/lora-sensors
- Прошивка датчиков (указание откуда читать данные CSV)
- Просмотр текущей установки (через equipment_installation)
- История перемещений (через equipment_installation)

Установка/снятие/перенос датчиков — через equipment_management.py.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form, HTTPException, Body
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.db import get_db
from backend.web.templates import templates, base_context

router = APIRouter(tags=["lora-sensors"])


def _default_role(csv_column: str) -> str:
    return "tube" if csv_column == "Ptr" else "line"


def _load_active_assignments(db: Session) -> dict:
    """{sensor_id: {"role": ..., "valid_from": ...}} for all active assignments."""
    try:
        rows = db.execute(
            text(
                "SELECT sensor_id, role, valid_from "
                "FROM lora_sensor_assignment WHERE valid_to IS NULL"
            )
        ).fetchall()
    except Exception:
        # Таблица ещё не создана (миграция не применена) — fallback.
        return {}
    return {r.sensor_id: {"role": r.role, "valid_from": r.valid_from} for r in rows}


@router.get("/admin/lora-sensors", response_class=HTMLResponse)
async def lora_sensors_page(
    request: Request,
    db: Session = Depends(get_db),
):
    """Страница управления LoRa-датчиками."""

    # Все датчики с текущей установкой (через equipment_installation)
    sensors_rows = db.execute(text("""
        SELECT
            s.id,
            s.serial_number,
            s.csv_group,
            s.csv_channel,
            s.csv_column,
            s.label,
            ei.id as installation_id,
            ei.well_id,
            w.name as well_name,
            ei.installed_at,
            ei.notes as installation_notes
        FROM lora_sensors s
        LEFT JOIN equipment e ON e.serial_number = s.serial_number
        LEFT JOIN equipment_installation ei ON ei.equipment_id = e.id AND ei.removed_at IS NULL
        LEFT JOIN wells w ON w.id = ei.well_id
        ORDER BY s.csv_group, s.csv_channel, s.csv_column
    """)).fetchall()

    # Активные назначения роли (если таблица существует)
    active_assignments = _load_active_assignments(db)

    # Обогащаем каждую строку: default_role, effective_role, assignment_*, global_channel, column_code
    sensors = []
    for r in sensors_rows:
        d = dict(r._mapping)
        default_role = _default_role(d["csv_column"])
        d["default_role"] = default_role
        assign = active_assignments.get(d["id"])
        if assign:
            d["assignment_role"] = assign["role"]
            d["assignment_valid_from"] = assign["valid_from"]
            d["effective_role"] = assign["role"]
            d["has_override"] = assign["role"] != default_role
        else:
            d["assignment_role"] = None
            d["assignment_valid_from"] = None
            d["effective_role"] = default_role
            d["has_override"] = False
        # Глобальный номер канала (1..10) и код колонки (1=Ptr, 2=Pshl)
        d["global_channel"] = (d["csv_group"] - 1) * 5 + d["csv_channel"]
        d["column_code"] = 1 if d["csv_column"] == "Ptr" else 2
        # Back-compat: 'position' в шаблоне — эффективная роль
        d["position"] = d["effective_role"]
        sensors.append(d)

    # Все скважины для выпадающего списка
    wells = db.execute(text("""
        SELECT id, name FROM wells ORDER BY name
    """)).fetchall()

    # Статистика
    total_sensors = len(sensors)
    installed_count = sum(1 for s in sensors if s["well_id"] is not None)
    uninstalled_count = total_sensors - installed_count
    overridden_count = sum(1 for s in sensors if s["has_override"])

    ctx = base_context(request)
    ctx.update({
        "sensors": sensors,
        "wells": wells,
        "total_sensors": total_sensors,
        "installed_count": installed_count,
        "uninstalled_count": uninstalled_count,
        "overridden_count": overridden_count,
    })
    return templates.TemplateResponse("lora_sensors.html", ctx)


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

    ctx = base_context(request)
    ctx.update({
        "sensor": sensor,
        "history": history,
    })
    return templates.TemplateResponse("lora_sensor_history.html", ctx)


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


# ═══════════════════════════════════════════════════════════
# Переприсвоение физической роли (устье↔шлейф)
# ═══════════════════════════════════════════════════════════

def _parse_local_dt(s: str) -> datetime:
    """Parse ISO-like naive datetime string entered by user (Kungrad local time)."""
    s = (s or "").strip()
    if not s:
        raise ValueError("valid_from is required")
    # Support "YYYY-MM-DDTHH:MM" and "YYYY-MM-DDTHH:MM:SS"
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError as e:
        raise ValueError(f"Invalid datetime format: {s!r}") from e


def _sensor_or_404(db: Session, sensor_id: int):
    row = db.execute(
        text(
            "SELECT id, serial_number, csv_group, csv_channel, csv_column "
            "FROM lora_sensors WHERE id = :sid"
        ),
        {"sid": sensor_id},
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Sensor {sensor_id} not found")
    return row


@router.get("/api/lora-sensors/{sensor_id}/assignments")
async def api_list_assignments(sensor_id: int, db: Session = Depends(get_db)):
    """История назначений роли + дефолт по csv_column."""
    sensor = _sensor_or_404(db, sensor_id)
    default_role = _default_role(sensor.csv_column)

    try:
        rows = db.execute(
            text(
                "SELECT id, role, valid_from, valid_to, note, created_by, created_at "
                "FROM lora_sensor_assignment "
                "WHERE sensor_id = :sid "
                "ORDER BY valid_from DESC"
            ),
            {"sid": sensor_id},
        ).fetchall()
    except Exception as e:
        raise HTTPException(
            500,
            f"Assignment table missing or inaccessible (migration not applied?): {e}",
        )

    history = [
        {
            "id": r.id,
            "role": r.role,
            "valid_from": r.valid_from.isoformat() if r.valid_from else None,
            "valid_to": r.valid_to.isoformat() if r.valid_to else None,
            "note": r.note,
            "created_by": r.created_by,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "is_active": r.valid_to is None,
        }
        for r in rows
    ]
    active = next((h for h in history if h["is_active"]), None)
    effective_role = active["role"] if active else default_role

    return JSONResponse({
        "sensor_id": sensor_id,
        "serial_number": sensor.serial_number,
        "csv_column": sensor.csv_column,
        "default_role": default_role,
        "effective_role": effective_role,
        "has_override": effective_role != default_role,
        "active": active,
        "history": history,
    })


@router.post("/api/lora-sensors/{sensor_id}/reassign/preview")
async def api_reassign_preview(
    sensor_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    """
    Предпросмотр retroactive переприсвоения. Body:
      {"new_role": "tube"|"line", "valid_from": "YYYY-MM-DDTHH:MM"}
    Возвращает количество строк к переносу и конфликтов.
    """
    from backend.services.sensor_role_reassign_service import preview

    _sensor_or_404(db, sensor_id)

    new_role = payload.get("new_role")
    if new_role not in ("tube", "line"):
        raise HTTPException(400, "new_role must be 'tube' or 'line'")

    try:
        valid_from_local = _parse_local_dt(payload.get("valid_from", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))

    try:
        result = preview(sensor_id, new_role, valid_from_local)
    except Exception as e:
        raise HTTPException(500, f"Preview failed: {e}")

    # Сериализация datetime
    vfu = result.get("valid_from_utc")
    return JSONResponse({
        "valid_from_local": valid_from_local.isoformat(),
        "valid_from_utc": vfu.isoformat() if vfu else None,
        "pg": result["pg"],
        "sqlite": result["sqlite"],
    })


@router.post("/api/lora-sensors/{sensor_id}/reassign/apply")
async def api_reassign_apply(
    sensor_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    """
    Применить переприсвоение: создать assignment + retroactive UPDATE + пересчёт aggregates.
    Body: {"new_role", "valid_from", "note"?, "overwrite_conflicts"?: bool}
    """
    from backend.services.sensor_role_reassign_service import apply as apply_reassign

    _sensor_or_404(db, sensor_id)

    new_role = payload.get("new_role")
    if new_role not in ("tube", "line"):
        raise HTTPException(400, "new_role must be 'tube' or 'line'")

    try:
        valid_from_local = _parse_local_dt(payload.get("valid_from", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))

    note = payload.get("note") or None
    overwrite = bool(payload.get("overwrite_conflicts", False))

    try:
        result = apply_reassign(
            pg_db=db,
            sensor_id=sensor_id,
            new_role=new_role,
            valid_from_local=valid_from_local,
            note=note,
            created_by=None,
            overwrite_conflicts=overwrite,
        )
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Apply failed: {e}")

    vfu = result.get("valid_from_utc")
    return JSONResponse({
        "assignment_id": result["assignment_id"],
        "valid_from_local": valid_from_local.isoformat(),
        "valid_from_utc": vfu.isoformat() if vfu else None,
        "pg": result["pg"],
        "sqlite": result["sqlite"],
        "affected_wells": result["affected_wells"],
    })


@router.post("/api/lora-sensors/{sensor_id}/backfill")
async def api_backfill_sensor(
    sensor_id: int,
    payload: dict = Body(default={}),
    db: Session = Depends(get_db),
):
    """
    Ретроактивный реимпорт CSV для датчика с даты `since` (local Кунград).

    Использовать, когда:
      - equipment_installation создана задним числом
      - в pressure_raw есть «дыры» по одному из каналов
      - после изменения маппинга датчика

    Body (all optional):
      - since: ISO datetime (local). По умолч.: последний installed_at датчика.
      - in_background: bool (default true) — запустить в фоновом потоке.
    """
    _sensor_or_404(db, sensor_id)

    since_raw = (payload.get("since") or "").strip()
    since_dt: Optional[datetime] = None
    if since_raw:
        try:
            since_dt = datetime.fromisoformat(since_raw.replace(" ", "T"))
        except Exception:
            raise HTTPException(400, "since must be ISO datetime (YYYY-MM-DDTHH:MM)")

    if since_dt is None:
        # Fallback: последняя установка датчика
        row = db.execute(text("""
            SELECT MIN(ei.installed_at) AS earliest
            FROM equipment_installation ei
            JOIN equipment e ON e.id = ei.equipment_id
            JOIN lora_sensors ls ON ls.serial_number = e.serial_number
            WHERE ls.id = :sid AND ei.removed_at IS NULL
        """), {"sid": sensor_id}).fetchone()
        if row and row[0]:
            since_dt = row[0]
    if since_dt is None:
        raise HTTPException(
            400,
            "Укажите since или назначьте датчик на скважину (equipment_installation)",
        )

    from backend.services.pressure_backfill_service import (
        backfill_for_sensor, maybe_backfill_after_install,
    )

    in_bg = bool(payload.get("in_background", True))
    if in_bg:
        # Запускаем через helper — сам выберет background thread
        # Нужно знать serial; берём через lora_sensors.id → any equipment_installation
        row = db.execute(text("""
            SELECT e.serial_number, ei.well_id, ei.installed_at
            FROM equipment_installation ei
            JOIN equipment e ON e.id = ei.equipment_id
            JOIN lora_sensors ls ON ls.serial_number = e.serial_number
            WHERE ls.id = :sid AND ei.removed_at IS NULL
            ORDER BY ei.installed_at DESC LIMIT 1
        """), {"sid": sensor_id}).fetchone()
        if not row:
            raise HTTPException(400, "Датчик не назначен на скважину")
        res = maybe_backfill_after_install(
            row[0], int(row[1]), since_dt, in_background=True,
        )
        return JSONResponse({
            "started": bool(res and res.get("started")),
            "sensor_id": sensor_id,
            "since_local": since_dt.isoformat(),
            "message": "Бэкфилл запущен в фоне. Обновите страницу через 1–2 минуты.",
        })

    # Synchronous mode (для отладки)
    res = backfill_for_sensor(sensor_id, since_dt, run_aggregation=True)
    return JSONResponse({
        "started": False,
        "sensor_id": sensor_id,
        "since_local": since_dt.isoformat(),
        **res,
    })
