from fastapi.templating import Jinja2Templates
from starlette.requests import Request

templates = Jinja2Templates(directory="backend/templates")
import time
templates.env.globals["time"] = lambda: int(time.time())


def base_context(request: Request) -> dict:
    """Build base template context with current_user and is_admin from session."""
    username = request.session.get("username")
    is_admin = request.session.get("is_admin", False)
    can_view_map = request.session.get("can_view_map", False)
    return {
        "request": request,
        "current_user": username,
        "is_admin": is_admin,
        "can_view_map": can_view_map,
    }