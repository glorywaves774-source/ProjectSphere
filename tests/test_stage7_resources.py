"""Stage 7: allocation, timesheets & approval, workload and performance."""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.extensions import db
from app.models import Department, Project, ProjectMember, Task, TimeEntry, Timesheet, User, week_start
from app.tenancy import tenant_scope

from .conftest import add_member, login, make_org

T = date.today()
WEEK = week_start(T)


@pytest.fixture
def world(client, app):
    org_id, _ = make_org(plan="professional")
    login(client, "admin@hope.org")
    client.post("/app/projects/new", data={"name": "Clean Water", "category_id": 0, "department_id": 0,
                                           "manager_id": 0, "status": "active", "priority": "high", "currency": "GHS",
                                           "start_date": (T - timedelta(days=30)).isoformat(),
                                           "end_date": (T + timedelta(days=60)).isoformat()})
    with tenant_scope(org_id):
        pid = db.session.scalar(select(Project.id))
    kofi = add_member(org_id, "kofi@hope.org", "staff", name="Kofi Boateng")
    client.post(f"/app/projects/{pid}/tasks/new", data={"title": "Drill site A", "parent_id": 0, "assignee_id": kofi,
                                                        "priority": "medium", "estimate_hours": "20",
                                                        "start_date": T.isoformat(),
                                                        "due_date": (T + timedelta(days=10)).isoformat()})
    with tenant_scope(org_id):
        tid = db.session.scalar(select(Task.id))
    k = app.test_client()
    login(k, "kofi@hope.org")
    return {"org": org_id, "pid": pid, "tid": tid, "kofi": kofi, "k": k}


def grid(rows, **extra):
    data = dict(extra)
    for i, (target, hours, note) in enumerate(rows):
        data[f"row-{i}-target"] = target
        data[f"row-{i}-note"] = note
        for d, h in enumerate(hours):
            data[f"row-{i}-h{d}"] = h
    return data


def sheet(org, user_id) -> Timesheet:
    with tenant_scope(org):
        db.session.expire_all()
        ts = db.session.scalar(select(Timesheet).where(Timesheet.user_id == user_id))
        if ts:
            _ = list(ts.entries), ts.total_hours
            db.session.expunge(ts)
        return ts


# ---------------------------------------------------------------- timesheets
def test_log_save_and_submit_week(world):
    w = world
    r = w["k"].post(f"/app/timesheet?week={WEEK}", data=grid([
        (f"t:{w['tid']}", ["8", "7.5", "", "", "", "", ""], "Drilling"),
        (f"p:{w['pid']}", ["", "", "2.25", "", "", "", ""], "Planning meeting"),
        ("", ["", "", "", "", "", "", ""], ""),
    ], action="save"))
    assert r.status_code == 302
    ts = sheet(w["org"], w["kofi"])
    assert ts.status == "draft" and ts.total_hours == Decimal("17.75") and len(ts.entries) == 3
    page = w["k"].get(f"/app/timesheet?week={WEEK}").get_data(as_text=True)
    assert 'value="7.5"' in page and "Planning meeting" in page
    w["k"].post(f"/app/timesheet?week={WEEK}", data=grid([(f"t:{w['tid']}", ["8", "", "", "", "", "", ""], "")],
                                                         action="submit"))
    ts = sheet(w["org"], w["kofi"])
    assert ts.status == "submitted" and ts.total_hours == Decimal("8.00")
    # locked once submitted
    r = w["k"].post(f"/app/timesheet?week={WEEK}", data=grid([(f"t:{w['tid']}", ["1"] * 7, "")], action="save"),
                    follow_redirects=True)
    assert b"can&#39;t be changed" in r.data and sheet(w["org"], w["kofi"]).total_hours == Decimal("8.00")


@pytest.mark.parametrize("hours,msg", [(["25"], b"between 0 and 24"), (["1.3"], b"quarter-hour"),
                                       (["abc"], b"as numbers"), (["-2"], b"between 0 and 24")])
def test_timesheet_validation(world, hours, msg):
    w = world
    r = w["k"].post(f"/app/timesheet?week={WEEK}", data=grid([(f"t:{w['tid']}", hours, "")], action="save"),
                    follow_redirects=True)
    assert msg in r.data


