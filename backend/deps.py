from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import RedirectResponse
import secrets

# from .config import settings  # ← теперь это работает
from .settings import settings  # ← ДОЛЖНО БЫТЬ
security = HTTPBasic()

# def get_current_user(credentials: HTTPBasicCredentials = Depends(security)):
#     correct_username = secrets.compare_digest(
#         credentials.username, settings.BASIC_AUTH_USERNAME
#     )
#     correct_password = secrets.compare_digest(
#         credentials.password, settings.BASIC_AUTH_PASSWORD
#     )
#
#     if not (correct_username and correct_password):
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Invalid login or password",
#             headers={"WWW-Authenticate": "Basic"},
#         )
#
#     return credentials.username

def get_current_user(request: Request):
    """
    Достаёт пользователя из сессии.
    Если не залогинен — шлём на /login.
    """
    user = request.session.get("user")
    if not user:
        # 303 Redirect на /login
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Not authenticated",
            headers={"Location": "/login"},
        )
    return user
def get_current_admin(
    current_user: str = Depends(get_current_user),
) -> str:
    """
    Доступ только для администратора.
    Админ — это тот, чьё имя совпадает с settings.ADMIN_USERNAME.
    """
    if current_user != settings.ADMIN_USERNAME:
        # 403 — доступ запрещён
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав. Изменять данные может только администратор.",
        )
    return current_user