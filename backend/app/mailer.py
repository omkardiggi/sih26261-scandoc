"""Outbound email. Uses SMTP when configured; in development writes .eml files locally.

In production without SMTP, messages are not sent and the caller is told so —
password reset links must then be issued by an operator via the CLI.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import uuid
from email.message import EmailMessage

from .config import get_settings

log = logging.getLogger("app.mailer")


def mail_transport() -> str:
    s = get_settings()
    if s.smtp_host and s.smtp_from:
        return "smtp"
    if s.app_env in {"development", "test"}:
        return "dev_outbox"
    return "unavailable"


def send_mail(to: str, subject: str, body: str) -> bool:
    s = get_settings()
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = s.smtp_from or "no-reply@localhost"
    msg["Subject"] = subject
    msg.set_content(body)
    transport = mail_transport()
    if transport == "smtp":
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
                if s.smtp_starttls:
                    smtp.starttls(context=ssl.create_default_context())
                if s.smtp_username:
                    smtp.login(s.smtp_username, s.smtp_password)
                smtp.send_message(msg)
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("SMTP delivery failed: %s", type(exc).__name__)
            return False
    if transport == "dev_outbox":
        s.mail_dir.mkdir(parents=True, exist_ok=True)
        path = s.mail_dir / f"{uuid.uuid4().hex}.eml"
        path.write_bytes(bytes(msg))
        log.info("Development mail written to local outbox (%s)", path.name)
        return True
    log.warning("Email not sent: SMTP is not configured in production.")
    return False
