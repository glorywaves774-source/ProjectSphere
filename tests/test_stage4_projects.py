"""Stage 4: project registration, management, closure and portfolio."""
import re
from datetime import date, timedelta

import pytest
from flask import current_app
from sqlalchemy import select, text

from app.extensions import db
from app.models import AuditLog, Project, ProjectCategory, ProjectUpdate
from app.tenancy import tenant_scope

from .conftest import add_member, login, make_org

TODAY = date.today()


def uid(org_id, email):
    from app.models import User

    with tenant_scope(org_id):
        return db.session.scalar(select(User.id).where(User.email == email))


def cat_id(org_id, name="Health"):
    with tenant_scope(org_id):
        return db.session.scalar(select(ProjectCategory.id).where(ProjectCategory.name == name))


def new_project(client, org_id, **over):
    data = {"name": "Clean Water for Savelugu", "summary": "Boreholes for 12 communities",
            "objectives": "Drill 12 boreholes\nTrain 24 WASH committees", "category_id": cat_id(org_id, "Water & sanitation"),
            "department_id": 0, "manager_id": uid(org_id, "admin@hope.org"), "status": "active", "priority": "high",
            "start_date": (TODAY - timedelta(days=30)).isoformat(), "end_date": (TODAY + timedelta(days=60)).isoformat(),
            "budget": "250000", "currency": "GHS", "location": "Savelugu", "funder": "UNICEF"}
    data.update(over)
    return client.post("/app/projects/new", data=data)


def project(org_id, name="Clean Water for Savelugu") -> Project:
    with tenant_scope(org_id):
        db.session.expire_all()
        p = db.session.scalar(select(Project).where(Project.name == name))
        if p is not None:  # load related rows while the tenant is in scope
            _ = [m.user for m in p.members], list(p.updates)
        return p


@pytest.fixture
def org(client):
    org_id, _ = make_org(plan="professional")
    login(client, "admin@hope.org")
    return org_id


# ---------------------------------------------------------------- registration
def test_register_project_assigns_code_and_manager(client, org):
    r = new_project(client, org)
    assert r.status_code == 302
    p = project(org)
    assert p.code == f"PRJ-{TODAY.year}-0001" and p.status == "active" and float(p.budget) == 250000
    assert [m.project_role for m in p.members] == ["Project manager"]
    assert p.updates[0].body == "Project registered."
    new_project(client, org, name="Second")
    assert project(org, "Second").code.endswith("-0002")
    page = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Clean Water for Savelugu" in page and "Drill 12 boreholes" in page and "GH₵250,000" in page


def test_default_categories_exist_for_new_orgs(org):
    with tenant_scope(org):
        names = set(db.session.scalars(select(ProjectCategory.name)))
    assert {"Health", "Education", "Water & sanitation", "Other"} <= names


def test_validation(client, org):
    r = new_project(client, org, end_date=(TODAY - timedelta(days=60)).isoformat())
    assert b"end date can" in r.data
    r = new_project(client, org, budget="-5")
    assert r.status_code == 200 and project(org) is None
    r = new_project(client, org, name="ab")
    assert project(org, "ab") is None


def test_project_limit_from_plan(client):
    org_id, _ = make_org("Tiny", "t@tiny.org", plan="starter")  # 5 open projects
    login(client, "t@tiny.org")
    for i in range(5):
        assert new_project(client, org_id, name=f"Project {i}", manager_id=0).status_code == 302
    r = new_project(client, org_id, name="Project 6", manager_id=0)
    assert b"Your plan allows 5 projects" in r.data
    # closing one frees a slot; reopening needs one
    p0 = project(org_id, "Project 0")
    client.post(f"/app/projects/{p0.id}/close", data={"outcome": "completed", "summary": "Delivered all outputs."})
    assert new_project(client, org_id, name="Project 6", manager_id=0).status_code == 302
    r = client.post(f"/app/projects/{p0.id}/reopen", follow_redirects=True)
    assert b"Your plan allows 5 projects" in r.data


# ---------------------------------------------------------------- permissions
def test_access_rules(client, org, app):
    new_project(client, org)
    p = project(org)
    add_member(org, "pm@hope.org", "project_manager", name="Paul PM")
    add_member(org, "staff@hope.org", "staff", name="Sam Staff")
    add_member(org, "dir@hope.org", "project_director", name="Dina Director")
    pm, staff, director = app.test_client(), app.test_client(), app.test_client()
    login(pm, "pm@hope.org")
    login(staff, "staff@hope.org")
    login(director, "dir@hope.org")
    # staff can view but not create or edit
    assert staff.get("/app/projects").status_code == 200
    assert staff.get(f"/app/projects/{p.id}").status_code == 200
    assert staff.get("/app/projects/new").status_code == 403
    assert staff.get(f"/app/projects/{p.id}/edit").status_code == 403
    assert staff.get("/app/portfolio").status_code == 403
    # a project manager can create but can't edit someone else's project
    assert pm.get("/app/projects/new").status_code == 200
    assert pm.get(f"/app/projects/{p.id}/edit").status_code == 403
    assert pm.get(f"/app/projects/{p.id}/close").status_code == 403
    # ...until they're made its manager
    client.post(f"/app/projects/{p.id}/edit", data={"name": p.name, "category_id": 0, "department_id": 0,
                                                    "manager_id": uid(org, "pm@hope.org"), "priority": "high",
                                                    "currency": "GHS"})
    assert pm.get(f"/app/projects/{p.id}/edit").status_code == 200
    # a director (portfolio oversight) can edit anything and sees the portfolio
    assert director.get(f"/app/projects/{p.id}/edit").status_code == 200
    assert director.get("/app/portfolio").status_code == 200


