# backend/documents/services/notification_service.py
"""
Сервис отправки документов через Telegram и Email.

Функции:
- send_document_telegram: отправка PDF в Telegram
- send_document_email: отправка PDF на Email
- get_send_history: история отправок документа
"""

import httpx
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from backend.settings import settings
from backend.documents.models import Document
from backend.documents.models_notifications import DocumentSendLog


@dataclass
class SendResult:
    """Результат отправки"""
    success: bool
    channel: str
    recipient: str
    error: Optional[str] = None
    response_data: Optional[dict] = None


def _send_telegram_message(text: str, chat_id: str, bot_token: str) -> None:
    """
    Низкоуровневая отправка текстового сообщения в Telegram.

    Выбрасывает исключение при сетевой ошибке или ответе API ok=false.
    Не пишет в БД, не требует Document.
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    with httpx.Client(timeout=15.0) as client:
        response = client.post(url, data={"chat_id": chat_id, "text": text})
    result_data = response.json()
    if not (response.status_code == 200 and result_data.get("ok")):
        desc = result_data.get("description", "unknown error")
        raise RuntimeError(f"Telegram API error: {desc}")


def send_document_telegram(
    db: Session,
    document: Document,
    chat_id: Optional[str] = None,
    *,
    triggered_by: str = "manual",
    triggered_by_user_id: Optional[int] = None,
    comment: Optional[str] = None,
) -> SendResult:
    """
    Отправить PDF документа в Telegram.

    Args:
        db: Сессия БД
        document: Документ для отправки
        chat_id: ID чата (если None — используется TELEGRAM_DEFAULT_CHAT_ID)
        triggered_by: Кто инициировал ('manual', 'auto', 'batch')
        triggered_by_user_id: ID пользователя (если manual)

    Returns:
        SendResult с результатом отправки
    """
    # Проверяем настройки
    bot_token = settings.TELEGRAM_BOT_TOKEN
    if not bot_token:
        return SendResult(
            success=False,
            channel="telegram",
            recipient=chat_id or "",
            error="TELEGRAM_BOT_TOKEN not configured in .env"
        )

    target_chat_id = chat_id or settings.TELEGRAM_DEFAULT_CHAT_ID
    if not target_chat_id:
        return SendResult(
            success=False,
            channel="telegram",
            recipient="",
            error="No chat_id provided and TELEGRAM_DEFAULT_CHAT_ID not set"
        )

    # Проверяем PDF
    if not document.pdf_filename:
        return SendResult(
            success=False,
            channel="telegram",
            recipient=target_chat_id,
            error="PDF not generated for this document"
        )

    pdf_path = Path("backend/static") / document.pdf_filename
    if not pdf_path.exists():
        return SendResult(
            success=False,
            channel="telegram",
            recipient=target_chat_id,
            error=f"PDF file not found: {document.pdf_filename}"
        )

    # Создаём запись в логе
    send_log = DocumentSendLog(
        document_id=document.id,
        channel="telegram",
        recipient=target_chat_id,
        status="pending",
        triggered_by=triggered_by,
        triggered_by_user_id=triggered_by_user_id,
    )
    db.add(send_log)
    db.flush()

    # Формируем caption
    well_info = f"Скв. {document.well.number}" if document.well else ""
    period_info = ""
    if document.period_month and document.period_year:
        period_info = f" за {document.period_month:02d}.{document.period_year}"

    caption = f"📄 {document.doc_type.name_ru if document.doc_type else 'Документ'}\n"
    caption += f"№ {document.doc_number}\n"
    if well_info:
        caption += f"🛢 {well_info}{period_info}\n"
    caption += f"\n📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    if comment:
        caption += f"\n\n💬 {comment}"

    # Отправляем
    try:
        with open(pdf_path, "rb") as pdf_file:
            url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
            files = {
                "document": (pdf_path.name, pdf_file, "application/pdf")
            }
            data = {
                "chat_id": target_chat_id,
                "caption": caption,
            }
            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, data=data, files=files)

        result_data = response.json()

        if response.status_code == 200 and result_data.get("ok"):
            # Успех
            send_log.status = "sent"
            send_log.sent_at = datetime.now()
            resp_data = {
                "message_id": result_data.get("result", {}).get("message_id"),
                "chat_id": target_chat_id,
            }
            if comment:
                resp_data["comment"] = comment
            send_log.response_data = resp_data
            db.commit()

            return SendResult(
                success=True,
                channel="telegram",
                recipient=target_chat_id,
                response_data=resp_data,
            )
        else:
            # Ошибка от Telegram API
            error_msg = result_data.get("description", "Unknown error")
            send_log.status = "failed"
            send_log.error_message = error_msg
            send_log.response_data = result_data
            db.commit()

            return SendResult(
                success=False,
                channel="telegram",
                recipient=target_chat_id,
                error=error_msg,
                response_data=result_data,
            )

    except Exception as e:
        # Исключение
        send_log.status = "failed"
        send_log.error_message = str(e)
        db.commit()

        return SendResult(
            success=False,
            channel="telegram",
            recipient=target_chat_id,
            error=str(e),
        )


def send_document_email(
    db: Session,
    document: Document,
    to_email: str,
    *,
    cc_email: Optional[str] = None,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    triggered_by: str = "manual",
    triggered_by_user_id: Optional[int] = None,
    comment: Optional[str] = None,
) -> SendResult:
    """
    Отправить PDF документа на Email.

    Args:
        db: Сессия БД
        document: Документ для отправки
        to_email: Email получателя
        cc_email: Email для копии (опционально)
        subject: Тема письма (если None — генерируется автоматически)
        body: Тело письма (если None — генерируется автоматически)
        triggered_by: Кто инициировал
        triggered_by_user_id: ID пользователя

    Returns:
        SendResult с результатом отправки
    """
    # Проверяем настройки SMTP с диагностикой
    missing = []
    if not settings.SMTP_HOST:
        missing.append("SMTP_HOST")
    if not settings.SMTP_USER:
        missing.append("SMTP_USER")
    if not settings.SMTP_PASSWORD:
        missing.append("SMTP_PASSWORD")

    if missing:
        return SendResult(
            success=False,
            channel="email",
            recipient=to_email,
            error=f"SMTP not configured. Missing in .env: {', '.join(missing)}"
        )

    # Проверяем PDF
    if not document.pdf_filename:
        return SendResult(
            success=False,
            channel="email",
            recipient=to_email,
            error="PDF not generated for this document"
        )

    pdf_path = Path("backend/static") / document.pdf_filename
    if not pdf_path.exists():
        return SendResult(
            success=False,
            channel="email",
            recipient=to_email,
            error=f"PDF file not found: {document.pdf_filename}"
        )

    # Создаём запись в логе
    send_log = DocumentSendLog(
        document_id=document.id,
        channel="email",
        recipient=to_email,
        status="pending",
        triggered_by=triggered_by,
        triggered_by_user_id=triggered_by_user_id,
    )
    db.add(send_log)
    db.flush()

    # Генерируем тему и тело если не заданы
    well_info = f"Скв. {document.well.number}" if document.well else ""
    period_info = ""
    if document.period_month and document.period_year:
        period_info = f" за {document.period_month:02d}.{document.period_year}"

    doc_type_name = document.doc_type.name_ru if document.doc_type else "Документ"

    if not subject:
        subject = f"{doc_type_name} № {document.doc_number}"
        if well_info:
            subject += f" ({well_info}{period_info})"

    if not body:
        comment_html = f"<p><em>{comment}</em></p>" if comment else ""
        body = f"""
