"""Stage 4: projects, project board and portfolio (organization side)."""
from datetime import date

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import case, or_, select

from app.extensions import db
from app.models import (OPEN_PROJECT_STATUSES, PROJECT_HEALTH, PROJECT_PRIORITIES, PROJECT_STATUSES, Department,
                        Project, ProjectCategory, User)
from app.security.rbac import permission_required
from app.services import projects as svc

from .forms import CategoryForm, ProjectCloseForm, ProjectForm, ProjectMemberForm, ProjectUpdateForm
from .routes import PER_PAGE, bp

PRIORITY_ORDER = case({"critical": 0, "high": 1, "medium": 2, "low": 3}, value=Project.priority)


def _actor() -> User:
    return current_user._get_current_object()


def _get(project_id: int) -> Project:
    project = db.session.get(Project, project_id)  # tenant-filtered
    if project is None:
        abort(404)
    return project


def _fill_choices(form: ProjectForm):
    cats = db.session.scalars(select(ProjectCategory).order_by(ProjectCategory.name)).all()
    depts = db.session.scalars(select(Department).order_by(Department.name)).all()
    people = db.session.scalars(select(User).where(User.is_active_user.is_(True)).order_by(User.full_name)).all()
    form.category_id.choices = [(0, "No category")] + [(c.id, c.name) for c in cats]
    form.department_id.choices = [(0, "No department")] + [(d.id, d.name) for d in depts]
    form.manager_id.choices = [(0, "Not assigned yet")] + [(u.id, u.full_name) for u in people]


def _fields(form: ProjectForm) -> dict:
    return {
        "name": form.name.data.strip(), "summary": (form.summary.data or "").strip(),
        "description": form.description.data or "", "objectives": form.objectives.data or "",
        "category_id": form.category_id.data or None, "department_id": form.department_id.data or None,
        "manager_id": form.manager_id.data or None, "priority": form.priority.data,
        "start_date": form.start_date.data, "end_date": form.end_date.data, "budget": form.budget.data,
        "currency": form.currency.data, "location": (form.location.data or "").strip(),
        "funder": (form.funder.data or "").strip(),
    }


def _filtered():
    """Shared filters for the list, board and CSV export."""
    a = request.args
    stmt = select(Project)
    q = (a.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Project.name.ilike(like), Project.code.ilike(like), Project.location.ilike(like),
                              Project.funder.ilike(like)))
    status = a.get("status", "open")
    if status == "open":
        stmt = stmt.where(Project.status.in_(OPEN_PROJECT_STATUSES))
    elif status == "closed":
        stmt = stmt.where(Project.status.in_(["completed", "cancelled"]))
    elif status in dict(PROJECT_STATUSES):
        stmt = stmt.where(Project.status == status)
    if a.get("priority") in dict(PROJECT_PRIORITIES):
        stmt = stmt.where(Project.priority == a["priority"])
    if a.get("health") in dict(PROJECT_HEALTH):
        stmt = stmt.where(Project.health == a["health"])
    if a.get("category", type=int):
        stmt = stmt.where(Project.category_id == a.get("category", type=int))
    if a.get("dept", type=int):
        stmt = stmt.where(Project.department_id == a.get("dept", type=int))
    if a.get("mine") == "1":
        from app.models import ProjectMember

        stmt = stmt.where(or_(Project.manager_id == current_user.id,
                              Project.id.in_(select(ProjectMember.project_id)
                                             .where(ProjectMember.user_id == current_user.id))))
    if a.get("delayed") == "1":
        stmt = stmt.where(Project.status.in_(OPEN_PROJECT_STATUSES),
                          or_(Project.end_date < date.today(), Project.health == "off_track"))
    sort = a.get("sort", "updated")
    order = {
        "updated": [Project.updated_at.desc()],
        "name": [Project.name],
        "end": [Project.end_date.asc().nulls_last()],
        "priority": [PRIORITY_ORDER, Project.end_date.asc().nulls_last()],
        "code": [Project.seq.desc()],
    }.get(sort, [Project.updated_at.desc()])
    return stmt.order_by(*order), {"q": q, "status": status, "sort": sort}


