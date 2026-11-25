"""Stage 5: tasks, Gantt, calendar and My tasks (organization side)."""
import calendar as cal
from datetime import date, timedelta

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_, select

from app.extensions import db
from app.models import TASK_STATUSES, Project, Task, TaskDependency, User
from app.security.rbac import permission_required
from app.services import tasks as svc

from .forms import DependencyForm, ReviewForm, TaskCommentForm, TaskForm, TaskStatusForm
from .routes import PER_PAGE, bp  # noqa: F401


def _actor() -> User:
    return current_user._get_current_object()


def _project(project_id: int) -> Project:
    p = db.session.get(Project, project_id)
    if p is None:
        abort(404)
    return p


def _task(task_id: int) -> Task:
    t = db.session.get(Task, task_id)
    if t is None:
        abort(404)
    return t


def _fill(form: TaskForm, project: Project, exclude: Task | None = None):
    tasks = svc.wbs(svc.project_tasks(project))
    banned = set()
    if exclude is not None:  # can't nest under itself or its descendants
        stack = [exclude]
        while stack:
            n = stack.pop()
            banned.add(n.id)
            stack.extend(n.children)
    form.parent_id.choices = [(0, "Top level")] + [
        (t.id, f"{code}  {t.title}") for t, code, _ in tasks if t.id not in banned and not t.is_milestone]
    people = db.session.scalars(select(User).where(User.is_active_user.is_(True)).order_by(User.full_name)).all()
    team = {m.user_id for m in project.members}
    form.assignee_id.choices = [(0, "Unassigned")] + [
        (u.id, u.full_name + ("" if u.id in team else "  (joins the team)")) for u in people]


def _fields(form: TaskForm) -> dict:
    return {"title": form.title.data, "description": form.description.data or "",
            "parent_id": form.parent_id.data or None, "assignee_id": form.assignee_id.data or None,
            "priority": form.priority.data, "start_date": form.start_date.data, "due_date": form.due_date.data,
            "estimate_hours": form.estimate_hours.data, "is_milestone": bool(form.is_milestone.data),
            "requires_approval": bool(form.requires_approval.data)}


# ---------------------------------------------------------------- project task list & Gantt
@bp.route("/projects/<int:project_id>/tasks")
@permission_required("projects.view")
def project_tasks(project_id):
    project = _project(project_id)
    tasks = svc.project_tasks(project)
    deps = svc.project_dependencies(project)
    rows = svc.wbs(tasks)
    counts = {k: sum(1 for t in tasks if t.status == k) for k, _ in TASK_STATUSES}
    waiting = {d.successor_id for d in deps if not d.predecessor.is_done}
    return render_template("workspace/project_tasks.html", project=project, rows=rows, counts=counts,
                           total=len(tasks), crit=svc.critical_path(tasks, deps), waiting=waiting,
                           can_plan=svc.can_plan(current_user, project),
                           overdue=sum(1 for t in tasks if t.is_overdue))


@bp.route("/projects/<int:project_id>/gantt")
@permission_required("projects.view")
def project_gantt(project_id):
    project = _project(project_id)
    return render_template("workspace/project_gantt.html", project=project, gantt=svc.gantt_rows(project),
                           can_plan=svc.can_plan(current_user, project))


# ---------------------------------------------------------------- create / edit
@bp.route("/projects/<int:project_id>/tasks/new", methods=["GET", "POST"])
@permission_required("tasks.view")
def task_new(project_id):
    project = _project(project_id)
    if not svc.can_plan(current_user, project):
        abort(403)
    form = TaskForm()
    _fill(form, project)
    if request.method == "GET":
        form.parent_id.data = request.args.get("parent", 0, type=int)
        form.is_milestone.data = request.args.get("milestone") == "1"
    if form.validate_on_submit():
        try:
            task = svc.create_task(_actor(), project, **_fields(form))
        except svc.TaskError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            db.session.commit()
            flash(f"{'Milestone' if task.is_milestone else 'Task'} added.", "success")
            if request.form.get("again"):
                return redirect(url_for("workspace.task_new", project_id=project.id, parent=task.parent_id or 0))
            return redirect(url_for("workspace.project_tasks", project_id=project.id))
    return render_template("workspace/task_form.html", form=form, project=project, task=None)


