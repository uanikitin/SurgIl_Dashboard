# backend/auth.py

from typing import Optional, Dict
from fastapi import Request
import hashlib
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session
from .db import get_db
from .models import DashboardUser

def get_password_hash(password: str) -> str:
    """
    Простейший хеш пароля через SHA256.
    ВАЖНО: если у тебя уже есть пользователи с паролями,
    хранимыми в другом виде, нужно будет либо:
      - пересоздать пользователей, либо
      - мигрировать пароли под эту схему.
    """
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Сравнение введённого пароля с хранимым хешем.
    Если где-то будешь использовать – уже готово.
    """
    return get_password_hash(plain_password) == hashed_password


async def get_current_user_optional(request: Request) -> Optional[Dict]:
    """
    Достаём пользователя из сессии, если он есть.
    Возвращает словарь вида {"id": ..., "username": ..., "role": ...}
    или None, если пользователь не залогинен.
    """
    if "user_id" not in request.session:
        return None
    return {
        "id": request.session["user_id"],
        "username": request.session.get("username"),
        "is_admin": request.session.get("is_admin", False)
    }


def hash_password(password: str) -> str:
    """
    Хэширует пароль так же, как мы уже делали раньше
    (sha256 в hex-строку). Важно, чтобы это совпадало
    со старыми пользователями в базе.
    """
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def verify_password(plain_password: str, password_hash: str) -> bool:
    """
    Проверяет введённый пароль против хэша из базы.
    """
    return hash_password(plain_password) == password_hash

def get_reagents_user(
    request: Request,
    db: Session = Depends(get_db),
) -> DashboardUser:
    """
    Пользователь, который имеет доступ к странице реагентов:
    - admin
    - или can_view_reagents = True
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    user = db.query(DashboardUser).filter(DashboardUser.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    if not (user.is_admin or getattr(user, "can_view_reagents", False)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к разделу реагентов")

    return user

