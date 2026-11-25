"""Stage 3: organization management — staff, invitations, departments, roles."""
import re
from datetime import timedelta

import pytest
from flask import current_app
from sqlalchemy import func, select, text

from app.extensions import db
from app.models import Department, Invitation, Role, User, utcnow
from app.tenancy import tenant_scope

from .conftest import add_member, login, make_org


def role_id(org_id, key):
    with tenant_scope(org_id):
        return db.session.scalar(select(Role.id).where(Role.key == key))


def user_id(email):
    with tenant_scope(platform=True):
        return db.session.scalar(select(User.id).where(User.email == email))


def get_user(email):
    with tenant_scope(platform=True):
        db.session.expire_all()
        return db.session.scalar(select(User).where(User.email == email))


def secret(html):
    m = re.search(r'<code class="secret"[^>]*>([^<]+)</code>', html)
    return m and m.group(1)


def invite_link(html):
    m = re.search(r'value="(http://localhost/invite/[^"]+)"', html)
    return m and m.group(1).replace("http://localhost", "")


@pytest.fixture
def org(client):
    org_id, _ = make_org(plan="professional")
    login(client, "admin@hope.org")
    return org_id


# ---------------------------------------------------------------- departments
def test_department_lifecycle(client, org):
    r = client.post("/app/departments/new", data={"name": "Programmes", "code": "prog", "head_id": "0",
                                                 "description": "Field programmes"})
    assert r.status_code == 302
    with tenant_scope(org):
        dept = db.session.scalar(select(Department).where(Department.name == "Programmes"))
        assert dept.code == "PROG"
        dept_id = dept.id
    # duplicate names refused
    r = client.post("/app/departments/new", data={"name": "programmes", "head_id": "0"})
    assert b"already exists" in r.data
    # set head, assign someone, then delete keeps the person
    kofi = add_member(org, "kofi@hope.org")
    client.post(f"/app/departments/{dept_id}", data={"name": "Programmes", "code": "PROG",
                                                    "head_id": str(user_id("admin@hope.org"))})
    with tenant_scope(org):
        db.session.execute(text("UPDATE users SET department_id = :d WHERE id = :u"), {"d": dept_id, "u": kofi})
        db.session.commit()
    page = client.get(f"/app/departments/{dept_id}").get_data(as_text=True)
    assert "Kofi Boateng" in page
    assert "1 person" in client.get("/app/departments").get_data(as_text=True)
    client.post(f"/app/departments/{dept_id}", data={"action": "delete"})
    assert get_user("kofi@hope.org").department_id is None


def test_departments_are_isolated(client, org):
    client.post("/app/departments/new", data={"name": "Finance", "head_id": "0"})
    with tenant_scope(org):
        dept_id = db.session.scalar(select(Department.id))
    other, _ = make_org("Other", "x@other.org")
    c2 = current_app.test_client()
    login(c2, "x@other.org")
    assert c2.get(f"/app/departments/{dept_id}").status_code == 404
    assert 'translate="no">Finance<' not in c2.get("/app/departments").get_data(as_text=True)
    with tenant_scope(other):
        assert db.session.execute(text("SELECT count(*) FROM departments")).scalar() == 0


def test_staff_member_cannot_manage_departments(client, org):
    add_member(org, "kofi@hope.org", "staff")
    c = current_app.test_client()
    login(c, "kofi@hope.org")
    assert c.get("/app/departments").status_code == 200  # can view
    assert c.get("/app/departments/new").status_code == 403


# ---------------------------------------------------------------- add staff
def test_add_staff_with_profile_and_one_time_password(client, org):
    client.post("/app/departments/new", data={"name": "Finance", "head_id": "0"})
    with tenant_scope(org):
        dept_id = db.session.scalar(select(Department.id))
    r = client.post("/app/team/new", data={
        "full_name": "Abena Owusu", "email": "Abena@Hope.org", "job_title": "Accountant", "department_id": dept_id,
        "phone": "0244000000", "staff_number": "HF-014", "employment_type": "full_time", "start_date": "2026-01-05",
        "roles": [role_id(org, "finance_officer")]})
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    temp = secret(r.get_data(as_text=True))
    u = get_user("abena@hope.org")
    assert (u.staff_number, u.department_id, u.employment_type, str(u.start_date)) == ("HF-014", dept_id, "full_time", "2026-01-05")
    assert u.role_names == ["Finance Officer"] and u.must_change_password
    c = current_app.test_client()
    login(c, "abena@hope.org", temp)
    assert c.get("/app/").headers["Location"].endswith("/app/account")