def test_day_over_24_and_wrong_project_refused(world, client):
    w = world
    r = w["k"].post(f"/app/timesheet?week={WEEK}", data=grid([
        (f"t:{w['tid']}", ["14"], ""), (f"p:{w['pid']}", ["12"], "")], action="save"), follow_redirects=True)
    assert b"more than 24 hours" in r.data
    # a project Kofi isn't on
    client.post("/app/projects/new", data={"name": "Other project", "category_id": 0, "department_id": 0,
                                           "manager_id": 0, "status": "active", "priority": "low", "currency": "GHS"})
    with tenant_scope(w["org"]):
        other = db.session.scalar(select(Project.id).where(Project.name == "Other project"))
    r = w["k"].post(f"/app/timesheet?week={WEEK}", data=grid([(f"p:{other}", ["2"], "")], action="save"),
                    follow_redirects=True)
    assert b"open projects you" in r.data


def test_submit_empty_week_refused(world):
    r = world["k"].post(f"/app/timesheet?week={WEEK}", data=grid([("", [""], "")], action="submit"),
                        follow_redirects=True)
    assert b"Log some hours before submitting" in r.data


def test_approval_rules(world, client, app):
    w = world
    w["k"].post(f"/app/timesheet?week={WEEK}", data=grid([(f"t:{w['tid']}", ["8"], "")], action="submit"))
    ts = sheet(w["org"], w["kofi"])
    # an unrelated staff member can't approve; the project manager (admin manages the project) can
    add_member(w["org"], "sam@hope.org", "staff", name="Sam Staff")
    s = app.test_client()
    login(s, "sam@hope.org")
    r = s.post(f"/app/timesheets/{ts.id}", data={"action": "approve"}, follow_redirects=True)
    assert r.status_code == 403 or b"can&#39;t approve" in r.data
    # Kofi can't approve his own
    r = w["k"].post(f"/app/timesheets/{ts.id}", data={"action": "approve"}, follow_redirects=True)
    assert b"can&#39;t approve" in r.data
    # returning needs a note
    assert "Kofi Boateng" in client.get("/app/timesheets/approvals").get_data(as_text=True)
    r = client.post(f"/app/timesheets/{ts.id}", data={"action": "return", "note": ""}, follow_redirects=True)
    assert b"Say what needs correcting" in r.data
    client.post(f"/app/timesheets/{ts.id}", data={"action": "return", "note": "Split by site please"})
    assert sheet(w["org"], w["kofi"]).status == "rejected"
    page = w["k"].get(f"/app/timesheet?week={WEEK}").get_data(as_text=True)
    assert "Split by site please" in page
    w["k"].post(f"/app/timesheet?week={WEEK}", data=grid([(f"t:{w['tid']}", ["8"], "Site A")], action="submit"))
    client.post(f"/app/timesheets/{ts.id}", data={"action": "approve"})
    assert sheet(w["org"], w["kofi"]).status == "approved"


def test_department_head_can_approve(world, app):
    w = world
    add_member(w["org"], "head@hope.org", "staff", name="Helen Head")
    with tenant_scope(w["org"]):
        head = db.session.scalar(select(User).where(User.email == "head@hope.org"))
        dept = Department(organization_id=w["org"], name="Field Ops", head_id=head.id)
        db.session.add(dept)
        db.session.flush()
        db.session.get(User, w["kofi"]).department_id = dept.id
        # take admin off as project manager so only the head path applies
        db.session.commit()
    w["k"].post(f"/app/timesheet?week={WEEK}", data=grid([(f"t:{w['tid']}", ["4"], "")], action="submit"))
    h = app.test_client()
    login(h, "head@hope.org")
    ts = sheet(w["org"], w["kofi"])
    assert "Kofi Boateng" in h.get("/app/timesheets/approvals").get_data(as_text=True)
    h.post(f"/app/timesheets/{ts.id}", data={"action": "approve"})
    assert sheet(w["org"], w["kofi"]).status == "approved"


# ---------------------------------------------------------------- allocation, workload, performance
def test_allocation_and_workload(world, client):
    w = world
    with tenant_scope(w["org"]):
        m = db.session.scalar(select(ProjectMember).where(ProjectMember.user_id == w["kofi"]))
        mid = m.id
    r = client.post(f"/app/projects/{w['pid']}/members/{mid}/allocation", data={"allocation": "120"},
                    follow_redirects=True)
    assert b"between 0 and 100" in r.data
    client.post(f"/app/projects/{w['pid']}/members/{mid}/allocation", data={"allocation": "60"})
    # second project allocation pushes Kofi over 100%
    client.post("/app/projects/new", data={"name": "Second", "category_id": 0, "department_id": 0,
                                           "manager_id": w["kofi"], "status": "active", "priority": "low",
                                           "currency": "GHS"})
    with tenant_scope(w["org"]):
        m2 = db.session.scalar(select(ProjectMember).join(Project).where(Project.name == "Second"))
        m2id, p2 = m2.id, m2.project_id
    client.post(f"/app/projects/{p2}/members/{m2id}/allocation", data={"allocation": "50"})
    html = client.get("/app/workload").get_data(as_text=True)
    assert "Kofi Boateng" in html and "110%" in html and "over-row" in html
    # the staff member can't change allocations or see workload
    assert w["k"].post(f"/app/projects/{w['pid']}/members/{mid}/allocation", data={"allocation": "0"},
                       follow_redirects=True).status_code == 200
    with tenant_scope(w["org"]):
        assert db.session.get(ProjectMember, mid).allocation_pct == 60
    assert w["k"].get("/app/workload").status_code == 403