# ---------------------------------------------------------------- list / board / export
@bp.route("/projects")
@permission_required("projects.view")
def projects():
    stmt, state = _filtered()
    view = request.args.get("view", "list")
    cats = db.session.scalars(select(ProjectCategory).order_by(ProjectCategory.name)).all()
    depts = db.session.scalars(select(Department).order_by(Department.name)).all()
    ctx = dict(state, view=view, categories=cats, departments=depts, statuses=PROJECT_STATUSES,
               priorities=PROJECT_PRIORITIES, healths=PROJECT_HEALTH, can_create=svc.can_create(current_user))
    if view == "board":
        items = db.session.scalars(stmt).all()
        columns = [(k, label, [p for p in items if p.status == k]) for k, label in PROJECT_STATUSES
                   if k in OPEN_PROJECT_STATUSES or state["status"] in ("all", "closed", k)]
        return render_template("workspace/projects_board.html", columns=columns, total=len(items), **ctx)
    page = db.paginate(stmt, per_page=PER_PAGE, error_out=False)
    return render_template("workspace/projects.html", page=page, **ctx)


@bp.route("/projects/export.csv")
@permission_required("projects.view")
def projects_export():
    stmt, _ = _filtered()
    body = svc.export_csv(db.session.scalars(stmt).all())
    return Response("﻿" + body, mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=projects-{date.today():%Y%m%d}.csv",
                             "Cache-Control": "no-store"})


# ---------------------------------------------------------------- create / edit
@bp.route("/projects/new", methods=["GET", "POST"])
@permission_required("projects.manage")
def project_new():
    form = ProjectForm()
    _fill_choices(form)
    if request.method == "GET":
        form.currency.data = current_user.organization.currency
        form.manager_id.data = current_user.id
        form.department_id.data = current_user.department_id or 0
    if form.validate_on_submit():
        try:
            project = svc.create_project(_actor(), status=form.status.data, **_fields(form))
        except svc.ProjectError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            db.session.commit()
            flash(f"Project {project.code} registered.", "success")
            return redirect(url_for("workspace.project", project_id=project.id))
    return render_template("workspace/project_form.html", form=form, project=None)


@bp.route("/projects/<int:project_id>/edit", methods=["GET", "POST"])
@permission_required("projects.view")
def project_edit(project_id):
    project = _get(project_id)
    if not svc.can_edit(current_user, project):
        abort(403)
    form = ProjectForm(obj=project)
    _fill_choices(form)
    if request.method == "GET":
        form.category_id.data = project.category_id or 0
        form.department_id.data = project.department_id or 0
        form.manager_id.data = project.manager_id or 0
    if form.validate_on_submit():
        try:
            changed = svc.update_project(_actor(), project, **_fields(form))
        except svc.ProjectError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            db.session.commit()
            flash("Project saved." if changed else "No changes to save.", "success" if changed else "info")
            return redirect(url_for("workspace.project", project_id=project.id))
    return render_template("workspace/project_form.html", form=form, project=project)


# ---------------------------------------------------------------- detail
@bp.route("/projects/<int:project_id>")
@permission_required("projects.view")
def project(project_id):
    project = _get(project_id)
    upd = ProjectUpdateForm(obj=project, prefix="u")
    upd.body.data = ""
    member_form = ProjectMemberForm(prefix="m")
    on_team = {m.user_id for m in project.members}
    member_form.user_id.choices = [(u.id, u.full_name) for u in db.session.scalars(
        select(User).where(User.is_active_user.is_(True)).order_by(User.full_name)) if u.id not in on_team]
    from app.services.resources import hours_by_person

    return render_template("workspace/project.html", project=project, upd=upd, member_form=member_form,
                           hours=hours_by_person(project),
                           me_summary=_me_summary(project), risk_summary=_risk_summary(project),
                           can_edit=svc.can_edit(current_user, project),
                           can_report=svc.can_report(current_user, project))


def _risk_summary(project):
    if not current_user.can("risks.view"):
        return None
    from app.services.risks import summary

    s = summary(project)
    if not (s["open_risks"] or s["open_issues"]):
        return None
    s["top"] = sorted([r for r in s["risks"] if r.status == "open"], key=lambda r: -r.score)[:3]
    return s


def _me_summary(project):
    if not current_user.can("me.view"):
        return None
    from app.services.me import project_summary

    s = project_summary(project)
    return s if s["total"] else None


@bp.route("/projects/<int:project_id>/updates", methods=["POST"])
@permission_required("projects.view")
def project_post_update(project_id):
    project = _get(project_id)
    form = ProjectUpdateForm(prefix="u")
    if form.validate_on_submit():
        try:
            svc.post_update(_actor(), project, body=form.body.data or "", status=form.status.data,
                            health=form.health.data, progress=form.progress.data)
        except svc.ProjectError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            db.session.commit()
            flash("Update posted.", "success")
    else:
        flash("Please check the update: progress must be 0–100.", "danger")
    return redirect(url_for("workspace.project", project_id=project.id, _anchor="updates"))


