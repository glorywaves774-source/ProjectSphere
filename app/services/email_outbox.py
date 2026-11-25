"""Stage 11: transactional email outbox.

Notifications and billing emails are queued as rows in the same transaction as the change that causes them.
A background job (Celery beat every minute, or `flask send-mail`) sends them through the mailer.
Email holds only a short title and a link; details stay behind sign-in.
"""
from html import escape

from flask import current_app
from sqlalchemy import select

from app.extensions import db
from app.models import EmailOutbox, User, utcnow

from . import mailer

# Notification kinds that also go by email (to people who keep email on in My account)
EMAIL_KINDS = {"approval", "task", "issue", "risk", "risk.review", "me.verify", "me.visit", "me.due", "me.reviewed",
               "meeting", "announcement", "message", "document", "billing"}
MAX_ATTEMPTS = 5


def absolute(link: str) -> str:
    base = current_app.config.get("APP_BASE_URL") or ""
    if link.startswith("http"):
        return link
    return f"{base}{link}" if base else link


def queue(user: User, subject: str, text: str, link: str = "", organization_id=None) -> EmailOutbox | None:
    if not user or not user.email or not user.is_active_user:
        return None
    url = absolute(link) if link else ""
    footer = ("\n\n—\nLaari ProjectSphere. You get these emails because email notifications are on in My account."
              " You can turn them off there.")
    body = text + (f"\n\nOpen: {url}" if url else "") + footer
    html = (f"<p>{escape(text)}</p>" + (f'<p><a href="{escape(url)}">Open in ProjectSphere</a></p>' if url else "")
            + '<p style="color:#64748b;font-size:12px">Laari ProjectSphere. You get these emails because email '
              "notifications are on in My account. You can turn them off there.</p>")
    row = EmailOutbox(organization_id=organization_id or user.organization_id, user_id=user.id,
                      to_address=user.email, subject=subject[:255], body_text=body, body_html=html)
    db.session.add(row)
    return row


def queue_notification(user_ids, kind: str, title: str, link: str, organization_id=None) -> int:
    if kind not in EMAIL_KINDS:
        return 0
    n = 0
    for u in db.session.scalars(select(User).where(User.id.in_(list(user_ids)))):
        if u.email_notify and queue(u, title, title + ".", link, organization_id) is not None:
            n += 1
    return n


def flush(limit: int = 100) -> dict:
    """Send queued emails (all organizations). Returns counts."""
    from app.tenancy import tenant_scope

    from datetime import timedelta

    sent = failed = 0
    with tenant_scope(platform=True):
        if not mailer.is_configured():
            # Keep recent mail for when email is set up; give up on anything older than two days.
            stale = db.session.scalars(select(EmailOutbox).where(
                EmailOutbox.status == "queued", EmailOutbox.created_at < utcnow() - timedelta(days=2))).all()
            for r in stale:
                r.status, r.last_error = "failed", "Mail server not configured"
            db.session.commit()
            return {"sent": 0, "not_sent": len(stale)}
        rows = db.session.scalars(select(EmailOutbox).where(EmailOutbox.status == "queued")
                                  .order_by(EmailOutbox.id).limit(limit).with_for_update(skip_locked=True)).all()
        for r in rows:
            r.attempts += 1
            if mailer.send(r.to_address, r.subject, r.body_text, r.body_html or None):
                r.status, r.sent_at, r.last_error = "sent", utcnow(), ""
                sent += 1
            else:
                r.last_error = "Send failed"
                if r.attempts >= MAX_ATTEMPTS:
                    r.status = "failed"
                failed += 1
        db.session.commit()
    return {"sent": sent, "not_sent": failed}
