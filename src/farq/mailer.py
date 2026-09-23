"""Email, rung three of the supplier notification ladder.

Nothing is configured yet, and nothing here invents a provider. It reads SMTP settings from
the environment the same way PaymentsConfig reads Moyasar's keys: without them ``send``
returns ``not_configured`` and the caller carries on, so a missing mail account degrades one
rung of the ladder instead of breaking the notification.

Set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD and MAIL_FROM to turn it on. The
connection is STARTTLS on 587 and implicit TLS on 465, which is what every provider offers.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

log = logging.getLogger("farq.mailer")

TIMEOUT_SECONDS = 10


def settings(env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    return {
        "host": (env.get("SMTP_HOST") or "").strip(),
        "port": int(env.get("SMTP_PORT") or 587),
        "user": (env.get("SMTP_USER") or "").strip(),
        "password": env.get("SMTP_PASSWORD") or "",
        "sender": (env.get("MAIL_FROM") or "no-reply@farq.sa").strip(),
        "sender_name": (env.get("MAIL_FROM_NAME") or "فرق تسعير").strip(),
    }


def configured(env: dict | None = None) -> bool:
    found = settings(env)
    return bool(found["host"] and found["sender"])


def send(to: str, subject: str, body: str, *, url: str | None = None, env: dict | None = None) -> str:
    """Returns 'sent', 'not_configured', or 'failed'. Never raises: a notification that
    could not be emailed is still a notification the supplier has in the app."""
    if not to or "@" not in to:
        return "failed"
    found = settings(env)
    if not (found["host"] and found["sender"]):
        return "not_configured"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((found["sender_name"], found["sender"]))
    message["To"] = to
    text = body if not url else f"{body}\n\n{url}"
    message.set_content(text)

    try:
        context = ssl.create_default_context()
        if found["port"] == 465:
            with smtplib.SMTP_SSL(found["host"], found["port"], timeout=TIMEOUT_SECONDS, context=context) as smtp:
                if found["user"]:
                    smtp.login(found["user"], found["password"])
                smtp.send_message(message)
        else:
            with smtplib.SMTP(found["host"], found["port"], timeout=TIMEOUT_SECONDS) as smtp:
                smtp.starttls(context=context)
                if found["user"]:
                    smtp.login(found["user"], found["password"])
                smtp.send_message(message)
    except Exception as error:  # noqa: BLE001 - one rung of the ladder, not the whole thing
        log.warning("email to %s failed: %s", to.split("@")[-1], type(error).__name__)
        return "failed"
    return "sent"