@bp.route("/projects/<int:project_id>/members", methods=["POST"])
@permission_required("projects.view")
def project_members(project_id):
    project = _get(project_id)
    try:
        if request.form.get("remove"):
            svc.remove_member(_actor(), project, int(request.form["remove"]))
            msg = "Removed from the project."
        else:
            svc.add_member(_actor(), project, int(request.form.get("m-user_id") or 0),
                           request.form.get("m-project_role") or "Team member")
            msg = "Added to the project team."
    except (svc.ProjectError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc) if isinstance(exc, svc.ProjectError) else "Choose a person.", "danger")
    else:
        db.session.commit()
        flash(msg, "success")
    return redirect(url_for("workspace.project", project_id=project.id, _anchor="team"))


@bp.route("/projects/<int:project_id>/close", methods=["GET", "POST"])
@permission_required("projects.view")
def project_close(project_id):
    project = _get(project_id)
    if not svc.can_edit(current_user, project):
        abort(403)
    form = ProjectCloseForm()
    if request.method == "GET":
        form.actual_end_date.data = date.today()
    if form.validate_on_submit():
        try:
            svc.close_project(_actor(), project, outcome=form.outcome.data, actual_end_date=form.actual_end_date.data,
                              summary=form.summary.data, lessons=form.lessons.data or "")
        except svc.ProjectError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            db.session.commit()
            flash(f"{project.code} closed as {project.status_label.lower()}.", "success")
            return redirect(url_for("workspace.project", project_id=project.id))
    return render_template("workspace/project_close.html", form=form, project=project)


@bp.route("/projects/<int:project_id>/reopen", methods=["POST"])
@permission_required("projects.view")
def project_reopen(project_id):
    project = _get(project_id)
    try:
        svc.reopen_project(_actor(), project)
    except svc.ProjectError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    else:
        db.session.commit()
        flash(f"{project.code} reopened.", "success")
    return redirect(url_for("workspace.project", project_id=project.id))


# ---------------------------------------------------------------- categories
@bp.route("/projects/categories", methods=["GET", "POST"])
@permission_required("projects.view")
def project_categories():
    from sqlalchemy import func

    can_edit = current_user.can("org.manage")
    form = CategoryForm()
    if request.method == "POST":
        if not can_edit:
            abort(403)
        if request.form.get("delete"):
            cat = db.session.get(ProjectCategory, int(request.form["delete"])) or abort(404)
            n = svc.delete_category(cat)
            db.session.commit()
            flash(f"Category deleted{f'; {n} project(s) now uncategorised' if n else ''}.", "info")
            return redirect(url_for("workspace.project_categories"))
        if form.validate_on_submit():
            try:
                svc.save_category(None, name=form.name.data, color=form.color.data.lower(),
                                  org_id=current_user.organization_id)
            except svc.ProjectError as exc:
                db.session.rollback()
                form.name.errors.append(str(exc))
            else:
                db.session.commit()
                flash("Category added.", "success")
                return redirect(url_for("workspace.project_categories"))
    cats = db.session.scalars(select(ProjectCategory).order_by(ProjectCategory.name)).all()
    counts = dict(db.session.execute(select(Project.category_id, func.count(Project.id))
                                     .group_by(Project.category_id)).all())
    return render_template("workspace/project_categories.html", cats=cats, counts=counts, form=form,
                           can_edit=can_edit)


# ---------------------------------------------------------------- portfolio
@bp.route("/portfolio")
@permission_required("portfolio.view")
def portfolio():
    s = svc.portfolio_summary()
    charts = {
        "status": {"labels": [dict(PROJECT_STATUSES)[k] for k in s["by_status"]], "values": list(s["by_status"].values())},
        "health": {"labels": [dict(PROJECT_HEALTH)[k] for k in s["health"]], "values": list(s["health"].values()),
                   "colors": ["#16a34a", "#f59e0b", "#dc2626"]},
        "category": {"labels": list(s["by_category"]), "values": [v["count"] for v in s["by_category"].values()],
                     "colors": [v["color"] for v in s["by_category"].values()]},
    }
    return render_template("workspace/portfolio.html", s=s, charts=charts)
