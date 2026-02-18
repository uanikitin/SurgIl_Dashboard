from fastapi import FastAPI, Request, Depends, Form, HTTPException, status, Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
import json
from starlette.middleware.sessions import SessionMiddleware
from fastapi import Form
from sqlalchemy.orm import Session
from sqlalchemy import func, case, text
from sqlalchemy import desc
from datetime import datetime, timedelta, date, time, timezone

from .api import wells
from .settings import settings
from .db import get_db
from .deps import get_current_user, get_current_admin

from .models.wells import Well
from .models.well_channel import WellChannel
from .models.well_equipment import WellEquipment
from .models.events import Event
from .models.users import User
from .models.well_status import WellStatus
from .models.well_sub_status import WellSubStatus
from .models.well_notes import WellNote

from backend.services.equipment_loader import EQUIPMENT_LIST, EQUIPMENT_BY_CODE
from .config.status_registry import (
    css_by_label,
    allowed_labels,
    STATUS_LIST,
    status_groups_for_sidebar,
)
from .config.substatus_registry import (
    SUBSTATUS_LIST as SUBSTATUS_CONFIG,
    color_by_label as substatus_color,
)
from datetime import datetime, timedelta, date, time
from collections import defaultdict, defaultdict as _dd
import io
import csv
from openpyxl import Workbook
import os
from fastapi.staticfiles import StaticFiles
import time as time_module
from backend.auth import get_password_hash, get_current_user_optional, verify_password, get_reagents_user
from backend.models import DashboardUser, DashboardLoginLog

from .db import get_db, SessionLocal
from collections import defaultdict

# Добавить в начало файла app.py
from backend.models.reagent_catalog import ReagentCatalog
from backend.services.reagent_balance_service import ReagentBalanceService
from backend.models.users import DashboardUser
from backend.services.well_sync_service import sync_wells_from_events, fix_wells_missing_data
from backend.services.pressure_aggregate_service import get_wells_pressure_stats
from backend.models.events import Event
from backend.models.reagents import ReagentSupply
from .api import wells
from .api import reagents as reagents_api
from backend.repositories.reagents_service import (
    create_reagent_supply,
    list_reagent_supplies,
)

# --- helpers for reagent catalog (ONE SOURCE OF TRUTH) ---
from decimal import Decimal

from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func
from fastapi import HTTPException
from starlette.datastructures import FormData

from fastapi.templating import Jinja2Templates

from backend.routers import equipment_documents
# Вариант 3: Абсолютный импорт
from backend.config.equipment_config import get_equipment_config, EQUIPMENT_TYPES

templates = Jinja2Templates(directory="backend/templates")

app = FastAPI(title=settings.APP_TITLE)
app.include_router(equipment_documents.router)
# --- sessions (обязательно, иначе request.session не работает) ---
app.add_middleware(
    SessionMiddleware,
    secret_key=getattr(settings, "SESSION_SECRET_KEY", "CHANGE_ME_SECRET_KEY"),
    session_cookie="surgil_session",
    same_site="lax",
    https_only=False,  # поставишь True, когда будет HTTPS
)

from backend.routers.documents_pages import router as documents_pages_router
from backend.routers.pressure import router as pressure_router

app.include_router(documents_pages_router)
app.include_router(pressure_router)

from backend.routers.jobs_api import router as jobs_api_router
app.include_router(jobs_api_router)

from backend.routers import documents_well_handover

app.include_router(documents_well_handover.router)


from backend.routers.equipment_management import router as equipment_router
app.include_router(equipment_router, prefix="")

# from backend.routers.equipment_admin import router as equipment_admin_router
# app.include_router(equipment_admin_router, prefix="")

from backend.models.equipment import Equipment, EquipmentInstallation


from backend.routers.equipment_admin import router as equipment_admin_router
app.include_router(equipment_admin_router)

# Находим в app.py блок с подключением роутеров
from backend.routers.well_equipment_integration import router as well_equipment_router
app.include_router(well_equipment_router)

# LoRa датчики (манометры)
from backend.routers.lora_sensors import router as lora_sensors_router
app.include_router(lora_sensors_router)

# Telegram chat (отправка сообщений из дашборда)
from backend.routers.chat import router as chat_router
app.include_router(chat_router)

# Расчёт дебита газа по данным давления
from backend.routers.flow_rate import router as flow_rate_router
app.include_router(flow_rate_router)

# Анализ дебита (сценарии, коррекции, результаты)
from backend.routers.flow_analysis import router as flow_analysis_router
from backend.routers.flow_analysis import pages_router as flow_analysis_pages_router
app.include_router(flow_analysis_router)
app.include_router(flow_analysis_pages_router)

# ------------------------------------------------------------
# 1) SAFE helpers: FormData -> string
# ------------------------------------------------------------
def _form_get_str(form: dict | FormData, key: str, default: str = "") -> str:
    """
    Безопасно достаёт строку из формы.
    Поддерживает:
      - обычный dict
      - Starlette FormData (MultiDict)
    Если значение list/tuple — берём первый элемент.
    """
    if form is None:
        return default

    val = form.get(key, default)

    # FormData / MultiDict иногда возвращает списки
    if isinstance(val, (list, tuple)):
        val = val[0] if val else default

    if val is None:
        return default

    return str(val).strip()


def _parse_datetime_local_to_db_naive(dt_str: str | None) -> datetime | None:
    """
    <input type="datetime-local"> ('YYYY-MM-DDTHH:MM') -> naive datetime.
    Без timezone-конвертаций.
    """
    if not dt_str:
        return None
    s = str(dt_str).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)  # naive
    except ValueError:
        return None


