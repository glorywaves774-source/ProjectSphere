"""Stage 4 business rules for projects and the portfolio."""
import csv
import io
from datetime import date, timedelta

from sqlalchemy import func, select

from app.extensions import db
from app.models import (OPEN_PROJECT_STATUSES, PROJECT_STATUSES, Department, Project, ProjectCategory, ProjectMember, ProjectUpdate,
                        User, utcnow)

from . import audit
from .limits import LimitExceeded, SubscriptionInactive, ensure_within_limit


class ProjectError(ValueError):
    pass


# ---------------------------------------------------------------- access
def can_create(user: User) -> bool:
    return user.can("projects.manage")


def can_edit(user: User, project: Project) -> bool:
    """Portfolio managers (with portfolio.view) edit any project; others only those they manage or created."""
    if not user.can("projects.manage"):
        return False
    return user.can("portfolio.view") or project.manager_id == user.id or project.created_by_id == user.id


def can_report(user: User, project: Project) -> bool:
    """Post status updates: editors plus anyone on the project team."""
    return can_edit(user, project) or any(m.user_id == user.id for m in project.members)


# ---------------------------------------------------------------- helpers
def _check_open_slot(org) -> None:
    try:
        ensure_within_limit(org, "projects")
    except (LimitExceeded, SubscriptionInactive) as exc:
        raise ProjectError(f"{exc} Close or complete a project, or upgrade your plan.") from exc


def _validate(fields: dict) -> None:
    start, end = fields.get("start_date"), fields.get("end_date")
    if start and end and end < start:
        raise ProjectError("The planned end date can't be before the start date.")
    if fields.get("manager_id"):
        mgr = db.session.get(User, fields["manager_id"])  # tenant-filtered
        if mgr is None or not mgr.is_active_user:
            raise ProjectError("Choose an active team member as project manager.")
    if fields.get("category_id") and db.session.get(ProjectCategory, fields["category_id"]) is None:
        raise ProjectError("Choose a valid category.")
    if fields.get("department_id") and db.session.get(Department, fields["department_id"]) is None:
        raise ProjectError("Choose a valid department.")
    if fields.get("budget") is not None and fields["budget"] < 0:
        raise ProjectError("The budget can't be negative.")
    progress = fields.get("progress")
    if progress is not None and not 0 <= progress <= 100:
        raise ProjectError("Progress must be between 0 and 100%.")


def _next_code() -> tuple[int, str]:
    seq = (db.session.scalar(select(func.max(Project.seq))) or 0) + 1
    return seq, f"PRJ-{date.today().year}-{seq:04d}"


def _ensure_member(project: Project, user_id: int | None, role: str) -> None:
    if user_id and not any(m.user_id == user_id for m in project.members):
        project.members.append(ProjectMember(organization_id=project.organization_id, user_id=user_id,
                                             project_role=role))


# ---------------------------------------------------------------- create / edit
EDITABLE = ("name", "summary", "description", "objectives", "category_id", "department_id", "manager_id",
            "priority", "start_date", "end_date", "budget", "currency", "location", "funder")


def create_project(actor: User, **fields) -> Project:
    org = actor.organization
    status = fields.pop("status", "planning") or "planning"
    if status not in OPEN_PROJECT_STATUSES:
        raise ProjectError("New projects start as Planning, Active or On hold.")
    _check_open_slot(org)
    _validate(fields)
    seq, code = _next_code()
    project = Project(organization_id=org.id, seq=seq, code=code, status=status, created_by_id=actor.id,
                      currency=fields.pop("currency", None) or org.currency)
    for k in EDITABLE:
        if k not in fields or k == "currency":
            continue
        value = fields[k]
        if k.endswith("_id"):
            value = value or None
        elif value is None and k not in ("start_date", "end_date", "budget"):
            value = ""
        setattr(project, k, value)
    db.session.add(project)
    db.session.flush()
    _ensure_member(project, project.manager_id, "Project manager")
    db.session.add(ProjectUpdate(organization_id=org.id, project_id=project.id, author_id=actor.id,
                                 body="Project registered.", status=status, health=project.health, progress=0))
    audit.record("project.created", summary=f"Registered project {project.code} · {project.name}", entity=project)
    return project


def update_project(actor: User, project: Project, **fields) -> list[str]:
    if not can_edit(actor, project):
        raise ProjectError("You can only edit projects you manage.")
    _validate(fields)
    changed = []
    for k in EDITABLE:
        if k not in fields:
            continue
        value = fields[k]
        if k.endswith("_id"):
            value = value or None
        elif value is None and k not in ("start_date", "end_date", "budget"):
            value = ""
        if getattr(project, k) != value:
            setattr(project, k, value)
            changed.append(k)
    if "manager_id" in changed:
        _ensure_member(project, project.manager_id, "Project manager")
    if changed:
        audit.record("project.updated", summary=f"Edited {project.code}: {', '.join(c.replace('_id', '').replace('_', ' ') for c in changed)}",
                     entity=project, details={"fields": changed})
    return changed


