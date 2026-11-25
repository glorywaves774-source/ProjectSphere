"""Stage 5: tasks, work breakdown, dependencies, critical path, approvals, Gantt and calendar."""
import json
import re
from datetime import date, timedelta

import pytest
from flask import current_app
from sqlalchemy import select, text

from app.extensions import db
from app.models import Project, Task, TaskDependency
from app.services.tasks import critical_path, wbs
from app.tenancy import tenant_scope

from .conftest import add_member, login, make_org

T = date.today()


def d(n):
    return (T + timedelta(days=n)).isoformat()


@pytest.fixture
def setup(client):
    org_id, _ = make_org(plan="professional")
    login(client, "admin@hope.org")
    client.post("/app/projects/new", data={"name": "Clean Water", "category_id": 0, "department_id": 0,
                                           "manager_id": 0, "status": "active", "priority": "high",
                                           "start_date": d(0), "end_date": d(60), "currency": "GHS"})
    with tenant_scope(org_id):
        pid = db.session.scalar(select(Project.id))
    return org_id, pid


def add_task(client, pid, title, **kw):
    data = {"title": title, "parent_id": 0, "assignee_id": 0, "priority": "medium"}
    data.update(kw)
    return client.post(f"/app/projects/{pid}/tasks/new", data=data)


def task(org, title) -> Task:
    with tenant_scope(org):
        db.session.expire_all()
        t = db.session.scalar(select(Task).where(Task.title == title))
        if t:
            _ = t.children, t.project, t.comments
            db.session.expunge(t)  # a detached snapshot: later requests can't expire it
        return t


def project(org) -> Project:
    with tenant_scope(org):
        db.session.expire_all()
        return db.session.scalar(select(Project))


def status(client, t, s, progress="", note=""):
    return client.post(f"/app/tasks/{t.id}", data={"action": "status", "s-status": s, "s-progress": progress,
                                                    "s-note": note}, follow_redirects=True)


# ---------------------------------------------------------------- create & WBS
def test_tasks_subtasks_and_wbs_numbers(client, setup):
    org, pid = setup
    assert add_task(client, pid, "Site survey", start_date=d(0), due_date=d(5)).status_code == 302
    add_task(client, pid, "Drilling", start_date=d(6), due_date=d(30))
    parent = task(org, "Drilling")
    add_task(client, pid, "Community A", parent_id=parent.id, start_date=d(6), due_date=d(15))
    add_task(client, pid, "Community B", parent_id=parent.id, start_date=d(16), due_date=d(30))
    add_task(client, pid, "Handover report", is_milestone="y", due_date=d(31))
    with tenant_scope(org):
        rows = wbs(db.session.scalars(select(Task)).all())
    assert [(t.title, code) for t, code, _ in rows] == [
        ("Site survey", "1"), ("Drilling", "2"), ("Community A", "2.1"), ("Community B", "2.2"), ("Handover report", "3")]
    ms = task(org, "Handover report")
    assert ms.is_milestone and ms.start_date == ms.due_date == T + timedelta(days=31)
    page = client.get(f"/app/projects/{pid}/tasks").get_data(as_text=True)
    assert "2.1" in page and "Community B" in page


def test_task_validation(client, setup):
    org, pid = setup
    r = add_task(client, pid, "Backwards", start_date=d(5), due_date=d(1))
    assert b"due date can" in r.data and task(org, "Backwards") is None
    add_task(client, pid, "Parent")
    p = task(org, "Parent")
    add_task(client, pid, "Child", parent_id=p.id)
    c = task(org, "Child")
    r = client.post(f"/app/tasks/{p.id}/edit", data={"title": "Parent", "parent_id": c.id, "assignee_id": 0,
                                                     "priority": "medium"})
    assert r.status_code == 200 and task(org, "Parent").parent_id is None  # child isn't offered / refused


def test_assigning_adds_person_to_project_team(client, setup):
    org, pid = setup
    kofi = add_member(org, "kofi@hope.org", "staff", name="Kofi Boateng")
    add_task(client, pid, "Drive rig", assignee_id=kofi)
    with tenant_scope(org):
        p = db.session.get(Project, pid)
        assert kofi in [m.user_id for m in p.members]


# ---------------------------------------------------------------- progress
def test_project_progress_follows_tasks(client, setup):
    org, pid = setup
    add_task(client, pid, "Small", estimate_hours="10")
    add_task(client, pid, "Big", estimate_hours="30")
    status(client, task(org, "Small"), "done")
    assert project(org).progress == 25
    status(client, task(org, "Big"), "in_progress", "50")
    assert project(org).progress == 62  # (10*100 + 30*50) / 40 = 62.5 → 62
    # manual progress on project updates is ignored once tasks exist
    client.post(f"/app/projects/{pid}/updates", data={"u-body": "x", "u-status": "active", "u-health": "on_track",
                                                      "u-progress": "5"})
    assert project(org).progress == 62


