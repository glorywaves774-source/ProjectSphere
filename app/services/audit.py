import logging

from flask import has_request_context, request
from flask_login import current_user

from app.extensions import db
from app.models import AuditLog
from app.tenancy import get_context

log = logging.getLogger("projectsphere.audit")


def record(action: str, *, summary: str = "", entity=None, user=None, organization_id=None, details=None) -> AuditLog:
    """Add an audit entry to the current transaction (caller commits)."""
    ctx = get_context()
    if user is None and has_request_context() and current_user and current_user.is_authenticated:
        user = current_user
    entry = AuditLog(
        organization_id=organization_id if organization_id is not None else ctx.tenant_id,
        user_id=getattr(user, "id", None),
        action=action,
        summary=summary[:255],
        entity_type=type(entity).__name__ if entity is not None else "",
        entity_id=str(getattr(entity, "id", "") or "") if entity is not None else "",
        details=details or {},
    )
    if has_request_context():
        entry.ip_address = (request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0]).strip()[:64]
        entry.user_agent = (request.user_agent.string or "")[:255]
    db.session.add(entry)
    log.info("audit %s org=%s user=%s %s", action, entry.organization_id, entry.user_id, summary)
    return entry
