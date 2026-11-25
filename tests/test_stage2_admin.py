"""Stage 2: SaaS administration by the Platform Super Admin."""
import re
from datetime import date, timedelta

from sqlalchemy import func, select, text

from app.extensions import db
from app.models import AuditLog, Organization, Plan, Subscription, User
from app.tenancy import tenant_scope

from .conftest import PASSWORD, add_member, login, make_org, make_superadmin


def admin_client(client):
    make_superadmin()
    login(client, "root@kayron.com")
    return client


def temp_password_from(html: str) -> str:
    m = re.search(r'<code class="secret"[^>]*>([^<]+)</code>', html)
    assert m, "one-time password not shown"
    return m.group(1)


# ---------------------------------------------------------------- plans
def test_create_plan_and_it_appears_on_pricing_page(client):
    admin_client(client)
    r = client.post("/admin/plans/new", data={
        "code": "ngo-lite", "name": "NGO Lite", "audience": "Community groups", "currency": "GHS",
        "price_monthly": "49", "price_annual": "490", "max_users": "3", "features": "Up to 3 members\nCore tools\n",
        "sort_order": "0", "is_public": "y", "is_active": "y"})
    assert r.status_code == 302
    plan = db.session.scalar(select(Plan).where(Plan.code == "ngo-lite"))
    assert plan.max_users == 3 and plan.max_projects is None and plan.features == ["Up to 3 members", "Core tools"]
    client.post("/auth/logout")
    html = client.get("/").get_data(as_text=True)
    assert "NGO Lite" in html and "GH₵49" in html
    assert "NGO Lite" in client.get("/auth/register").get_data(as_text=True)


def test_plan_validation(client):
    admin_client(client)
    r = client.post("/admin/plans/new", data={"code": "Bad Code", "name": "X", "currency": "GHS"})
    assert r.status_code == 200 and b"lowercase letters" in r.data
    r = client.post("/admin/plans/new", data={"code": "starter", "name": "Dup", "currency": "GHS"})
    assert b"already exists" in r.data
    r = client.post("/admin/plans/new", data={"code": "odd", "name": "Odd", "currency": "GHS", "price_annual": "100"})
    assert b"Set a monthly price too" in r.data


def test_edit_plan_changes_limits_for_subscribers_and_keeps_code(client):
    org_id, _ = make_org(plan="starter")
    admin_client(client)
    plan = db.session.scalar(select(Plan).where(Plan.code == "starter"))
    r = client.post(f"/admin/plans/{plan.id}/edit", data={
        "code": "hacked", "name": "Starter", "currency": "GHS", "price_monthly": "119", "max_users": "8",
        "max_projects": "5", "max_storage_mb": "2048", "features": "A", "sort_order": "1", "is_public": "y"})
    assert r.status_code == 302
    db.session.expire_all()
    plan = db.session.get(Plan, plan.id)
    assert plan.code == "starter" and plan.max_users == 8 and float(plan.price_monthly) == 119
    assert plan.is_active is False  # unticked → retired
    assert client.get("/admin/plans").status_code == 200
    with tenant_scope(org_id):
        assert db.session.get(Organization, org_id).subscription.plan.max_users == 8
    # retired plans are not offered at sign-up
    client.post("/auth/logout")
    assert b"Starter" not in client.get("/auth/register").data


# ---------------------------------------------------------------- organizations
def test_super_admin_creates_organization_with_one_time_password(client, app):
    admin_client(client)
    r = client.post("/admin/organizations/new", data={
        "org_name": "Northern Water Trust", "org_type": "ngo", "country": "Ghana", "plan": "business",
        "status": "active", "admin_name": "Abdul Rahman", "admin_email": "abdul@nwt.org"})
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    temp = temp_password_from(r.get_data(as_text=True))
    with tenant_scope(platform=True):
        org = db.session.scalar(select(Organization).where(Organization.name == "Northern Water Trust"))
        assert org.subscription.status == "active" and org.subscription.plan.code == "business"
        user = db.session.scalar(select(User).where(User.email == "abdul@nwt.org"))
        assert user.must_change_password and user.role_names == ["Organization Admin"]
        assert temp not in str(user.password_hash)

    # The new admin must change the password before anything else
    c = app.test_client()
    login(c, "abdul@nwt.org", temp)
    r = c.get("/app/team")
    assert r.status_code == 302 and r.headers["Location"].endswith("/app/account")
    r = c.post("/app/account", data={"form": "password", "pw-current_password": temp,
                                     "pw-new_password": "MyOwnPassw0rd", "pw-confirm": "MyOwnPassw0rd"})
    assert r.status_code == 302
    assert c.get("/app/team").status_code == 200