def test_parent_progress_and_completion_rules(client, setup):
    org, pid = setup
    add_task(client, pid, "Phase 1")
    p = task(org, "Phase 1")
    add_task(client, pid, "Part A", parent_id=p.id)
    add_task(client, pid, "Part B", parent_id=p.id)
    r = status(client, task(org, "Phase 1"), "done")
    assert b"2 sub-task(s) are still open" in r.data
    status(client, task(org, "Part A"), "done")
    assert task(org, "Phase 1").progress == 50
    status(client, task(org, "Part B"), "done")
    assert status(client, task(org, "Phase 1"), "done").status_code == 200
    assert task(org, "Phase 1").is_done and project(org).progress == 100


# ---------------------------------------------------------------- dependencies & critical path
def test_dependencies_block_completion_and_prevent_loops(client, setup):
    org, pid = setup
    for name in ("Survey", "Drill", "Test water"):
        add_task(client, pid, name)
    survey, drill, test = (task(org, n) for n in ("Survey", "Drill", "Test water"))
    client.post(f"/app/tasks/{drill.id}", data={"action": "dependency", "d-predecessor_id": survey.id})
    client.post(f"/app/tasks/{test.id}", data={"action": "dependency", "d-predecessor_id": drill.id})
    r = status(client, task(org, "Drill"), "done")
    assert b"Finish the tasks this one depends on first" in r.data and not task(org, "Drill").is_done
    # loop refused
    r = client.post(f"/app/tasks/{survey.id}", data={"action": "dependency", "d-predecessor_id": test.id},
                    follow_redirects=True)
    assert b"would create a loop" in r.data
    # self-dependency and duplicates refused at service level
    from app.services.tasks import TaskError, add_dependency

    with tenant_scope(org):
        from app.models import User

        admin = db.session.scalar(select(User))
        t = db.session.get(Task, drill.id)
        _ = admin.roles, t.project.members
        with pytest.raises(TaskError):
            add_dependency(admin, t, drill.id)
        with pytest.raises(TaskError):
            add_dependency(admin, t, survey.id)
        db.session.rollback()
    status(client, task(org, "Survey"), "done")
    status(client, task(org, "Drill"), "done")
    assert task(org, "Drill").is_done


def test_critical_path_algorithm():
    def mk(i, start, end, milestone=False):
        t = Task(id=i, title=str(i), start_date=T + timedelta(days=start), due_date=T + timedelta(days=end),
                 is_milestone=milestone, status="todo")
        return t

    a, b, c, e = mk(1, 0, 4), mk(2, 5, 14), mk(3, 5, 7), mk(4, 15, 15, True)  # A(5d) → B(10d) / C(3d) → E
    deps = [TaskDependency(predecessor_id=1, successor_id=2), TaskDependency(predecessor_id=1, successor_id=3),
            TaskDependency(predecessor_id=2, successor_id=4), TaskDependency(predecessor_id=3, successor_id=4)]
    assert critical_path([a, b, c, e], deps) == {1, 2, 4}
    assert critical_path([], []) == set()


def test_gantt_page_and_data(client, setup):
    org, pid = setup
    add_task(client, pid, "Survey", start_date=d(0), due_date=d(4))
    add_task(client, pid, "Drill", start_date=d(5), due_date=d(20))
    add_task(client, pid, "Report", is_milestone="y", due_date=d(21))
    survey, drill = task(org, "Survey"), task(org, "Drill")
    client.post(f"/app/tasks/{drill.id}", data={"action": "dependency", "d-predecessor_id": survey.id})
    html = client.get(f"/app/projects/{pid}/gantt").get_data(as_text=True)
    data = json.loads(re.search(r'<script type="application/json" id="gantt-data">(.*?)</script>', html, re.S).group(1))
    assert [r["title"] for r in data["rows"]] == ["Survey", "Drill", "Report"]
    assert data["links"] == [{"from": survey.id, "to": drill.id}]
    assert all(r["critical"] for r in data["rows"][:2]) and data["rows"][2]["milestone"]
    assert "gantt.js" in html


# ---------------------------------------------------------------- permissions & approvals
def test_assignee_can_update_but_not_edit(client, setup, app):
    org, pid = setup
    kofi = add_member(org, "kofi@hope.org", "staff", name="Kofi")
    add_task(client, pid, "Mine", assignee_id=kofi)
    add_task(client, pid, "Not mine")
    c = app.test_client()
    login(c, "kofi@hope.org")
    mine, other = task(org, "Mine"), task(org, "Not mine")
    status(c, mine, "in_progress", "40")
    assert task(org, "Mine").progress == 40
    r = status(c, other, "done")
    assert b"Only the person assigned" in r.data and not task(org, "Not mine").is_done
    assert c.get(f"/app/tasks/{mine.id}/edit").status_code == 403
    assert c.get(f"/app/projects/{pid}/tasks/new").status_code == 403
    # comments allowed on own task (and as project member)
    c.post(f"/app/tasks/{other.id}", data={"action": "comment", "c-body": "Can I help?"})
    assert len(task(org, "Not mine").comments) == 1