def post_update(actor: User, project: Project, *, body: str, status: str | None = None, health: str | None = None,
                progress: int | None = None) -> ProjectUpdate:
    if not can_report(actor, project):
        raise ProjectError("Only the project team can post updates.")
    if not project.is_open:
        raise ProjectError("This project is closed. Reopen it to post updates.")
    if status and status not in OPEN_PROJECT_STATUSES:
        raise ProjectError("Use Close project to complete or cancel a project.")
    if (status and status != project.status) and not can_edit(actor, project):
        raise ProjectError("Only the project manager can change the status.")
    _validate({"progress": progress})
    from app.models import Task

    if db.session.scalar(select(func.count(Task.id)).where(Task.project_id == project.id)):
        progress = None  # progress is calculated from the project's tasks
    changes = []
    if status and status != project.status:
        changes.append(f"status {project.status_label} → {dict(PROJECT_STATUSES)[status]}")
        project.status = status
    if health and health != project.health:
        project.health = health
        changes.append(f"health {project.health_label}")
    if progress is not None and progress != project.progress:
        project.progress = progress
        changes.append(f"progress {progress}%")
    if not body.strip() and not changes:
        raise ProjectError("Write an update or change the status, health or progress.")
    upd = ProjectUpdate(organization_id=project.organization_id, project_id=project.id, author_id=actor.id,
                        body=body.strip(), status=project.status, health=project.health, progress=project.progress)
    db.session.add(upd)
    audit.record("project.update_posted", summary=f"Update on {project.code}" + (f" ({', '.join(changes)})" if changes else ""),
                 entity=project)
    return upd


# ---------------------------------------------------------------- team
def add_member(actor: User, project: Project, user_id: int, role: str) -> ProjectMember:
    if not can_edit(actor, project):
        raise ProjectError("Only the project manager can change the team.")
    user = db.session.get(User, user_id)
    if user is None or not user.is_active_user:
        raise ProjectError("Choose an active team member.")
    if any(m.user_id == user_id for m in project.members):
        raise ProjectError(f"{user.full_name} is already on this project.")
    m = ProjectMember(organization_id=project.organization_id, user_id=user_id,
                      project_role=(role or "Team member").strip()[:80])
    project.members.append(m)
    audit.record("project.member_added", summary=f"Added {user.full_name} to {project.code}", entity=project)
    return m


def remove_member(actor: User, project: Project, member_id: int) -> None:
    if not can_edit(actor, project):
        raise ProjectError("Only the project manager can change the team.")
    m = next((m for m in project.members if m.id == member_id), None)
    if m is None:
        raise ProjectError("That person isn't on this project.")
    if m.user_id == project.manager_id:
        raise ProjectError("Choose a different project manager before removing this person.")
    project.members.remove(m)
    audit.record("project.member_removed", summary=f"Removed {m.user.full_name} from {project.code}", entity=project)


# ---------------------------------------------------------------- closure
def close_project(actor: User, project: Project, *, outcome: str, actual_end_date: date | None, summary: str,
                  lessons: str = "") -> None:
    if not can_edit(actor, project):
        raise ProjectError("Only the project manager can close the project.")
    if not project.is_open:
        raise ProjectError("This project is already closed.")
    if outcome not in ("completed", "cancelled"):
        raise ProjectError("Choose whether the project was completed or cancelled.")
    if len(summary.strip()) < 10:
        raise ProjectError("Write a short closure summary (at least 10 characters).")
    project.status = outcome
    project.actual_end_date = actual_end_date or date.today()
    if project.start_date and project.actual_end_date < project.start_date:
        raise ProjectError("The actual end date can't be before the start date.")
    project.closed_at = utcnow()
    project.closure_summary, project.lessons_learned = summary.strip(), lessons.strip()
    if outcome == "completed":
        project.progress = 100
    db.session.add(ProjectUpdate(organization_id=project.organization_id, project_id=project.id, author_id=actor.id,
                                 body=f"Project {outcome}. {summary.strip()}", status=outcome, health=project.health,
                                 progress=project.progress))
    audit.record("project.closed", summary=f"{project.code} {outcome}", entity=project)