def test_create_organization_rejects_existing_email(client):
    make_org(email="taken@hope.org")
    admin_client(client)
    r = client.post("/admin/organizations/new", data={
        "org_name": "Other", "org_type": "ngo", "country": "Ghana", "plan": "starter", "status": "trialing",
        "admin_name": "X Y", "admin_email": "taken@hope.org"})
    assert r.status_code == 200 and b"already exists" in r.data


def test_admin_edits_organization_details(client):
    org_id, _ = make_org()
    admin_client(client)
    r = client.post(f"/admin/organizations/{org_id}/edit", data={
        "org-name": "Hope Foundation Ghana", "org-org_type": "ngo", "org-country": "Ghana",
        "org-email": "info@hope.org", "org-phone": "0302000000"})
    assert r.status_code == 302
    with tenant_scope(platform=True):
        assert db.session.get(Organization, org_id).name == "Hope Foundation Ghana"


def test_admin_changes_subscription(client):
    org_id, _ = make_org(plan="starter")
    admin_client(client)
    pro = db.session.scalar(select(Plan).where(Plan.code == "professional"))
    paid_until = date.today() + timedelta(days=30)
    r = client.post(f"/admin/organizations/{org_id}/subscription", data={
        "sub-plan_id": pro.id, "sub-status": "active", "sub-billing_cycle": "monthly",
        "sub-current_period_end": paid_until.isoformat()})
    assert r.status_code == 302
    with tenant_scope(platform=True):
        sub = db.session.scalar(select(Subscription).where(Subscription.organization_id == org_id))
        assert (sub.plan.code, sub.status, sub.current_period_end.date()) == ("professional", "active", paid_until)
        log = db.session.scalar(select(AuditLog).where(AuditLog.action == "platform.subscription_changed"))
        assert "Professional" in log.summary and log.organization_id == org_id
    # MRR now counts it
    assert "GH₵249" in client.get("/admin/").get_data(as_text=True)


def test_trial_status_requires_end_date(client):
    org_id, _ = make_org()
    admin_client(client)
    starter = db.session.scalar(select(Plan).where(Plan.code == "starter"))
    r = client.post(f"/admin/organizations/{org_id}/subscription", data={
        "sub-plan_id": starter.id, "sub-status": "trialing", "sub-billing_cycle": "monthly"}, follow_redirects=True)
    assert b"A trial needs an end date" in r.data


# ---------------------------------------------------------------- org users
def _user_id(email):
    with tenant_scope(platform=True):
        return db.session.scalar(select(User.id).where(User.email == email))


def test_deactivate_reactivate_member_signs_them_out(client, app):
    org_id, _ = make_org()
    add_member(org_id, "kofi@hope.org", "staff")
    member = app.test_client()
    login(member, "kofi@hope.org")
    assert member.get("/app/").status_code == 200
    admin_client(client)
    client.post(f"/admin/users/{_user_id('kofi@hope.org')}/deactivate")
    assert member.get("/app/").status_code == 302  # signed out
    assert login(member, "kofi@hope.org").status_code == 401
    client.post(f"/admin/users/{_user_id('kofi@hope.org')}/activate")
    assert login(member, "kofi@hope.org").status_code == 302


def test_cannot_deactivate_last_org_admin(client):
    make_org()
    admin_client(client)
    r = client.post(f"/admin/users/{_user_id('admin@hope.org')}/deactivate", follow_redirects=True)
    assert b"only active Organization Admin" in r.data
    with tenant_scope(platform=True):
        assert db.session.get(User, _user_id("admin@hope.org")).is_active_user


def test_unlock_and_reset_password(client, app):
    make_org()
    other = app.test_client()
    for _ in range(5):
        login(other, "admin@hope.org", "Wrong-pass1")
    assert login(other, "admin@hope.org").status_code == 423
    admin_client(client)
    uid = _user_id("admin@hope.org")
    client.post(f"/admin/users/{uid}/unlock")
    assert login(other, "admin@hope.org").status_code == 302
    r = client.post(f"/admin/users/{uid}/reset")
    temp = temp_password_from(r.get_data(as_text=True))
    assert other.get("/app/").status_code == 302  # old session ended
    other.post("/auth/logout")
    assert login(other, "admin@hope.org", PASSWORD).status_code == 401
    login(other, "admin@hope.org", temp)
    assert other.get("/app/").headers["Location"].endswith("/app/account")


