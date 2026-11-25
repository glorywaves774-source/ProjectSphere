"""Super Admin operations on tenants and their users."""
from datetime import datetime

from sqlalchemy import func, select

from app.extensions import db
from app.models import Organization, Plan, Role, Subscription, User, UserRole

from . import audit
from .onboarding import temporary_password


class TenantAdminError(ValueError):
    pass


def change_subscription(org: Organization, *, plan: Plan, status: str, billing_cycle: str,
                        trial_ends_at: datetime | None, current_period_end: datetime | None) -> list[str]:
    sub = org.subscription
    before = {"plan": sub.plan.name, "status": sub.status, "billing_cycle": sub.billing_cycle,
              "trial_ends_at": sub.trial_ends_at, "current_period_end": sub.current_period_end}
    if status == Subscription.TRIALING and not trial_ends_at:
        raise TenantAdminError("A trial needs an end date.")
    sub.plan_id, sub.plan = plan.id, plan
    sub.status = status
    sub.billing_cycle = billing_cycle
    sub.trial_ends_at = trial_ends_at
    sub.current_period_end = current_period_end
    if status == Subscription.CANCELLED and not sub.cancelled_at:
        from app.models import utcnow

        sub.cancelled_at = utcnow()
    after = {"plan": plan.name, "status": status, "billing_cycle": billing_cycle,
             "trial_ends_at": trial_ends_at, "current_period_end": current_period_end}
    changed = [k for k in after if before[k] != after[k]]

    def show(value):
        if value is None:
            return "cleared"
        return value.strftime("%d %b %Y") if isinstance(value, datetime) else str(value)

    if changed:
        audit.record("platform.subscription_changed",
                     summary=f"Subscription for {org.name} changed: " + ", ".join(
                         f"{k.replace('_', ' ')} → {show(after[k])}" for k in changed),
                     entity=sub, organization_id=org.id,
                     details={k: str(after[k]) for k in changed})
    return changed


def _active_admin_count(org_id) -> int:
    return db.session.scalar(
        select(func.count(User.id)).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id)
        .where(User.organization_id == org_id, User.is_active_user.is_(True), Role.key == "org_admin")
    ) or 0


def set_user_active(user: User, active: bool) -> None:
    if user.is_active_user == active:
        return
    if not active and not user.is_platform_admin and "org_admin" in {r.key for r in user.roles} \
            and _active_admin_count(user.organization_id) <= 1:
        raise TenantAdminError("This is the organization's only active Organization Admin. Add another admin first.")
    user.is_active_user = active
    if not active:
        user.session_version += 1  # sign them out everywhere
    audit.record("platform.user_activated" if active else "platform.user_deactivated",
                 summary=f"{'Reactivated' if active else 'Deactivated'} {user.full_name}",
                 entity=user, organization_id=user.organization_id)


def reset_password(user: User) -> str:
    temp = temporary_password()
    user.set_password(temp)  # also signs out every session
    user.must_change_password = True
    user.failed_logins = 0
    user.locked_until = None
    audit.record("platform.password_reset", summary=f"Temporary password issued to {user.full_name}",
                 entity=user, organization_id=user.organization_id)
    return temp


def unlock(user: User) -> None:
    user.failed_logins = 0
    user.locked_until = None
    audit.record("platform.user_unlocked", summary=f"Unlocked {user.full_name}", entity=user,
                 organization_id=user.organization_id)
