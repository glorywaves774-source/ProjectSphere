"""Subscription lifecycle jobs (run by Celery beat or `flask expire-trials`)."""
from sqlalchemy import select

from app.extensions import db
from app.models import Subscription, utcnow
from app.tenancy import tenant_scope

from . import audit


def expire_trials() -> int:
    """Move trials whose end date has passed to past_due. Returns how many changed."""
    changed = 0
    with tenant_scope(platform=True):
        due = db.session.scalars(
            select(Subscription).where(
                Subscription.status == Subscription.TRIALING, Subscription.trial_ends_at < utcnow()
            )
        ).all()
        for sub in due:
            sub.status = Subscription.PAST_DUE
            audit.record("subscription.trial_expired", summary="Free trial ended", entity=sub,
                         organization_id=sub.organization_id)
            changed += 1
        db.session.commit()
    return changed
