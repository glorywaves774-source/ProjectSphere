from datetime import timedelta

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, Organization, Subscription, utcnow
from app.services.limits import LimitExceeded, SubscriptionInactive, ensure_within_limit, usage_summary
from app.services.subscriptions import expire_trials
from app.tenancy import tenant_scope

from .conftest import add_member, login, make_org, make_superadmin


def test_admin_dashboard_counts_come_from_database(client):
    a, _ = make_org("Alpha", "a@alpha.org", plan="starter")
    b, _ = make_org("Beta", "b@beta.org", plan="business")
    with tenant_scope(platform=True):
        sub = db.session.scalar(select(Subscription).where(Subscription.organization_id == b))
        sub.status = Subscription.ACTIVE
        db.session.commit()
    make_superadmin()
    login(client, "root@kayron.com")
    html = client.get("/admin/").get_data(as_text=True)
    assert "GH₵499" in html  # MRR = Business plan only (trials are not revenue)
    assert "From 1 paying subscription" in html


def test_admin_can_search_and_filter_organizations(client):
    make_org("Alpha Water NGO", "a@alpha.org")
    make_org("Beta Builders", "b@beta.org", org_type="private")
    make_superadmin()
    login(client, "root@kayron.com")
    html = client.get("/admin/organizations?q=water").get_data(as_text=True)
    assert "Alpha Water NGO" in html and "Beta Builders" not in html
    html = client.get("/admin/organizations?type=private").get_data(as_text=True)
    assert "Beta Builders" in html and "Alpha Water NGO" not in html


def test_admin_suspend_and_reactivate(client):
    org_id, _ = make_org()
    make_superadmin()
    login(client, "root@kayron.com")
    r = client.post(f"/admin/organizations/{org_id}/status", data={"action": "suspend", "reason": "Unpaid"})
    assert r.status_code == 302
    with tenant_scope(platform=True):
        assert db.session.get(Organization, org_id).status == "suspended"
        logs = db.session.scalars(select(AuditLog.action).where(AuditLog.organization_id == org_id)).all()
        assert "platform.org_suspended" in logs
    client.post(f"/admin/organizations/{org_id}/status", data={"action": "reactivate"})
    db.session.expire_all()
    with tenant_scope(platform=True):
        assert db.session.get(Organization, org_id).status == "active"
    assert client.get("/admin/organizations/not-a-uuid").status_code == 404


def test_user_limit_enforced_on_starter_plan():
    org_id, _ = make_org(plan="starter")  # 5 users
    for i in range(4):
        add_member(org_id, f"m{i}@hope.org")
    with tenant_scope(org_id):
        org = db.session.get(Organization, org_id)
        with pytest.raises(LimitExceeded):
            ensure_within_limit(org, "users")
        users = next(u for u in usage_summary(org) if u.resource == "users")
        assert (users.used, users.limit, users.percent) == (5, 5, 100)


def test_inactive_subscription_blocks_new_resources():
    org_id, _ = make_org()
    with tenant_scope(org_id):
        org = db.session.get(Organization, org_id)
        org.subscription.status = Subscription.PAST_DUE
        db.session.commit()
        with pytest.raises(SubscriptionInactive):
            ensure_within_limit(org, "users")


def test_expire_trials_job():
    a, _ = make_org("Old", "o@old.org")
    b, _ = make_org("New", "n@new.org")
    with tenant_scope(a):
        db.session.get(Organization, a).subscription.trial_ends_at = utcnow() - timedelta(days=1)
        db.session.commit()
    assert expire_trials() == 1
    with tenant_scope(platform=True):
        statuses = dict(db.session.execute(select(Subscription.organization_id, Subscription.status)).all())
    assert statuses == {a: "past_due", b: "trialing"}
    assert expire_trials() == 0