<html>
<body>
<p>Добрый день!</p>
<p>Во вложении направляем: <strong>{doc_type_name}</strong></p>
<ul>
    <li>Номер: {document.doc_number}</li>
    {"<li>Скважина: " + well_info + "</li>" if well_info else ""}
    {"<li>Период: " + period_info.strip() + "</li>" if period_info else ""}
</ul>
{comment_html}
<p>С уважением,<br>Система СУРГИЛ</p>
</body>
</html>
"""

    # Формируем письмо
    msg = MIMEMultipart()
    msg["From"] = settings.EMAIL_FROM or settings.SMTP_USER
    msg["To"] = to_email
    if cc_email:
        msg["Cc"] = cc_email
    msg["Subject"] = subject

    # Добавляем HTML тело
    msg.attach(MIMEText(body, "html", "utf-8"))

    # Добавляем PDF вложение
    with open(pdf_path, "rb") as f:
        pdf_attachment = MIMEApplication(f.read(), _subtype="pdf")
        pdf_attachment.add_header(
            "Content-Disposition",
            "attachment",
            filename=pdf_path.name
        )
        msg.attach(pdf_attachment)

    # Отправляем
    try:
        recipients = [to_email]
        if cc_email:
            recipients.append(cc_email)

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(msg["From"], recipients, msg.as_string())

        # Успех
        send_log.status = "sent"
        send_log.sent_at = datetime.now()
        resp_data = {
            "to": to_email,
            "cc": cc_email,
            "subject": subject,
        }
        if comment:
            resp_data["comment"] = comment
        send_log.response_data = resp_data
        db.commit()

        return SendResult(
            success=True,
            channel="email",
            recipient=to_email,
            response_data=resp_data,
        )

    except Exception as e:
        # Ошибка
        send_log.status = "failed"
        send_log.error_message = str(e)
        db.commit()

        return SendResult(
            success=False,
            channel="email",
            recipient=to_email,
            error=str(e),
        )


def get_send_history(
    db: Session,
    document_id: int,
    channel: Optional[str] = None,
) -> list[DocumentSendLog]:
    """
    Получить историю отправок документа.

    Args:
        db: Сессия БД
        document_id: ID документа
        channel: Фильтр по каналу ('telegram', 'email')

    Returns:
        Список записей DocumentSendLog
    """
    query = db.query(DocumentSendLog).filter(
        DocumentSendLog.document_id == document_id
    )
    if channel:
        query = query.filter(DocumentSendLog.channel == channel)

    return query.order_by(DocumentSendLog.created_at.desc()).all()