# ---------------------------------------------------------------- super admins
def test_add_super_admin_and_safety_rules(client, app):
    make_org()
    admin_client(client)
    r = client.post("/admin/admins", data={"full_name": "Second Admin", "email": "two@kayron.com"})
    temp = temp_password_from(r.get_data(as_text=True))
    # Can't promote an organization user's email
    r = client.post("/admin/admins", data={"full_name": "Sneaky", "email": "admin@hope.org"})
    assert b"already exists" in r.data
    # New admin must change password, then can work
    c = app.test_client()
    login(c, "two@kayron.com", temp)
    assert c.get("/admin/organizations").headers["Location"].endswith("/admin/account")
    c.post("/admin/account", data={"pw-current_password": temp, "pw-new_password": "Second-Adm1n",
                                   "pw-confirm": "Second-Adm1n"})
    assert c.get("/admin/organizations").status_code == 200
    # Can't deactivate yourself
    me = _user_id("root@kayron.com")
    r = client.post(f"/admin/users/{me}/deactivate", follow_redirects=True)
    assert b"your own account" in r.data
    # Deactivating the other leaves at least one
    client.post(f"/admin/users/{_user_id('two@kayron.com')}/deactivate")
    assert c.get("/admin/").status_code == 302


# ---------------------------------------------------------------- settings
def test_settings_toggle_signup_trial_and_banner(client, app):
    org_id, _ = make_org()
    admin_client(client)
    r = client.post("/admin/settings", data={
        "platform_name": "Laari ProjectSphere", "support_email": "help@kayron.example",
        "sales_email": "sales@kayron.example", "trial_days": "30", "announcement": "Maintenance on Sunday 2am",
        "announcement_level": "warning"})  # allow_signup unticked → off
    assert r.status_code == 302
    visitor = app.test_client()
    assert visitor.get("/auth/register").status_code == 403
    landing = visitor.get("/").get_data(as_text=True)
    assert "Start your free trial" not in landing and "sales@kayron.example" in landing
    member = app.test_client()
    login(member, "admin@hope.org")
    page = member.get("/app/").get_data(as_text=True)
    assert "Maintenance on Sunday 2am" in page
    assert "help@kayron.example" in member.get("/app/support").get_data(as_text=True)
    # Trial length applies to new organizations
    client.post("/admin/settings", data={"platform_name": "Laari ProjectSphere", "support_email": "", "sales_email": "",
                                         "allow_signup": "on", "trial_days": "30", "announcement": "",
                                         "announcement_level": "info"})
    new_org, _ = make_org("Later Org", "later@org.org")
    with tenant_scope(new_org):
        assert db.session.get(Organization, new_org).subscription.trial_days_left == 30
    with tenant_scope(platform=True):
        assert db.session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "platform.settings_updated")) == 2


def test_settings_validation(client):
    admin_client(client)
    r = client.post("/admin/settings", data={"platform_name": "", "support_email": "not-an-email", "sales_email": "",
                                             "trial_days": "abc", "announcement": "", "announcement_level": "x"})
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "valid email" in html and "whole number" in html and "needs a name" in html


def test_zero_trial_days_starts_active():
    from app.services import settings as ps

    ps.save({"trial_days": "0"})
    db.session.commit()
    org_id, _ = make_org("Paid Org", "paid@org.org")
    with tenant_scope(org_id):
        sub = db.session.get(Organization, org_id).subscription
        assert sub.status == "active" and sub.current_period_end is not None


def test_all_stage2_admin_pages_render(client):
    org_id, _ = make_org()
    admin_client(client)
    for url in ["/admin/organizations/new", f"/admin/organizations/{org_id}", "/admin/plans/new",
                "/admin/settings", "/admin/admins", "/admin/support", "/admin/account"]:
        assert client.get(url).status_code == 200, url


def test_org_users_cannot_use_admin_actions(client):
    org_id, _ = make_org()
    login(client, "admin@hope.org")
    uid = _user_id("admin@hope.org")
    for method, url in [("post", f"/admin/users/{uid}/reset"), ("post", "/admin/settings"),
                        ("get", "/admin/plans/new"), ("post", f"/admin/organizations/{org_id}/subscription")]:
        assert getattr(client, method)(url).status_code == 403, url


def test_stage2_tables_have_rls():
    rows = db.session.execute(text(
        "SELECT relname FROM pg_class WHERE relrowsecurity AND relforcerowsecurity AND relname LIKE 'support_%'")).all()
    assert {r[0] for r in rows} == {"support_tickets", "support_messages"}