def _get_or_create_catalog_item(db: Session, name: str, unit: str | None = None) -> "ReagentCatalog":
    name_clean = (name or "").strip()
    if not name_clean:
        raise ValueError("reagent name is empty")

    item = (
        db.query(ReagentCatalog)
        .filter(func.lower(ReagentCatalog.name) == name_clean.lower())
        .first()
    )

    if item:
        # если unit в каталоге пустой — можно заполнить из формы
        if (not (item.default_unit or "").strip()) and unit:
            item.default_unit = unit.strip() or "шт"
            db.commit()
            db.refresh(item)
        return item

    item = ReagentCatalog(
        name=name_clean,
        default_unit=(unit or "шт").strip() or "шт",
        is_active=True,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


# ------------------------------------------------------------
# 2) REPLACE ENTIRE FUNCTION: единый разбор реагента из формы
# ------------------------------------------------------------
def _resolve_reagent_from_form(
        db: Session,
        form: dict | FormData,
        *,
        select_field: str = "reagent",
        new_field: str = "reagent_new",
        unit_field: str = "unit",
) -> tuple[str, int | None, str]:
    """
    ЕДИНЫЙ алгоритм для Supply и Inventory:
      - <select name="reagent">: либо имя реагента, либо "__new__"
      - <input name="reagent_new">: имя нового реагента (если выбран "__new__")
      - <input/select name="unit">: единица измерения

    Возвращает: (reagent_name, reagent_id, unit)
    """

    raw_select = _form_get_str(form, select_field)
    raw_new = _form_get_str(form, new_field)
    raw_unit = _form_get_str(form, unit_field)

    if raw_select == "__new__":
        name = raw_new
        if not name:
            raise ValueError("Не указано название нового реагента")
    else:
        name = raw_select
        if not name:
            raise ValueError("Не указан реагент")

    # сначала создаём/находим в каталоге
    item = _get_or_create_catalog_item(db, name=name, unit=(raw_unit or None))

    # unit: приоритет формы -> иначе из каталога -> иначе "шт"
    unit = (raw_unit or item.default_unit or "шт").strip() or "шт"

    return item.name, item.id, unit


# ------------------------------------------------------------
# 3) REPLACE ENTIRE ENDPOINT: /admin/reagents/add
# ------------------------------------------------------------
@app.post("/admin/reagents/add")
async def admin_reagents_add_supply(
        request: Request,
        db: Session = Depends(get_db),
        current_user=Depends(get_reagents_user),
):
    form = await request.form()

    # reagent + unit
    try:
        reagent_name, reagent_id, unit = _resolve_reagent_from_form(
            db,
            form,
            select_field="reagent",
            new_field="reagent_new",
            unit_field="unit",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # qty
    qty_raw = _form_get_str(form, "qty")
    try:
        qty = float(qty_raw.replace(",", "."))
    except Exception:
        raise HTTPException(status_code=400, detail="Некорректное количество (qty)")

    # received_at
    received_at_str = _form_get_str(form, "received_at")
    dt = _parse_datetime_local_to_db_naive(received_at_str) or _now_db()

    source = _form_get_str(form, "source") or None
    location = _form_get_str(form, "location") or None
    comment = _form_get_str(form, "comment") or None

    supply = ReagentSupply(
        reagent=reagent_name,
        reagent_id=reagent_id,
        qty=qty,
        unit=unit,
        received_at=dt,
        source=source,
        location=location,
        comment=comment,
    )
    db.add(supply)
    db.commit()

    return RedirectResponse("/admin/reagents", status_code=303)


# === Автоматическое создание мастер-админа ===
@app.on_event("startup")
def create_master_admin():
    """
    При старте приложения проверяем, есть ли пользователь 'admin'.
    Если нет — создаём его с правами администратора.
    """
    db = SessionLocal()
    try:
        # тут можно читать из settings, если захочешь:
        # username = settings.MASTER_ADMIN_USERNAME
        # password = settings.MASTER_ADMIN_PASSWORD
        username = "admin"
        password = "admin123"  # ЗАДАЙ СВОЙ ПАРОЛЬ
        email = "ua.nikitin@gmail.com"

        admin = (
            db.query(DashboardUser)
            .filter(DashboardUser.username == username)
            .first()
        )

        if not admin:
            admin = DashboardUser(
                username=username,
                password_hash=get_password_hash(password),
                email=email,
                first_name="Admin",
                last_name="User",
                is_admin=True,
                is_active=True,
            )
            db.add(admin)
            db.commit()
            print(">>> Мастер-админ создан: admin / admin123")
        else:
            # на всякий случай включаем ему админские права и активность
            changed = False
            if not admin.is_admin:
                admin.is_admin = True
                changed = True
            if not admin.is_active:
                admin.is_active = True
                changed = True
            if changed:
                db.commit()
                print(">>> Обновлены права существующего admin (is_admin/is_active)")
    finally:
        db.close()


@app.on_event("startup")
def ensure_pressure_raw_table():
    """
    Создаёт таблицу pressure_raw если её нет.
    Безопасно: CREATE TABLE IF NOT EXISTS не трогает существующие таблицы.
    Нужна для графиков давлений на Render (где нет локального SQLite).
    """
    from backend.db import engine as pg_engine
    try:
        with pg_engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS pressure_raw (
                    id BIGSERIAL PRIMARY KEY,
                    well_id INTEGER NOT NULL,
                    measured_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                    p_tube DOUBLE PRECISION,
                    p_line DOUBLE PRECISION,
                    sensor_id_tube INTEGER,
                    sensor_id_line INTEGER,
                    CONSTRAINT uq_pressure_raw_well_time
                        UNIQUE (well_id, measured_at)
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_pressure_raw_well_measured
                ON pressure_raw (well_id, measured_at)
            """))
            # Добавить колонки если таблица уже существует без них
            for col in ("sensor_id_tube", "sensor_id_line"):
                try:
                    conn.execute(text(
                        f"ALTER TABLE pressure_raw ADD COLUMN IF NOT EXISTS {col} INTEGER"
                    ))
                except Exception:
                    pass
        print(">>> pressure_raw таблица проверена/создана")
    except Exception as e:
        print(f">>> pressure_raw: ошибка при создании: {e}")


@app.get("/", include_in_schema=False)
async def root(current_user: str = Depends(get_current_user)):
    return RedirectResponse("/visual")


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": None,
            "current_user": None,
            "is_admin": False,
        }
    )


@app.get("/register", response_class=HTMLResponse)
def register_get(
        request: Request,
        current_user: User | None = Depends(get_current_user_optional),
):
    base_context = {
        "request": request,
        "current_user": current_user,
        "is_admin": bool(getattr(current_user, "is_admin", False)),
        "form_username": "",
        "form_full_name": "",
        "form_email": "",
        "error": None,
    }

    return templates.TemplateResponse(
        "register.html",
        base_context,
    )


@app.post("/register", response_class=HTMLResponse)
def register_post(
        request: Request,
        username: str = Form(...),
        full_name: str = Form(""),
        email: str = Form(""),
        password: str = Form(...),
        password2: str = Form(...),
        db: Session = Depends(get_db),
        current_user: User | None = Depends(get_current_user_optional),
):
    # значения для возврата формы при ошибке
    base_context = {
        "request": request,
        "current_user": current_user,
        "is_admin": bool(getattr(current_user, "is_admin", False)),
        "form_username": username,
        "form_full_name": full_name,
        "form_email": email,
    }

    # 1) проверки
    if not username or not password:
        return templates.TemplateResponse(
            "register.html",
            {**base_context, "error": "Логин и пароль обязательны"},
            status_code=400,
        )

    if password != password2:
        return templates.TemplateResponse(
            "register.html",
            {**base_context, "error": "Пароли не совпадают"},
            status_code=400,
        )

    # логин уже занят в dashboard_users
    existing = (
        db.query(DashboardUser)
        .filter(DashboardUser.username == username)
        .first()
    )
    if existing:
        return templates.TemplateResponse(
            "register.html",
            {**base_context, "error": "Пользователь с таким логином уже существует"},
            status_code=400,
        )

    # 2) разбираем ФИО: первое слово — имя, остальное — фамилия
    first_name = None
    last_name = None
    if full_name.strip():
        parts = full_name.strip().split(maxsplit=1)
        first_name = parts[0]
        if len(parts) > 1:
            last_name = parts[1]

    # 3) создаём пользователя в dashboard_users
    user = DashboardUser(
        username=username,
        password_hash=get_password_hash(password),
        email=email or None,
        first_name=first_name,
        last_name=last_name,
        is_admin=False,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # 4) сразу логиним
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["is_admin"] = user.is_admin

    return RedirectResponse(url="/visual", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/login")
async def login_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    username = form.get("username", "").strip()
    password = form.get("password", "")

    user = (
        db.query(DashboardUser)
        .filter(DashboardUser.username == username)
        .first()
    )

    if not user or not verify_password(password, user.password_hash):
        # Ошибка — просто снова показываем login.html с сообщением
        context = {
            "request": request,
            "error": "Неверный логин или пароль",
            "current_user": None,
            "is_admin": False,
        }
        return templates.TemplateResponse(
            "login.html",  # ВАЖНО: без "auth/"
            context,
            status_code=400,
        )

    # ==== ВАЖНО: записываем ВСЕ ключи, которые ждёт старый код ====
    # старый get_current_user, скорее всего, смотрит на session["user"]
    request.session["user"] = user.username  # ← ЭТО главный ключ
    request.session["user_id"] = user.id  # удобно для БД
    request.session["username"] = user.username  # если где-то используется
    request.session["is_admin"] = bool(user.is_admin)
    # ============================================================
    # обновляем поле last_login_at у пользователя
    user.last_login_at = _now_db()
    db.add(user)
    db.commit()
    # ==== Закрываем предыдущую незавершённую сессию ====
    old_log_id = request.session.get("session_log_id")
    if old_log_id:
        old_log = db.query(DashboardLoginLog).filter_by(id=old_log_id).first()
        if old_log and old_log.logout_at is None:
            old_log.logout_at = _now_db()
            db.commit()
    # ===================================================

    # создаём запись в журнале логинов (с IP и User-Agent)
    log = DashboardLoginLog(
        user_id=user.id,
        ip_address=request.client.host,
        user_agent=request.headers.get("User-Agent"),
    )
    db.add(log)
    db.commit()

    # сохраняем id журнала в сессии
    request.session["session_log_id"] = log.id

    return RedirectResponse(url="/visual", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/logout")
def logout(
        request: Request,
        db: Session = Depends(get_db),
):
    # закрываем лог сессии, если есть
    log_id = request.session.get("session_log_id")
    if log_id:
        log = db.query(DashboardLoginLog).filter(DashboardLoginLog.id == log_id).first()
        if log and log.logout_at is None:
            log.logout_at = _now_db()
            db.add(log)
            db.commit()

    # чистим сессию
    # for key in ("user", "user_id", "username", "is_admin", "session_log_id"):
    #     request.session.pop(key, None)
    request.session.clear()

    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


def _parse_coord(value: str) -> float | None:
    """
    Аккуратно парсим координату из строки.
    Правила:
    - если пусто -> None (координата не задана)
    - заменяем запятую на точку
    - если не получается преобразовать -> ошибка 400
    """
    if value is None:
        return None

    value = value.strip()
    if not value:
        return None  # пользователь оставил поле пустым

    # Разрешаем ввод "43,621" -> "43.621"
    value = value.replace(",", ".")
    try:
        return float(value)
    except ValueError:
        # Здесь мы выбрасываем HTTP-исключение — FastAPI превратит его в ответ 400 Bad Request
        raise HTTPException(
            status_code=400,
            detail=f"Некорректное значение координаты: {value!r}. Ожидаю число, например 43.621",
        )


def _parse_dt_local(value: str | None):
    """
    Парсим строку из <input type="datetime-local">.
    Формат: 'YYYY-MM-DDTHH:MM'. Если пусто или формат кривой — возвращаем None.
    """
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def _to_naive(dt: datetime | None) -> datetime | None:
    """
    Приводим datetime к "naive" (без tzinfo), чтобы можно было
    спокойно вычитать и сравнивать.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.replace(tzinfo=None)


# === Шаблоны и статика ===
# Папка с HTML-шаблонами
templates = Jinja2Templates(directory="backend/templates")
templates.env.globals['time'] = lambda: int(time_module.time())  # для обновления CSS

# Фильтр для конвертации UTC → UTC+5 (Кунград, Узбекистан)
def to_kungrad_tz(dt):
    """Конвертирует datetime из UTC в UTC+5 (время Кунграда)."""
    if dt is None:
        return None
    from datetime import timedelta
    return dt + timedelta(hours=5)

templates.env.filters['to_kungrad'] = to_kungrad_tz

# Чтобы браузер ВСЕГДА брал свежий CSS
version = str(int(time_module.time()))
# Папка со статикой (css, js, картинки)
# Статика
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"  # backend/static

app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

# Подключаем API-роутеры
app.include_router(
    wells.router,
    dependencies=[Depends(get_current_user)]
)

app.include_router(
    reagents_api.router,
    dependencies=[Depends(get_current_user)]  # или get_reagents_user, если хочешь ужесточить
)


def _now_db() -> datetime:
    """
    Единый источник времени для записи в БД.
    Возвращает NAIVE datetime в Кунградском времени (UTC+5).
    """
    return datetime.utcnow() + timedelta(hours=5)


# ── Дебит для карточек дашборда ─────────────────────────────
import math as _math

def _calc_daily_flow_for_tiles(
    well_ids: list[int],
) -> tuple[dict[int, float | None], dict[int, float | None]]:
    """
    Средний суточный дебит за сегодня и вчера для списка скважин.

    Использует pressure_hourly (уже агрегированные средние за час),
    формулу истечения газа через штуцер (та же, что в flow_rate/calculator.py),
    и choke_diam_mm из well_construction.

    Returns: (flow_today, flow_yesterday) — dict[well_id] → float | None
    """
    from backend.db import engine as pg_engine

    if not well_ids:
        return {}, {}

    # Кунград UTC+5: «сегодня» = 00:00 .. now по UTC+5
    now_kungrad = datetime.utcnow() + timedelta(hours=5)
    today_start_utc = (
        now_kungrad.replace(hour=0, minute=0, second=0, microsecond=0)
        - timedelta(hours=5)
    )
    yesterday_start_utc = today_start_utc - timedelta(days=1)

    well_id_csv = ",".join(str(int(w)) for w in well_ids)

    # 1) Медианные давления за сегодня и вчера (из pressure_hourly)
    # Медиана вместо AVG — защита от ложных скачков датчиков (tube=1.7 вместо 17)
    sql_pressure = text(f"""
        SELECT
            well_id,
            CASE WHEN hour_start >= :today THEN 'today' ELSE 'yesterday' END AS day,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY NULLIF(p_tube_avg, 0.0)) AS p_tube,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY NULLIF(p_line_avg, 0.0)) AS p_line
        FROM pressure_hourly
        WHERE well_id IN ({well_id_csv})
          AND hour_start >= :yesterday
        GROUP BY well_id, day
    """)

    # 2) Штуцеры для всех скважин (batch)
    sql_choke = text(f"""
        SELECT DISTINCT ON (w.id)
            w.id AS well_id,
            wc.choke_diam_mm
        FROM wells w
        JOIN well_construction wc ON w.number::text = wc.well_no
        WHERE w.id IN ({well_id_csv})
          AND wc.choke_diam_mm IS NOT NULL
        ORDER BY w.id, wc.data_as_of DESC NULLS LAST
    """)

    flow_today: dict[int, float | None] = {}
    flow_yesterday: dict[int, float | None] = {}

    try:
        with pg_engine.connect() as conn:
            # Давления
            pressure_rows = conn.execute(
                sql_pressure,
                {"today": today_start_utc, "yesterday": yesterday_start_utc},
            ).fetchall()

            # Штуцеры
            choke_rows = conn.execute(sql_choke).fetchall()
    except Exception:
        return {}, {}

    choke_map: dict[int, float] = {r[0]: float(r[1]) for r in choke_rows}

    # Формула (идентична calculator.py DEFAULT_FLOW)
    C1 = 2.919
    C2 = 4.654
    C3 = 286.95
    multiplier = 4.1
    crit_ratio = 0.5

    def _q(p_tube: float, p_line: float, choke_mm: float) -> float:
        if p_tube <= p_line or p_tube <= 0:
            return 0.0
        r = (p_tube - p_line) / p_tube
        choke_sq = (choke_mm / C2) ** 2
        if r < crit_ratio:
            q = C1 * choke_sq * p_tube * (1.0 - r / 1.5) * _math.sqrt(max(r / C3, 0.0))
        else:
            q = 0.667 * C1 * choke_sq * p_tube * _math.sqrt(0.5 / C3)
        return max(q * multiplier, 0.0)

    for row in pressure_rows:
        wid, day_label, pt, pl = row[0], row[1], row[2], row[3]
        choke = choke_map.get(wid)
        if pt is None or pl is None or choke is None:
            val = None
        else:
            val = round(_q(float(pt), float(pl), choke), 2)
        if day_label == "today":
            flow_today[wid] = val
        else:
            flow_yesterday[wid] = val

    return flow_today, flow_yesterday


# === Наша первая страница дашборда ===
@app.get("/visual", response_class=HTMLResponse)
def visual_page(
        request: Request,
        db: Session = Depends(get_db),
        selected: list[int] = Query(default=[]),
        current_user: str = Depends(get_current_user),
        # ЕДИНЫЙ НАБОР ФИЛЬТРОВ для плиток И графика
        tl_wells: list[str] = Query(default=[]),  # Мультивыбор скважин
        tl_statuses: list[str] = Query(default=[]),  # Мультивыбор статусов
        tl_event_types: list[str] = Query(default=[]),  # Мультивыбор типов событий
        tl_reagents: list[str] = Query(default=[]),  # Мультивыбор реагентов
        tl_period: str = Query("3d"),  # Быстрый выбор периода: 1d, 3d, 1w, 1m, custom
        tl_date_from: str = Query(None),  # Для ручного периода
        tl_date_to: str = Query(None),
        tl_sort: str = Query("desc"),
        # Фильтр периода для давлений на плитках
        pressure_period: str = Query("1h"),  # 10m, 1h, 1d, 1m
):
    """
    Главная страница дашборда:
    - слева: список скважин с галочками
    - справа: плитки по выбранным скважинам
    - внизу: карта со всеми скважинами
    """

    # 0) Синхронизация: создаём скважины из events, которых нет в wells
    # Это гарантирует что новые скважины из Telegram-бота появятся в списке
    try:
        new_wells = sync_wells_from_events(db)
        if new_wells:
            print(f"[visual_page] Созданы новые скважины из events: {[w.name for w in new_wells]}")

        # Исправляем скважины без имён или координат
        fixed = fix_wells_missing_data(db)
        if fixed:
            print(f"[visual_page] Исправлено {fixed} скважин")
    except Exception as e:
        print(f"[visual_page] Ошибка синхронизации скважин: {e}")
        import traceback
        traceback.print_exc()

    # 1) Все скважины для списка слева
    all_wells = (
        db.query(Well)
        .order_by(Well.name.asc().nulls_last(), Well.id.asc())
        .all()
    )

    print(f"[visual_page] Загружено {len(all_wells)} скважин из wells")
    print(f"[visual_page] Скважины: {[(w.id, w.number, w.name) for w in all_wells]}")

    # 2) Какие скважины показывать как плитки
    if selected:
        selected_set = set(selected)
        tiles = [w for w in all_wells if w.id in selected_set]
    else:
        tiles = all_wells

    now = datetime.now()
    now_naive = _to_naive(now)

    # ----- A) ТЕКУЩИЙ СТАТУС ДЛЯ ВСЕХ СКВАЖИН -----
    if all_wells:
        all_ids = [w.id for w in all_wells]

        active_statuses = (
            db.query(WellStatus)
            .filter(
                WellStatus.well_id.in_(all_ids),
                WellStatus.dt_end.is_(None),
            )
            .all()
        )
        by_well_id = {st.well_id: st for st in active_statuses}
    else:
        by_well_id = {}

    for w in all_wells:
        st = by_well_id.get(w.id)
        if st:
            w.current_status = st.status
            w.current_status_css = css_by_label(st.status)

            start_dt = _to_naive(st.dt_start)
            w.current_status_start = start_dt

            if start_dt and now_naive:
                delta = now_naive - start_dt
                w.current_status_days = round(delta.total_seconds() / 86400, 1)
            else:
                w.current_status_days = None
        else:
            w.current_status = None
            w.current_status_css = None
            w.current_status_start = None
            w.current_status_days = None

    # ----- A2) ТЕКУЩИЙ ПОДСТАТУС ДЛЯ ВСЕХ СКВАЖИН -----
    if all_wells:
        active_substatuses = (
            db.query(WellSubStatus)
            .filter(
                WellSubStatus.well_id.in_(all_ids),
                WellSubStatus.dt_end.is_(None),
            )
            .all()
        )
        substatus_by_well = {sst.well_id: sst for sst in active_substatuses}
    else:
        substatus_by_well = {}

    for w in all_wells:
        sst = substatus_by_well.get(w.id)
        if sst:
            w.current_substatus = sst.sub_status
            w.current_substatus_color = substatus_color(sst.sub_status)
            w.current_substatus_start = _to_naive(sst.dt_start)
        else:
            w.current_substatus = "В работе"
            w.current_substatus_color = "#10b981"
            w.current_substatus_start = None

    # ----- A3) ПОСЛЕДНИЕ СОБЫТИЯ ДЛЯ КАЖДОЙ СКВАЖИНЫ (2 шт) -----
    if tiles:
        well_keys = []
        _key_to_id = {}
        for w in tiles:
            k = str(w.number) if w.number else str(w.id)
            well_keys.append(k)
            _key_to_id[k] = w.id

        recent_events_raw = (
            db.query(Event)
            .filter(Event.well.in_(well_keys))
            .order_by(Event.event_time.desc())
            .limit(len(well_keys) * 3)
            .all()
        )
        recent_by_well: dict[int, list] = {}
        for ev in recent_events_raw:
            wid = _key_to_id.get(str(ev.well))
            if wid is None:
                continue
            lst = recent_by_well.setdefault(wid, [])
            if len(lst) < 2:
                lst.append(ev)

        for w in tiles:
            w.recent_events = recent_by_well.get(w.id, [])
    else:
        for w in tiles:
            w.recent_events = []

    # ----- A4) ГЛОБАЛЬНАЯ ЛЕНТА (последние 20 событий) -----
    global_recent_events = (
        db.query(Event)
        .order_by(Event.event_time.desc())
        .limit(20)
        .all()
    )

    # ----- B) СТАТИСТИКА СОБЫТИЙ ПО КАЛЕНДАРНЫМ СУТКАМ -----
    if tiles:
        today = now_naive.date()
        yesterday = today - timedelta(days=1)

        start_range = datetime.combine(yesterday, datetime.min.time())
        end_range = datetime.combine(today, datetime.max.time())

        key_by_id: dict[int, str] = {}
        for w in tiles:
            if w.number:
                key_by_id[w.id] = str(w.number)
            else:
                key_by_id[w.id] = str(w.id)

        id_by_key = {v: k for k, v in key_by_id.items()}

        events = (
            db.query(Event)
            .filter(
                Event.event_time >= start_range,
                Event.event_time <= end_range,
                Event.well.in_(list(id_by_key.keys())),
            )
            .all()
        )

        from collections import defaultdict as _dd

        stats_by_well = _dd(
            lambda: {
                # сегодня
                "today_total": 0,
                "today_reagent_count": 0,
                "today_pressure_count": 0,
                "today_reagent_qty": 0.0,
                "today_reagent_types": set(),

                "today_purge_count": 0,

                # вчера
                "yesterday_total": 0,
                "yesterday_reagent_count": 0,
                "yesterday_pressure_count": 0,
                "yesterday_reagent_qty": 0.0,
                "yesterday_reagent_types": set(),
                "yesterday_purge_count": 0,
            }
        )

        for ev in events:
            if not ev.event_time:
                continue

            ev_date = ev.event_time.date()
            well_id = id_by_key.get(ev.well)
            if not well_id:
                continue

            bucket = stats_by_well[well_id]

            # ==== ДОБАВЛЯЕМ ИНИЦИАЛИЗАЦИЮ ГЛОБАЛЬНЫХ СЧЁТЧИКОВ ====
            if "total" not in bucket:
                bucket["total"] = 0
            if "reagent_count" not in bucket:
                bucket["reagent_count"] = 0
            if "reagent_qty" not in bucket:
                bucket["reagent_qty"] = 0.0
            if "reagent_types" not in bucket:
                bucket["reagent_types"] = set()
            if "pressure_count" not in bucket:
                bucket["pressure_count"] = 0
            # =======================================================

            et = (ev.event_type or "other").lower()

            # определяем, за какой день считаем
            if ev_date == today:
                day = "today"
            elif ev_date == yesterday:
                day = "yesterday"
            else:
                # на всякий случай, но по идее сюда не попадаем
                continue

            # общий счётчик событий за день
            bucket[f"{day}_total"] += 1

            if et == "reagent":
                bucket[f"{day}_reagent_count"] += 1
                if ev.qty is not None:
                    bucket[f"{day}_reagent_qty"] += float(ev.qty)
                if ev.reagent:
                    bucket[f"{day}_reagent_types"].add(ev.reagent)

            elif et == "pressure":
                bucket[f"{day}_pressure_count"] += 1
            elif et == "purge":
                bucket[f"{day}_purge_count"] += 1

                continue

            if ev_date != today:
                continue
            if "total" not in bucket:
                bucket["total"] = 0
            bucket["total"] += 1

            et = (ev.event_type or "other").lower()
            if et == "reagent":
                bucket["reagent_count"] += 1
                if ev.qty is not None:
                    bucket["reagent_qty"] += float(ev.qty)
                if ev.reagent:
                    bucket["reagent_types"].add(ev.reagent)
            elif et == "pressure":
                bucket["pressure_count"] += 1

        for w in tiles:
            s = stats_by_well.get(w.id)
            if not s:
                # сегодня
                w.events_today_total = 0
                w.events_today_reagents = 0
                w.events_today_pressure = 0
                w.events_today_reagent_qty = 0.0
                w.events_today_reagent_types = ""
                w.events_today_purges = 0

                # вчера
                w.events_yesterday_total = 0
                w.events_yesterday_reagents = 0
                w.events_yesterday_pressure = 0
                w.events_yesterday_reagent_qty = 0.0
                w.events_yesterday_reagent_types = ""
                w.events_yesterday_purges = 0
            else:
                # сегодня
                w.events_today_total = s["today_total"]
                w.events_today_reagents = s["today_reagent_count"]
                w.events_today_pressure = s["today_pressure_count"]
                w.events_today_reagent_qty = s["today_reagent_qty"]
                w.events_today_reagent_types = ", ".join(sorted(s["today_reagent_types"]))
                w.events_today_purges = s["today_purge_count"]

                # вчера
                w.events_yesterday_total = s["yesterday_total"]
                w.events_yesterday_reagents = s["yesterday_reagent_count"]
                w.events_yesterday_pressure = s["yesterday_pressure_count"]
                w.events_yesterday_reagent_qty = s["yesterday_reagent_qty"]
                w.events_yesterday_reagent_types = ", ".join(sorted(s["yesterday_reagent_types"]))
                w.events_yesterday_purges = s["yesterday_purge_count"]
    else:
        tiles = []

    # ----- C) ДАННЫЕ ДЛЯ КАРТЫ -----
    wells_for_map = [w for w in all_wells if w.lat is not None and w.lon is not None]

    if wells_for_map:
        map_center_lat = sum(w.lat for w in wells_for_map) / len(wells_for_map)
        map_center_lon = sum(w.lon for w in wells_for_map) / len(wells_for_map)
    else:
        map_center_lat = None
        map_center_lon = None

    # ----- D) АКТИВНОЕ ОБОРУДОВАНИЕ И КАНАЛЫ СВЯЗИ -----
    if tiles:
        well_ids = [w.id for w in tiles]

        # ===== ШАГ 1: ПОЛУЧЕНИЕ АКТИВНОГО ОБОРУДОВАНИЯ ИЗ Equipment + EquipmentInstallation =====
        from collections import defaultdict as _dd
        eq_by_well = _dd(list)

        # Запрос активных установок оборудования через ORM
        active_installations = (
            db.query(EquipmentInstallation, Equipment)
            .join(Equipment, Equipment.id == EquipmentInstallation.equipment_id)
            .filter(
                EquipmentInstallation.well_id.in_(well_ids),
                EquipmentInstallation.removed_at.is_(None),  # ещё установлено
                Equipment.deleted_at.is_(None)              # оборудование не удалено
            )
            .order_by(EquipmentInstallation.installed_at.desc())
            .all()
        )

        for inst, eq in active_installations:
            # Определяем тип оборудования для конфигурации
            # Приоритет: equipment_type -> name -> serial_number
            type_code = eq.equipment_type or eq.name or eq.serial_number or 'unknown'

            # Получаем конфигурацию (иконка, цвет, label)
            eq_config = get_equipment_config(code=type_code, equipment_type=eq.name)

            # Формируем объект для шаблона (совместимый с текущим форматом)
            eq_obj = {
                'id': eq.id,
                'type_code': type_code,
                'model': eq.name,  # название оборудования
                'serial': eq.serial_number,
                'installation_location': inst.installation_location,  # Устье, НКТ, Шлейф и т.д.
                'installation_date': inst.installed_at,
                'config': eq_config
            }

            eq_by_well[inst.well_id].append(eq_obj)

        # Присваиваем оборудование скважинам
        for w in tiles:
            equipment_list = eq_by_well.get(w.id, [])
            w.equipment_active = equipment_list

        # ===== ШАГ 2: КАНАЛЫ СВЯЗИ ИЗ well_channels =====
        # Получаем активные каналы (ended_at IS NULL)
        ch_rows = (
            db.query(WellChannel)
            .filter(
                WellChannel.well_id.in_(well_ids),
                WellChannel.ended_at.is_(None),  # активный канал
            )
            .all()
        )

        # Если для одной скважины несколько активных каналов - берём самый свежий по started_at
        channel_by_well: dict[int, WellChannel] = {}
        for ch in ch_rows:
            prev = channel_by_well.get(ch.well_id)
            prev_start = prev.started_at if prev and prev.started_at else datetime.min
            cur_start = ch.started_at if ch.started_at else datetime.min
            if (not prev) or (cur_start > prev_start):
                channel_by_well[ch.well_id] = ch

        # ===== ШАГ 3: КАНАЛЫ СВЯЗИ ИЗ LoRa-датчиков (equipment_installation → lora_sensors) =====
        lora_channel_by_well: dict[int, int] = {}
        lora_sensors_by_well: dict[int, list] = {}
        try:
            _lora_rows = db.execute(
                text("""
                    SELECT ei.well_id, ls.csv_channel, ls.csv_column,
                           ls.serial_number, ls.csv_group
                    FROM equipment_installation ei
                    JOIN equipment e ON e.id = ei.equipment_id
                    JOIN lora_sensors ls ON ls.serial_number = e.serial_number
                    WHERE ei.well_id = ANY(:well_ids) AND ei.removed_at IS NULL
                    ORDER BY ei.well_id, ls.csv_column
                """),
                {"well_ids": well_ids},
            ).fetchall()
            for r in _lora_rows:
                wid, ch, col, sn, grp = r[0], r[1], r[2], r[3], r[4]
                logical_ch = (grp - 1) * 5 + ch
                lora_channel_by_well[wid] = logical_ch
                lora_sensors_by_well.setdefault(wid, []).append({
                    "csv_channel": logical_ch, "csv_column": col, "serial_number": sn,
                    "position": "tube" if col == "Ptr" else "line",
                })
        except Exception as e:
            print(f"[visual_page] LoRa channel query error: {e}")

        # Присваиваем каналы скважинам
        # Приоритет: LoRa-канал (из прошивки датчиков) → WellChannel (ручной)
        for w in tiles:
            lora_ch = lora_channel_by_well.get(w.id)
            if lora_ch is not None:
                w.current_channel = lora_ch
                w.lora_sensors_info = lora_sensors_by_well.get(w.id, [])
            else:
                current_ch = channel_by_well.get(w.id)
                w.current_channel = current_ch.channel if current_ch else None
                w.lora_sensors_info = []
    else:
        # Если нет выбранных скважин - инициализируем пустые списки
        for w in tiles:
            w.equipment_active = []
            w.current_channel = None
            w.lora_sensors_info = []

    # ==== Сортировка ПЛИТОК по статусу ====
    status_order = {
        "status-opt": 3,  # Оптимизация
        "status-adapt": 2,  # Адаптация
        "status-watch": 1,  # Наблюдение
        "status-dev": 4,  # Освоение
        "status-idle": 6,  # Простой
        "status-off": 5,  # Не обслуживается
        "status-other": 7,  # Другое
        None: 8,  # Статус не задан
    }

    tiles_sorted = sorted(
        tiles,
        key=lambda w: status_order.get(getattr(w, "current_status_css", None), 99),
    )
    updated_at = datetime.utcnow() + timedelta(hours=5)  # Кунград UTC+5
    is_admin = bool(request.session.get("is_admin", False))

    # ========== ДАВЛЕНИЯ ДЛЯ ПЛИТОК ==========
    # Получаем средние давления для всех плиток
    pressure_stats = {}
    if tiles_sorted:
        well_ids = [w.id for w in tiles_sorted]
        try:
            pressure_stats = get_wells_pressure_stats(db, well_ids, pressure_period)
        except Exception as e:
            print(f"[visual_page] Ошибка получения давлений: {e}")

    # Присваиваем давления скважинам
    # Показываем давления только если на скважине есть активные LoRa-датчики
    for w in tiles_sorted:
        has_sensors = bool(getattr(w, 'lora_sensors_info', None))
        ps = pressure_stats.get(w.id)
        if has_sensors and ps and ps.get("has_data"):
            w.pressure_tube = ps.get("p_tube_avg")
            w.pressure_line = ps.get("p_line_avg")
            w.pressure_diff = ps.get("p_diff_avg")
            w.pressure_updated = ps.get("updated_at")
            w.has_pressure = True
        else:
            w.pressure_tube = None
            w.pressure_line = None
            w.pressure_diff = None
            w.pressure_updated = None
            w.has_pressure = False

    # ========== ДЕБИТ НА КАРТОЧКАХ ==========
    # Средний суточный дебит за сегодня и вчера (Кунград UTC+5)
    _flow_today: dict[int, float | None] = {}
    _flow_yesterday: dict[int, float | None] = {}
    if tiles_sorted:
        try:
            _flow_today, _flow_yesterday = _calc_daily_flow_for_tiles(
                [w.id for w in tiles_sorted]
            )
        except Exception as e:
            print(f"[visual_page] Ошибка расчёта дебита: {e}")

    for w in tiles_sorted:
        w.flow_today = _flow_today.get(w.id)
        w.flow_yesterday = _flow_yesterday.get(w.id)

    # ========== ТАЙМЛАЙН: ЗАВАНТАЖЕННЯ ДАНИХ ==========

    # Завантажуємо дані за останні 3 місяці (90 днів)
    # Фільтрація по періоду буде на клієнті
    date_from_dt = datetime.now() - timedelta(days=90)
    date_from_dt = date_from_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    date_to_dt = datetime.now()
    date_to_dt = date_to_dt.replace(hour=23, minute=59, second=59, microsecond=999999)

    date_from_str = date_from_dt.strftime('%Y-%m-%d')
    date_to_str = date_to_dt.strftime('%Y-%m-%d')

    # Зберігаємо поточний період для UI
    current_period = tl_period if tl_period else '3d'
    current_date_from = tl_date_from if tl_date_from else (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
    current_date_to = tl_date_to if tl_date_to else datetime.now().strftime('%Y-%m-%d')

    timeline_filters = {
        'period': current_period,
        'date_from': current_date_from,
        'date_to': current_date_to,
    }

    # Отримуємо ВСІ свердловини для легенди
    all_wells_dict = {str(w.number): w for w in all_wells if w.number}

    # Визначаємо які свердловини показувати
    wells_to_show = []

    if tl_wells:
        # Якщо вибрано конкретні свердловини - показуємо їх
        wells_to_show = tl_wells
    elif tl_statuses:
        # Якщо вибрано статуси - знаходимо свердловини з цими статусами
        status_wells = (
            db.query(Well)
            .join(WellStatus, Well.id == WellStatus.well_id)
            .filter(
                WellStatus.dt_end.is_(None),
                WellStatus.status.in_(tl_statuses)
            )
            .all()
        )
        wells_to_show = [str(w.number) for w in status_wells if w.number]
    else:
        # Якщо нічого не вибрано - показуємо ВСІ свердловини
        wells_to_show = [str(w.number) for w in all_wells if w.number]

    # Запит подій
    events_query = db.query(Event).filter(
        Event.event_time >= date_from_dt,
        Event.event_time <= date_to_dt
    )

    # Фільтр по свердловинах
    if wells_to_show:
        events_query = events_query.filter(Event.well.in_(wells_to_show))

    # Фільтр по типах подій (якщо вибрано)
    if tl_event_types:
        events_query = events_query.filter(Event.event_type.in_(tl_event_types))

    # Сортування
    if tl_sort == 'asc':
        timeline_events_raw = events_query.order_by(Event.event_time.asc()).all()
    else:
        timeline_events_raw = events_query.order_by(Event.event_time.desc()).all()

    # Підготовка подій для JS
    timeline_events = []
    timeline_injections = []

    # ОПТИМІЗАЦІЯ: Завантажуємо всіх users одним запитом
    all_user_ids = set(evt.user_id for evt in timeline_events_raw if evt.user_id)
    users_dict = {}
    if all_user_ids:
        users_from_db = db.query(User).filter(User.id.in_(all_user_ids)).all()
        for user in users_from_db:
            users_dict[user.id] = user.username or user.full_name or f"User {user.id}"

    for evt in timeline_events_raw:
        if not evt.event_time:
            continue

        # Отримуємо username з кешу
        username = users_dict.get(evt.user_id) if evt.user_id else None

        # Базова подія
        event_data = {
            't': evt.event_time.isoformat(),
            'well': str(evt.well) if evt.well else '',
            'type': evt.event_type or 'other',
            'description': evt.description or '',
            'p_tube': float(evt.p_tube) if evt.p_tube is not None else None,
            'p_line': float(evt.p_line) if evt.p_line is not None else None,
            'user_id': evt.user_id,
            'username': username,
            'geo_status': evt.geo_status or 'Не указан',
            'purge_phase': evt.purge_phase,
        }

        # Якщо подія - вброс реагента
        if evt.event_type == 'reagent' and evt.reagent:
            # Фільтр по реагентах (якщо вибрано)
            if tl_reagents and evt.reagent not in tl_reagents:
                continue

            timeline_injections.append({
                't': evt.event_time.isoformat(),
                'well': str(evt.well) if evt.well else '',
                'reagent': evt.reagent,
                'qty': float(evt.qty) if evt.qty else 1.0,
                'description': evt.description or '',
                'user_id': evt.user_id,
                'username': username,
                'geo_status': evt.geo_status or 'Не указан',
            })
        else:
            timeline_events.append(event_data)

    # Збираємо унікальні значення для фільтрів
    all_reagents = set(inj['reagent'] for inj in timeline_injections)
    all_event_types_in_data = set(
        evt.event_type for evt in timeline_events_raw if evt.event_type and evt.event_type != 'reagent')

    # Кольори для реагентів
    reagent_colors_base = {
        'Пенний реагент': '#ff6b6b',
        'Інгібітор': '#4ecdc4',
        'Surfactant': '#95e1d3',
        'Foamer': '#f38181',
        'ПАР': '#aa96da',
        'Деемульгатор': '#fcbad3',
    }

    color_palette = [
        '#ff6b6b', '#4ecdc4', '#95e1d3', '#f38181',
        '#aa96da', '#fcbad3', '#ffffd2', '#a8e6cf',
        '#ffd3b6', '#ffaaa5', '#ff8b94', '#c7ceea'
    ]

    timeline_reagent_colors = {}
    for idx, reagent in enumerate(sorted(all_reagents)):
        if reagent in reagent_colors_base:
            timeline_reagent_colors[reagent] = reagent_colors_base[reagent]
        else:
            timeline_reagent_colors[reagent] = color_palette[idx % len(color_palette)]

    # Кольори для подій
    timeline_event_colors = {
        'equip': '#f39c12',
        'pressure': '#3498db',
        'reagent': '#9b59b6',
        'purge': '#e74c3c',
        'production': '#27ae60',
        'maintenance': '#e67e22',
        'other': '#34495e',
    }

    # Переклад типів подій на російську
    event_type_translations = {
        'purge': 'Продувка',
        'reagent': 'Вброс реагента',
        'pressure': 'Замер давления',
        'equip': 'Оборудование',
        'production': 'Добыча',
        'maintenance': 'Обслуживание',
        'other': 'Другое',
    }

    # Списки для фільтрів
    timeline_all_event_types = [
        {'code': et, 'label': event_type_translations.get(et, et)}
        for et in sorted(all_event_types_in_data)
    ]

    timeline_all_reagents = sorted(all_reagents)

    # Список всіх статусів
    timeline_all_statuses = [
        {'code': 'Наблюдение', 'label': 'Наблюдение'},
        {'code': 'Адаптация', 'label': 'Адаптация'},
        {'code': 'Оптимизация', 'label': 'Оптимизация'},
        {'code': 'Освоение', 'label': 'Освоение'},
        {'code': 'Не обслуживается', 'label': 'Не обслуживается'},
        {'code': 'Простой', 'label': 'Простой'},
        {'code': 'Другое', 'label': 'Другое'},
    ]

    # Словник статусів свердловин для JS
    timeline_well_statuses = {}
    for w in all_wells:
        if w.number:
            well_key = str(w.number)
            st = by_well_id.get(w.id)
            if st:
                timeline_well_statuses[well_key] = st.status
            else:
                timeline_well_statuses[well_key] = None

    # Кольори статусів (з style.css CSS variables)
    timeline_status_colors = {
        'Наблюдение': '#007bff',       # синій (status-watch)
        'Адаптация': '#17a2b8',        # бірюзовий (status-adapt)
        'Оптимизация': '#28a745',      # ЗЕЛЕНИЙ (status-opt)
        'Освоение': '#ffc107',         # жовтий (status-dev)
        'Простой': '#fd7e14',          # оранжевий (status-idle)
        'Не обслуживается': '#6c757d', # сірий (status-off)
        'Другое': '#343a40',           # темно-сірий (status-other)
    }

    # ===== ДНЕВНАЯ СТАТИСТИКА (сегодня + вчера) =====
    from collections import defaultdict as _defaultdict
    from decimal import Decimal as _Decimal

    _today_start = now_naive.replace(hour=0, minute=0, second=0, microsecond=0)
    _yesterday_start = _today_start - timedelta(days=1)

    def _classify_purge(event) -> str:
        """Определить тип продувки по описанию: скважина / штуцер / манометр."""
        desc = ((event.description or "") + " " + (event.purge_phase or "")).lower()
        if "штуцер" in desc or "штуц" in desc:
            return "штуцер"
        if "манометр" in desc or "маном" in desc:
            return "манометр"
        return "скважина"

    def _group_purge_sessions(purge_events: list) -> list[dict]:
        """
        Группируем продувки в сессии.

        Правила:
        - Сортируем по (well, event_time)
        - start открывает сессию, stop закрывает
        - Если между событиями одной скважины > 4ч — новая сессия
        - Если нет явного start/stop — каждое событие = сессия
        """
        MAX_GAP = timedelta(hours=4)
        # Сортируем по скважине, потом по времени
        sorted_evts = sorted(purge_events, key=lambda e: (e.well or "", e.event_time))

        sessions: list[dict] = []
        open_sessions: dict[str, dict] = {}  # well -> current session

        for e in sorted_evts:
            well = e.well or "?"
            phase = (e.purge_phase or "").lower().strip()
            ptype = _classify_purge(e)

            cur = open_sessions.get(well)

            # Закрыть старую сессию если большой разрыв
            if cur and e.event_time - cur["last_time"] > MAX_GAP:
                cur["complete"] = False
                sessions.append(cur)
                cur = None
                open_sessions.pop(well, None)

            if phase == "start" or (not cur and phase in ("", "press")):
                # Новая сессия
                if cur:  # предыдущая не закрыта
                    cur["complete"] = False
                    sessions.append(cur)
                open_sessions[well] = {
                    "well": well,
                    "type": ptype,
                    "start_time": e.event_time,
                    "last_time": e.event_time,
                    "phases": [phase or "?"],
                    "complete": False,
                }
            elif phase == "stop":
                if cur:
                    cur["phases"].append("stop")
                    cur["last_time"] = e.event_time
                    cur["duration_min"] = int((e.event_time - cur["start_time"]).total_seconds() / 60)
                    cur["complete"] = "start" in cur["phases"]
                    sessions.append(cur)
                    open_sessions.pop(well, None)
                else:
                    # stop без start — одиночная сессия
                    sessions.append({
                        "well": well, "type": ptype,
                        "start_time": e.event_time, "last_time": e.event_time,
                        "phases": ["stop"], "complete": False, "duration_min": 0,
                    })
            else:
                # press или пустая фаза — добавить к текущей
                if cur:
                    cur["phases"].append(phase or "?")
                    cur["last_time"] = e.event_time
                else:
                    open_sessions[well] = {
                        "well": well, "type": ptype,
                        "start_time": e.event_time, "last_time": e.event_time,
                        "phases": [phase or "?"], "complete": False,
                    }

        # Закрыть оставшиеся
        for cur in open_sessions.values():
            cur["complete"] = False
            sessions.append(cur)

        # Вычислить длительность для всех
        for s in sessions:
            if "duration_min" not in s:
                s["duration_min"] = int((s["last_time"] - s["start_time"]).total_seconds() / 60)

        return sessions

    def _day_stats(dt_from, dt_to):
        """Собрать статистику событий за период [dt_from, dt_to)."""
        evts = (
            db.query(Event)
            .filter(Event.event_time >= dt_from, Event.event_time < dt_to)
            .all()
        )
        st = {
            "pressure": 0,
            "purge_events": 0,  # сырое кол-во событий продувки
            "reagent": 0,
            "equip": 0,
            "other": 0,
            "total": len(evts),
            "reagent_qty": _Decimal(0),  # штуки
            "reagent_wells": _defaultdict(lambda: _Decimal(0)),
            "reagent_well_details": _defaultdict(lambda: _defaultdict(lambda: _Decimal(0))),  # well -> reagent -> qty
            "reagent_by_name": _defaultdict(lambda: _Decimal(0)),
            "pressure_wells": set(),
        }
        purge_raw = []
        for e in evts:
            et = (e.event_type or "other").lower()
            if et == "pressure":
                st["pressure"] += 1
                st["pressure_wells"].add(e.well or "?")
            elif et == "purge":
                st["purge_events"] += 1
                purge_raw.append(e)
            elif et == "reagent":
                st["reagent"] += 1
                if e.qty:
                    q = _Decimal(str(e.qty))
                    st["reagent_qty"] += q
                    st["reagent_wells"][e.well or "?"] += q
                    st["reagent_well_details"][e.well or "?"][e.reagent or "Реагент"] += q
                    st["reagent_by_name"][e.reagent or "Реагент"] += q
            elif et == "equip":
                st["equip"] += 1
            else:
                st["other"] += 1

        # Группировка продувок
        purge_sessions = _group_purge_sessions(purge_raw)
        # Разбивка по типу
        purge_by_type = _defaultdict(list)
        for s in purge_sessions:
            purge_by_type[s["type"]].append(s)

        st["purge_sessions"] = purge_sessions
        st["purge_count"] = len(purge_sessions)
        st["purge_by_type"] = {
            "скважина": len(purge_by_type.get("скважина", [])),
            "штуцер": len(purge_by_type.get("штуцер", [])),
            "манометр": len(purge_by_type.get("манометр", [])),
        }
        st["purge_wells"] = sorted({s["well"] for s in purge_sessions})
        st["purge_incomplete"] = sum(1 for s in purge_sessions if not s["complete"])
        st["purge_details"] = [
            {
                "well": s["well"],
                "type": s["type"],
                "start": s["start_time"].strftime("%H:%M"),
                "duration": s["duration_min"],
                "phases": "→".join(s["phases"]),
                "complete": s["complete"],
            }
            for s in sorted(purge_sessions, key=lambda x: x["start_time"])
        ]

        # Сводка по статусам скважин, на которых были работы
        active_well_nums = set()
        for e in evts:
            if e.well:
                active_well_nums.add(e.well)
        status_summary = _defaultdict(list)  # status -> [well_num, ...]
        if active_well_nums:
            int_nums = [int(n) for n in active_well_nums if str(n).isdigit()]
            if int_nums:
                from sqlalchemy import func as _sqla_func
                # Подзапрос: последний статус для каждой скважины (dt_end IS NULL = активный)
                rows = (
                    db.query(Well.number, WellStatus.status)
                    .join(WellStatus, WellStatus.well_id == Well.id)
                    .filter(Well.number.in_(int_nums), WellStatus.dt_end.is_(None))
                    .all()
                )
                for wn, ws in rows:
                    status_summary[ws or "Без статуса"].append(wn)
        st["active_wells_count"] = len(active_well_nums)
        st["status_summary"] = dict(sorted(status_summary.items(), key=lambda x: -len(x[1])))

        # Конвертация для шаблона
        st["reagent_qty"] = float(st["reagent_qty"])
        st["reagent_wells"] = dict(sorted(st["reagent_wells"].items(), key=lambda x: -float(x[1])))
        st["reagent_well_details"] = [
            {"well": w, "reagent": r, "qty": float(q)}
            for w, reagents in sorted(st["reagent_well_details"].items())
            for r, q in sorted(reagents.items())
        ]
        st["reagent_by_name"] = dict(sorted(st["reagent_by_name"].items(), key=lambda x: -float(x[1])))
        st["pressure_wells"] = sorted(st["pressure_wells"])
        return st

    daily_stats = {
        "today": _day_stats(_today_start, _today_start + timedelta(days=1)),
        "yesterday": _day_stats(_yesterday_start, _today_start),
        "today_label": _today_start.strftime("%d.%m.%Y"),
        "yesterday_label": _yesterday_start.strftime("%d.%m.%Y"),
    }

    return templates.TemplateResponse(
        "visual.html",
        {
            "request": request,
            "title": "СУРГИЛ · Оптимизация работы газовых скважин",
            "all_wells": all_wells,
            "wells": tiles_sorted,
            "selected_ids": selected,
            "wells_for_map": wells_for_map,
            "map_center_lat": map_center_lat,
            "map_center_lon": map_center_lon,
            "status_groups": status_groups_for_sidebar(),
            "status_config": STATUS_LIST,
            "substatus_config": SUBSTATUS_CONFIG,
            "equipment_types": EQUIPMENT_LIST,
            # Передаем ваш конфиг в шаблон
            "equipment_by_code": EQUIPMENT_TYPES,  # из equipment_config.py
            "get_equipment_config": get_equipment_config,  # функция для шаблона
            "updated_at": updated_at,
            "current_user": current_user,
            "is_admin": is_admin,

            # ТАЙМЛАЙН - обновленные переменные
            "timeline_filters": timeline_filters,
            "timeline_injections": timeline_injections,
            "timeline_events": timeline_events,
            "timeline_reagent_colors": timeline_reagent_colors,
            "timeline_event_colors": timeline_event_colors,
            "timeline_all_event_types": timeline_all_event_types,
            "timeline_all_reagents": timeline_all_reagents,
            "timeline_all_statuses": timeline_all_statuses,
            "timeline_event_translations": event_type_translations,
            "timeline_well_statuses": timeline_well_statuses,
            "timeline_status_colors": timeline_status_colors,
            "daily_stats": daily_stats,
            # Давления
            "pressure_period": pressure_period,
            # Глобальная лента событий
            "global_recent_events": global_recent_events,
            "event_type_translations": event_type_translations,
        },
    )


# === АДМИН-ПАНЕЛЬ ПОЛЬЗОВАТЕЛЕЙ ===

@app.get("/admin/users", response_class=HTMLResponse)
def admin_users_page(
        request: Request,
        db: Session = Depends(get_db),
        current_admin: str = Depends(get_current_admin),  # защита: только админ
):
    users = (
        db.query(DashboardUser)
        .order_by(DashboardUser.id.asc())
        .all()
    )

    return templates.TemplateResponse(
        "admin_panel.html",
        {
            "request": request,
            "title": "Админ-панель · Пользователи",
            "users": users,
            "current_user": current_admin,
            "is_admin": True,
        },
    )


PAGE_SIZE = 200  # или сколько тебе нужно


@app.get("/admin/logins", response_class=HTMLResponse)
def admin_logins_page(
        request: Request,
        db: Session = Depends(get_db),
        current_admin: str = Depends(get_current_admin),
        user: str | None = Query(None),
        date_from: str | None = Query(None),
        date_to: str | None = Query(None),
        only_active: int | None = Query(None),
):
    """
    Админ-панель: сессии пользователей с фильтрами, сводкой и графиками.
    """

    # --- список пользователей для select в фильтре ---
    filter_users = (
        db.query(DashboardUser)
        .order_by(DashboardUser.username.asc())
        .all()
    )

    # --- разбираем фильтры ---
    current_filter_user = user or ""
    only_active_flag = bool(only_active)

    dt_from = None
    dt_to = None
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            dt_from = None

    if date_to:
        try:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
        except ValueError:
            dt_to = None

    # --- общие условия фильтра (без only_active) ---
    filters_common = []
    if current_filter_user:
        filters_common.append(DashboardUser.username == current_filter_user)
    if dt_from is not None:
        filters_common.append(DashboardLoginLog.login_at >= dt_from)
    if dt_to is not None:
        filters_common.append(DashboardLoginLog.login_at <= dt_to)

    # фильтр для "всех" сессий (к таблице и графикам)
    filters_total = list(filters_common)
    if only_active_flag:
        filters_total.append(DashboardLoginLog.logout_at.is_(None))

    # фильтр для "активных" сессий (сводка)
    filters_active = list(filters_common)
    filters_active.append(DashboardLoginLog.logout_at.is_(None))

    # --- сводка: всего сессий ---
    total_sessions = (
            db.query(func.count("*"))
            .select_from(DashboardLoginLog)
            .join(DashboardUser, DashboardLoginLog.user_id == DashboardUser.id)
            .filter(*filters_total)
            .scalar()
            or 0
    )

    # --- сводка: активных сессий ---
    active_sessions_count = (
            db.query(func.count("*"))
            .select_from(DashboardLoginLog)
            .join(DashboardUser, DashboardLoginLog.user_id == DashboardUser.id)
            .filter(*filters_active)
            .scalar()
            or 0
    )

    # --- сводка: уникальных пользователей ---
    unique_users_count = (
            db.query(func.count(func.distinct(DashboardLoginLog.user_id)))
            .select_from(DashboardLoginLog)
            .join(DashboardUser, DashboardLoginLog.user_id == DashboardUser.id)
            .filter(*filters_total)
            .scalar()
            or 0
    )

    # --- таблица сессий (последние 200) ---
    logs = (
        db.query(DashboardLoginLog, DashboardUser)
        .join(DashboardUser, DashboardLoginLog.user_id == DashboardUser.id)
        .filter(*filters_total)
        .order_by(DashboardLoginLog.login_at.desc())
        .limit(200)
        .all()
    )

    sessions_items: list[dict] = []
    for log, user_obj in logs:
        is_active = log.logout_at is None

        # Берём "конец" сессии: либо logout_at, либо "сейчас"
        end_dt = log.logout_at or _now_db()
        start_dt = log.login_at

        # Приводим оба к naive-формату, чтобы не было конфликта aware/naive
        if start_dt is not None and start_dt.tzinfo is not None:
            start_dt = start_dt.replace(tzinfo=None)
        if end_dt is not None and end_dt.tzinfo is not None:
            end_dt = end_dt.replace(tzinfo=None)

        seconds = int((end_dt - start_dt).total_seconds())

        if seconds < 60:
            duration_human = f"{seconds} сек"
        elif seconds < 3600:
            duration_human = f"{seconds // 60} мин"
        else:
            h = seconds // 3600
            m = (seconds % 3600) // 60
            duration_human = f"{h} ч {m} мин"

        full_name = (
                f"{user_obj.first_name or ''} {user_obj.last_name or ''}".strip()
                or None
        )

        sessions_items.append({
            "id": log.id,
            "username": user_obj.username,
            "full_name": full_name,
            "login_at": log.login_at.strftime("%Y-%m-%d %H:%M") if log.login_at else "—",
            "logout_at": log.logout_at.strftime("%Y-%m-%d %H:%M") if log.logout_at else None,
            "duration_human": duration_human,
            "is_active": is_active,
            "ip_address": getattr(log, "ip_address", None),
            "user_agent": getattr(log, "user_agent", None),
        })

    # --- график: количество сессий по дням ---
    sessions_by_date_rows = (
        db.query(
            func.date(DashboardLoginLog.login_at).label("date"),
            func.count("*").label("count"),
        )
        .select_from(DashboardLoginLog)
        .join(DashboardUser, DashboardLoginLog.user_id == DashboardUser.id)
        .filter(*filters_total)
        .group_by(func.date(DashboardLoginLog.login_at))
        .order_by(func.date(DashboardLoginLog.login_at))
        .all()
    )

    sessions_by_date = [
        {"date": str(row.date), "count": row.count}
        for row in sessions_by_date_rows
    ]

    # --- график: уникальные пользователи по дням ---
    users_by_date_rows = (
        db.query(
            func.date(DashboardLoginLog.login_at).label("date"),
            func.count(func.distinct(DashboardLoginLog.user_id)).label("users"),
        )
        .select_from(DashboardLoginLog)
        .join(DashboardUser, DashboardLoginLog.user_id == DashboardUser.id)
        .filter(*filters_total)
        .group_by(func.date(DashboardLoginLog.login_at))
        .order_by(func.date(DashboardLoginLog.login_at))
        .all()
    )

    users_by_date = [
        {"date": str(row.date), "users": row.users}
        for row in users_by_date_rows
    ]

    return templates.TemplateResponse(
        "admin_logins.html",
        {
            "request": request,
            "title": "Админ-панель · Сессии",

            "total_sessions": total_sessions,
            "active_sessions_count": active_sessions_count,
            "unique_users_count": unique_users_count,

            "filter_users": filter_users,
            "current_filter_user": current_filter_user,
            "date_from": date_from or "",
            "date_to": date_to or "",
            "only_active": only_active_flag,

            "sessions": sessions_items,
            "chart_sessions_by_date": json.dumps(sessions_by_date, ensure_ascii=False),
            "chart_users_by_date": json.dumps(users_by_date, ensure_ascii=False),

            "current_user": current_admin,
            "is_admin": True,
        },
    )


@app.get("/admin/reagents", response_class=HTMLResponse)
def admin_reagents_page(
        request: Request,
        db: Session = Depends(get_db),
        current_user: DashboardUser = Depends(get_reagents_user),
):
    """
    Сторінка обліку реагентів з актуальними залишками
    """
    params = request.query_params

    # Зріз залишків на дату
    as_of_str = params.get("as_of")
    if as_of_str:
        try:
            as_of_date = datetime.strptime(as_of_str, "%Y-%m-%d").date()
        except ValueError:
            as_of_date = date.today()
    else:
        as_of_date = date.today()

    as_of_dt = datetime.combine(as_of_date, time(23, 59, 59))

    # Отримуємо всі реагенти з каталогу
    all_reagents_catalog = (
        db.query(ReagentCatalog)
        .filter(ReagentCatalog.is_active == True)
        .order_by(ReagentCatalog.name)
        .all()
    )

    # Розраховуємо актуальні залишки
    reagents_data = []
    ZERO = Decimal("0")
    total_stock = ZERO
    total_used_today = ZERO

    for reagent in all_reagents_catalog:
        balance_info = ReagentBalanceService.get_current_balance(
            db, reagent.name, as_of_dt
        )

        avg_daily = ReagentBalanceService.get_average_daily_consumption(
            db, reagent.name, 30
        )

        today_start = datetime.combine(date.today(), time(0, 0, 0))
        raw_today = (
            db.query(func.sum(Event.qty))
            .filter(
                Event.reagent == reagent.name,
                Event.event_time >= today_start,
                Event.event_type == "reagent",
            )
            .scalar()
        )

        consumption_today = ZERO if raw_today is None else (
            raw_today if isinstance(raw_today, Decimal) else Decimal(str(raw_today))
        )

        total_used_today += consumption_today

        # Конвертуємо datetime в строку для JSON-сериалізації
        last_inv_date = balance_info.get("last_inventory_date")
        last_inv_date_str = last_inv_date.isoformat() if last_inv_date else None

        reagents_data.append({
            "name": reagent.name,
            "unit": reagent.default_unit,
            "stock": float(balance_info["current_balance"]),
            "avg_daily": avg_daily,
            "consumption_today": float(consumption_today),
            "last_inventory_date": last_inv_date,  # datetime для Jinja2 шаблону
            "last_inventory_date_str": last_inv_date_str,  # строка для JSON
            "calculation_method": balance_info["calculation_method"]
        })

        total_stock += balance_info["current_balance"]

    # Історія поставок
    supplies_all = (
        db.query(ReagentSupply)
        .order_by(ReagentSupply.received_at.desc())
        .all()
    )

    reagent_names = [r["name"] for r in reagents_data]

    wells = db.query(Event.well).filter(
        Event.event_type == 'reagent'
    ).distinct().all()
    wells = [str(w[0]) for w in wells if w[0]]

    # ТОП-30 по залишку
    top = sorted(reagents_data, key=lambda x: float(x.get("stock") or 0), reverse=True)[:30]
    # У функції admin_reagents_page, після створення top:
    stock_units = [r["unit"] for r in top]

    stock_labels = [r["name"] for r in top]
    stock_values = [float(r["stock"] or 0) for r in top]

    # =========================
    # TIMELINE: події з БД
    # =========================

    def _parse_date_ymd(x: str | None) -> date | None:
        if not x:
            return None
        try:
            return datetime.strptime(x, "%Y-%m-%d").date()
        except ValueError:
            return None

    # 🔧 ВИПРАВЛЕНО: використовуємо правильні назви параметрів
    tf_from = _parse_date_ymd(params.get("date_from"))
    tf_to = _parse_date_ymd(params.get("date_to"))
    tf_well = (params.get("well") or "").strip() or None
    tf_event_type = (params.get("event_type") or "").strip().lower() or None
    tf_reagent = (params.get("reagent") or "").strip() or None

    # За замовчуванням: поточний місяць
    if tf_to is None:
        tf_to = date.today()
    if tf_from is None:
        # Перший день поточного місяця
        tf_from = tf_to.replace(day=1)

    dt_from = datetime.combine(tf_from, time(0, 0, 0))
    dt_to = datetime.combine(tf_to, time(23, 59, 59))

    q_ev = (
        db.query(Event)
        .filter(Event.event_time >= dt_from, Event.event_time <= dt_to)
    )

    if tf_well:
        q_ev = q_ev.filter(Event.well == tf_well)
    if tf_event_type:
        q_ev = q_ev.filter(func.lower(Event.event_type) == tf_event_type)
    if tf_reagent:
        q_ev = q_ev.filter(Event.reagent == tf_reagent)

    events_rows = q_ev.order_by(Event.event_time.asc()).all()

    # --- Кольори (детерміновані) ---
    def _stable_color(key: str) -> str:
        s = (key or "x").encode("utf-8")
        h = 0
        for b in s:
            h = (h * 33 + b) % 360
        return f"hsl({h}, 70%, 45%)"

    reagent_colors = {}
    event_colors = {}

    # --- Ділимо на вбросы та інші події ---
    timeline_injections = []
    timeline_events = []

    for ev in events_rows:
        et = (ev.event_type or "other").lower().strip()

        # Колір по реагенту
        if ev.reagent:
            reagent_colors.setdefault(ev.reagent, _stable_color("reag:" + ev.reagent))

        # Колір по типу події
        event_colors.setdefault(et, _stable_color("type:" + et))

        if et == "reagent":
            timeline_injections.append({
                "t": ev.event_time.isoformat() if ev.event_time else None,
                "reagent": ev.reagent,
                "qty": float(ev.qty or 0.0),
                "well": ev.well,
                "description": ev.description,
            })
        else:
            timeline_events.append({
                "t": ev.event_time.isoformat() if ev.event_time else None,
                "type": et,
                "well": ev.well,
                "reagent": ev.reagent,
                "qty": float(ev.qty or 0.0) if ev.qty is not None else None,
                "description": ev.description,
                "p_tube": ev.p_tube,
                "p_line": ev.p_line,
                "purge_phase": ev.purge_phase,
            })

    # 🔧 ДОДАНО: отримуємо список типів подій
    event_types = list(set(ev.event_type.lower() for ev in events_rows if ev.event_type))

    # Отримуємо доступні статуси скважин
    from backend.models.well_status import ALLOWED_STATUS
    well_statuses = list(ALLOWED_STATUS)

    return templates.TemplateResponse(
        "admin_reagents.html",
        {
            "request": request,
            "current_user": current_user,

            # Основні дані
            "reagents": reagents_data,
            "total_reagents": len(reagents_data),
            "total_stock": float(total_stock),
            "total_used_today": float(total_used_today),

            # Для форм
            "supplies": supplies_all,
            "reagent_catalog": all_reagents_catalog,

            # Для фільтрів
            "reagent_names": reagent_names,
            "wells": wells,
            "event_types": event_types,  # 🔧 ДОДАНО
            "well_statuses": well_statuses,  # Статуси скважин для фільтра

            # Дати
            "as_of_date": as_of_date.strftime("%Y-%m-%d"),
            "as_of_date_human": as_of_date.strftime("%d.%m.%Y"),

            # Пусті дані для старих графіків (можна видалити пізніше)
            "by_reagent_labels": [],
            "by_reagent_values": [],
            "by_reagent_table": [],
            "by_well_labels": [],
            "by_well_values": [],
            "by_well_table": [],
            "daily_labels": [],
            "daily_usage": [],
            "mode": "by_reagent",
            "selected_reagent": None,
            "selected_well": None,

            "stock_labels": stock_labels,
            "stock_values": stock_values,
            # У return templates.TemplateResponse додайте:
            "stock_units": stock_units,

            # 🔧 ВИПРАВЛЕНО: передаємо кольори та події
            "reagent_colors": reagent_colors,
            "event_colors": event_colors,
            "timeline_injections": timeline_injections,
            "timeline_events": timeline_events,

            # 🔧 ДОДАНО: фільтри для відображення у формі
            "timeline_filters": {
                "date_from": tf_from.isoformat(),
                "date_to": tf_to.isoformat(),
                "well": tf_well,
                "event_type": tf_event_type,
                "reagent": tf_reagent,
            },
        },
    )


# Добавить в app.py или создать отдельный сервис

def get_or_create_reagent(db: Session, reagent_name: str, unit: str = "шт"):
    """
    Получает реагент из каталога или создает новый.
    Возвращает кортеж: (ReagentCatalog объект, created: bool)
    """
    reagent_name = reagent_name.strip()
    if not reagent_name:
        return None, False

    # Ищем в каталоге
    reagent = db.query(ReagentCatalog).filter(
        ReagentCatalog.name == reagent_name
    ).first()

    if reagent:
        return reagent, False  # Уже существует

    # Создаем новый
    reagent = ReagentCatalog(
        name=reagent_name,
        default_unit=unit,
        is_active=True
    )
    db.add(reagent)
    db.flush()  # Получаем ID

    return reagent, True  # Был создан


# ===== Инвентаризация реагентов =====


from fastapi import Request, Depends
from sqlalchemy.orm import Session
from datetime import datetime


@app.get("/admin/reagents/inventory")
def admin_reagents_inventory_page(
        request: Request,
        db: Session = Depends(get_db),
        current_user: DashboardUser = Depends(get_reagents_user),
        as_of: str | None = Query(None),
):
    # история (последние записи)
    snapshots = (
        db.query(ReagentInventorySnapshot)
        .order_by(ReagentInventorySnapshot.snapshot_at.desc())
        .limit(100)
        .all()
    )

    # каталог (единый список)
    reagent_catalog = (
        db.query(ReagentCatalog)
        .filter(ReagentCatalog.is_active == True)  # noqa: E712
        .order_by(ReagentCatalog.name)
        .all()
    )
    # --- "срез" остатков на дату (как на странице /admin/reagents) ---
    if as_of:
        try:
            as_of_date = datetime.strptime(as_of, "%Y-%m-%d").date()
        except ValueError:
            as_of_date = date.today()
    else:
        as_of_date = date.today()

    as_of_dt = datetime.combine(as_of_date, time(23, 59, 59))
    # ===== ФАКТИЧЕСКИЕ ОСТАТКИ "НА СЕЙЧАС" (по последнему снимку каждого реагента) =====
    # берём последнюю инвентаризацию (snapshot) на каждый реагент
    # ===== Последний snapshot по каждому реагенту (для справки) =====
    subq = (
        db.query(
            ReagentInventorySnapshot.reagent.label("reagent"),
            func.max(ReagentInventorySnapshot.snapshot_at).label("max_dt"),
        )
        .group_by(ReagentInventorySnapshot.reagent)
        .subquery()
    )

    latest_rows = (
        db.query(ReagentInventorySnapshot)
        .join(
            subq,
            (ReagentInventorySnapshot.reagent == subq.c.reagent)
            & (ReagentInventorySnapshot.snapshot_at == subq.c.max_dt),
        )
        .all()
    )

    latest_by_reagent: dict[str, ReagentInventorySnapshot] = {}
    for r in latest_rows:
        key = (r.reagent or "").strip()
        if key:
            latest_by_reagent[key] = r

    unit_by_name = {r.name: (r.default_unit or "шт") for r in reagent_catalog}

    # ===== ЕДИНЫЙ ИСТОЧНИК ОСТАТКОВ: расчёт через ReagentBalanceService =====
    balances = []
    for cat in reagent_catalog:
        name = (cat.name or "").strip()
        if not name:
            continue

        balance_info = ReagentBalanceService.get_current_balance(db, name, as_of_dt)
        calc_qty = balance_info.get("current_balance")

        # Decimal/None -> float
        try:
            calc_qty_f = float(calc_qty or 0)
        except Exception:
            calc_qty_f = 0.0

        snap = latest_by_reagent.get(name)
        snap_qty = float(snap.qty or 0) if snap else 0.0
        snap_at = snap.snapshot_at.isoformat() if (snap and snap.snapshot_at) else None

        lid = balance_info.get("last_inventory_date")
        if isinstance(lid, (datetime, date)):
            lid = lid.isoformat()  # '2026-01-05' или '2026-01-05T12:34:56'
        elif lid is not None:
            lid = str(lid)

        balances.append(
            {
                "reagent": name,
                "qty": calc_qty_f,
                "unit": (cat.default_unit or "шт"),
                "snapshot_qty": snap_qty,
                "snapshot_at": snap_at,
                "diff": calc_qty_f - snap_qty,
                "calculation_method": balance_info.get("calculation_method"),
                "last_inventory_date": lid,  # <-- стало строкой/None
            }
        )

    balances.sort(key=lambda x: float(x.get("qty") or 0.0), reverse=True)

    # данные для графика
    chart_labels = [b["reagent"] for b in balances]
    chart_values = [b["qty"] for b in balances]
    chart_units = [b["unit"] for b in balances]

    return templates.TemplateResponse(
        "admin_reagents_inventory.html",
        {
            "request": request,
            "current_user": current_user,
            "snapshots": snapshots,
            "reagent_catalog": reagent_catalog,

            # фактическое состояние "на сейчас"
            "balances": balances,

            # для графика (если используешь window.inventoryChartData)
            "chart_labels": chart_labels,
            "chart_values": chart_values,
            "chart_units": chart_units,
            "as_of_date": as_of_date.strftime("%Y-%m-%d"),
            "as_of_date_human": as_of_date.strftime("%d.%m.%Y"),
        },
    )


@app.post("/admin/users/{user_id}/toggle-admin")
def admin_toggle_admin(
        user_id: int,
        request: Request,
        db: Session = Depends(get_db),
        current_admin: str = Depends(get_current_admin),
):
    user = db.query(DashboardUser).filter(DashboardUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    # нельзя снять права сам с себя
    if user.username == current_admin:
        raise HTTPException(status_code=400, detail="Нельзя менять свои админские права")

    user.is_admin = not bool(user.is_admin)
    db.commit()

    return RedirectResponse("/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/toggle-active")
def admin_toggle_active(
        user_id: int,
        request: Request,
        db: Session = Depends(get_db),
        current_admin: str = Depends(get_current_admin),
):
    user = db.query(DashboardUser).filter(DashboardUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    # не даём себе отключить самого себя
    if user.username == current_admin:
        raise HTTPException(status_code=400, detail="Нельзя деактивировать самого себя")

    user.is_active = not bool(user.is_active)
    db.commit()

    return RedirectResponse("/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/delete")
def admin_delete_user(
        user_id: int,
        request: Request,
        db: Session = Depends(get_db),
        current_admin: str = Depends(get_current_admin),
):
    user = db.query(DashboardUser).filter(DashboardUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    if user.username == current_admin:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")

    db.delete(user)
    db.commit()

    return RedirectResponse("/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/toggle-reagents")
def admin_toggle_reagents_access(
        user_id: int,
        request: Request,
        db: Session = Depends(get_db),
        current_admin: str = Depends(get_current_admin),
):
    """
    Включает/выключает флаг can_view_reagents для пользователя.
    Доступно только админам.
    """
    user = db.query(DashboardUser).filter(DashboardUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    # Можно и себе дать/забрать доступ — это не критично, в отличие от is_admin.
    user.can_view_reagents = not bool(user.can_view_reagents)
    db.commit()

    return RedirectResponse("/admin/users", status_code=status.HTTP_303_SEE_OTHER)


# В app.py добавьте ПЕРЕД функцией well_page:
@app.get("/well/number/{well_number}")
def redirect_from_number_to_id(
        well_number: int,
        db: Session = Depends(get_db)
):
    """Редирект с номера скважины на её ID"""
    well = db.query(Well).filter(Well.number == well_number).first()
    if not well:
        raise HTTPException(status_code=404, detail=f"Скважина с номером {well_number} не найдена")

    # Редирект на страницу с ID
    return RedirectResponse(f"/well/{well.id}", status_code=302)
@app.get("/well/{well_identifier}", response_class=HTMLResponse)
def well_page(
        well_identifier: str,  # <- Может быть и номером, и ID
        request: Request,
        db: Session = Depends(get_db),
        preset: str = Query("all"),
        start: str | None = Query(None),
        end: str | None = Query(None),
        edit_status: int | None = Query(None, alias="edit_status"),
        edit_equipment_id: int | None = Query(None, alias="edit_eq"),
        edit_channel_id: int | None = Query(None, alias="edit_ch"),
        edit_note: int | None = Query(None, alias="edit_note"),
        current_user: str = Depends(get_current_user),
):
    """
    Страница отдельной скважины:
    - данные скважины
    - события по этой скважине из таблицы events (join с users)
    - фильтр по периоду + статистика
    - история статусов по скважине (грид + редактирование)
    """
    from backend.routers.well_equipment_integration import get_well_equipment, get_available_equipment
    # По умолчанию - текущая неделя с понедельника
    preset = preset or "week"

    # 1) Скважина
    # 1) Скважина
    well = None

    # Сначала пробуем найти по номеру (преобразуем в int)
    try:
        well_number = int(well_identifier)
        well = db.query(Well).filter(Well.number == well_number).first()
    except ValueError:
        # Если не получилось преобразовать в int
        pass

    # Если не нашли по номеру, пробуем как ID
    if not well:
        try:
            well_id_int = int(well_identifier)
            well = db.query(Well).filter(Well.id == well_id_int).first()
        except ValueError:
            well = None

    if not well:
        raise HTTPException(status_code=404, detail="Скважина не найдена")

    # --- Конструкция скважины + интервалы перфорации ---
    well_construction = None
    perforation_intervals = []

    # предполагаем, что в well.number хранится номер скважины (38, 45, 64, ...)
    if well.number:
        well_no = str(well.number).strip()

        cons_row = db.execute(
            text(
                """
                SELECT *
                FROM well_construction
                WHERE well_no = :well_no
                ORDER BY data_as_of DESC, id DESC
                LIMIT 1
                """
            ),
            {"well_no": well_no},
        ).mappings().first()

        if cons_row:
            well_construction = cons_row

            perf_rows = db.execute(
                text(
                    """
                    SELECT interval_index, top_depth_m, bottom_depth_m
                    FROM well_perforation_interval
                    WHERE well_construction_id = :cid
                    ORDER BY interval_index
                    """
                ),
                {"cid": cons_row["id"]},
            ).mappings().all()

            perforation_intervals = list(perf_rows)

    # --- оборудование на этой скважине ---
    equipment_list = (
        db.query(WellEquipment)
        .filter(WellEquipment.well_id == well.id)
        .order_by(WellEquipment.type_code.asc(), WellEquipment.installed_at.desc())
        .all()
    )
    # Текущее оборудование (не демонтировано)

    # ==== ПРАВИЛЬНОЕ ПОЛУЧЕНИЕ ОБОРУДОВАНИЯ СКВАЖИНЫ ====
    # Получаем активные установки оборудования на этой скважине
    active_installations = db.query(
        EquipmentInstallation,
        Equipment
    ).join(
        Equipment, EquipmentInstallation.equipment_id == Equipment.id
    ).filter(
        EquipmentInstallation.well_id == well.id,  # Используем ID найденной скважины
        EquipmentInstallation.removed_at.is_(None)
    ).all()

    # Формируем список текущего оборудования
    current_equipment = []
    for installation, equipment in active_installations:
        current_equipment.append({
            "id": equipment.id,
            "name": equipment.name,
            "type": equipment.equipment_type,
            "serial_number": equipment.serial_number,
            "manufacturer": equipment.manufacturer,
            "model": getattr(equipment, 'model', None),  # Безопасно
            "condition": equipment.condition,
            "installation_date": installation.installed_at,
            "installation_location": getattr(installation, 'installation_location', None),
            "notes": installation.notes,
        })

    # Получаем доступное оборудование (статус = 'available')
    available_equipment_list = db.query(Equipment).filter(
        Equipment.status == 'available',
        Equipment.deleted_at.is_(None)
    ).order_by(Equipment.name).all()

    # Вся история оборудования по скважине (для таблицы истории)
    equipment_history = sorted(
        well.equipment,
        key=lambda e: e.installed_at or datetime.min,
        reverse=True,
    )
    # Группировка истории оборудования по типу для таблицы в шаблоне
    from collections import defaultdict as _ddict

    equipment_by_type = _ddict(list)
    for eq in equipment_history:
        equipment_by_type[eq.type_code].append(eq)
    # --- каналы связи на этой скважине ---

    channel_history = (
        db.query(WellChannel)
        .filter(WellChannel.well_id == well.id)
        .order_by(WellChannel.started_at.desc())
        .all()
    )

    channel_current = None
    for ch in channel_history:
        if ch.ended_at is None:
            channel_current = ch
            break

    # --- Активные LoRa-датчики: берём из реально установленного оборудования ---
    # equipment_installation → equipment.serial_number → lora_sensors (прошивка)
    well_lora_sensors = []
    try:
        _lora_rows = db.execute(
            text("""
                SELECT ls.serial_number, ls.csv_group, ls.csv_channel, ls.csv_column,
                       ls.label, ei.installed_at
                FROM equipment_installation ei
                JOIN equipment e ON e.id = ei.equipment_id
                JOIN lora_sensors ls ON ls.serial_number = e.serial_number
                WHERE ei.well_id = :well_id AND ei.removed_at IS NULL
                ORDER BY ls.csv_column, ls.csv_channel
            """),
            {"well_id": well.id},
        ).fetchall()
        for r in _lora_rows:
            csv_col = r[3]  # 'Ptr' or 'Pshl'
            logical_ch = (r[1] - 1) * 5 + r[2]  # (csv_group-1)*5 + csv_channel
            well_lora_sensors.append({
                "serial_number": r[0],
                "csv_group": r[1],
                "csv_channel": logical_ch,
                "csv_column": csv_col,
                "label": r[4],
                "position": "tube" if csv_col == "Ptr" else "line",
                "installed_at": r[5],
            })
    except Exception:
        db.rollback()  # Откатываем сломанную транзакцию

    # --- История LoRa-датчиков на этой скважине (включая снятые) ---
    well_lora_history = []
    try:
        _lora_hist_rows = db.execute(
            text("""
                SELECT ls.serial_number, ls.csv_group, ls.csv_channel, ls.csv_column,
                       ls.label, ei.installed_at, ei.removed_at, e.status as eq_status
                FROM equipment_installation ei
                JOIN equipment e ON e.id = ei.equipment_id
                JOIN lora_sensors ls ON ls.serial_number = e.serial_number
                WHERE ei.well_id = :well_id AND ei.removed_at IS NOT NULL
                ORDER BY ei.removed_at DESC
            """),
            {"well_id": well.id},
        ).fetchall()
        for r in _lora_hist_rows:
            csv_col = r[3]
            logical_ch = (r[1] - 1) * 5 + r[2]
            well_lora_history.append({
                "serial_number": r[0],
                "csv_group": r[1],
                "csv_channel": logical_ch,
                "csv_column": csv_col,
                "label": r[4],
                "position": "tube" if csv_col == "Ptr" else "line",
                "installed_at": r[5],
                "removed_at": r[6],
                "eq_status": r[7],
            })
    except Exception:
        db.rollback()

    # --- что редактируем сейчас (оборудование / канал) ---
    edit_equipment = None
    if edit_equipment_id is not None:
        edit_equipment = (
            db.query(WellEquipment)
            .filter(
                WellEquipment.id == edit_equipment_id,
                WellEquipment.well_id == well.id,
            )
            .first()
        )

    edit_channel = None
    if edit_channel_id is not None:
        edit_channel = (
            db.query(WellChannel)
            .filter(
                WellChannel.id == edit_channel_id,
                WellChannel.well_id == well.id,
            )
            .first()
        )

    # --- ЗАМЕТКИ ПО СКВАЖИНЕ (для левого/правого окна) ---
    notes = (
        db.query(WellNote)
        .filter(WellNote.well_id == well.id)
        .order_by(WellNote.created_at.desc())
        .all()
    )

    note_edit = None
    if edit_note is not None:
        note_edit = (
            db.query(WellNote)
            .filter(
                WellNote.id == edit_note,
                WellNote.well_id == well.id,
            )
            .first()
        )
    # 2) Ключ скважины в events.well
    if well.number:
        well_key = str(well.number)
    else:
        well_key = str(well.id)

    # ==============================================================================
    # ИЗМЕНЕНИЯ В app.py - ФУНКЦИЯ well_page
    # Заменить секцию определения периода (строки ~1772-1796)
    # ==============================================================================

    # 3) Определяем период по preset / start / end
    now = datetime.now()
    dt_from = None
    dt_to = None

    if preset == "day":
        # Последние сутки
        dt_from = now - timedelta(days=1)
        dt_to = now
    elif preset == "month":
        # Текущий календарный месяц (с 1-го числа)
        today = now.date()
        first_day_of_month = today.replace(day=1)
        dt_from = datetime.combine(first_day_of_month, datetime.min.time())
        dt_to = now
    elif preset == "week":
        # Текущая календарная неделя (с понедельника)
        today = now.date()
        # Находим понедельник текущей недели (weekday: 0=понедельник, 6=воскресенье)
        monday = today - timedelta(days=today.weekday())
        dt_from = datetime.combine(monday, datetime.min.time())
        dt_to = now
    elif preset == "custom":
        # Произвольный период
        if start:
            try:
                dt_from = datetime.strptime(start, "%Y-%m-%d")
            except ValueError:
                dt_from = None
        if end:
            try:
                # включаем весь день "end"
                dt_to = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
            except ValueError:
                dt_to = None
    # preset == "all" -> dt_from/dt_to не задаём (всё время)

    # Эти значения обратно в форму
    if preset == "custom":
        start_date_value = start if start else ""
        end_date_value = end if end else ""
    elif preset == "week" or (preset == "all" and dt_from):
        start_date_value = dt_from.date().isoformat() if dt_from else ""
        end_date_value = dt_to.date().isoformat() if dt_to else ""
    else:
        start_date_value = ""
        end_date_value = ""

    # 4) Запрос событий + full_name
    q = (
        db.query(Event, User.full_name)
        .outerjoin(User, Event.user_id == User.id)
        .filter(Event.well == well_key)
    )
    if dt_from is not None:
        q = q.filter(Event.event_time >= dt_from)
    if dt_to is not None:
        q = q.filter(Event.event_time <= dt_to)

    raw_events = q.order_by(Event.event_time.desc()).all()

    # 5) Статистика
    stats = None
    events_for_template: list[dict] = []

    if raw_events:
        type_labels = {
            "reagent": "Вброс реагента",
            "pressure": "Замер давления",
            "purge": "Продувка скважины",
            "equip": "Установка оборудования",
            "note": "Заметка",
            "other": "Другое",
        }

        geo_labels = {
            "received": "Получено",
            "skipped_by_user": "Пропущено пользователем",
        }

        total_reagent_injections = 0
        total_reagent_qty = 0.0
        total_purges = 0
        total_measurements = 0

        per_reagent = defaultdict(lambda: {"injections": 0, "total_qty": 0.0})
        all_reagent_times: list[datetime] = []

        min_time = None
        max_time = None

        # --- обход всех событий для статистики ---
        for ev, full_name in raw_events:
            et_norm = (ev.event_type or "other").lower()

            # границы периода по фактическим данным
            if ev.event_time:
                if min_time is None or ev.event_time < min_time:
                    min_time = ev.event_time
                if max_time is None or ev.event_time > max_time:
                    max_time = ev.event_time

            if et_norm == "reagent":
                total_reagent_injections += 1
                qty = float(ev.qty or 0.0)
                total_reagent_qty += qty

                key = ev.reagent or "—"
                per_reagent[key]["injections"] += 1
                per_reagent[key]["total_qty"] += qty

                if ev.event_time:
                    all_reagent_times.append(ev.event_time)

            elif et_norm == "purge":
                total_purges += 1

            elif et_norm == "pressure":
                total_measurements += 1

        # --- средний (медианный) интервал между всеми вбросами, ч ---
        global_avg_interval_hours = None
        if len(all_reagent_times) >= 2:
            times_sorted = sorted(all_reagent_times)
            diffs_hours = []
            for i in range(1, len(times_sorted)):
                delta = times_sorted[i] - times_sorted[i - 1]
                diffs_hours.append(delta.total_seconds() / 3600.0)

            diffs_hours.sort()
            n = len(diffs_hours)
            if n % 2 == 1:
                global_avg_interval_hours = diffs_hours[n // 2]
            else:
                global_avg_interval_hours = (diffs_hours[n // 2 - 1] + diffs_hours[n // 2]) / 2

        # --- список по реагентам (без интервала, просто qty и количество вбросов) ---
        per_reagent_list = []
        for name, d in per_reagent.items():
            per_reagent_list.append(
                {
                    "reagent": name,
                    "injections": d["injections"],
                    "total_qty": d["total_qty"],
                }
            )

        # Период для вывода (если фильтр не задан — границы по данным)
        period_start = dt_from or min_time
        period_end = dt_to or max_time

        stats = {
            "start": period_start,
            "end": period_end,
            "summary": {
                "total_reagent_injections": total_reagent_injections,
                "total_reagent_qty": total_reagent_qty,
                "total_purges": total_purges,
                "total_measurements": total_measurements,
                "global_avg_interval_hours": global_avg_interval_hours,
            },
            "per_reagent": per_reagent_list,
        }

        # --- готовим события для шаблона ---
        for ev, full_name in raw_events:
            et_norm = (ev.event_type or "other").lower()
            type_label = type_labels.get(et_norm, ev.event_type or "Другое")

            geo_raw = (ev.geo_status or "").strip()
            geo_status_label = geo_labels.get(geo_raw, geo_raw)

            events_for_template.append(
                {
                    "event_time": ev.event_time,
                    "event_type": et_norm,
                    "type_label": type_label,
                    "reagent": ev.reagent,
                    "qty": ev.qty,
                    "p_tube": ev.p_tube,
                    "p_line": ev.p_line,
                    "description": ev.description,
                    "equip_type": ev.equip_type,
                    "equip_points": ev.equip_points,
                    "equip_other": ev.equip_other,
                    "purge_phase": ev.purge_phase,
                    "geo_status": geo_raw,
                    "geo_status_label": geo_status_label,
                    "user_full_name": full_name,
                }
            )
    else:
        stats = None
        events_for_template = []

    # --- ГРАФИК СОБЫТИЙ ДЛЯ СКВАЖИНЫ ---
    def _stable_color(key: str) -> str:
        s = (key or "x").encode("utf-8")
        h = 0
        for b in s:
            h = (h * 33 + b) % 360
        return f"hsl({h}, 70%, 45%)"

    reagent_colors = {}
    event_colors = {}
    timeline_injections = []
    timeline_events = []

    for ev, full_name in raw_events:
        et = (ev.event_type or "other").lower().strip()

        if ev.reagent:
            reagent_colors.setdefault(ev.reagent, _stable_color("reag:" + ev.reagent))

        event_colors.setdefault(et, _stable_color("type:" + et))

        if et == "reagent":
            timeline_injections.append({
                "t": ev.event_time.isoformat() if ev.event_time else None,
                "reagent": ev.reagent,
                "qty": float(ev.qty or 0.0),
                "well": ev.well,
                "description": ev.description,
                "operator": full_name,
                "geo_status": ev.geo_status,
            })
        else:
            timeline_events.append({
                "t": ev.event_time.isoformat() if ev.event_time else None,
                "type": et,
                "well": ev.well,
                "reagent": ev.reagent,
                "qty": float(ev.qty or 0.0) if ev.qty is not None else None,
                "description": ev.description,
                "p_tube": ev.p_tube,
                "p_line": ev.p_line,
                "operator": full_name,
                "geo_status": ev.geo_status,
                "purge_phase": ev.purge_phase,
            })

    # --- История статусов для этой скважины ---
    raw_statuses = (
        db.query(WellStatus)
        .filter(WellStatus.well_id == well.id)
        .order_by(WellStatus.dt_start.asc())
        .all()
    )

    status_history: list[dict] = []
    current_status_label: str | None = None
    edit_status_obj: dict | None = None

    for st in raw_statuses:
        days = st.duration_days()

        item = {
            "id": st.id,
            "label": st.status,  # текст статуса
            "css": css_by_label(st.status),
            "start": st.dt_start,
            "end": st.dt_end,
            "days": round(days, 1),
            "note": st.note,
        }
        status_history.append(item)

        # текущий активный статус
        if st.dt_end is None:
            current_status_label = st.status

        # статус, который хотим отредактировать
        if edit_status is not None and st.id == edit_status:
            edit_status_obj = item

    # --- группировка в колонки для грида ---
    from collections import defaultdict as _ddict

    status_grid: list[dict] = []
    if status_history:
        by_label = _ddict(list)
        for item in status_history:
            by_label[item["label"]].append(item)

        for label, items in by_label.items():
            items_sorted = sorted(
                items,
                key=lambda x: x["start"] or datetime.min,
            )
            status_grid.append(
                {
                    "label": label,
                    "css": css_by_label(label),
                    "items": items_sorted,
                }
            )

    # --- контекст для шаблона по статусам ---
    if status_history:
        well_status_ctx = {
            "current_status": current_status_label or status_history[-1]["label"],
            "history": status_history,
            "grid": status_grid,
            "edit_status": edit_status_obj,
        }
    else:
        well_status_ctx = {
            "current_status": None,
            "history": [],
            "grid": [],
            "edit_status": edit_status_obj,
        }
    is_admin = bool(request.session.get("is_admin", False))

    # --- Последние давления скважины (из PostgreSQL pressure_latest) ---
    pressure_latest = None
    pressure_sensors = {}  # {'tube': 'U2401-0004', 'line': 'U2401-0005'}
    try:
        pl_row = db.execute(
            text("""
                SELECT well_id, measured_at, p_tube, p_line, updated_at
                FROM pressure_latest
                WHERE well_id = :well_id
            """),
            {"well_id": well.id},
        ).mappings().first()
        if pl_row:
            pressure_latest = dict(pl_row)
            # Safety: treat 0.0 as None (sensor artifact, not real pressure)
            if pressure_latest.get("p_tube") == 0.0:
                pressure_latest["p_tube"] = None
            if pressure_latest.get("p_line") == 0.0:
                pressure_latest["p_line"] = None
            # Добавляем время Кунграда (UTC+5)
            if pressure_latest.get("measured_at"):
                pressure_latest["measured_at_local"] = (
                    pressure_latest["measured_at"] + timedelta(hours=5)
                )

        # Серийники датчиков — берём из well_lora_sensors (уже загружены выше)
        for s in well_lora_sensors:
            pressure_sensors[s["position"]] = s["serial_number"]
    except Exception:
        db.rollback()

    # Список всех скважин (для модалки перевода оборудования)
    all_wells = db.query(Well).order_by(Well.number).all()

    return templates.TemplateResponse(
        "well.html",
        {
            "request": request,
            "title": f"Скважина {well.number or well.id}",
            "well": well,
            "all_wells": all_wells,
            "events": events_for_template,
            "stats": stats,
            "preset": preset,
            "start_date_value": start_date_value,
            "end_date_value": end_date_value,
            "well_status": well_status_ctx,
            "allowed_statuses": allowed_labels(),
            # Заметки
            "notes": notes,
            "note_edit": note_edit,

            # Оборудование - ОБНОВЛЕНО
            "current_equipment": current_equipment,  # Теперь данные из БД
            "available_equipment": available_equipment_list,  # НОВЫЙ параметр!
            "equipment_history": equipment_history,
            "equipment_by_type": dict(equipment_by_type),
            "equipment_list": equipment_list,
            "equipment_types": EQUIPMENT_LIST,
            "equipment_by_code": EQUIPMENT_BY_CODE,
            "available_equipment": available_equipment_list,

            # Каналы связи
            "channel_current": channel_current,
            "well_lora_sensors": well_lora_sensors,
            "well_lora_history": well_lora_history,
            "channel_history": channel_history,
            "edit_equipment": edit_equipment,
            "edit_channel": edit_channel,
            # Конструкция и интервалы перфорации
            "well_construction": well_construction,
            "perforation_intervals": perforation_intervals,
            "current_user": current_user,
            "is_admin": is_admin,

            "reagent_colors": reagent_colors,
            "event_colors": event_colors,
            "timeline_injections": timeline_injections,
            "timeline_events": timeline_events,
            "well_number": str(well.number) if well.number else str(well.id),

            "period_info": {
                "from": dt_from.strftime("%d.%m.%Y %H:%M") if dt_from else "Начало",
                "to": dt_to.strftime("%d.%m.%Y %H:%M") if dt_to else "Сейчас",
                "preset_label": {
                    "day": "День",
                    "week": "Неделя",
                    "month": "Месяц",
                    "custom": "Период",
                    "all": "Всё время"
                }.get(preset, "Период")
            },
            "pressure_latest": pressure_latest,
            "pressure_sensors": pressure_sensors,
            "now_utc": datetime.utcnow(),
            # Серверное время UTC+5 (Кунград) как naive ISO строка для JS-фильтрации
            "server_now_iso": datetime.now(timezone(timedelta(hours=5))).replace(tzinfo=None).isoformat(),
        },
    )


@app.post("/well/{well_id}/status")
def set_well_status(
        well_id: int,
        status_value: str = Form(..., alias="status"),
        custom_status: str = Form(""),
        status_start: str = Form(""),
        status_end: str = Form(""),
        status_note: str = Form(""),
        db: Session = Depends(get_db),
        current_user: str = Depends(get_current_admin),
):
    """
    Установка нового статуса для скважины.

    - закрывает предыдущий активный статус (dt_end = момент начала нового)
    - создаёт новую запись в well_status с dt_start и (опциональным) dt_end
    """
    # 1) Проверяем, что скважина есть
    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        raise HTTPException(status_code=404, detail="Скважина не найдена")

    # 2) Определяем текст статуса
    status_text = (status_value or "").strip()
    if status_text == "custom":
        status_text = (custom_status or "").strip()

    if not status_text:
        raise HTTPException(status_code=400, detail="Статус не указан")

    # 3) Время начала / конца статуса
    start_dt = _to_naive(_parse_dt_local(status_start) or datetime.now())
    end_dt = _to_naive(_parse_dt_local(status_end) if status_end else None)

    # 4) Закрываем предыдущий активный статус (dt_end = start_dt)
    last_active = (
        db.query(WellStatus)
        .filter(WellStatus.well_id == well.id, WellStatus.dt_end.is_(None))
        .order_by(WellStatus.dt_start.desc())
        .first()
    )
    if last_active:
        last_start = _to_naive(last_active.dt_start)
        # На всякий случай: чтобы не получить отрицательный интервал
        if last_start is not None and last_start >= start_dt:
            start_dt = last_start + timedelta(seconds=1)
        last_active.dt_end = start_dt

    # 5) Создаём новую запись
    new_status = WellStatus(
        well_id=well_id,
        status=status_text,
        dt_start=start_dt,
        dt_end=end_dt,
        note=(status_note or None),
    )
    db.add(new_status)
    db.commit()

    # 6) Возврат на страницу скважины
    return RedirectResponse(
        url=f"/well/{well_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/well/{well_id}/substatus")
def set_well_substatus(
        well_id: int,
        substatus_value: str = Form(..., alias="substatus"),
        custom_substatus: str = Form(""),
        substatus_note: str = Form(""),
        db: Session = Depends(get_db),
        current_user: str = Depends(get_current_admin),
):
    """Установка нового подстатуса (оперативного состояния) скважины."""
    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        raise HTTPException(status_code=404, detail="Скважина не найдена")

    substatus_text = (substatus_value or "").strip()
    if substatus_text == "custom":
        substatus_text = (custom_substatus or "").strip()
    if not substatus_text:
        substatus_text = "В работе"

    start_dt = _to_naive(datetime.now())

    # Закрываем предыдущий активный подстатус
    last_active = (
        db.query(WellSubStatus)
        .filter(WellSubStatus.well_id == well.id, WellSubStatus.dt_end.is_(None))
        .order_by(WellSubStatus.dt_start.desc())
        .first()
    )
    if last_active:
        last_start = _to_naive(last_active.dt_start)
        if last_start is not None and last_start >= start_dt:
            start_dt = last_start + timedelta(seconds=1)
        last_active.dt_end = start_dt

    new_sub = WellSubStatus(
        well_id=well_id,
        sub_status=substatus_text,
        dt_start=start_dt,
        note=(substatus_note or None),
    )
    db.add(new_sub)
    db.commit()

    return {"success": True, "substatus": substatus_text, "color": substatus_color(substatus_text)}


@app.post("/well/{well_id}/status/{status_id}/edit")
def edit_well_status(
        well_id: int,
        status_id: int,
        status_value: str = Form(..., alias="status"),
        custom_status: str = Form(""),
        status_start: str = Form(""),
        status_end: str = Form(""),
        status_note: str = Form(""),
        db: Session = Depends(get_db),
        current_user: str = Depends(get_current_admin),
):
    """
    Редактирование существующей записи статуса.
    Можно поменять название, даты и примечание.
    Другие статусы НЕ трогаем.
    """
    # Проверяем скважину
    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        raise HTTPException(status_code=404, detail="Скважина не найдена")

    st = (
        db.query(WellStatus)
        .filter(WellStatus.id == status_id, WellStatus.well_id == well.id)
        .first()
    )
    if not st:
        raise HTTPException(status_code=404, detail="Статус не найден")

    # Текст статуса
    status_text = (status_value or "").strip()
    if status_text == "custom":
        status_text = (custom_status or "").strip()

    if not status_text:
        raise HTTPException(status_code=400, detail="Статус не указан")

    # Если поле пустое — оставляем старое значение
    new_start = _parse_dt_local(status_start) or st.dt_start
    new_end = _parse_dt_local(status_end) if status_end else None

    st.status = status_text
    st.dt_start = new_start
    st.dt_end = new_end
    st.note = (status_note or None)

    db.commit()

    return RedirectResponse(
        url=f"/well/{well_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/well/{well_id}/status/{status_id}/delete")
def delete_well_status(
        well_id: int,
        status_id: int,
        db: Session = Depends(get_db),
        current_user: str = Depends(get_current_admin),
):
    """
    Удаляет одну запись истории статуса.

    Используем как простой способ "исправить" период:
    удаляем старую запись и задаём новую через форму.
    """
    st = (
        db.query(WellStatus)
        .filter(WellStatus.id == status_id, WellStatus.well_id == well.id)
        .first()
    )
    if not st:
        raise HTTPException(status_code=404, detail="Статус не найден")

    db.delete(st)
    db.commit()

    return RedirectResponse(
        url=f"/well/{well_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/well/{well_id}/update")
def update_well(
        well_id: int,
        lat: str = Form(""),
        lon: str = Form(""),
        description: str = Form(""),
        db: Session = Depends(get_db),
        current_user: str = Depends(get_current_admin),
):
    """
    Обновление координат и описания скважины.

    - Принимает данные из HTML-формы (метод POST)
    - Находит нужную скважину в БД
    - Обновляет lat/lon/description
    - Сохраняет изменения (commit)
    - Делает redirect обратно на /well/{id}
    """

    # 1) Находим скважину в базе
    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        raise HTTPException(status_code=404, detail="Скважина не найдена")

    # 2) Парсим координаты (строка -> float или None)
    well.lat = _parse_coord(lat)
    well.lon = _parse_coord(lon)

    # 3) Описание просто сохраняем как есть (обрежем пробелы по краям)
    desc_clean = (description or "").strip()
    well.description = desc_clean if desc_clean else None

    # 4) Физически записываем изменения в БД
    db.commit()

    # 5) Перенаправляем пользователя обратно на страницу скважины
    return RedirectResponse(
        url=f"/well/{well_id}",
        status_code=status.HTTP_303_SEE_OTHER,  # 303 = "после POST иди по GET"
    )


@app.post("/well/{well_id}/notes/save")
def save_well_note(
        well_id: int,
        note_id_raw: str = Form(""),  # hidden поле note_id (может быть пустым)
        note_time: str = Form(""),  # datetime-local
        note_text: str = Form(""),  # текст заметки
        db: Session = Depends(get_db),
        current_user: str = Depends(get_current_admin),
):
    """
    Добавление / редактирование заметки по скважине.
    - если note_id есть -> редактируем существующую запись
    - если note_id нет -> создаём новую
    """

    # --- аккуратно разбираем note_id как строку ---
    note_id_raw = (note_id_raw or "").strip()
    note_id: int | None
    if note_id_raw:
        try:
            note_id = int(note_id_raw)
        except ValueError:
            note_id = None
    else:
        note_id = None

    # Проверяем, что скважина существует
    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        raise HTTPException(status_code=404, detail="Скважина не найдена")

    # Парсим дату/время наблюдения (note_time -> колонка note_time)
    note_dt = _parse_dt_local(note_time) or datetime.now()
    note_dt = _to_naive(note_dt)

    text_clean = (note_text or "").strip()
    if not text_clean:
        # Пустой текст — просто уходим обратно
        return RedirectResponse(
            url=f"/well/{well_id}#notes-card",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if note_id:
        # --- РЕДАКТИРОВАНИЕ ---
        note = (
            db.query(WellNote)
            .filter(WellNote.id == note_id, WellNote.well_id == well.id)
            .first()
        )
        if not note:
            raise HTTPException(status_code=404, detail="Заметка не найдена")

        note.note_time = note_dt  # <-- ВАЖНО: обновляем note_time
        note.text = text_clean
    else:
        # --- СОЗДАНИЕ НОВОЙ ---
        note = WellNote(
            well_id=well_id,
            note_time=note_dt,  # <-- ВАЖНО: записываем note_time
            text=text_clean,
        )
        db.add(note)

    db.commit()

    return RedirectResponse(
        url=f"/well/{well_id}#notes-card",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/well/{well_id}/notes/{note_id}/delete")
def delete_well_note(
        well_id: int,
        note_id: int,
        db: Session = Depends(get_db),
        current_user: str = Depends(get_current_admin),
):
    """
    Удаление одной заметки по скважине.
    """
    note = (
        db.query(WellNote)
        .filter(WellNote.id == note_id, WellNote.well_id == well.id)
        .first()
    )
    if not note:
        raise HTTPException(status_code=404, detail="Заметка не найдена")

    db.delete(note)
    db.commit()

    return RedirectResponse(
        url=f"/well/{well_id}#notes-card",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/well/{well_id}/equipment/add")
def add_well_equipment(
        well_id: int,
        type_code: str = Form(...),
        serial_number: str = Form(""),
        installed_at: str = Form(""),
        removed_at: str = Form(""),
        note: str = Form(""),
        db: Session = Depends(get_db),
        current_user: str = Depends(get_current_admin),
):
    # Проверяем, что скважина существует
    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        raise HTTPException(status_code=404, detail="Скважина не найдена")

    # Время установки: если поле пустое — берем "сейчас"
    inst_dt = _parse_dt_local(installed_at) or datetime.now()
    inst_dt = _to_naive(inst_dt)

    # Время демонтажа (опционально)
    rem_dt = _parse_dt_local(removed_at) if removed_at else None
    rem_dt = _to_naive(rem_dt) if rem_dt else None

    # Создаём запись оборудования
    eq = WellEquipment(
        well_id=well_id,
        type_code=type_code,
        serial_number=(serial_number or None),
        channel=None,  # канал связи не используем
        installed_at=inst_dt,
        removed_at=rem_dt,
        note=(note or None),
    )
    db.add(eq)
    db.commit()

    return RedirectResponse(
        url=f"/well/{well_id}#equipment-form",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _fake_events_for_well(well_id: int) -> list[dict]:
    """
    ВРЕМЕННАЯ заглушка для событий Telegram-бота.
    """
    now = datetime.now()

    return [
        {
            "id": 1,
            "well_id": well_id,
            "event_time": (now.replace(microsecond=0)).isoformat(),
            "event_type": "reagent",
            "description": "Ввод реагента 1259, 1 шт, оператор @operator1",
        },
        {
            "id": 2,
            "well_id": well_id,
            "event_time": (now.replace(microsecond=0) - timedelta(hours=3)).isoformat(),
            "event_type": "pressure",
            "description": "Замер давления: Труб.=48.2 атм; Лин.=40.3 атм",
        },
        {
            "id": 3,
            "well_id": well_id,
            "event_time": (now.replace(microsecond=0) - timedelta(days=1)).isoformat(),
            "event_type": "note",
            "description": "Скважина в работе, без замечаний",
        },
    ]


@app.post("/well/{well_id}/equipment/{eq_id}/edit")
def edit_well_equipment(
        well_id: int,
        eq_id: int,
        type_code: str = Form(...),
        serial_number: str = Form(""),
        installed_at: str = Form(""),
        removed_at: str = Form(""),
        note: str = Form(""),
        db: Session = Depends(get_db),
        current_user: str = Depends(get_current_admin),
):
    eq = (
        db.query(WellEquipment)
        .filter(WellEquipment.id == eq_id, WellEquipment.well_id == well.id)
        .first()
    )
    if not eq:
        raise HTTPException(status_code=404, detail="Оборудование не найдено")

    eq.type_code = type_code
    eq.serial_number = (serial_number or None)

    # дата установки: если поле пустое — оставляем старую
    if installed_at and installed_at.strip():
        dt_inst = _parse_dt_local(installed_at) or eq.installed_at
        eq.installed_at = _to_naive(dt_inst)

    # дата демонтажа (может быть None)
    if removed_at and removed_at.strip():
        dt_rem = _parse_dt_local(removed_at)
        eq.removed_at = _to_naive(dt_rem) if dt_rem else None
    else:
        eq.removed_at = None

    eq.note = (note or None)

    db.commit()

    return RedirectResponse(
        url=f"/well/{well_id}#equipment-form",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/well/{well_id}/equipment/{eq_id}/delete")
def delete_well_equipment(
        well_id: int,
        eq_id: int,
        db: Session = Depends(get_db),
        current_user: str = Depends(get_current_admin),
):
    eq = (
        db.query(WellEquipment)
        .filter(WellEquipment.id == eq_id, WellEquipment.well_id == well.id)
        .first()
    )
    if not eq:
        raise HTTPException(status_code=404, detail="Оборудование не найдено")

    db.delete(eq)
    db.commit()

    return RedirectResponse(
        url=f"/well/{well_id}#equipment-form",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/well/{well_id}/channel/add")
def add_well_channel(
        well_id: int,
        channel: int = Form(...),
        dt_start: str = Form(""),
        dt_end: str = Form(""),
        note: str = Form(""),
        db: Session = Depends(get_db),
        current_user: str = Depends(get_current_admin),
):
    well = db.query(Well).filter(Well.id == well_id).first()
    if not well:
        raise HTTPException(status_code=404, detail="Скважина не найдена")

    start_dt = _parse_dt_local(dt_start) or datetime.now()
    end_dt = _parse_dt_local(dt_end) if dt_end else None
    start_dt = _to_naive(start_dt)
    end_dt = _to_naive(end_dt)

    # Закрываем предыдущий активный канал
    last_active = (
        db.query(WellChannel)
        .filter(WellChannel.well_id == well.id, WellChannel.ended_at.is_(None))
        .order_by(WellChannel.started_at.desc())
        .first()
    )
    if last_active:
        if last_active.started_at and last_active.started_at >= start_dt:
            start_dt = last_active.started_at + timedelta(seconds=1)
        last_active.ended_at = start_dt

    ch = WellChannel(
        well_id=well_id,
        channel=channel,
        started_at=start_dt,
        ended_at=end_dt,
        note=(note or None),
    )
    db.add(ch)
    db.commit()

    return RedirectResponse(
        url=f"/well/{well_id}#channel-form",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/well/{well_id}/channel/{channel_id}/edit")
def edit_well_channel(
        well_id: int,
        channel_id: int,
        channel: int = Form(...),
        dt_start: str = Form(""),
        dt_end: str = Form(""),
        note: str = Form(""),
        db: Session = Depends(get_db),
        current_user: str = Depends(get_current_admin),
):
    ch = (
        db.query(WellChannel)
        .filter(WellChannel.id == channel_id, WellChannel.well_id == well_id)
        .first()
    )
    if not ch:
        raise HTTPException(status_code=404, detail="Запись канала не найдена")

    ch.channel = channel
    new_start = _parse_dt_local(dt_start) or ch.started_at
    new_end = _parse_dt_local(dt_end) if dt_end else None

    ch.started_at = _to_naive(new_start) if new_start else None
    ch.ended_at = _to_naive(new_end) if new_end else None
    ch.note = (note or None)

    db.commit()

    return RedirectResponse(
        url=f"/well/{well_id}#channel-form",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/well/{well_id}/channel/{channel_id}/delete")
def delete_well_channel(
        well_id: int,
        channel_id: int,
        db: Session = Depends(get_db),
        current_user: str = Depends(get_current_admin),
):
    ch = (
        db.query(WellChannel)
        .filter(WellChannel.id == channel_id, WellChannel.well_id == well_id)
        .first()
    )
    if not ch:
        raise HTTPException(status_code=404, detail="Запись канала не найдена")

    db.delete(ch)
    db.commit()

    return RedirectResponse(
        url=f"/well/{well_id}#channel-form",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/api/well/{well_id}/events")
def well_events_api(
        well_id: int,
        current_user: str = Depends(get_current_user),
):
    events = _fake_events_for_well(well_id)
    return events


@app.get("/api/well/{well_id}/events.csv")
def well_events_csv(
        well_id: int,
        current_user: str = Depends(get_current_user),
):
    events = _fake_events_for_well(well_id)

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["id", "well_id", "event_time", "event_type", "description"])

    for ev in events:
        writer.writerow([
            ev.get("id", ""),
            ev.get("well_id", ""),
            ev.get("event_time", ""),
            ev.get("event_type", ""),
            ev.get("description", ""),
        ])

    output.seek(0)
    filename = f"well_{well_id}_events.csv"

    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/well/{well_id}/events.xlsx")
def well_events_xlsx(
        well_id: int,
        current_user: str = Depends(get_current_user),
):
    events = _fake_events_for_well(well_id)

    wb = Workbook()
    ws = wb.active
    ws.title = "Events"

    ws.append(["id", "well_id", "event_time", "event_type", "description"])

    for ev in events:
        ws.append([
            ev.get("id", ""),
            ev.get("well_id", ""),
            ev.get("event_time", ""),
            ev.get("event_type", ""),
            ev.get("description", ""),
        ])

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)

    filename = f"well_{well_id}_events.xlsx"

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


from fastapi import UploadFile, File, Form
import pandas as pd
from backend.models.reagents import ReagentSupply
from backend.models.reagent_inventory import ReagentInventorySnapshot


@app.get("/admin/reagents/import")
def admin_reagents_import_page(request: Request):
    return templates.TemplateResponse(
        "admin_reagents_import.html",
        {"request": request}
    )


@app.post("/admin/reagents/import")
def admin_reagents_import(
        request: Request,
        file: UploadFile = File(...)
        , db: Session = Depends(get_db)
):
    # Читаем Excel в DataFrame
    df = pd.read_excel(file.file)

    required_cols = {"record_type", "date", "reagent", "qty"}
    missing = required_cols - set(df.columns)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"В файле отсутствуют обязательные колонки: {', '.join(missing)}"
        )

    # Приводим названия к нормальному виду
    df["record_type"] = df["record_type"].str.lower().str.strip()

    for _, row in df.iterrows():
        rec_type = row["record_type"]
        dt = pd.to_datetime(row["date"])
        reagent = str(row["reagent"]).strip()
        qty = float(row["qty"])

        unit = str(row.get("unit") or "шт").strip()
        location = str(row.get("location") or "").strip() or None
        source = str(row.get("source") or "").strip() or None
        comment = str(row.get("comment") or "").strip() or None

        if rec_type == "supply":
            obj = ReagentSupply(
                reagent=reagent,
                qty=qty,
                unit=unit,
                received_at=dt.to_pydatetime(),
                source=source,
                location=location,
                comment=comment
            )
            db.add(obj)

        elif rec_type == "inventory":
            obj = ReagentInventorySnapshot(
                reagent=reagent,
                qty=qty,
                unit=unit,
                snapshot_at=dt.to_pydatetime(),
                location=location,
                comment=comment
            )
            db.add(obj)
        else:
            # Можно логировать/пропускать, можно падать ошибкой
            continue

    db.commit()

    return RedirectResponse("/admin/reagents", status_code=303)


# ==========================
# POST: Добавить инвентаризацию
# ==========================
@app.post("/admin/reagents/inventory/add")
async def admin_reagents_inventory_add(
        request: Request,
        db: Session = Depends(get_db),
        current_user: DashboardUser = Depends(get_reagents_user),
):
    form = await request.form()

    # snapshot_at обязателен
    snapshot_at_str = (form.get("snapshot_at") or "").strip()
    dt = _parse_datetime_local_to_db_naive(snapshot_at_str)
    if dt is None:
        raise HTTPException(status_code=400, detail="Некорректная дата/время snapshot_at")

    # единый разбор реагента
    try:
        reagent_name, reagent_id, unit = _resolve_reagent_from_form(
            db,
            dict(form),
            select_field="reagent",
            new_field="reagent_new",
            unit_field="unit",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # qty
    try:
        qty = float(form.get("qty"))
    except Exception:
        raise HTTPException(status_code=400, detail="Некорректное количество (qty)")

    location = (form.get("location") or "").strip() or None
    comment = (form.get("comment") or "").strip() or None

    snap = ReagentInventorySnapshot(
        reagent=reagent_name,
        reagent_id=reagent_id,
        qty=qty,
        unit=unit,
        snapshot_at=dt,
        location=location,
        comment=comment,
        created_by=(
                getattr(current_user, "username", None)
                or getattr(current_user, "login", None)
                or str(getattr(current_user, "id", "")) or None
        ),
    )
    db.add(snap)
    db.commit()

    return RedirectResponse(url="/admin/reagents/inventory", status_code=303)


# Временно добавьте эту функцию в app.py для диагностики
@app.get("/debug/well/{well_id}/equipment")
def debug_well_equipment(
        well_id: int,
        db: Session = Depends(get_db),
        current_user: str = Depends(get_current_admin)
):
    """Диагностическая страница для проверки оборудования"""

    # 1. Проверим данные в equipment_installation
    installations = db.execute(
        text("""
        SELECT ei.*, e.name, e.serial_number, e.equipment_type 
        FROM equipment_installation ei
        JOIN equipment e ON e.id = ei.equipment_id
        WHERE ei.well_id = :well_id AND ei.removed_at IS NULL
        """),
        {"well_id": well_id}
    ).fetchall()

    # 2. Проверим данные в equipment (старое поле current_location)
    equipment_with_location = db.execute(
        text("""
        SELECT * FROM equipment 
        WHERE current_location LIKE :pattern
        """),
        {"pattern": f"%{well_id}%"}
    ).fetchall()

    return {
        "well_id": well_id,
        "installations": [
            dict(row._mapping) for row in installations
        ],
        "equipment_by_location": [
            dict(row._mapping) for row in equipment_with_location
        ]
    }


@app.get("/debug/wells-sync")
def debug_wells_sync(
        db: Session = Depends(get_db),
        current_user: str = Depends(get_current_admin)
):
    """Диагностика синхронизации скважин из events в wells."""
    from sqlalchemy import distinct

    # 1. Уникальные скважины из events
    event_wells = (
        db.query(distinct(Event.well))
        .filter(Event.well.isnot(None))
        .filter(Event.well != "")
        .all()
    )
    event_wells_list = [r[0] for r in event_wells if r[0]]

    # 2. Все скважины из wells
    wells_db = db.query(Well.id, Well.number, Well.name, Well.lat, Well.lon).all()

    # 3. Запустить синхронизацию
    from backend.services.well_sync_service import sync_wells_from_events
    new_wells = sync_wells_from_events(db)

    return {
        "event_wells_count": len(event_wells_list),
        "event_wells": event_wells_list[:50],  # первые 50
        "db_wells_count": len(wells_db),
        "db_wells": [
            {"id": w.id, "number": w.number, "name": w.name, "lat": w.lat, "lon": w.lon}
            for w in wells_db
        ],
        "newly_created": [
            {"id": w.id, "number": w.number, "name": w.name, "lat": w.lat, "lon": w.lon}
            for w in new_wells
        ]
    }
