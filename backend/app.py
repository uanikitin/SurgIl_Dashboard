
from fastapi import FastAPI, Request, Depends, Form, HTTPException, status, Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
import json
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy.orm import Session
from sqlalchemy import func, case, text

from datetime import datetime, timedelta, timezone

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
from .models.well_notes import WellNote

from backend.services.equipment_loader import EQUIPMENT_LIST, EQUIPMENT_BY_CODE
from .config.status_registry import (
    css_by_label,
    allowed_labels,
    STATUS_LIST,
    status_groups_for_sidebar,
)

from collections import defaultdict, defaultdict as _dd
import io
import csv
from openpyxl import Workbook
import os
from fastapi.staticfiles import StaticFiles
import time
from backend.auth import get_password_hash, get_current_user_optional, verify_password
from backend.models import DashboardUser, DashboardLoginLog

from .db import get_db, SessionLocal


app = FastAPI(title=settings.APP_TITLE)

app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

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
        password = "admin123"   # ЗАДАЙ СВОЙ ПАРОЛЬ
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
    request.session["user"] = user.username       # ← ЭТО главный ключ
    request.session["user_id"] = user.id          # удобно для БД
    request.session["username"] = user.username   # если где-то используется
    request.session["is_admin"] = bool(user.is_admin)
    # ============================================================
    # обновляем поле last_login_at у пользователя
    user.last_login_at = datetime.utcnow()
    db.add(user)
    db.commit()
    # ==== Закрываем предыдущую незавершённую сессию ====
    old_log_id = request.session.get("session_log_id")
    if old_log_id:
        old_log = db.query(DashboardLoginLog).filter_by(id=old_log_id).first()
        if old_log and old_log.logout_at is None:
            old_log.logout_at = datetime.utcnow()
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
            log.logout_at = datetime.utcnow()
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
templates.env.globals['time'] = lambda: int(time.time())  # для обновления CSS

# Чтобы браузер ВСЕГДА брал свежий CSS
version = str(int(time.time()))
# Папка со статикой (css, js, картинки)
# Статика
app.mount(
    "/static",
    StaticFiles(directory="backend/static", html=True),
    name="static"
)

# Подключаем API-роутеры
app.include_router(
    wells.router,
    dependencies=[Depends(get_current_user)]
)


# === Наша первая страница дашборда ===
@app.get("/visual", response_class=HTMLResponse)
def visual_page(
    request: Request,
    db: Session = Depends(get_db),
    selected: list[int] = Query(default=[]),
    current_user: str = Depends(get_current_user),
):
    """
    Главная страница дашборда:
    - слева: список скважин с галочками
    - справа: плитки по выбранным скважинам
    - внизу: карта со всеми скважинами
    """

    # 1) Все скважины для списка слева
    all_wells = (
        db.query(Well)
        .order_by(Well.name.asc().nulls_last(), Well.id.asc())
        .all()
    )

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

                # вчера
                "yesterday_total": 0,
                "yesterday_reagent_count": 0,
                "yesterday_pressure_count": 0,
                "yesterday_reagent_qty": 0.0,
                "yesterday_reagent_types": set(),
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

                # вчера
                w.events_yesterday_total = 0
                w.events_yesterday_reagents = 0
                w.events_yesterday_pressure = 0
                w.events_yesterday_reagent_qty = 0.0
                w.events_yesterday_reagent_types = ""
            else:
                # сегодня
                w.events_today_total = s["today_total"]
                w.events_today_reagents = s["today_reagent_count"]
                w.events_today_pressure = s["today_pressure_count"]
                w.events_today_reagent_qty = s["today_reagent_qty"]
                w.events_today_reagent_types = ", ".join(sorted(s["today_reagent_types"]))

                # вчера
                w.events_yesterday_total = s["yesterday_total"]
                w.events_yesterday_reagents = s["yesterday_reagent_count"]
                w.events_yesterday_pressure = s["yesterday_pressure_count"]
                w.events_yesterday_reagent_qty = s["yesterday_reagent_qty"]
                w.events_yesterday_reagent_types = ", ".join(sorted(s["yesterday_reagent_types"]))
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

    # ----- D) АКТИВНОЕ ОБОРУДОВАНИЕ И КАНАЛ СВЯЗИ НА ПЛИТКАХ -----
    if tiles:
        well_ids = [w.id for w in tiles]

        eq_rows = (
            db.query(WellEquipment)
            .filter(
                WellEquipment.well_id.in_(well_ids),
                WellEquipment.removed_at.is_(None),
            )
            .all()
        )

        from collections import defaultdict as _dd
        eq_by_well = _dd(list)
        for eq in eq_rows:
            eq_by_well[eq.well_id].append(eq)

        ch_rows = (
            db.query(WellChannel)
            .filter(
                WellChannel.well_id.in_(well_ids),
                WellChannel.ended_at.is_(None),
            )
            .all()
        )

        channel_by_well: dict[int, WellChannel] = {}
        for ch in ch_rows:
            prev = channel_by_well.get(ch.well_id)
            prev_start = prev.started_at if prev and prev.started_at else datetime.min
            cur_start = ch.started_at if ch.started_at else datetime.min
            if (not prev) or (cur_start > prev_start):
                channel_by_well[ch.well_id] = ch

        for w in tiles:
            w.equipment_active = eq_by_well.get(w.id, [])
            current_ch = channel_by_well.get(w.id)
            w.current_channel = current_ch.channel if current_ch else None
    else:
        for w in tiles:
            w.equipment_active = []
            w.current_channel = None

    # ==== Сортировка ПЛИТОК по статусу ====
    status_order = {
        "status-opt": 3,      # Оптимизация
        "status-adapt": 2,    # Адаптация
        "status-watch": 1,    # Наблюдение
        "status-dev": 4,      # Освоение
        "status-idle": 6,     # Простой
        "status-off": 5,      # Не обслуживается
        "status-other": 7,    # Другое
        None: 8,              # Статус не задан
    }

    tiles_sorted = sorted(
        tiles,
        key=lambda w: status_order.get(getattr(w, "current_status_css", None), 99),
    )
    updated_at = datetime.now()
    is_admin = bool(request.session.get("is_admin", False))

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
            "equipment_types": EQUIPMENT_LIST,
            "equipment_by_code": EQUIPMENT_BY_CODE,
            "updated_at": updated_at,
            "current_user": current_user,
            "is_admin": is_admin,
        },
    )
