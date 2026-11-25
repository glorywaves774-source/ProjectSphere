"""Stage 7: allocation, workload, timesheets and team performance."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select

from app.extensions import db
from app.models import (OPEN_PROJECT_STATUSES, Department, Project, ProjectMember, Task, TimeEntry, Timesheet, User,
                        utcnow, week_start)

from . import audit
from .notify import notify
from . import projects as project_svc


class ResourceError(ValueError):
    pass


# ---------------------------------------------------------------- allocation
def set_allocation(actor: User, member: ProjectMember, pct: int) -> None:
    if not project_svc.can_edit(actor, member.project):
        raise ResourceError("Only the project manager can change allocations.")
    if pct is None or not 0 <= pct <= 100:
        raise ResourceError("Allocation must be between 0 and 100%.")
    member.allocation_pct = pct
    audit.record("resource.allocation", summary=f"{member.user.full_name}: {pct}% on {member.project.code}",
                 entity=member.project)


# ---------------------------------------------------------------- timesheets
def loggable_targets(user: User) -> list[tuple[Project, list[Task]]]:
    """Open projects the person works on, with their tasks, for the timesheet picker."""
    projects = db.session.scalars(
        select(Project).where(Project.status.in_(OPEN_PROJECT_STATUSES),
                              or_(Project.manager_id == user.id,
                                  Project.id.in_(select(ProjectMember.project_id)
                                                 .where(ProjectMember.user_id == user.id))))
        .order_by(Project.name)).all()
    out = []
    for p in projects:
        tasks = db.session.scalars(select(Task).where(Task.project_id == p.id, Task.is_milestone.is_(False))
                                   .order_by(Task.parent_id.nulls_first(), Task.position)).all()
        out.append((p, tasks))
    return out


def get_timesheet(user: User, week: date, create: bool = False) -> Timesheet | None:
    week = week_start(week)
    ts = db.session.scalar(select(Timesheet).where(Timesheet.user_id == user.id, Timesheet.week_start == week))
    if ts is None and create:
        ts = Timesheet(organization_id=user.organization_id, user_id=user.id, week_start=week)
        db.session.add(ts)
        db.session.flush()
    return ts


def parse_target(value: str, allowed: list[tuple[Project, list[Task]]]) -> tuple[int, int | None]:
    kind, _, ident = (value or "").partition(":")
    try:
        ident = int(ident)
    except ValueError:
        raise ResourceError("Choose a project or task for each row.") from None
    for p, tasks in allowed:
        if kind == "p" and p.id == ident:
            return p.id, None
        for t in tasks:
            if kind == "t" and t.id == ident:
                return p.id, t.id
    raise ResourceError("You can only log time on open projects you're part of.")


def save_week(actor: User, week: date, rows: list[dict]) -> Timesheet:
    """rows: [{"target": "t:12", "hours": [Decimal|None]*7, "note": str}]"""
    ts = get_timesheet(actor, week, create=True)
    if ts.is_locked:
        raise ResourceError("This week is submitted or approved and can't be changed.")
    allowed = loggable_targets(actor)
    per_day = [Decimal(0)] * 7
    new_entries = []
    for row in rows:
        hours = row.get("hours") or []
        if not any(h for h in hours if h):
            continue
        project_id, task_id = parse_target(row.get("target", ""), allowed)
        for i, h in enumerate(hours[:7]):
            if not h:
                continue
            if h < 0 or h > 24:
                raise ResourceError("Hours must be between 0 and 24.")
            if (h * 4) % 1:
                raise ResourceError("Log hours in quarter-hour steps (for example 1.25 or 7.5).")
            per_day[i] += h
            new_entries.append(TimeEntry(organization_id=actor.organization_id, user_id=actor.id,
                                         project_id=project_id, task_id=task_id,
                                         work_date=ts.week_start + timedelta(days=i), hours=h,
                                         note=(row.get("note") or "").strip()[:255]))
    if any(d > 24 for d in per_day):
        raise ResourceError("A day can't have more than 24 hours.")
    ts.entries.clear()
    db.session.flush()
    ts.entries.extend(new_entries)
    if ts.status == "rejected":
        ts.status = "draft"
    db.session.flush()
    return ts


def submit_week(actor: User, ts: Timesheet) -> None:
    if ts.user_id != actor.id:
        raise ResourceError("You can only submit your own timesheet.")
    if ts.is_locked:
        raise ResourceError("This week is already submitted.")
    if not ts.entries:
        raise ResourceError("Log some hours before submitting.")
    ts.status, ts.submitted_at = "submitted", utcnow()
    ts.decided_by_id = ts.decided_at = None
    ts.decision_note = ""
    people = db.session.scalars(select(User).where(User.is_active_user.is_(True))).all()
    notify([u for u in people if can_approve(u, ts)], "approval",
           f"{actor.full_name}'s timesheet for week of {ts.week_start:%d %b} is waiting for approval",
           f"/app/timesheets/{ts.id}", actor=actor)
    audit.record("resource.timesheet_submitted",
                 summary=f"Submitted timesheet for week of {ts.week_start:%d %b %Y} ({ts.total_hours} h)", entity=ts)


def can_approve(actor: User, ts: Timesheet) -> bool:
    if ts.user_id == actor.id:
        return False
    if actor.can("hr.manage"):
        return True
    person = ts.user
    if person.department_id:
        dept = db.session.get(Department, person.department_id)
        if dept and dept.head_id == actor.id:
            return True
    return any(e.project.manager_id == actor.id for e in ts.entries)


def decide_week(actor: User, ts: Timesheet, *, approve: bool, note: str = "") -> None:
    if not can_approve(actor, ts):
        raise ResourceError("You can't approve this timesheet.")
    if ts.status != "submitted":
        raise ResourceError("This timesheet isn't awaiting approval.")
    if not approve and len(note.strip()) < 3:
        raise ResourceError("Say what needs correcting when returning a timesheet.")
    ts.status = "approved" if approve else "rejected"
    ts.decided_by_id, ts.decided_at, ts.decision_note = actor.id, utcnow(), note.strip()
    notify([ts.user_id], "approval", f"Your timesheet for week of {ts.week_start:%d %b} was "
           f"{'approved' if approve else 'returned'} by {actor.full_name}",
           f"/app/timesheet?week={ts.week_start.isoformat()}", actor=actor)
    audit.record("resource.timesheet_" + ("approved" if approve else "returned"),
                 summary=f"{'Approved' if approve else 'Returned'} {ts.user.full_name}'s timesheet for week of {ts.week_start:%d %b %Y}",
                 entity=ts)


def approvable(actor: User) -> list[Timesheet]:
    rows = db.session.scalars(select(Timesheet).where(Timesheet.status == "submitted")
                              .order_by(Timesheet.week_start.desc())).all()
    return [t for t in rows if can_approve(actor, t)]


# ---------------------------------------------------------------- hours
def hours_by_task(task_ids) -> dict[int, Decimal]:
    if not task_ids:
        return {}
    return {tid: Decimal(h) for tid, h in db.session.execute(
        select(TimeEntry.task_id, func.sum(TimeEntry.hours)).where(TimeEntry.task_id.in_(task_ids))
        .group_by(TimeEntry.task_id)).all()}


def hours_by_person(project: Project) -> list[tuple[User, Decimal]]:
    rows = db.session.execute(select(TimeEntry.user_id, func.sum(TimeEntry.hours))
                              .where(TimeEntry.project_id == project.id).group_by(TimeEntry.user_id)).all()
    people = {u.id: u for u in db.session.scalars(select(User).where(User.id.in_([r[0] for r in rows] or [-1])))}
    return sorted(((people[u], Decimal(h)) for u, h in rows if u in people), key=lambda x: -x[1])


# ---------------------------------------------------------------- workload & performance (live data only)
def workload(today: date | None = None) -> list[dict]:
    today = today or date.today()
    horizon = today + timedelta(days=27)
    people = db.session.scalars(select(User).where(User.is_active_user.is_(True)).order_by(User.full_name)).all()
    alloc = defaultdict(int)
    projects_of = defaultdict(list)
    for m in db.session.scalars(select(ProjectMember).join(Project, Project.id == ProjectMember.project_id)
                                .where(Project.status.in_(OPEN_PROJECT_STATUSES))):
        alloc[m.user_id] += m.allocation_pct
        projects_of[m.user_id].append((m.project, m.allocation_pct))
    open_tasks = db.session.scalars(select(Task).join(Project, Project.id == Task.project_id).where(
        Task.status != "done", Task.assignee_id.is_not(None), Task.is_milestone.is_(False),
        Project.status.in_(OPEN_PROJECT_STATUSES))).all()
    by_person = defaultdict(list)
    for t in open_tasks:
        by_person[t.assignee_id].append(t)
    week_ago = today - timedelta(days=7)
    logged = dict(db.session.execute(select(TimeEntry.user_id, func.sum(TimeEntry.hours))
                                     .where(TimeEntry.work_date > week_ago, TimeEntry.work_date <= today)
                                     .group_by(TimeEntry.user_id)).all())
    out = []
    for u in people:
        tasks = by_person[u.id]
        planned = Decimal(0)
        for t in tasks:
            if t.due_date and t.due_date <= horizon and t.estimate_hours:
                planned += Decimal(t.estimate_hours) * Decimal(100 - t.progress) / 100
        capacity = Decimal(u.weekly_hours or 40) * 4
        load = int(round(planned / capacity * 100)) if capacity else 0
        out.append({"user": u, "allocation": alloc[u.id], "projects": projects_of[u.id],
                    "open_tasks": len(tasks), "overdue": sum(1 for t in tasks if t.is_overdue),
                    "planned_hours": planned.quantize(Decimal("0.1")), "capacity": capacity, "load": load,
                    "logged_7d": Decimal(logged.get(u.id) or 0),
                    "over": alloc[u.id] > 100 or load > 100, "free": alloc[u.id] < 50 and load < 50})
    return out


def performance(days: int = 90, today: date | None = None) -> list[dict]:
    today = today or date.today()
    since = today - timedelta(days=days)
    people = db.session.scalars(select(User).where(User.is_active_user.is_(True)).order_by(User.full_name)).all()
    done = db.session.scalars(select(Task).where(Task.status == "done", Task.assignee_id.is_not(None),
                                                 Task.completed_at >= since)).all()
    open_overdue = db.session.scalars(select(Task).join(Project, Project.id == Task.project_id).where(
        Task.status != "done", Task.assignee_id.is_not(None), Task.due_date < today,
        Project.status.in_(OPEN_PROJECT_STATUSES))).all()
    hours = dict(db.session.execute(select(TimeEntry.user_id, func.sum(TimeEntry.hours))
                                    .where(TimeEntry.work_date >= since, TimeEntry.work_date <= today)
                                    .group_by(TimeEntry.user_id)).all())
    by_done, by_overdue = defaultdict(list), defaultdict(int)
    for t in done:
        by_done[t.assignee_id].append(t)
    for t in open_overdue:
        by_overdue[t.assignee_id] += 1
    weeks = Decimal(days) / 7
    out = []
    for u in people:
        finished = by_done[u.id]
        with_due = [t for t in finished if t.due_date]
        on_time = sum(1 for t in with_due if t.completed_at.date() <= t.due_date)
        h = Decimal(hours.get(u.id) or 0)
        out.append({"user": u, "completed": len(finished),
                    "on_time_pct": round(on_time * 100 / len(with_due)) if with_due else None,
                    "overdue": by_overdue[u.id], "hours": h,
                    "hours_per_week": (h / weeks).quantize(Decimal("0.1")) if weeks else Decimal(0)})
    return out