def reopen_project(actor: User, project: Project) -> None:
    if not can_edit(actor, project):
        raise ProjectError("Only the project manager can reopen the project.")
    if project.is_open:
        raise ProjectError("This project is already open.")
    _check_open_slot(actor.organization)
    project.status, project.closed_at, project.actual_end_date = "active", None, None
    db.session.add(ProjectUpdate(organization_id=project.organization_id, project_id=project.id, author_id=actor.id,
                                 body="Project reopened.", status="active", health=project.health,
                                 progress=project.progress))
    audit.record("project.reopened", summary=f"Reopened {project.code}", entity=project)


# ---------------------------------------------------------------- categories
def save_category(category: ProjectCategory | None, *, name: str, color: str, org_id) -> ProjectCategory:
    name = name.strip()
    clash = db.session.scalar(select(ProjectCategory).where(func.lower(ProjectCategory.name) == name.lower(),
                                                            ProjectCategory.id != (category.id if category else -1)))
    if clash:
        raise ProjectError("A category with this name already exists.")
    if category is None:
        category = ProjectCategory(organization_id=org_id)
        db.session.add(category)
    category.name, category.color = name, color
    db.session.flush()
    audit.record("project.category_saved", summary=f"Saved project category {name}", entity=category)
    return category


def delete_category(category: ProjectCategory) -> int:
    n = db.session.execute(Project.__table__.update().where(Project.category_id == category.id)
                           .values(category_id=None)).rowcount
    audit.record("project.category_deleted", summary=f"Deleted project category {category.name}", entity=category)
    db.session.delete(category)
    return n


# ---------------------------------------------------------------- portfolio analytics (live data only)
def portfolio_summary() -> dict:
    projects = db.session.scalars(select(Project)).all()
    today = date.today()
    open_ = [p for p in projects if p.is_open]
    by_status = {k: 0 for k in ("planning", "active", "on_hold", "completed", "cancelled")}
    for p in projects:
        by_status[p.status] = by_status.get(p.status, 0) + 1
    health = {"on_track": 0, "at_risk": 0, "off_track": 0}
    for p in open_:
        health[p.health] += 1
    by_category: dict[str, dict] = {}
    for p in open_:
        key = p.category.name if p.category else "Uncategorised"
        entry = by_category.setdefault(key, {"count": 0, "color": p.category.color if p.category else "#94a3b8"})
        entry["count"] += 1
    by_department: dict[str, dict] = {}
    for p in open_:
        key = p.department.name if p.department else "No department"
        d = by_department.setdefault(key, {"open": 0, "delayed": 0, "progress": []})
        d["open"] += 1
        d["delayed"] += 1 if p.is_delayed else 0
        d["progress"].append(p.progress)
    for d in by_department.values():
        d["avg_progress"] = round(sum(d["progress"]) / len(d["progress"])) if d["progress"] else 0
    budgets: dict[str, float] = {}
    for p in open_:
        if p.budget is not None:
            budgets[p.currency] = budgets.get(p.currency, 0) + float(p.budget)
    closed = [p for p in projects if p.status == "completed" and p.end_date and p.actual_end_date]
    on_time = sum(1 for p in closed if p.actual_end_date <= p.end_date)
    return {
        "total": len(projects),
        "open": len(open_),
        "active": by_status["active"],
        "completed": by_status["completed"],
        "by_status": by_status,
        "health": health,
        "delayed": sorted([p for p in open_ if p.is_delayed], key=lambda p: p.end_date or today),
        "due_soon": sorted([p for p in open_ if p.end_date and today <= p.end_date <= today + timedelta(days=30)],
                           key=lambda p: p.end_date),
        "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1]["count"])),
        "by_department": dict(sorted(by_department.items(), key=lambda kv: -kv[1]["open"])),
        "budgets": budgets,
        "avg_progress": round(sum(p.progress for p in open_) / len(open_)) if open_ else 0,
        "on_time_rate": round(on_time * 100 / len(closed)) if closed else None,
        "completed_count_with_dates": len(closed),
    }


def export_csv(projects) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Code", "Name", "Status", "Priority", "Health", "Progress %", "Category", "Department", "Manager",
                "Start", "Planned end", "Actual end", "Budget", "Currency", "Location", "Funder"])
    for p in projects:
        w.writerow([p.code, _safe(p.name), p.status_label, p.priority_label, p.health_label, p.progress,
                    _safe(p.category.name if p.category else ""), _safe(p.department.name if p.department else ""),
                    _safe(p.manager.full_name if p.manager else ""), p.start_date or "", p.end_date or "",
                    p.actual_end_date or "", f"{p.budget:.2f}" if p.budget is not None else "", p.currency,
                    _safe(p.location), _safe(p.funder)])
    return out.getvalue()


def _safe(value: str) -> str:
    """Stop spreadsheet formula injection when the CSV is opened in Excel."""
    value = value or ""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value
