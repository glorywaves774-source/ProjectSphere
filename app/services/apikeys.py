"""Stage 11: organization API keys.

A key is shown once when created; only its SHA-256 hash is stored. Keys carry scopes, can expire and can be
revoked. API access is part of the Professional plan and above.
"""
import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select

from app.extensions import db
from app.models import API_SCOPES, ApiKey, Organization, User, utcnow

from . import audit

PREFIX = "ps_live_"


class ApiKeyError(ValueError):
    pass


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def plan_allows(org: Organization) -> bool:
    sub = org.subscription
    return bool(sub and sub.is_usable and sub.plan.api_access)


def create(actor: User, name: str, scopes: list[str], days: int | None) -> tuple[ApiKey, str]:
    if not actor.can("org.manage"):
        raise ApiKeyError("Only organization admins can create API keys.")
    org = actor.organization
    if not plan_allows(org):
        raise ApiKeyError("API access is included in the Professional plan and above.")
    name = (name or "").strip()
    if len(name) < 2:
        raise ApiKeyError("Name the key after what will use it, e.g. “Power BI dashboard”.")
    valid = dict(API_SCOPES)
    scopes = [s for s in dict.fromkeys(scopes or []) if s in valid]
    if not scopes:
        raise ApiKeyError("Choose at least one thing the key may access.")
    active = db.session.scalars(select(ApiKey).where(ApiKey.revoked_at.is_(None))).all()
    if len([k for k in active if k.is_active]) >= 20:
        raise ApiKeyError("You have 20 active keys. Revoke one you no longer use first.")
    token = PREFIX + secrets.token_urlsafe(32)
    key = ApiKey(organization_id=org.id, name=name[:80], prefix=token[:14], key_hash=digest(token), scopes=scopes,
                 created_by_id=actor.id, expires_at=utcnow() + timedelta(days=days) if days else None)
    db.session.add(key)
    db.session.flush()
    audit.record("integration.key_created", summary=f"Created API key “{key.name}” ({', '.join(scopes)})",
                 entity=key)
    return key, token


def revoke(actor: User, key: ApiKey) -> None:
    if not actor.can("org.manage"):
        raise ApiKeyError("Only organization admins can revoke API keys.")
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        audit.record("integration.key_revoked", summary=f"Revoked API key “{key.name}”", entity=key)


def authenticate(token: str) -> ApiKey | None:
    """Find the active key for a token (before the organization is known)."""
    from app.tenancy import auth_lookup_scope

    if not token or not token.startswith(PREFIX) or len(token) > 200:
        return None
    with auth_lookup_scope():
        key = db.session.scalar(select(ApiKey).where(ApiKey.key_hash == digest(token)))
        if key is not None:
            db.session.expunge(key)
    if key is None or not key.is_active:
        return None
    return key
