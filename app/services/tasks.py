"""Stage 5: tasks, work breakdown, dependencies, approvals and scheduling."""
from collections import defaultdict, deque
from datetime import date, timedelta

from sqlalchemy import func, select

from app.extensions import db
from app.models import TASK_STATUSES, Project, ProjectMember, Task, TaskComment, TaskDependency, User, utcnow

from . import audit
from .notify import notify, users_with
from . import projects as project_svc


class TaskError(ValueError):
    pass


# ---------------------------------------------------------------- access
def is_member(user: User, project: Project) -> bool:
    return any(m.user_id == user.id for m in project.members)


def can_plan(user: User, project: Project) -> bool:
    """Create, edit, schedule and delete tasks."""
    return user.can("tasks.manage") and (project_svc.can_edit(user, project) or is_member(user, project))


def can_work(user: User, task: Task) -> bool:
    """Update status/progress and comment: planners plus the person assigned."""
    return can_plan(user, task.project) or task.assignee_id == user.id


def can_comment(user: User, task: Task) -> bool:
    return can_work(user, task) or is_member(user, task.project)


def can_approve(user: User, project: Project) -> bool:
    return user.can("tasks.approve") and (project_svc.can_edit(user, project) or user.can("portfolio.view"))


# ---------------------------------------------------------------- helpers
def _check_open(project: Project) -> None:
    if not project.is_open:
        raise TaskError("This project is closed. Reopen it to change its tasks.")


def _validate(project: Project, fields: dict, task: Task | None = None) -> None:
    start, due = fields.get("start_date"), fields.get("due_date")
    if start and due and due < start:
        raise TaskError("The due date can't be before the start date.")
    if fields.get("assignee_id"):
        person = db.session.get(User, fields["assignee_id"])  # tenant-filtered
        if person is None or not person.is_active_user:
            raise TaskError("Assign the task to an active team member.")
    parent_id = fields.get("parent_id")
    if parent_id:
        parent = db.session.get(Task, parent_id)
        if parent is None or parent.project_id != project.id:
            raise TaskError("The parent task must belong to the same project.")
        if task is not None:
            node = parent
            while node is not None:
                if node.id == task.id:
                    raise TaskError("A task can't be placed under itself or one of its own sub-tasks.")
                node = node.parent
    if fields.get("estimate_hours") is not None and fields["estimate_hours"] < 0:
        raise TaskError("The estimate can't be negative.")
    progress = fields.get("progress")
    if progress is not None and not 0 <= progress <= 100:
        raise TaskError("Progress must be between 0 and 100%.")


def _ensure_on_team(project: Project, user_id: int | None) -> None:
    if user_id and not any(m.user_id == user_id for m in project.members):
        project.members.append(ProjectMember(organization_id=project.organization_id, user_id=user_id,
                                             project_role="Team member"))


def project_tasks(project: Project) -> list[Task]:
    return db.session.scalars(select(Task).where(Task.project_id == project.id)
                              .order_by(Task.parent_id.nulls_first(), Task.position, Task.id)).all()


def project_dependencies(project: Project) -> list[TaskDependency]:
    return db.session.scalars(select(TaskDependency).join(Task, Task.id == TaskDependency.successor_id)
                              .where(Task.project_id == project.id)).all()


def recompute_progress(project: Project) -> None:
    """Project progress follows its tasks: leaf tasks weighted by estimated hours (or equally)."""
    tasks = project_tasks(project)
    if not tasks:
        return
    parents = {t.parent_id for t in tasks if t.parent_id}
    leaves = [t for t in tasks if t.id not in parents]
    weights = [float(t.estimate_hours) if t.estimate_hours else 1.0 for t in leaves]
    done = [100 if t.is_done else t.progress for t in leaves]
    total = sum(weights)
    project.progress = round(sum(w * p for w, p in zip(weights, done)) / total) if total else 0
    # parent tasks summarise their children
    by_parent = defaultdict(list)
    for t in tasks:
        if t.parent_id:
            by_parent[t.parent_id].append(t)
    for t in sorted(tasks, key=lambda x: -_depth(x)):
        kids = by_parent.get(t.id)
        if kids:
            kw = [float(k.estimate_hours) if k.estimate_hours else 1.0 for k in kids]
            kp = [100 if k.is_done else k.progress for k in kids]
            t.progress = round(sum(w * p for w, p in zip(kw, kp)) / sum(kw))


def _depth(task: Task) -> int:
    d, node = 0, task.parent
    while node is not None:
        d, node = d + 1, node.parent
    return d


