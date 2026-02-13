"""
/api/chat/* — Telegram messaging from dashboard.

Allows admin to send text messages to Telegram users (DM) and group chats.
Recipients come from the shared `users` and `chats` tables
(populated by the Telegram bot).
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from backend.db import engine as pg_engine, SessionLocal
from backend.deps import get_current_user
from backend.models.chat_message_log import ChatMessageLog
from backend.settings import settings

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ── Pydantic schemas ──

class SendRequest(BaseModel):
    chat_ids: list[int]
    text: str
    parse_mode: str = "HTML"


class GroupAction(BaseModel):
    action: str  # "add" or "remove"
    chat_id: int
    title: str = ""


class SubgroupCreate(BaseModel):
    name: str
    user_ids: list[int] = []


class SubgroupUpdate(BaseModel):
    name: str | None = None
    user_ids: list[int] | None = None


# ── Endpoints ──

@router.get("/recipients")
def get_recipients(current_user: str = Depends(get_current_user)):
    """List available Telegram users and group chats."""
    bot_configured = bool(settings.TELEGRAM_BOT_TOKEN)

    with pg_engine.connect() as conn:
        user_rows = conn.execute(
            text("SELECT id, username, full_name FROM users ORDER BY full_name")
        ).fetchall()

        group_rows = conn.execute(
            text("SELECT id, title FROM chats ORDER BY title")
        ).fetchall()

        sg_rows = conn.execute(
            text("""
                SELECT sg.id, sg.name, array_agg(m.user_id) AS user_ids
                FROM chat_subgroup sg
                LEFT JOIN chat_subgroup_member m ON m.subgroup_id = sg.id
                GROUP BY sg.id, sg.name
                ORDER BY sg.sort_order, sg.name
            """)
        ).fetchall()

    users = [
        {
            "chat_id": r[0],
            "username": r[1],
            "full_name": r[2] or r[1] or str(r[0]),
            "type": "user",
        }
        for r in user_rows
    ]

    groups = [
        {"chat_id": r[0], "title": r[1] or str(r[0]), "type": "group"}
        for r in group_rows
    ]

    subgroups = [
        {
            "id": r[0],
            "name": r[1],
            "user_ids": [uid for uid in (r[2] or []) if uid is not None],
        }
        for r in sg_rows
    ]

    return {
        "bot_configured": bot_configured,
        "users": users,
        "groups": groups,
        "subgroups": subgroups,
    }


@router.post("/send")
def send_message(body: SendRequest, current_user: str = Depends(get_current_user)):
    """Send a text message to one or more Telegram recipients."""
    bot_token = settings.TELEGRAM_BOT_TOKEN
    if not bot_token:
        raise HTTPException(503, "TELEGRAM_BOT_TOKEN not configured")

    if not body.chat_ids:
        raise HTTPException(400, "chat_ids must be non-empty")

    if len(body.text) > 4096:
        raise HTTPException(400, "Message exceeds Telegram's 4096 character limit")

    if not body.text.strip():
        raise HTTPException(400, "Message text is empty")

    # Resolve display names for logging
    titles = _resolve_chat_titles(body.chat_ids)

    # Resolve sender info
    sender_id, sender_name = _resolve_sender(current_user)

    results = []
    db = SessionLocal()
    try:
        for chat_id in body.chat_ids:
            resp = _telegram_send_message(
                bot_token, chat_id, body.text, body.parse_mode,
            )

            log_entry = ChatMessageLog(
                chat_id=chat_id,
                chat_title=titles.get(chat_id, str(chat_id)),
                message_text=body.text,
                parse_mode=body.parse_mode,
                sent_by_user_id=sender_id,
                sent_by_username=sender_name,
            )

            if resp["ok"]:
                log_entry.status = "sent"
                log_entry.telegram_message_id = resp.get("message_id")
                log_entry.sent_at = datetime.utcnow()
                results.append({
                    "chat_id": chat_id,
                    "status": "sent",
                    "message_id": resp.get("message_id"),
                })
            else:
                log_entry.status = "failed"
                log_entry.error_message = resp.get("error", "Unknown error")
                results.append({
                    "chat_id": chat_id,
                    "status": "failed",
                    "error": resp.get("error"),
                })

            db.add(log_entry)

            # Rate limit safety between messages
            if len(body.chat_ids) > 1:
                time.sleep(0.05)

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return {"results": results}


@router.get("/history")
def get_history(
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: str = Depends(get_current_user),
):
    """Recent sent messages log."""
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT id, chat_id, chat_title, message_text, status,
                       error_message, telegram_message_id,
                       sent_by_username, sent_at, created_at
                FROM chat_message_log
                ORDER BY created_at DESC
                LIMIT :lim OFFSET :off
            """),
            {"lim": limit, "off": offset},
        ).fetchall()

    return [
        {
            "id": r[0],
            "chat_id": r[1],
            "chat_title": r[2],
            "text_preview": (r[3] or "")[:100],
            "status": r[4],
            "error": r[5],
            "message_id": r[6],
            "sent_by": r[7],
            "sent_at": r[8].isoformat() if r[8] else None,
            "created_at": r[9].isoformat() if r[9] else None,
        }
        for r in rows
    ]


