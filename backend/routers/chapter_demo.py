"""
chapter_demo.py — DEV/демо-роутер для проверки универсального каркаса
главы отчёта (components/_chapter_panel.html + chapter_preview.js).

⚠️ ВРЕМЕННЫЙ. Изолированный полигон: проверить layout 60/40 и панель
HTML/PDF, НЕ трогая customer_daily.html. После того как каркас будет
применён к реальной главе — этот роутер удаляется или заменяется.

Содержит:
  GET /dev/chapter-panel        — демо-страница
  GET /api/chapter/preview      — универсальный HTML-превью endpoint
                                  (тонкая обёртка над build_chapter_preview)
"""
from __future__ import annotations

import logging
import time as time_module

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from backend.db import SessionLocal
from backend.deps import get_current_user
from backend.services import customer_daily_service as svc

log = logging.getLogger(__name__)

router = APIRouter(tags=["chapter-demo"])

templates = Jinja2Templates(directory="backend/templates")
templates.env.globals["time"] = lambda: int(time_module.time())


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/dev/chapter-panel", response_class=HTMLResponse)
def chapter_panel_demo_page(
    request: Request,
    well_id: int = 21,
    current_user: str = Depends(get_current_user),
):
    """DEV: демо-страница универсального каркаса главы.

    well_id по умолчанию 21 (есть данные). Меняется через ?well_id=N.
    """
    return templates.TemplateResponse(
        "chapter_panel_demo.html",
        {
            "request": request,
            "current_user": current_user,
            "is_admin": request.session.get("is_admin", False),
            "embedded": False,
            "demo_well_id": well_id,
        },
    )


@router.get("/api/chapter/preview")
def api_universal_chapter_preview(
    well_id: int = Query(...),
    kinds: str | None = Query(None,
        description="CSV-список kind для фильтра по главе; пусто → все блоки"),
    db: Session = Depends(get_db),
):
    """Универсальный JSON-превью главы для правой панели любого раздела.

    Тонкая обёртка над customer_daily_service.build_chapter_preview.
    Не дублирует логику — переиспользует существующую функцию с
    опциональным kinds-фильтром.

    Гарантия: только READ, никакого INSERT/UPDATE/DELETE.
    """
    kinds_list = None
    if kinds:
        kinds_list = [k.strip() for k in kinds.split(",") if k.strip()]
    return svc.build_chapter_preview(db, well_id, kinds=kinds_list)