@bp.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
@permission_required("tasks.view")
def task_edit(task_id):
    task = _task(task_id)
    project = task.project
    if not svc.can_plan(current_user, project):
        abort(403)
    form = TaskForm(obj=task)
    _fill(form, project, exclude=task)
    if request.method == "GET":
        form.parent_id.data = task.parent_id or 0
        form.assignee_id.data = task.assignee_id or 0
    if form.validate_on_submit():
        try:
            svc.update_task(_actor(), task, **_fields(form))
        except svc.TaskError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            db.session.commit()
            flash("Task saved.", "success")
            return redirect(url_for("workspace.task", task_id=task.id))
    return render_template("workspace/task_form.html", form=form, project=project, task=task)


# ---------------------------------------------------------------- task page & actions
@bp.route("/tasks/<int:task_id>", methods=["GET", "POST"])
@permission_required("projects.view")
def task(task_id):
    task = _task(task_id)
    project = task.project
    status_form = TaskStatusForm(obj=task, prefix="s")
    comment_form = TaskCommentForm(prefix="c")
    dep_form = DependencyForm(prefix="d")
    review_form = ReviewForm(prefix="r")
    deps_in = db.session.scalars(select(TaskDependency).where(TaskDependency.successor_id == task.id)).all()
    deps_out = db.session.scalars(select(TaskDependency).where(TaskDependency.predecessor_id == task.id)).all()
    taken = {d.predecessor_id for d in deps_in} | {task.id}
    dep_form.predecessor_id.choices = [(t.id, f"{code}  {t.title}") for t, code, _ in
                                       svc.wbs(svc.project_tasks(project)) if t.id not in taken]
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "status" and status_form.validate_on_submit():
                applied = svc.set_status(_actor(), task, status_form.status.data, status_form.progress.data,
                                         status_form.note.data or "")
                msg = ("Sent for approval." if applied == "review" and status_form.status.data == "done"
                       else "Status updated.")
            elif action == "comment" and comment_form.validate_on_submit():
                svc.add_comment(_actor(), task, comment_form.body.data)
                msg = "Comment added."
            elif action == "dependency" and dep_form.validate_on_submit():
                svc.add_dependency(_actor(), task, dep_form.predecessor_id.data)
                msg = "Dependency added."
            elif action == "remove_dependency":
                dep = db.session.get(TaskDependency, request.form.get("dep", type=int) or 0) or abort(404)
                if task.id not in (dep.successor_id, dep.predecessor_id):
                    abort(404)
                svc.remove_dependency(_actor(), dep)
                msg = "Dependency removed."
            elif action in ("approve", "return"):
                svc.review(_actor(), task, approve=action == "approve", note=request.form.get("r-note", ""))
                msg = "Task approved." if action == "approve" else "Task sent back."
            elif action == "delete":
                project_id = project.id
                svc.delete_task(_actor(), task)
                db.session.commit()
                flash("Task deleted.", "info")
                return redirect(url_for("workspace.project_tasks", project_id=project_id))
            else:
                raise svc.TaskError("Please check the form and try again.")
        except svc.TaskError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            db.session.commit()
            flash(msg, "success")
        return redirect(url_for("workspace.task", task_id=task.id))
    from app.services.resources import hours_by_task

    return render_template("workspace/task.html", task=task, project=project, status_form=status_form,
                           logged=hours_by_task([task.id]).get(task.id),
                           comment_form=comment_form, dep_form=dep_form, review_form=review_form,
                           deps_in=deps_in, deps_out=deps_out,
                           can_plan=svc.can_plan(current_user, project), can_work=svc.can_work(current_user, task),
                           can_comment=svc.can_comment(current_user, task),
                           can_approve=svc.can_approve(current_user, project),
                           waiting=svc.open_predecessors(task))