@router.post("/groups")
def manage_groups(body: GroupAction, current_user: str = Depends(get_current_user)):
    """Add or remove a group chat."""
    if body.action == "add":
        if body.chat_id >= 0:
            raise HTTPException(400, "Group chat_id must be negative")
        with pg_engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO chats (id, title)
                    VALUES (:cid, :title)
                    ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title
                """),
                {"cid": body.chat_id, "title": body.title},
            )
        return {"status": "ok", "action": "added", "chat_id": body.chat_id}

    elif body.action == "remove":
        with pg_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM chats WHERE id = :cid"),
                {"cid": body.chat_id},
            )
        return {"status": "ok", "action": "removed", "chat_id": body.chat_id}

    raise HTTPException(400, "action must be 'add' or 'remove'")


# ── Subgroup CRUD ──

@router.post("/subgroups")
def create_subgroup(body: SubgroupCreate, current_user: str = Depends(get_current_user)):
    """Create a recipient subgroup."""
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Subgroup name is required")
    try:
        with pg_engine.begin() as conn:
            row = conn.execute(
                text("INSERT INTO chat_subgroup (name) VALUES (:name) RETURNING id"),
                {"name": name},
            ).fetchone()
            sg_id = row[0]
            for uid in body.user_ids:
                conn.execute(
                    text("""INSERT INTO chat_subgroup_member (subgroup_id, user_id)
                            VALUES (:sg, :uid) ON CONFLICT DO NOTHING"""),
                    {"sg": sg_id, "uid": uid},
                )
    except Exception as e:
        if "uq_chat_subgroup_name" in str(e):
            raise HTTPException(409, "Подгруппа с таким именем уже существует")
        raise
    return {"status": "ok", "id": sg_id}


@router.put("/subgroups/{subgroup_id}")
def update_subgroup(
    subgroup_id: int,
    body: SubgroupUpdate,
    current_user: str = Depends(get_current_user),
):
    """Update subgroup name and/or members."""
    with pg_engine.begin() as conn:
        if body.name is not None:
            name = body.name.strip()
            if not name:
                raise HTTPException(400, "Subgroup name is required")
            try:
                conn.execute(
                    text("UPDATE chat_subgroup SET name = :name WHERE id = :id"),
                    {"name": name, "id": subgroup_id},
                )
            except Exception as e:
                if "uq_chat_subgroup_name" in str(e):
                    raise HTTPException(409, "Подгруппа с таким именем уже существует")
                raise
        if body.user_ids is not None:
            conn.execute(
                text("DELETE FROM chat_subgroup_member WHERE subgroup_id = :sg"),
                {"sg": subgroup_id},
            )
            for uid in body.user_ids:
                conn.execute(
                    text("""INSERT INTO chat_subgroup_member (subgroup_id, user_id)
                            VALUES (:sg, :uid) ON CONFLICT DO NOTHING"""),
                    {"sg": subgroup_id, "uid": uid},
                )
    return {"status": "ok"}


@router.delete("/subgroups/{subgroup_id}")
def delete_subgroup(subgroup_id: int, current_user: str = Depends(get_current_user)):
    """Delete a subgroup (CASCADE removes memberships)."""
    with pg_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM chat_subgroup WHERE id = :id"),
            {"id": subgroup_id},
        )
    return {"status": "ok"}


# ── Internal helpers ──

def _telegram_send_message(
    bot_token: str, chat_id: int, text_msg: str, parse_mode: str = "HTML",
) -> dict:
    """Send a single text message via Telegram Bot API."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json={
                "chat_id": chat_id,
                "text": text_msg,
                "parse_mode": parse_mode,
            })
        data = resp.json()
        if data.get("ok"):
            return {"ok": True, "message_id": data["result"]["message_id"]}
        return {"ok": False, "error": data.get("description", "Unknown error")}
    except httpx.TimeoutException:
        return {"ok": False, "error": "Request timed out (15s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _resolve_chat_titles(chat_ids: list[int]) -> dict[int, str]:
    """Resolve display names for a list of chat_ids."""
    titles = {}
    with pg_engine.connect() as conn:
        for cid in chat_ids:
            if cid > 0:
                row = conn.execute(
                    text("SELECT full_name, username FROM users WHERE id = :uid"),
                    {"uid": cid},
                ).fetchone()
                if row:
                    titles[cid] = row[0] or row[1] or str(cid)
            else:
                row = conn.execute(
                    text("SELECT title FROM chats WHERE id = :cid"),
                    {"cid": cid},
                ).fetchone()
                if row:
                    titles[cid] = row[0] or str(cid)
    return titles


def _resolve_sender(username: str) -> tuple[Optional[int], str]:
    """Get dashboard user id and username."""
    with pg_engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM dashboard_users WHERE username = :u"),
            {"u": username},
        ).fetchone()
    return (row[0] if row else None), username
