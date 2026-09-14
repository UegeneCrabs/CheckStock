from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app import db
from app.config import settings

logger = logging.getLogger(__name__)


def _send(recipients: list[str], subject: str, body: str) -> bool:
    recipients = [value.strip() for value in recipients if value and value.strip()]
    sender = settings.smtp_sender or settings.smtp_username
    if not recipients or not settings.smtp_host or not sender:
        return False
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as client:
            if settings.smtp_starttls:
                client.starttls()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
        return True
    except (OSError, smtplib.SMTPException):
        logger.exception("access_notification_failed recipients=%s subject=%s", recipients, subject)
        return False


def notify_request_created(access_request: dict) -> bool:
    target = access_request.get("target_marketplace")
    direction = (
        f"{access_request['source_marketplace']} -> {target}"
        if target
        else str(access_request["source_marketplace"])
    )
    return _send(
        db.list_superadmin_emails(),
        f"CheckStock: запрос доступа от {access_request['user_name']}",
        "\n".join(
            (
                f"Сотрудник: {access_request['user_name']}",
                f"Магазин: {access_request['store_slug']}",
                f"Площадка: {direction}",
                f"Действие: {access_request['permission']}",
                f"Причина: {access_request.get('reason') or 'не указана'}",
                f"Срок запрашиваемого разрешения: {access_request['duration_days']} дн.",
                "Откройте админ-панель CheckStock, чтобы принять решение.",
            )
        ),
    )


def notify_request_decided(access_request: dict) -> bool:
    email = str(access_request.get("user_email") or "").strip()
    if not email:
        return False
    approved = access_request.get("status") == "approved"
    return _send(
        [email],
        "CheckStock: временный доступ одобрен" if approved else "CheckStock: запрос доступа отклонён",
        "\n".join(
            (
                f"Запрос №{access_request['id']}",
                f"Решение: {'одобрено' if approved else 'отклонено'}",
                f"Магазин: {access_request['store_slug']}",
                f"Площадка: {access_request['source_marketplace']}",
                f"Комментарий: {access_request.get('decision_note') or '—'}",
                (
                    f"Доступ действует до {access_request.get('valid_until')}"
                    if approved
                    else "Временный доступ не выдан."
                ),
            )
        ),
    )