def test_approval_workflow(client, setup, app):
    org, pid = setup
    kofi = add_member(org, "kofi@hope.org", "staff", name="Kofi")
    add_task(client, pid, "Water quality test", assignee_id=kofi, requires_approval="y")
    t = task(org, "Water quality test")
    c = app.test_client()
    login(c, "kofi@hope.org")
    r = status(c, t, "done")
    assert b"Sent for approval" in r.data and task(org, "Water quality test").status == "review"
    # approver sees it in To approve
    assert "Water quality test" in client.get("/app/tasks?tab=review").get_data(as_text=True)
    # sending back needs a note
    r = client.post(f"/app/tasks/{t.id}", data={"action": "return", "r-note": ""}, follow_redirects=True)
    assert b"Say what needs to change" in r.data
    client.post(f"/app/tasks/{t.id}", data={"action": "return", "r-note": "Attach lab results"})
    assert task(org, "Water quality test").status == "in_progress"
    status(c, t, "done")
    client.post(f"/app/tasks/{t.id}", data={"action": "approve", "r-note": "Looks good"})
    done = task(org, "Water quality test")
    assert done.is_done and done.approved_by_id is not None and done.progress == 100
    # staff can't approve
    assert c.post(f"/app/tasks/{t.id}", data={"action": "approve"}, follow_redirects=True).status_code == 200


def test_closed_project_tasks_are_read_only(client, setup):
    org, pid = setup
    add_task(client, pid, "Last bit")
    client.post(f"/app/projects/{pid}/close", data={"outcome": "cancelled", "summary": "Funding withdrawn by donor."})
    r = add_task(client, pid, "Too late")
    assert task(org, "Too late") is None
    r = status(client, task(org, "Last bit"), "done")
    assert b"project is closed" in r.data


def test_tasks_isolated_between_organizations(client, setup, app):
    org, pid = setup
    add_task(client, pid, "Secret task")
    t = task(org, "Secret task")
    other, _ = make_org("Other", "x@other.org")
    c = app.test_client()
    login(c, "x@other.org")
    assert c.get(f"/app/tasks/{t.id}").status_code == 404
    assert c.post(f"/app/tasks/{t.id}", data={"action": "comment", "c-body": "hi"}).status_code == 404
    assert c.get(f"/app/projects/{pid}/gantt").status_code == 404
    assert "Secret task" not in c.get("/app/calendar").get_data(as_text=True)
    with tenant_scope(other):
        for table in ("tasks", "task_dependencies", "task_comments"):
            assert db.session.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0


# ---------------------------------------------------------------- My tasks & calendar
def test_my_tasks_tabs_and_board(client, setup):
    org, pid = setup
    me = add_member(org, "me@hope.org", "project_manager", name="Me")
    add_task(client, pid, "Late thing", assignee_id=me, start_date=d(-10), due_date=d(-2))
    add_task(client, pid, "Future thing", assignee_id=me, due_date=d(10))
    add_task(client, pid, "Someone else's")
    c = current_app.test_client()
    login(c, "me@hope.org")
    html = c.get("/app/tasks").get_data(as_text=True)
    assert "Late thing" in html and "Future thing" in html and "Someone else" not in html
    html = c.get("/app/tasks?tab=overdue").get_data(as_text=True)
    assert "Late thing" in html and "Future thing" not in html
    assert "board-card" in c.get("/app/tasks?view=board").get_data(as_text=True)


def test_calendar_shows_deadlines(client, setup):
    org, pid = setup
    add_task(client, pid, "Calendar task", due_date=T.isoformat())
    add_task(client, pid, "Funder report", is_milestone="y", due_date=T.isoformat())
    html = client.get(f"/app/calendar?month={T:%Y-%m}").get_data(as_text=True)
    assert "Calendar task" in html and "Funder report" in html
    assert client.get("/app/calendar?month=garbage").status_code == 200
    end = project(org).end_date
    html = client.get(f"/app/calendar?month={end:%Y-%m}&project={pid}").get_data(as_text=True)
    assert "Clean Water ends" in html


def test_delete_task(client, setup):
    org, pid = setup
    add_task(client, pid, "Temporary", estimate_hours="5")
    add_task(client, pid, "Keeper")
    status(client, task(org, "Keeper"), "done")
    client.post(f"/app/tasks/{task(org, 'Temporary').id}", data={"action": "delete"})
    assert task(org, "Temporary") is None and project(org).progress == 100


def test_stage5_pages_render(client, setup):
    org, pid = setup
    add_task(client, pid, "One", start_date=d(0), due_date=d(3))
    t = task(org, "One")
    for url in [f"/app/projects/{pid}/tasks", f"/app/projects/{pid}/gantt", f"/app/projects/{pid}/tasks/new",
                f"/app/tasks/{t.id}", f"/app/tasks/{t.id}/edit", "/app/tasks", "/app/tasks?view=board",
                "/app/tasks?tab=review", "/app/calendar"]:
        assert client.get(url).status_code == 200, url


def test_stage5_tables_have_rls():
    n = db.session.execute(text("SELECT count(*) FROM pg_class WHERE relrowsecurity AND relforcerowsecurity "
                                "AND relname IN ('tasks','task_dependencies','task_comments')")).scalar()
    assert n == 3
