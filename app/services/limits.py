"""Subscription usage limits.

Every module that creates a limited resource must call ``ensure_within_limit``
first. Projects and storage are wired in when those modules arrive.
"""
from dataclasses import dataclass

from sqlalchemy import func, select

from app.extensions import db
from app.models import Organization, User


class LimitExceeded(Exception):
    def __init__(self, resource: str, limit: int):
        self.resource, self.limit = resource, limit
        super().__init__(f"Your plan allows {limit} {resource.replace('_', ' ')}. Upgrade to add more.")


class SubscriptionInactive(Exception):
    pass


@dataclass
class Usage:
    resource: str
    label: str
    used: int
    limit: int | None

    @property
    def percent(self) -> int:
        if not self.limit:
            return 0
        return min(100, round(self.used * 100 / self.limit))

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None else max(0, self.limit - self.used)


def count_usage(org: Organization, resource: str) -> int:
    if resource == "users":
        return db.session.scalar(
            select(func.count(User.id)).where(User.organization_id == org.id, User.is_active_user.is_(True))
        ) or 0
    if resource == "projects":
        from app.models import OPEN_PROJECT_STATUSES, Project

        return db.session.scalar(
            select(func.count(Project.id)).where(Project.organization_id == org.id,
                                                 Project.status.in_(OPEN_PROJECT_STATUSES))
        ) or 0
    if resource == "storage_mb":
        from app.models import DocumentVersion

        used = db.session.scalar(select(func.coalesce(func.sum(DocumentVersion.size_bytes), 0))
                                 .where(DocumentVersion.organization_id == org.id)) or 0
        return -(-int(used) // (1024 * 1024))  # whole megabytes, rounded up
    return 0


def usage_summary(org: Organization) -> list[Usage]:
    plan = org.subscription.plan if org.subscription else None
    out = []
    for resource, label in (("users", "Team members"), ("projects", "Open projects"), ("storage_mb", "Storage (MB)")):
        out.append(Usage(resource, label, count_usage(org, resource), plan.limit_for(resource) if plan else 0))
    return out


def ensure_within_limit(org: Organization, resource: str, adding: int = 1) -> None:
    sub = org.subscription
    if sub is None or not sub.is_usable or org.is_suspended:
        raise SubscriptionInactive("This organization's subscription is not active.")
    limit = sub.plan.limit_for(resource)
    if limit is not None and count_usage(org, resource) + adding > limit:
        raise LimitExceeded(resource, limit)