# ---------------------------------------------------------------- create / edit / delete
FIELDS = ("title", "description", "assignee_id", "priority", "start_date", "due_date", "estimate_hours",
          "is_milestone", "requires_approval", "parent_id")


def create_task(actor: User, project: Project, **fields) -> Task:
    if not can_plan(actor, project):
        raise TaskError("Only the project team can add tasks.")
    _check_open(project)
    fields = _normalise(fields)
    _validate(project, fields)
    siblings = db.session.scalar(select(func.coalesce(func.max(Task.position), 0)).where(
        Task.project_id == project.id,
        Task.parent_id == fields.get("parent_id") if fields.get("parent_id") else Task.parent_id.is_(None)))
    task = Task(organization_id=project.organization_id, project_id=project.id, created_by_id=actor.id,
                position=(siblings or 0) + 1, **{k: fields.get(k) for k in FIELDS if k in fields})
    task.title = task.title.strip()
    db.session.add(task)
    _ensure_on_team(project, task.assignee_id)
    db.session.flush()
    recompute_progress(project)
    audit.record("task.created", summary=f"Added task “{task.title}” to {project.code}", entity=task)
    if task.assignee_id:
        notify([task.assignee_id], "task", f"{actor.full_name} assigned you “{task.title}” ({project.name})",
               f"/app/tasks/{task.id}", actor=actor)
    return task


def _normalise(fields: dict) -> dict:
    f = dict(fields)
    for k in ("assignee_id", "parent_id"):
        if k in f:
            f[k] = f[k] or None
    if f.get("is_milestone"):
        # a milestone is a single date with no effort
        f["start_date"] = f.get("due_date") or f.get("start_date")
        f["due_date"] = f["start_date"]
        f["estimate_hours"] = None
    return f


def update_task(actor: User, task: Task, **fields) -> list[str]:
    if not can_plan(actor, task.project):
        raise TaskError("Only the project team can edit tasks.")
    _check_open(task.project)
    fields = _normalise(fields)
    _validate(task.project, fields, task)
    changed = []
    for k in FIELDS:
        if k in fields and getattr(task, k) != fields[k]:
            setattr(task, k, fields[k])
            changed.append(k)
    if "assignee_id" in changed:
        _ensure_on_team(task.project, task.assignee_id)
        if task.assignee_id:
            notify([task.assignee_id], "task", f"{actor.full_name} assigned you “{task.title}” ({task.project.name})",
                   f"/app/tasks/{task.id}", actor=actor)
    db.session.flush()
    recompute_progress(task.project)
    if changed:
        audit.record("task.updated", summary=f"Edited task “{task.title}”: {', '.join(c.replace('_id', '').replace('_', ' ') for c in changed)}",
                     entity=task)
    return changed


def delete_task(actor: User, task: Task) -> None:
    if not can_plan(actor, task.project):
        raise TaskError("Only the project team can delete tasks.")
    _check_open(task.project)
    project = task.project
    audit.record("task.deleted", summary=f"Deleted task “{task.title}” from {project.code}", entity=task)
    db.session.delete(task)
    db.session.flush()
    recompute_progress(project)


# ---------------------------------------------------------------- status, approval, comments
def open_predecessors(task: Task) -> list[Task]:
    return [d.predecessor for d in db.session.scalars(
        select(TaskDependency).where(TaskDependency.successor_id == task.id)) if not d.predecessor.is_done]


def set_status(actor: User, task: Task, status: str, progress: int | None = None, note: str = "") -> str:
    """Returns the status actually applied (completion may become 'awaiting approval')."""
    if not can_work(actor, task):
        raise TaskError("Only the person assigned or the project team can update this task.")
    _check_open(task.project)
    if status not in {"todo", "in_progress", "blocked", "review", "done"}:
        raise TaskError("Unknown status.")
    _validate(task.project, {"progress": progress})
    if status in ("in_progress", "review", "done"):
        waiting = open_predecessors(task)
        if waiting and status == "done":
            raise TaskError("Finish the tasks this one depends on first: " + ", ".join(f"“{t.title}”" for t in waiting))
    if status == "done":
        open_kids = [k for k in task.children if not k.is_done]
        if open_kids:
            raise TaskError(f"{len(open_kids)} sub-task(s) are still open.")
        if task.requires_approval and not can_approve(actor, task.project):
            status = "review"
    previous = task.status
    task.status = status
    if status == "done":
        task.progress, task.completed_at = 100, utcnow()
        if task.requires_approval:
            task.approved_by_id, task.approved_at = actor.id, utcnow()
    else:
        task.completed_at = None
        task.approved_by_id = task.approved_at = None
        if progress is not None:
            task.progress = 99 if progress == 100 and status != "done" else progress
        elif previous == "done":
            task.progress = min(task.progress, 90)
    if note.strip():
        db.session.add(TaskComment(organization_id=task.organization_id, task_id=task.id, author_id=actor.id,
                                   body=note.strip()))
    db.session.flush()
    recompute_progress(task.project)
    if status == "review":
        approvers = [u for u in users_with("tasks.approve") if can_approve(u, task.project)]
        notify(approvers, "approval", f"“{task.title}” is waiting for your approval",
               f"/app/tasks/{task.id}", actor=actor)
    audit.record("task.status", summary=f"“{task.title}”: {dict(TASK_STATUSES)[status]}",
                 entity=task)
    return status