# === АДМИН-ПАНЕЛЬ ПОЛЬЗОВАТЕЛЕЙ ===

@app.get("/admin/users", response_class=HTMLResponse)
def admin_users_page(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: str = Depends(get_current_admin),   # защита: только админ
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

        if log.logout_at:
            seconds = int((log.logout_at - log.login_at).total_seconds())
        else:
            now = datetime.now(timezone.utc)  # <<<<<< FIX
            seconds = int((now - log.login_at).total_seconds())

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
@app.get("/well/{well_id}", response_class=HTMLResponse)
def well_page(
    well_id: int,
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

    # 1) Скважина
    well = db.query(Well).filter(Well.id == well_id).first()
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
        .filter(WellEquipment.well_id == well_id)
        .order_by(WellEquipment.type_code.asc(), WellEquipment.installed_at.desc())
        .all()
    )
    # Текущее оборудование (не демонтировано)
    current_equipment = [
        eq for eq in well.equipment
        if eq.removed_at is None
    ]

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
        .filter(WellChannel.well_id == well_id)
        .order_by(WellChannel.started_at.desc())
        .all()
    )

    channel_current = None
    for ch in channel_history:
        if ch.ended_at is None:
            channel_current = ch
            break

    # --- что редактируем сейчас (оборудование / канал) ---
    edit_equipment = None
    if edit_equipment_id is not None:
        edit_equipment = (
            db.query(WellEquipment)
            .filter(
                WellEquipment.id == edit_equipment_id,
                WellEquipment.well_id == well_id,
            )
            .first()
        )

    edit_channel = None
    if edit_channel_id is not None:
        edit_channel = (
            db.query(WellChannel)
            .filter(
                WellChannel.id == edit_channel_id,
                WellChannel.well_id == well_id,
            )
            .first()
        )

    # --- ЗАМЕТКИ ПО СКВАЖИНЕ (для левого/правого окна) ---
    notes = (
        db.query(WellNote)
        .filter(WellNote.well_id == well_id)
        .order_by(WellNote.created_at.desc())
        .all()
    )

    note_edit = None
    if edit_note is not None:
        note_edit = (
            db.query(WellNote)
            .filter(
                WellNote.id == edit_note,
                WellNote.well_id == well_id,
            )
            .first()
        )
    # 2) Ключ скважины в events.well
    if well.number:
        well_key = str(well.number)
    else:
        well_key = str(well.id)

    # 3) Определяем период по preset / start / end
    now = datetime.now()
    dt_from = None
    dt_to = None

    if preset == "day":
        dt_from = now - timedelta(days=1)
        dt_to = now
    elif preset == "month":
        dt_from = now - timedelta(days=30)
        dt_to = now
    elif preset == "custom":
        # start / end приходят как 'YYYY-MM-DD'
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
    start_date_value = start if start else (dt_from.date().isoformat() if dt_from and preset == "custom" else "")
    end_date_value = end if end else (dt_to.date().isoformat() if dt_to and preset == "custom" else "")

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
    return templates.TemplateResponse(
        "well.html",
        {
            "request": request,
            "title": f"Скважина {well.number or well.id}",
            "well": well,
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

            # Оборудование
            "current_equipment": current_equipment,
            "equipment_history": equipment_history,
            "equipment_by_type": dict(equipment_by_type),
            "equipment_list": equipment_list,
            "equipment_types": EQUIPMENT_LIST,        # ← ИСПОЛЬЗУЕМ JSON из equipment.json
            "equipment_by_code": EQUIPMENT_BY_CODE,   # ← dict code -> объект

            # Каналы связи
            "channel_current": channel_current,
            "channel_history": channel_history,
            "edit_equipment": edit_equipment,
            "edit_channel": edit_channel,
            # Конструкция и интервалы перфорации
            "well_construction": well_construction,
            "perforation_intervals": perforation_intervals,
            "current_user": current_user,
            "is_admin": is_admin,
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
        .filter(WellStatus.well_id == well_id, WellStatus.dt_end.is_(None))
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
        .filter(WellStatus.id == status_id, WellStatus.well_id == well_id)
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
        .filter(WellStatus.id == status_id, WellStatus.well_id == well_id)
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
    note_id_raw: str = Form(""),   # hidden поле note_id (может быть пустым)
    note_time: str = Form(""),     # datetime-local
    note_text: str = Form(""),     # текст заметки
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
            .filter(WellNote.id == note_id, WellNote.well_id == well_id)
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
        .filter(WellNote.id == note_id, WellNote.well_id == well_id)
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
        channel=None,          # канал связи не используем
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
        .filter(WellEquipment.id == eq_id, WellEquipment.well_id == well_id)
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
        .filter(WellEquipment.id == eq_id, WellEquipment.well_id == well_id)
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
        .filter(WellChannel.well_id == well_id, WellChannel.ended_at.is_(None))
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

