"""In-app notifications, also emailed (through the outbox) to people who keep email notifications on."""
from sqlalchemy import func, select, update

from app.extensions import db
from app.models import Notification, User, utcnow


def users_with(code: str, *, exclude: set[int] | None = None) -> list[User]:
    exclude = exclude or set()
    people = db.session.scalars(select(User).where(User.is_active_user.is_(True),
                                                   User.organization_id.is_not(None))).all()
    return [u for u in people if u.id not in exclude and code in u.permission_codes]


def notify(users, kind: str, title: str, link: str = "", *, actor=None, organization_id=None) -> int:
    """Create one notification per recipient (never for the person who caused it)."""
    seen, n = set(), 0
    actor_id = getattr(actor, "id", None)
    for u in users:
        uid = getattr(u, "id", u)
        if not uid or uid in seen or uid == actor_id:
            continue
        seen.add(uid)
        org = organization_id or getattr(u, "organization_id", None)
        if org is None:
            org = db.session.scalar(select(User.organization_id).where(User.id == uid))
        db.session.add(Notification(organization_id=org, user_id=uid, kind=kind, title=title[:255],
                                    link=link[:255], actor_id=actor_id))
        n += 1
    if seen:
        from .email_outbox import queue_notification

        queue_notification(seen, kind, title, link, organization_id)
    return n


def unread_count(user) -> int:
    return db.session.scalar(select(func.count(Notification.id)).where(
        Notification.user_id == user.id, Notification.read_at.is_(None))) or 0


def mark_read(user, ids: list[int] | None = None) -> None:
    stmt = update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    if ids:
        stmt = stmt.where(Notification.id.in_(ids))
    db.session.execute(stmt.values(read_at=utcnow()))