def review(actor: User, task: Task, *, approve: bool, note: str = "") -> None:
    if not can_approve(actor, task.project):
        raise TaskError("You can't approve tasks on this project.")
    if task.status != "review":
        raise TaskError("This task isn't awaiting approval.")
    if approve:
        task.status, task.progress = "done", 100
        task.completed_at = task.approved_at = utcnow()
        task.approved_by_id = actor.id
    else:
        if len(note.strip()) < 3:
            raise TaskError("Say what needs to change when sending a task back.")
        task.status = "in_progress"
        task.progress = min(task.progress, 90)
    if note.strip():
        db.session.add(TaskComment(organization_id=task.organization_id, task_id=task.id, author_id=actor.id,
                                   body=("Approved. " if approve else "Sent back: ") + note.strip()))
    db.session.flush()
    recompute_progress(task.project)
    if task.assignee_id:
        notify([task.assignee_id], "approval",
               f"“{task.title}” was {'approved' if approve else 'sent back'} by {actor.full_name}",
               f"/app/tasks/{task.id}", actor=actor)
    audit.record("task.approved" if approve else "task.returned",
                 summary=f"{'Approved' if approve else 'Sent back'} “{task.title}”", entity=task)


def add_comment(actor: User, task: Task, body: str) -> TaskComment:
    if not can_comment(actor, task):
        raise TaskError("Only the project team can comment on this task.")
    if not body.strip():
        raise TaskError("Write a comment first.")
    c = TaskComment(organization_id=task.organization_id, task_id=task.id, author_id=actor.id, body=body.strip())
    db.session.add(c)
    return c


# ---------------------------------------------------------------- dependencies
def _creates_cycle(pred_id: int, succ_id: int, deps: list[TaskDependency]) -> bool:
    graph = defaultdict(list)
    for d in deps:
        graph[d.predecessor_id].append(d.successor_id)
    graph[pred_id].append(succ_id)
    seen, stack = set(), [succ_id]
    while stack:
        node = stack.pop()
        if node == pred_id:
            return True
        if node not in seen:
            seen.add(node)
            stack.extend(graph[node])
    return False


def add_dependency(actor: User, successor: Task, predecessor_id: int) -> TaskDependency:
    project = successor.project
    if not can_plan(actor, project):
        raise TaskError("Only the project team can change dependencies.")
    _check_open(project)
    pred = db.session.get(Task, predecessor_id)
    if pred is None or pred.project_id != project.id:
        raise TaskError("Both tasks must be in the same project.")
    if pred.id == successor.id:
        raise TaskError("A task can't depend on itself.")
    node = successor.parent
    while node is not None:
        if node.id == pred.id:
            raise TaskError("A sub-task can't depend on its own parent.")
        node = node.parent
    deps = project_dependencies(project)
    if any(d.predecessor_id == pred.id and d.successor_id == successor.id for d in deps):
        raise TaskError("That dependency already exists.")
    if _creates_cycle(pred.id, successor.id, deps):
        raise TaskError("That would create a loop: these tasks would be waiting on each other.")
    dep = TaskDependency(organization_id=project.organization_id, predecessor_id=pred.id, successor_id=successor.id)
    db.session.add(dep)
    audit.record("task.dependency_added", summary=f"“{successor.title}” now waits for “{pred.title}”",
                 entity=successor)
    return dep


def remove_dependency(actor: User, dep: TaskDependency) -> None:
    if not can_plan(actor, dep.successor.project):
        raise TaskError("Only the project team can change dependencies.")
    db.session.delete(dep)


# ---------------------------------------------------------------- work breakdown & critical path
def wbs(tasks: list[Task]) -> list[tuple[Task, str, int]]:
    """Flatten tasks into work-breakdown order: (task, '1.2.1', depth)."""
    kids = defaultdict(list)
    for t in tasks:
        kids[t.parent_id].append(t)
    for v in kids.values():
        v.sort(key=lambda t: (t.position, t.id))
    out = []

    def walk(parent_id, prefix, depth):
        for i, t in enumerate(kids.get(parent_id, []), start=1):
            code = f"{prefix}{i}"
            out.append((t, code, depth))
            walk(t.id, code + ".", depth + 1)

    walk(None, "", 0)
    return out