def test_projects_are_isolated_between_organizations(client, org, app):
    new_project(client, org)
    p = project(org)
    other, _ = make_org("Other", "x@other.org")
    c = app.test_client()
    login(c, "x@other.org")
    assert c.get(f"/app/projects/{p.id}").status_code == 404
    assert c.post(f"/app/projects/{p.id}/updates", data={"u-body": "hi", "u-status": "active",
                                                         "u-health": "on_track"}).status_code == 404
    assert "Clean Water" not in c.get("/app/projects?status=all").get_data(as_text=True)
    assert "Clean Water" not in c.get("/app/projects/export.csv?status=all").get_data(as_text=True)
    with tenant_scope(other):
        for table in ("projects", "project_members", "project_updates"):
            assert db.session.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0
    # other org's codes start from 0001 too
    login(c, "x@other.org")
    new_project(c, other, category_id=cat_id(other, "Health"), manager_id=0)
    assert project(other).code.endswith("-0001")


def test_cannot_use_other_orgs_category_or_manager(client, org):
    other, _ = make_org("Other", "x@other.org")
    r = new_project(client, org, category_id=cat_id(other, "Health"))
    assert project(org) is None
    r = new_project(client, org, manager_id=uid(other, "x@other.org"))
    assert project(org) is None and r.status_code == 200


# ---------------------------------------------------------------- updates, team, closure
def test_updates_change_status_health_progress(client, org, app):
    new_project(client, org)
    p = project(org)
    r = client.post(f"/app/projects/{p.id}/updates", data={"u-body": "Drilling started in 4 communities.",
                                                           "u-status": "active", "u-health": "at_risk",
                                                           "u-progress": "35"})
    assert r.status_code == 302
    p = project(org)
    assert (p.health, p.progress) == ("at_risk", 35) and len(p.updates) == 2
    # team member (not manager) can post but not change status
    staff = add_member(org, "staff@hope.org", "staff", name="Sam Staff")
    client.post(f"/app/projects/{p.id}/members", data={"m-user_id": staff, "m-project_role": "Field engineer"})
    c = app.test_client()
    login(c, "staff@hope.org")
    c.post(f"/app/projects/{p.id}/updates", data={"u-body": "Site 5 done.", "u-status": "on_hold",
                                                   "u-health": "at_risk", "u-progress": "40"})
    p = project(org)
    assert p.status == "active" and len(p.updates) == 2  # refused
    c.post(f"/app/projects/{p.id}/updates", data={"u-body": "Site 5 done.", "u-status": "active",
                                                   "u-health": "at_risk", "u-progress": "40"})
    assert project(org).progress == 40
    # non-member can't post
    add_member(org, "out@hope.org", "staff", name="Out Sider")
    o = app.test_client()
    login(o, "out@hope.org")
    o.post(f"/app/projects/{p.id}/updates", data={"u-body": "x", "u-status": "active", "u-health": "on_track"})
    assert len(project(org).updates) == 3
    # empty update refused, invalid progress refused
    client.post(f"/app/projects/{p.id}/updates", data={"u-body": "", "u-status": "active", "u-health": "at_risk",
                                                       "u-progress": "40"})
    client.post(f"/app/projects/{p.id}/updates", data={"u-body": "x", "u-status": "active", "u-health": "at_risk",
                                                       "u-progress": "140"})
    assert len(project(org).updates) == 3


def test_team_management(client, org):
    new_project(client, org)
    p = project(org)
    kofi = add_member(org, "kofi@hope.org", "staff", name="Kofi Boateng")
    client.post(f"/app/projects/{p.id}/members", data={"m-user_id": kofi, "m-project_role": "Driver"})
    r = client.post(f"/app/projects/{p.id}/members", data={"m-user_id": kofi}, follow_redirects=True)
    assert b"already on this project" in r.data
    p = project(org)
    mgr = next(m for m in p.members if m.user_id == p.manager_id)
    r = client.post(f"/app/projects/{p.id}/members", data={"remove": mgr.id}, follow_redirects=True)
    assert b"different project manager" in r.data
    p = project(org)
    kofi_m = next(m for m in p.members if m.user_id == kofi)
    client.post(f"/app/projects/{p.id}/members", data={"remove": kofi_m.id})
    assert len(project(org).members) == 1