def test_add_staff_rejects_duplicate_email_and_no_roles(client, org):
    r = client.post("/app/team/new", data={"full_name": "Dup", "email": "admin@hope.org", "department_id": 0,
                                           "roles": [role_id(org, "staff")]})
    assert b"already uses this email" in r.data
    r = client.post("/app/team/new", data={"full_name": "No Role", "email": "n@hope.org", "department_id": 0})
    assert b"at least one role" in r.data


def test_plan_limit_counts_members_and_invitations(client):
    org_id, _ = make_org("Small", "s@small.org", plan="starter")  # 5 users
    login(client, "s@small.org")
    staff = role_id(org_id, "staff")
    for i in range(3):
        add_member(org_id, f"m{i}@small.org")
    r = client.post("/app/team/invite", data={"email": "fifth@small.org", "department_id": 0, "roles": [staff]})
    assert r.status_code == 200 and b"Invitation ready" in r.data  # 4 members + 1 invite = 5
    r = client.post("/app/team/new", data={"full_name": "Sixth", "email": "six@small.org", "department_id": 0,
                                           "roles": [staff]})
    assert b"Your plan allows 5" in r.data and b"Pending invitations count" in r.data


# ---------------------------------------------------------------- invitations
def test_invitation_flow_without_mail_server(client, org, app):
    me_role = role_id(org, "me_officer")
    r = client.post("/app/team/invite", data={"email": "yaw@hope.org", "full_name": "Yaw Darko",
                                              "job_title": "M&E Lead", "department_id": 0, "roles": [me_role]})
    link = invite_link(r.get_data(as_text=True))
    assert link and r.headers["Cache-Control"] == "no-store"
    with tenant_scope(org):
        inv = db.session.scalar(select(Invitation))
        assert link.rsplit("/", 1)[1] not in inv.token_hash  # only the hash is stored
    assert len(app.extensions["mail_outbox"]) >= 1  # message prepared, not sent

    visitor = app.test_client()
    page = visitor.get(link)
    assert page.status_code == 200 and b"Join" in page.data and page.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    r = visitor.post(link, data={"full_name": "Yaw Darko", "password": "YawPassw0rd!", "confirm": "YawPassw0rd!"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/app/")
    assert visitor.get("/app/").status_code == 200
    u = get_user("yaw@hope.org")
    assert u.role_names == ["M&E Officer"] and u.job_title == "M&E Lead" and not u.must_change_password
    # link can't be reused
    assert app.test_client().get(link).status_code == 410


def test_invitation_email_sent_when_mail_configured(client, org, app, monkeypatch):
    from app.services import mailer

    sent = []
    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    monkeypatch.setattr(mailer, "send", lambda to, subject, body, html=None: sent.append((to, subject, body)) or True)
    r = client.post("/app/team/invite", data={"email": "esi@hope.org", "department_id": 0,
                                              "roles": [role_id(org, "staff")]})
    assert r.status_code == 302
    to, subject, body = sent[0]
    assert to == "esi@hope.org" and "Hope Foundation" in subject and "/invite/" in body


def test_resend_revoke_and_expiry(client, org, app):
    staff = role_id(org, "staff")
    r = client.post("/app/team/invite", data={"email": "kwame@hope.org", "department_id": 0, "roles": [staff]})
    first = invite_link(r.get_data(as_text=True))
    # a second invite for the same email is refused
    r = client.post("/app/team/invite", data={"email": "kwame@hope.org", "department_id": 0, "roles": [staff]})
    assert b"already has a pending invitation" in r.data
    with tenant_scope(org):
        inv_id = db.session.scalar(select(Invitation.id))
    r = client.post(f"/app/team/invitations/{inv_id}/resend")
    second = invite_link(r.get_data(as_text=True))
    assert second != first
    assert app.test_client().get(first).status_code == 404  # old link dead
    assert app.test_client().get(second).status_code == 200
    client.post(f"/app/team/invitations/{inv_id}/revoke")
    assert app.test_client().get(second).status_code == 410
    # expiry
    r = client.post("/app/team/invite", data={"email": "late@hope.org", "department_id": 0, "roles": [staff]})
    late = invite_link(r.get_data(as_text=True))
    with tenant_scope(org):
        db.session.execute(text("UPDATE invitations SET expires_at = :t WHERE email = 'late@hope.org'"),
                           {"t": utcnow() - timedelta(minutes=1)})
        db.session.commit()
    resp = app.test_client().get(late)
    assert resp.status_code == 410 and b"expired" in resp.data


def test_bad_invite_tokens(app):
    c = app.test_client()
    assert c.get("/invite/nonsense").status_code == 404
    assert c.get("/invite/" + "x" * 200).status_code == 404


def test_invitation_cannot_be_seen_by_other_org(client, org):
    client.post("/app/team/invite", data={"email": "kwame@hope.org", "department_id": 0,
                                          "roles": [role_id(org, "staff")]})
    other, _ = make_org("Other", "x@other.org")
    with tenant_scope(other):
        assert db.session.scalar(select(func.count(Invitation.id))) == 0
        assert db.session.execute(text("SELECT count(*) FROM invitations")).scalar() == 0


# ---------------------------------------------------------------- editing members & escalation
def test_edit_member_details_and_roles(client, org):
    kofi = add_member(org, "kofi@hope.org", "staff")
    r = client.post(f"/app/team/{kofi}", data={
        "full_name": "Kofi Boateng", "job_title": "Driver", "department_id": 0, "phone": "", "staff_number": "HF-2",
        "employment_type": "contract", "start_date": "", "roles": [role_id(org, "field_officer"), role_id(org, "staff")]})
    assert r.status_code == 302
    u = get_user("kofi@hope.org")
    assert u.job_title == "Driver" and sorted(u.role_names) == ["Field Officer", "Staff Member"]


def test_hr_officer_cannot_escalate(client, org, app):
    hr = add_member(org, "hr@hope.org", "hr_officer", name="Harriet HR")
    kofi = add_member(org, "kofi@hope.org", "staff")
    c = app.test_client()
    login(c, "hr@hope.org")
    admin_role = role_id(org, "org_admin")
    # can't make themselves admin
    base = {"full_name": "Harriet HR", "department_id": 0, "employment_type": ""}
    r = c.post(f"/app/team/{hr}", data=dict(base, roles=[admin_role]), follow_redirects=True)
    assert b"more access than you have" in r.data
    assert get_user("hr@hope.org").role_names == ["HR Officer"]
    # can't make anyone else admin, nor invite an admin
    r = c.post(f"/app/team/{kofi}", data=dict(base, full_name="Kofi", roles=[admin_role]), follow_redirects=True)
    assert b"more access than you have" in r.data
    r = c.post("/app/team/invite", data={"email": "boss@x.org", "department_id": 0, "roles": [admin_role]})
    assert b"more access than you have" in r.data
    # can't touch the admin
    admin_id = user_id("admin@hope.org")
    r = c.post(f"/app/team/{admin_id}/deactivate", follow_redirects=True)
    assert b"access you don" in r.data
    r = c.post(f"/app/team/{admin_id}/reset", follow_redirects=True)
    assert b"access you don" in r.data and get_user("admin@hope.org").is_active_user
    # admin role isn't offered as a choice
    assert b"Organization Admin</span>" not in c.get("/app/team/invite").data
    # but HR can manage ordinary staff
    r = c.post(f"/app/team/{kofi}/deactivate")
    assert r.status_code == 302 and not get_user("kofi@hope.org").is_active_user


def test_last_admin_protection_and_self_rules(client, org):
    me = user_id("admin@hope.org")
    r = client.post(f"/app/team/{me}", data={"full_name": "Ama", "department_id": 0, "employment_type": "",
                                            "roles": [role_id(org, "staff")]}, follow_redirects=True)
    assert b"only active Organization Admin" in r.data
    r = client.post(f"/app/team/{me}/deactivate", follow_redirects=True)
    assert b"your own account" in r.data
    # with a second admin, the first can step down
    add_member(org, "second@hope.org", "org_admin", name="Second Admin")
    r = client.post(f"/app/team/{me}", data={"full_name": "Ama", "department_id": 0, "employment_type": "",
                                            "roles": [role_id(org, "staff")]})
    assert get_user("admin@hope.org").role_names == ["Staff Member"]
    assert client.get("/app/team/new").status_code == 403  # access dropped on the very next page


def test_deactivated_member_is_signed_out_and_reset_works(client, org, app):
    kofi = add_member(org, "kofi@hope.org")
    c = app.test_client()
    login(c, "kofi@hope.org")
    client.post(f"/app/team/{kofi}/deactivate")
    assert c.get("/app/").status_code == 302
    client.post(f"/app/team/{kofi}/activate")
    r = client.post(f"/app/team/{kofi}/reset")
    temp = secret(r.get_data(as_text=True))
    login(c, "kofi@hope.org", temp)
    assert c.get("/app/").headers["Location"].endswith("/app/account")


def test_staff_can_view_but_not_edit_members(client, org, app):
    add_member(org, "aud@hope.org", "auditor")
    c = app.test_client()
    login(c, "aud@hope.org")
    admin_id = user_id("admin@hope.org")
    assert c.get(f"/app/team/{admin_id}").status_code == 200
    assert c.post(f"/app/team/{admin_id}", data={"full_name": "X"}).status_code == 403
    assert c.get("/app/team/invite").status_code == 403


def test_member_of_other_org_is_404(client, org):
    other, _ = make_org("Other", "x@other.org")
    assert client.get(f"/app/team/{user_id('x@other.org')}").status_code == 404
    assert client.post(f"/app/team/{user_id('x@other.org')}/deactivate").status_code == 404


# ---------------------------------------------------------------- roles
def test_custom_role_create_edit_delete(client, org):
    r = client.post("/app/roles/new", data={"name": "Driver", "description": "Fleet",
                                            "permissions": ["org.view", "field.report"]})
    assert r.status_code == 302
    with tenant_scope(org):
        role = db.session.scalar(select(Role).where(Role.name == "Driver"))
        assert role.permission_codes == {"org.view", "field.report"} and not role.is_system
    kofi = add_member(org, "kofi@hope.org", "staff")
    client.post(f"/app/team/{kofi}", data={"full_name": "Kofi", "department_id": 0, "employment_type": "",
                                          "roles": [role.id]})
    r = client.post(f"/app/roles/{role.id}", data={"action": "delete"}, follow_redirects=True)
    assert b"1 person has this role" in r.data
    client.post(f"/app/roles/{role.id}", data={"name": "Fleet driver", "permissions": ["org.view"]})
    with tenant_scope(org):
        db.session.expire_all()
        assert db.session.get(Role, role.id).permission_codes == {"org.view"}
    client.post(f"/app/team/{kofi}", data={"full_name": "Kofi", "department_id": 0, "employment_type": "",
                                          "roles": [role_id(org, "staff")]})
    client.post(f"/app/roles/{role.id}", data={"action": "delete"})
    with tenant_scope(org):
        assert db.session.get(Role, role.id) is None


def test_role_rules(client, org):
    admin_role = role_id(org, "org_admin")
    r = client.post(f"/app/roles/{admin_role}", data={"name": "Organization Admin", "permissions": ["org.view"]},
                    follow_redirects=True)
    assert b"always has full access" in r.data
    r = client.post(f"/app/roles/{role_id(org, 'staff')}", data={"action": "delete"}, follow_redirects=True)
    assert b"Built-in roles can" in r.data
    r = client.post("/app/roles/new", data={"name": "Staff Member", "permissions": ["org.view"]})
    assert b"already has this name" in r.data
    r = client.post("/app/roles/new", data={"name": "Empty"})
    assert b"at least one permission" in r.data
    r = client.post("/app/roles/new", data={"name": "Bad", "permissions": ["root.everything"]})
    assert b"grant permissions you don" in r.data  # unknown codes are never yours to grant
    # editing a built-in role changes access live
    add_member(org, "kofi@hope.org", "staff")
    client.post(f"/app/roles/{role_id(org, 'staff')}", data={"name": "Staff Member",
                                                            "permissions": ["org.view", "users.view"]})
    c = current_app.test_client()
    login(c, "kofi@hope.org")
    assert c.get("/app/team").status_code == 200
    # duplicate page pre-fills
    assert b"Staff Member (copy)" in client.get(f"/app/roles/new?copy={role_id(org, 'staff')}").data


def test_role_manager_cannot_grant_what_they_lack(client, org, app):
    with tenant_scope(org):
        from app.models import Permission

        role = Role(organization_id=org, key="people_lead", name="People lead", is_system=False)
        role.permissions = db.session.scalars(select(Permission).where(
            Permission.code.in_(["roles.view", "roles.manage", "users.view", "org.view"]))).all()
        db.session.add(role)
        db.session.commit()
    add_member(org, "lead@hope.org", "people_lead")
    c = app.test_client()
    login(c, "lead@hope.org")
    r = c.post("/app/roles/new", data={"name": "Sneaky", "permissions": ["org.view", "budget.approve"]})
    assert b"grant permissions you don" in r.data
    r = c.post(f"/app/roles/{role_id(org, 'staff')}", data={"name": "Staff Member",
                                                           "permissions": ["org.view", "subscription.manage"]})
    assert b"grant permissions you don" in r.data


def test_stage3_pages_render(client, org):
    kofi = add_member(org, "kofi@hope.org")
    client.post("/app/departments/new", data={"name": "Finance", "head_id": "0"})
    with tenant_scope(org):
        dept = db.session.scalar(select(Department.id))
    for url in ["/app/team", "/app/team?dept=0", "/app/team/new", "/app/team/invite", f"/app/team/{kofi}",
                "/app/departments", "/app/departments/new", f"/app/departments/{dept}", "/app/roles",
                "/app/roles/new", f"/app/roles/{role_id(org, 'staff')}"]:
        assert client.get(url).status_code == 200, url


def test_stage3_tables_have_rls():
    rows = db.session.execute(text("SELECT relname FROM pg_class WHERE relrowsecurity AND relforcerowsecurity "
                                   "AND relname IN ('departments', 'invitations')")).all()
    assert len(rows) == 2