def critical_path(tasks: list[Task], deps: list[TaskDependency]) -> set[int]:
    """Classic critical path method over leaf tasks, using each task's duration in days.

    Tasks with zero slack (any delay pushes the end of the project) are critical.
    """
    parents = {t.parent_id for t in tasks if t.parent_id}
    nodes = {t.id: t for t in tasks if t.id not in parents}
    if not nodes:
        return set()
    succ, pred = defaultdict(list), defaultdict(list)
    for d in deps:
        if d.predecessor_id in nodes and d.successor_id in nodes:
            succ[d.predecessor_id].append(d.successor_id)
            pred[d.successor_id].append(d.predecessor_id)
    dur = {i: (0 if t.is_milestone else t.duration_days) for i, t in nodes.items()}
    indeg = {i: len(pred[i]) for i in nodes}
    queue = deque(i for i in nodes if indeg[i] == 0)
    order = []
    while queue:
        n = queue.popleft()
        order.append(n)
        for s in succ[n]:
            indeg[s] -= 1
            if indeg[s] == 0:
                queue.append(s)
    if len(order) != len(nodes):  # a cycle slipped in; never claim a path
        return set()
    es, ef = {}, {}
    for n in order:
        es[n] = max((ef[p] for p in pred[n]), default=0)
        ef[n] = es[n] + dur[n]
    end = max(ef.values())
    lf, ls = {}, {}
    for n in reversed(order):
        lf[n] = min((ls[s] for s in succ[n]), default=end)
        ls[n] = lf[n] - dur[n]
    return {n for n in nodes if ls[n] - es[n] == 0 and (succ[n] or pred[n] or ef[n] == end)}


def gantt_rows(project: Project) -> dict:
    tasks = project_tasks(project)
    deps = project_dependencies(project)
    crit = critical_path(tasks, deps)
    rows = []
    for t, code, depth in wbs(tasks):
        rows.append({
            "id": t.id, "wbs": code, "title": t.title, "depth": depth,
            "start": (t.start_date or t.due_date).isoformat() if (t.start_date or t.due_date) else None,
            "end": (t.due_date or t.start_date).isoformat() if (t.due_date or t.start_date) else None,
            "progress": 100 if t.is_done else t.progress, "milestone": t.is_milestone, "critical": t.id in crit,
            "status": t.status, "done": t.is_done, "overdue": t.is_overdue, "summary": bool(t.children),
            "assignee": t.assignee.full_name if t.assignee else "",
        })
    links = [{"from": d.predecessor_id, "to": d.successor_id} for d in deps]
    dated = [r for r in rows if r["start"]]
    span = None
    if dated or project.start_date:
        starts = [date.fromisoformat(r["start"]) for r in dated] + ([project.start_date] if project.start_date else [])
        ends = [date.fromisoformat(r["end"]) for r in dated] + ([project.end_date] if project.end_date else [])
        span = {"start": (min(starts) - timedelta(days=3)).isoformat(),
                "end": (max(ends or starts) + timedelta(days=7)).isoformat()}
    return {"rows": rows, "links": links, "span": span, "today": date.today().isoformat(),
            "projectEnd": project.end_date.isoformat() if project.end_date else None,
            "critical": len(crit)}


# ---------------------------------------------------------------- calendar
def calendar_events(first: date, last: date, *, project_id: int | None = None, mine_for: User | None = None):
    stmt = select(Task).join(Project, Project.id == Task.project_id).where(
        Task.due_date.between(first, last), Project.status.in_(["planning", "active", "on_hold"]))
    if project_id:
        stmt = stmt.where(Task.project_id == project_id)
    if mine_for is not None:
        stmt = stmt.where(Task.assignee_id == mine_for.id)
    events = defaultdict(list)
    for t in db.session.scalars(stmt):
        events[t.due_date].append({"kind": "milestone" if t.is_milestone else "task", "title": t.title,
                                   "task": t, "done": t.is_done, "overdue": t.is_overdue})
    pstmt = select(Project).where(Project.end_date.between(first, last),
                                  Project.status.in_(["planning", "active", "on_hold"]))
    if project_id:
        pstmt = pstmt.where(Project.id == project_id)
    if mine_for is None:
        for p in db.session.scalars(pstmt):
            events[p.end_date].append({"kind": "project", "title": f"{p.name} ends", "project": p,
                                       "done": False, "overdue": p.is_overdue})
    return events