def test_close_and_reopen(client, org):
    new_project(client, org)
    p = project(org)
    r = client.post(f"/app/projects/{p.id}/close", data={"outcome": "completed", "summary": "short"})
    assert b"between 10 and 5000" in r.data or b"at least 10" in r.data
    client.post(f"/app/projects/{p.id}/close", data={"outcome": "completed", "actual_end_date": TODAY.isoformat(),
                                                     "summary": "All 12 boreholes drilled and handed over.",
                                                     "lessons": "Start community engagement earlier."})
    p = project(org)
    assert (p.status, p.progress, p.actual_end_date) == ("completed", 100, TODAY) and p.closed_at
    page = client.get(f"/app/projects/{p.id}").get_data(as_text=True)
    assert "Start community engagement earlier." in page and "Post update" not in page
    r = client.post(f"/app/projects/{p.id}/updates", data={"u-body": "late", "u-status": "active",
                                                           "u-health": "on_track"}, follow_redirects=True)
    assert b"is closed" in r.data
    client.post(f"/app/projects/{p.id}/reopen")
    p = project(org)
    assert p.status == "active" and p.closed_at is None
    with tenant_scope(org):
        actions = set(db.session.scalars(select(AuditLog.action).where(AuditLog.action.like("project.%"))))
    assert {"project.created", "project.closed", "project.reopened"} <= actions


# ---------------------------------------------------------------- list, board, export, portfolio
def test_list_filters_board_and_export(client, org):
    new_project(client, org)
    new_project(client, org, name="School Feeding", category_id=cat_id(org, "Education"), priority="low",
                status="planning", end_date=(TODAY - timedelta(days=3)).isoformat(),
                start_date=(TODAY - timedelta(days=90)).isoformat(), location="Tamale")
    html = client.get("/app/projects?q=tamale").get_data(as_text=True)
    assert "School Feeding" in html and "Clean Water" not in html
    html = client.get(f"/app/projects?category={cat_id(org, 'Education')}").get_data(as_text=True)
    assert "School Feeding" in html and "Clean Water" not in html
    html = client.get("/app/projects?delayed=1").get_data(as_text=True)
    assert "School Feeding" in html and "Clean Water" not in html and "overdue" in html
    html = client.get("/app/projects?view=board").get_data(as_text=True)
    assert "School Feeding" in html and "Planning" in html and "board-card" in html
    r = client.get("/app/projects/export.csv?sort=name")
    body = r.get_data(as_text=True)
    assert r.mimetype == "text/csv" and body.index("Clean Water") < body.index("School Feeding")
    assert "UNICEF" in body


def test_csv_blocks_formula_injection(client, org):
    new_project(client, org, name="=HYPERLINK(\"http://evil\")", funder="+cmd")
    body = client.get("/app/projects/export.csv").get_data(as_text=True)
    assert "'=HYPERLINK" in body and "'+cmd" in body


def test_portfolio_and_dashboard_use_live_data(client, org):
    new_project(client, org)
    new_project(client, org, name="Late One", end_date=(TODAY - timedelta(days=5)).isoformat(),
                start_date=(TODAY - timedelta(days=50)).isoformat(), budget="1000")
    html = client.get("/app/portfolio").get_data(as_text=True)
    assert "Late One" in html and "GH₵251,000" in html
    assert re.search(r"Delayed</div>\s*<div class=\"kpi-value\">1<", html)
    dash = client.get("/app/").get_data(as_text=True)
    assert "My projects" in dash and "Late One" in dash
    assert re.search(r"Projects</div>\s*<div class=\"kpi-value\">2<", dash)


def test_categories_management(client, org, app):
    r = client.post("/app/projects/categories", data={"name": "Climate", "color": "#22aa66"})
    assert r.status_code == 302 and cat_id(org, "Climate")
    r = client.post("/app/projects/categories", data={"name": "climate", "color": "#22aa66"})
    assert b"already exists" in r.data
    r = client.post("/app/projects/categories", data={"name": "Bad", "color": "red"})
    assert b"#6d5bf6" in r.data
    new_project(client, org, category_id=cat_id(org, "Climate"))
    client.post("/app/projects/categories", data={"delete": cat_id(org, "Climate")})
    assert project(org).category_id is None
    add_member(org, "staff@hope.org", "staff")
    c = app.test_client()
    login(c, "staff@hope.org")
    assert c.post("/app/projects/categories", data={"name": "X", "color": "#000000"}).status_code == 403


def test_usage_meter_counts_open_projects(client, org):
    new_project(client, org)
    html = client.get("/app/subscription").get_data(as_text=True)
    assert re.search(r"Open projects</span><span class=\"val\">1 / 25", html)


def test_stage4_tables_have_rls():
    rows = db.session.execute(text("SELECT count(*) FROM pg_class WHERE relrowsecurity AND relforcerowsecurity "
                                   "AND relname IN ('projects','project_members','project_updates','project_categories')")).scalar()
    assert rows == 4
