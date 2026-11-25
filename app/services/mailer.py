"""Minimal email sender.

Uses SMTP when MAIL_SERVER is configured (any provider: Gmail Workspace,
Zoho, Mailgun, Brevo, Amazon SES ...). Without it, nothing is sent: the
message is kept in an in-memory outbox (used by tests) and the caller shows
the link on screen instead. Fuller notifications arrive in Stage 11.
"""
import logging
import smtplib
import ssl
from email.message import EmailMessage

from flask import current_app

log = logging.getLogger("projectsphere.mail")


def is_configured() -> bool:
    return bool(current_app.config.get("MAIL_SERVER"))


def outbox() -> list[EmailMessage]:
    return current_app.extensions.setdefault("mail_outbox", [])


def send(to: str, subject: str, text_body: str, html_body: str | None = None) -> bool:
    """Returns True only when the message was handed to a mail server."""
    cfg = current_app.config
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.get("MAIL_FROM") or "Laari ProjectSphere <no-reply@example.com>"
    msg["To"] = to
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    if not is_configured():
        outbox().append(msg)
        log.info("Mail server not configured; message '%s' not sent", subject)
        return False
    try:
        port = int(cfg.get("MAIL_PORT", 587))
        if port == 465:
            with smtplib.SMTP_SSL(cfg["MAIL_SERVER"], port, context=ssl.create_default_context(), timeout=15) as s:
                _login_and_send(s, msg)
        else:
            with smtplib.SMTP(cfg["MAIL_SERVER"], port, timeout=15) as s:
                s.starttls(context=ssl.create_default_context())
                _login_and_send(s, msg)
        return True
    except (OSError, smtplib.SMTPException) as exc:
        log.warning("Could not send '%s': %s", subject, exc.__class__.__name__)
        return False


def _login_and_send(server, msg):
    cfg = current_app.config
    if cfg.get("MAIL_USERNAME"):
        server.login(cfg["MAIL_USERNAME"], cfg.get("MAIL_PASSWORD") or "")
    server.send_message(msg)
