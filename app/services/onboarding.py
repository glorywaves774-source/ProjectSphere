"""Creating a new tenant: organization, trial subscription, default roles, first admin."""
import re
import secrets
import uuid
from datetime import timedelta

from flask import current_app
from sqlalchemy import func, select

from app.extensions import db
from app.models import Organization, Permission, Plan, Role, Subscription, User, UserRole, utcnow
from app.security.catalog import DEFAULT_ROLES
from app.tenancy import auth_lookup_scope, tenant_scope

from . import audit


class OnboardingError(ValueError):
    pass


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:60] or "org"


def email_taken(email: str) -> bool:
    with auth_lookup_scope():
        return bool(db.session.scalar(select(func.count(User.id)).where(func.lower(User.email) == email.lower())))


def _unique_slug(name: str) -> str:
    base = slugify(name)
    # Slugs are unique across the platform; check with a platform-wide read
    with tenant_scope(platform=True):
        taken = set(db.session.scalars(select(Organization.slug).where(Organization.slug.like(f"{base}%"))))
    if base not in taken:
        return base
    while True:
        candidate = f"{base}-{secrets.token_hex(2)}"
        if candidate not in taken:
            return candidate


def create_default_roles(org_id: uuid.UUID) -> dict[str, Role]:
    perms = {p.code: p for p in db.session.scalars(select(Permission))}
    roles = {}
    for key, (name, desc, codes) in DEFAULT_ROLES.items():
        role = Role(organization_id=org_id, key=key, name=name, description=desc, is_system=True)
        role.permissions = [perms[c] for c in sorted(codes) if c in perms]
        db.session.add(role)
        roles[key] = role
    return roles


def temporary_password() -> str:
    """A strong one-time password, shown once to the Super Admin."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(14))
        if any(c.isdigit() for c in pw) and any(c.isupper() for c in pw) and any(c.islower() for c in pw):
            return pw


def create_default_categories(org_id: uuid.UUID) -> None:
    from app.models import DEFAULT_CATEGORIES, ProjectCategory

    for name, color in DEFAULT_CATEGORIES:
        db.session.add(ProjectCategory(organization_id=org_id, name=name, color=color))


def register_organization(*, org_name: str, org_type: str, country: str, admin_name: str,
                          admin_email: str, password: str, plan_code: str = "starter",
                          status: str | None = None, must_change_password: bool = False,
                          created_by_platform: bool = False) -> tuple[Organization, User]:
    admin_email = admin_email.strip().lower()
    if email_taken(admin_email):
        raise OnboardingError("An account with this email already exists. Sign in instead.")
    plan = db.session.scalar(select(Plan).where(Plan.code == plan_code, Plan.is_active.is_(True)))
    if plan is None:
        raise OnboardingError("Please choose a valid plan.")
    slug = _unique_slug(org_name)

    org_id = uuid.uuid4()
    with tenant_scope(org_id):
        org = Organization(id=org_id, name=org_name.strip(), slug=slug, org_type=org_type,
                           country=country.strip() or "Ghana", email=admin_email,
                           currency=current_app.config["DEFAULT_CURRENCY"])
        db.session.add(org)
        db.session.flush()
        from .settings import trial_days as _trial_days

        days = _trial_days()
        if status is None:
            status = Subscription.TRIALING if days > 0 else Subscription.ACTIVE
        sub = Subscription(organization_id=org_id, plan_id=plan.id, status=status)
        if status == Subscription.TRIALING:
            sub.trial_ends_at = utcnow() + timedelta(days=max(days, 1))
        else:
            sub.current_period_end = utcnow() + timedelta(days=30)
        db.session.add(sub)
        roles = create_default_roles(org_id)
        create_default_categories(org_id)
        user = User(organization_id=org_id, email=admin_email, full_name=admin_name.strip(),
                    must_change_password=must_change_password)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        db.session.add(UserRole(organization_id=org_id, user_id=user.id, role_id=roles["org_admin"].id))
        how = "created by Kayron Global Tech Services" if created_by_platform else "joined"
        audit.record("org.registered", summary=f"{org.name} {how} on the {plan.name} plan ({sub.status})",
                     entity=org, user=None if created_by_platform else user)
        db.session.commit()
    return org, user


def create_platform_admin(email: str, full_name: str, password: str, must_change_password: bool = False) -> User:
    with tenant_scope(platform=True):
        user = db.session.scalar(select(User).where(func.lower(User.email) == email.lower()))
        if user is not None and user.organization_id is not None:
            raise OnboardingError("That email belongs to an organization user. Use a different email for a Super Admin.")
        if user is None:
            user = User(email=email.lower(), full_name=full_name, is_platform_admin=True, organization_id=None)
            db.session.add(user)
        user.full_name = full_name
        user.is_platform_admin = True
        user.is_active_user = True
        user.must_change_password = must_change_password
        user.set_password(password)
        audit.record("platform.admin_saved", summary=f"Super Admin account saved for {email}",
                     organization_id=None)
        db.session.commit()
        return user