# ---------------------------------------------------------------- My tasks
@bp.route("/tasks")
@permission_required("tasks.view")
def my_tasks():
    tab = request.args.get("tab", "open")
    view = request.args.get("view", "list")
    stmt = select(Task).join(Project, Project.id == Task.project_id).where(
        Project.status.in_(["planning", "active", "on_hold"]))
    if tab == "review":
        # tasks waiting for my approval
        reviewable = [p.id for p in db.session.scalars(select(Project)) if svc.can_approve(current_user, p)]
        stmt = stmt.where(Task.status == "review", Task.project_id.in_(reviewable or [-1]))
    else:
        stmt = stmt.where(Task.assignee_id == current_user.id)
        if tab == "open":
            stmt = stmt.where(Task.status != "done")
        elif tab == "overdue":
            stmt = stmt.where(Task.status != "done", Task.due_date < date.today())
        elif tab == "done":
            stmt = stmt.where(Task.status == "done")
    q = (request.args.get("q") or "").strip()
    if q:
        stmt = stmt.where(or_(Task.title.ilike(f"%{q}%"), Project.name.ilike(f"%{q}%")))
    stmt = stmt.order_by(Task.due_date.asc().nulls_last(), Task.priority.desc())
    can_review_any = current_user.can("tasks.approve")
    if view == "board":
        items = db.session.scalars(stmt.limit(300)).all()
        columns = [(k, label, [t for t in items if t.status == k]) for k, label in TASK_STATUSES]
        return render_template("workspace/my_tasks_board.html", columns=columns, tab=tab, view=view, q=q,
                               can_review_any=can_review_any)
    page = db.paginate(stmt, per_page=PER_PAGE, error_out=False)
    return render_template("workspace/my_tasks.html", page=page, tab=tab, view=view, q=q,
                           can_review_any=can_review_any)


# ---------------------------------------------------------------- calendar
def _add_meetings(events, first, last, project_id):
    """Put the meetings this person attends on the calendar, on their local date."""
    from datetime import datetime, time
    from zoneinfo import ZoneInfo

    from app.models import Meeting, MeetingAttendee

    try:
        tz = ZoneInfo(current_user.organization.timezone or "Africa/Accra")
    except Exception:
        tz = ZoneInfo("Africa/Accra")
    start = datetime.combine(first, time.min, tz)
    end = datetime.combine(last, time.max, tz)
    stmt = (select(Meeting).join(MeetingAttendee).where(MeetingAttendee.user_id == current_user.id,
                                                        Meeting.cancelled.is_(False),
                                                        Meeting.starts_at.between(start, end)).order_by(Meeting.starts_at))
    if project_id:
        stmt = stmt.where(Meeting.project_id == project_id)
    for m in db.session.scalars(stmt).unique():
        local = m.starts_at.astimezone(tz)
        events.setdefault(local.date(), []).insert(0, {
            "kind": "meeting", "title": f"{local:%H:%M} {m.title}", "done": False, "overdue": False,
            "href": url_for("workspace.meeting", meeting_id=m.id)})


@bp.route("/calendar")
@permission_required("tasks.view")
def calendar_view():
    try:
        y, m = (int(x) for x in request.args.get("month", date.today().strftime("%Y-%m")).split("-"))
        first = date(y, m, 1)
    except (ValueError, TypeError):
        first = date.today().replace(day=1)
    last = date(first.year, first.month, cal.monthrange(first.year, first.month)[1])
    project_id = request.args.get("project", type=int)
    mine = request.args.get("mine") == "1"
    events = svc.calendar_events(first, last, project_id=project_id, mine_for=current_user if mine else None)
    _add_meetings(events, first, last, project_id)
    weeks = cal.Calendar(firstweekday=0).monthdatescalendar(first.year, first.month)
    prev_m = (first - timedelta(days=1)).replace(day=1)
    next_m = last + timedelta(days=1)
    projects = db.session.scalars(select(Project).where(Project.status.in_(["planning", "active", "on_hold"]))
                                  .order_by(Project.name)).all()
    agenda = sorted(((d, e) for d, evs in events.items() for e in evs), key=lambda x: x[0])
    return render_template("workspace/calendar.html", first=first, weeks=weeks, events=events, agenda=agenda,
                           prev_m=prev_m, next_m=next_m, projects=projects, project_id=project_id, mine=mine,
                           today=date.today())

