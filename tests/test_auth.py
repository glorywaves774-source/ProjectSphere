from sqlalchemy import func, select

from app.extensions import db
from app.models import AuditLog, Organization, Role, Subscription, User
from app.tenancy import tenant_scope

from .conftest import PASSWORD, login, make_org, make_superadmin

REG = dict(org_name="Green Earth Initiative", org_type="ngo", country="Ghana", full_name="Esi Owusu",
           email="esi@greenearth.org", password="GoodPassw0rd", confirm="GoodPassw0rd", plan="professional", accept="y")


def test_register_creates_complete_tenant(client):
    r = client.post("/auth/register", data=REG)
    assert r.status_code == 302 and r.headers["Location"].endswith("/app/")
    with tenant_scope(platform=True):
        org = db.session.scalar(select(Organization).where(Organization.name == "Green Earth Initiative"))
        assert org.slug == "green-earth-initiative"
        sub = db.session.scalar(select(Subscription).where(Subscription.organization_id == org.id))
        assert sub.status == "trialing" and sub.plan.code == "professional" and sub.trial_days_left == 14
        assert db.session.scalar(select(func.count(Role.id)).where(Role.organization_id == org.id)) == 11
        user = db.session.scalar(select(User).where(User.email == "esi@greenearth.org"))
        assert user.organization_id == org.id and user.role_names == ["Organization Admin"]
        assert user.password_hash != "GoodPassw0rd" and user.check_password("GoodPassw0rd")
        assert db.session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "org.registered")) == 1
    # Signed straight in
    assert client.get("/app/").status_code == 200


def test_register_rejects_duplicate_email_and_weak_password(client):
    make_org(email="esi@greenearth.org")
    r = client.post("/auth/register", data=REG)
    assert r.status_code == 400 and b"already exists" in r.data
    weak = dict(REG, email="new@x.org", password="password", confirm="password")
    r = client.post("/auth/register", data=weak)
    assert r.status_code == 200 and b"at least 10" in r.data


def test_duplicate_org_names_get_unique_slugs():
    make_org("Same Name", "one@a.org")
    make_org("Same Name", "two@b.org")
    with tenant_scope(platform=True):
        slugs = db.session.scalars(select(Organization.slug)).all()
    assert len(set(slugs)) == 2 and "same-name" in slugs


def test_login_success_and_logout(client):
    make_org()
    r = login(client, "ADMIN@hope.org")
    assert r.status_code == 302 and r.headers["Location"].endswith("/app/")
    assert client.get("/app/").status_code == 200
    assert client.get("/auth/logout").status_code == 405  # sign-out must be a POST
    client.post("/auth/logout")
    assert client.get("/app/").status_code == 302


def test_wrong_password_and_unknown_email_get_same_message(client):
    make_org()
    r1 = login(client, "admin@hope.org", "Wrong-pass1")
    r2 = login(client, "nobody@hope.org", "Wrong-pass1")
    assert r1.status_code == r2.status_code == 401
    assert b"don&#39;t match an active account" in r1.data and b"don&#39;t match an active account" in r2.data


def test_lockout_after_repeated_failures(client):
    make_org()
    for _ in range(5):
        login(client, "admin@hope.org", "Wrong-pass1")
    r = login(client, "admin@hope.org")  # correct password, but locked
    assert r.status_code == 423
    with tenant_scope(platform=True):
        assert db.session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "auth.locked")) == 1


def test_suspended_org_cannot_sign_in_and_is_locked_out_of_workspace(client):
    org_id, _ = make_org()
    login(client, "admin@hope.org")
    with tenant_scope(platform=True):
        db.session.get(Organization, org_id).status = "suspended"
        db.session.commit()
    assert client.get("/app/").status_code == 403  # existing session blocked
    client.post("/auth/logout")
    assert login(client, "admin@hope.org").status_code == 403


def test_open_redirect_is_blocked(client):
    make_org()
    r = client.post("/auth/login?next=https://evil.example/x", data={"email": "admin@hope.org", "password": PASSWORD})
    assert r.headers["Location"].endswith("/app/")
    client.post("/auth/logout")
    r = client.post("/auth/login?next=/app/team", data={"email": "admin@hope.org", "password": PASSWORD})
    assert r.headers["Location"].endswith("/app/team")


def test_password_change_signs_out_other_sessions(app):
    make_org()
    phone, laptop = app.test_client(), app.test_client()
    login(phone, "admin@hope.org")
    login(laptop, "admin@hope.org")
    r = laptop.post("/app/account", data={"form": "password", "pw-current_password": PASSWORD,
                                          "pw-new_password": "NewPassw0rd99", "pw-confirm": "NewPassw0rd99"})
    assert r.status_code == 302
    assert laptop.get("/app/").status_code == 200  # this device stays signed in
    assert phone.get("/app/").status_code == 302  # other device signed out


def test_superadmin_lands_on_platform_console(client):
    make_superadmin()
    r = login(client, "root@kayron.com")
    assert r.headers["Location"].endswith("/admin/")
    assert client.get("/admin/").status_code == 200
    assert client.get("/app/").status_code == 302  # platform admins don't enter tenant workspaces


def test_csrf_is_enforced(app):
    make_org()
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        r = app.test_client().post("/auth/login", data={"email": "admin@hope.org", "password": PASSWORD})
        assert r.status_code == 400
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