def test_workload_planned_hours_and_task_actuals(world, client):
    from app.services.resources import workload

    w = world
    w["k"].post(f"/app/timesheet?week={WEEK}", data=grid([(f"t:{w['tid']}", ["6"], "")], action="save"))
    with tenant_scope(w["org"]):
        row = next(r for r in workload() if r["user"].id == w["kofi"])
    assert row["planned_hours"] == Decimal("20.0") and row["capacity"] == Decimal("160") and row["load"] == 12
    assert row["open_tasks"] == 1
    page = client.get(f"/app/tasks/{w['tid']}").get_data(as_text=True)
    assert "6 h logged of 20 h estimated" in page


def test_performance_counts(world, client):
    w = world
    w["k"].post(f"/app/tasks/{w['tid']}", data={"action": "status", "s-status": "done", "s-progress": "", "s-note": ""})
    w["k"].post(f"/app/timesheet?week={WEEK}", data=grid([(f"t:{w['tid']}", ["5"], "")], action="save"))
    html = client.get("/app/performance?days=30").get_data(as_text=True)
    assert "Kofi Boateng" in html and "100%" in html
    assert client.get("/app/performance?days=7").status_code == 200  # falls back to 90


def test_weekly_hours_on_member(world, client):
    w = world
    client.post(f"/app/team/{w['kofi']}", data={"full_name": "Kofi Boateng", "department_id": 0, "employment_type": "",
                                                "weekly_hours": "20", "roles": [_role(w["org"], "staff")]})
    with tenant_scope(w["org"]):
        assert db.session.get(User, w["kofi"]).weekly_hours == Decimal("20.0")
    assert "of 80 h capacity" in client.get(f"/app/team/{w['kofi']}").get_data(as_text=True)


def _role(org, key):
    from app.models import Role

    with tenant_scope(org):
        return db.session.scalar(select(Role.id).where(Role.key == key))


def test_resources_isolated(world, app):
    w = world
    w["k"].post(f"/app/timesheet?week={WEEK}", data=grid([(f"t:{w['tid']}", ["3"], "")], action="submit"))
    ts = sheet(w["org"], w["kofi"])
    other, _ = make_org("Other", "x@other.org")
    c = app.test_client()
    login(c, "x@other.org")
    assert c.get(f"/app/timesheets/{ts.id}").status_code == 404
    assert "Kofi" not in c.get("/app/workload").get_data(as_text=True)
    r = c.post(f"/app/timesheet?week={WEEK}", data=grid([(f"t:{w['tid']}", ["3"], "")], action="save"),
               follow_redirects=True)
    assert b"open projects you" in r.data
    with tenant_scope(other):
        for t in ("timesheets", "time_entries"):
            assert db.session.execute(text(f"SELECT count(*) FROM {t}")).scalar() == 0


def test_stage7_pages_render(world, client):
    w = world
    w["k"].post(f"/app/timesheet?week={WEEK}", data=grid([(f"t:{w['tid']}", ["3"], "")], action="submit"))
    ts = sheet(w["org"], w["kofi"])
    for url in ["/app/timesheet", f"/app/timesheet?week={WEEK - timedelta(days=7)}", "/app/timesheet?week=bad",
                "/app/timesheets/approvals", f"/app/timesheets/{ts.id}", "/app/workload", "/app/performance",
                f"/app/projects/{w['pid']}", f"/app/team/{w['kofi']}"]:
        assert client.get(url).status_code == 200, url
    assert w["k"].get("/app/timesheet").status_code == 200


def test_stage7_tables_have_rls():
    n = db.session.execute(text("SELECT count(*) FROM pg_class WHERE relrowsecurity AND relforcerowsecurity "
                                "AND relname IN ('timesheets','time_entries')")).scalar()
    assert n == 2
