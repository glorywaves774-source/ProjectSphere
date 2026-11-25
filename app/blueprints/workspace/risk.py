"""Stage 10: risk register and issue log pages."""
from datetime import date

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from app.extensions import db
from app.models import (ISSUE_PRIORITIES, ISSUE_STATUSES, LEVEL_LABELS, RISK_CATEGORIES, RISK_STATUSES, Issue,
                        Project, Risk, User)
from app.security.rbac import permission_required
from app.services import excel
from app.services import risks as svc

from .forms import EscalateForm, IssueForm, IssueStatusForm, RiskForm, RiskReviewForm, TaskCommentForm
from .routes import PER_PAGE, bp

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LEVEL_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _actor() -> User:
    return current_user._get_current_object()


def _project(pid) -> Project:
    p = db.session.get(Project, pid) or abort(404)
    if not current_user.can("projects.view"):
        abort(403)
    return p


def _people():
    return [(0, "Nobody yet")] + [(u.id, u.full_name) for u in db.session.scalars(
        select(User).where(User.is_active_user.is_(True)).order_by(User.full_name))]


def _fail(exc):
    db.session.rollback()
    flash(str(exc), "danger")


def _open_projects(check):
    return [p for p in db.session.scalars(select(Project).where(Project.status.in_(["planning", "active", "on_hold"]))
                                          .order_by(Project.name)) if check(current_user, p)]


def _filtered_risks(project_id=None):
    a = request.args
    stmt = select(Risk)
    pid = project_id or a.get("project", type=int)
    if pid:
        stmt = stmt.where(Risk.project_id == pid)
    status = a.get("status", "open")
    if status in dict(RISK_STATUSES):
        stmt = stmt.where(Risk.status == status)
    if a.get("category") in dict(RISK_CATEGORIES):
        stmt = stmt.where(Risk.category == a["category"])
    if a.get("mine") == "1":
        stmt = stmt.where(Risk.owner_id == current_user.id)
    risks = db.session.scalars(stmt).all()
    if a.get("level") in LEVEL_LABELS:
        risks = [r for r in risks if r.level == a["level"]]
    if a.get("cell"):
        try:
            l, i = (int(x) for x in a["cell"].split("-"))
            risks = [r for r in risks if (r.likelihood, r.impact) == (l, i)]
        except ValueError:
            pass
    return sorted(risks, key=lambda r: (-r.score, r.review_date or date.max, r.id))


# ---------------------------------------------------------------- registers
@bp.route("/risks")
@permission_required("risks.view")
def risks():
    s = svc.summary()
    rows = _filtered_risks()
    projects = db.session.scalars(select(Project).order_by(Project.name)).all()
    return render_template("workspace/risks.html", s=s, rows=rows, projects=projects, project=None,
                           categories=RISK_CATEGORIES, statuses=RISK_STATUSES,
                           can_add=bool(_open_projects(svc.can_manage)))


@bp.route("/projects/<int:project_id>/risks")
@permission_required("risks.view")
def project_risks(project_id):
    project = _project(project_id)
    s = svc.summary(project)
    issues = sorted([i for i in s["issues"] if i.is_open or request.args.get("issues") == "all"],
                    key=lambda i: (not i.is_open, LEVEL_ORDER.get(i.priority, 4), i.due_date or date.max))
    return render_template("workspace/risks.html", s=s, rows=_filtered_risks(project.id), projects=[],
                           project=project, issues=issues, categories=RISK_CATEGORIES, statuses=RISK_STATUSES,
                           can_add=svc.can_manage(current_user, project) and project.is_open,
                           can_raise=svc.can_raise_issue(current_user, project) and project.is_open)


@bp.route("/risks.xlsx")
@permission_required("risks.view")
def risks_xlsx():
    pid = request.args.get("project", type=int)
    rstmt, istmt = select(Risk), select(Issue)
    if pid:
        rstmt, istmt = rstmt.where(Risk.project_id == pid), istmt.where(Issue.project_id == pid)
    rs = sorted(db.session.scalars(rstmt).all(), key=lambda r: (r.project.code, -r.score))
    iss = sorted(db.session.scalars(istmt).all(), key=lambda i: (i.project.code, i.seq))
    data = excel.risk_register(rs, iss, current_user.organization, current_user.full_name)
    return Response(data, mimetype=XLSX, headers={
        "Content-Disposition": f"attachment; filename=risk-register-{date.today():%Y%m%d}.xlsx",
        "Cache-Control": "private, no-store"})


# ---------------------------------------------------------------- risks
def _risk_form(risk=None):
    form = RiskForm(obj=risk)
    form.owner_id.choices = _people()
    if request.method == "GET" and risk is not None:
        form.owner_id.data = risk.owner_id or 0
    return form


def _risk_fields(form):
    return {k: getattr(form, k).data for k in ("title", "description", "category", "likelihood", "impact", "response",
                                               "mitigation", "contingency", "owner_id", "review_date")}


@bp.route("/risks/new", methods=["GET", "POST"])
@permission_required("risks.view")
def risk_new():
    projects = _open_projects(svc.can_manage)
    if not projects:
        abort(403)
    pid = request.values.get("project", type=int) or projects[0].id
    project = next((p for p in projects if p.id == pid), None) or abort(403)
    form = _risk_form()
    if request.method == "GET":
        form.owner_id.data = project.manager_id or 0
    if form.validate_on_submit():
        try:
            r = svc.save_risk(_actor(), project, None, **_risk_fields(form))
        except svc.RiskError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash(f"{r.code} added to the risk register.", "success")
            return redirect(url_for("workspace.risk", risk_id=r.id))
    return render_template("workspace/risk_form.html", form=form, project=project, risk=None, projects=projects)


@bp.route("/risks/<int:risk_id>/edit", methods=["GET", "POST"])
@permission_required("risks.view")
def risk_edit(risk_id):
    r = db.session.get(Risk, risk_id) or abort(404)
    if not svc.can_manage(current_user, r.project):
        abort(403)
    form = _risk_form(r)
    if form.validate_on_submit():
        try:
            svc.save_risk(_actor(), r.project, r, **_risk_fields(form))
        except svc.RiskError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash("Risk updated.", "success")
            return redirect(url_for("workspace.risk", risk_id=r.id))
    return render_template("workspace/risk_form.html", form=form, project=r.project, risk=r, projects=[])


@bp.route("/risks/<int:risk_id>", methods=["GET", "POST"])
@permission_required("risks.view")
def risk(risk_id):
    r = db.session.get(Risk, risk_id) or abort(404)
    _project(r.project_id)
    rform, eform = RiskReviewForm(prefix="rv"), EscalateForm(prefix="es")
    eform.assignee_id.choices = _people()
    if request.method == "GET":
        rform.likelihood.data, rform.impact.data = r.likelihood, r.impact
        eform.assignee_id.data = r.owner_id or 0
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "review":
                rform.validate_on_submit()
                svc.review_risk(_actor(), r, likelihood=rform.likelihood.data, impact=rform.impact.data,
                                note=rform.note.data or "", next_review=rform.next_review.data)
                msg = "Review recorded."
            elif action == "close":
                svc.close_risk(_actor(), r, request.form.get("note", ""))
                msg = "Risk closed."
            elif action == "reopen":
                svc.reopen_risk(_actor(), r)
                msg = "Risk reopened."
            elif action == "escalate":
                eform.validate_on_submit()
                issue = svc.escalate(_actor(), r, priority=eform.priority.data, assignee_id=eform.assignee_id.data,
                                     due_date=eform.due_date.data, description=eform.description.data or "")
                db.session.commit()
                flash(f"The risk has been logged as issue {issue.code}.", "success")
                return redirect(url_for("workspace.issue", issue_id=issue.id))
            else:
                raise svc.RiskError("Unknown action.")
        except svc.RiskError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash(msg, "success")
        return redirect(url_for("workspace.risk", risk_id=r.id))
    can_manage = svc.can_manage(current_user, r.project) and r.project.is_open
    return render_template("workspace/risk.html", r=r, rform=rform, eform=eform, can_manage=can_manage,
                           can_review=(can_manage or r.owner_id == current_user.id) and r.status == "open"
                           and r.project.is_open)


# ---------------------------------------------------------------- issues
@bp.route("/issues")
@permission_required("risks.view")
def issues():
    a = request.args
    stmt = select(Issue)
    if a.get("project", type=int):
        stmt = stmt.where(Issue.project_id == a.get("project", type=int))
    status = a.get("status", "active")
    if status == "active":
        stmt = stmt.where(Issue.status.in_(["open", "in_progress"]))
    elif status in dict(ISSUE_STATUSES):
        stmt = stmt.where(Issue.status == status)
    if a.get("priority") in dict(ISSUE_PRIORITIES):
        stmt = stmt.where(Issue.priority == a["priority"])
    if a.get("mine") == "1":
        stmt = stmt.where(Issue.assignee_id == current_user.id)
    page = db.paginate(stmt.order_by(Issue.due_date.asc().nulls_last(), Issue.id.desc()), per_page=PER_PAGE,
                       error_out=False)
    projects = db.session.scalars(select(Project).order_by(Project.name)).all()
    return render_template("workspace/issues.html", page=page, projects=projects, status=status,
                           statuses=ISSUE_STATUSES, priorities=ISSUE_PRIORITIES,
                           can_add=bool(_open_projects(svc.can_raise_issue)))


def _issue_form(issue=None):
    form = IssueForm(obj=issue)
    form.assignee_id.choices = _people()
    if request.method == "GET" and issue is not None:
        form.assignee_id.data = issue.assignee_id or 0
    return form


def _issue_fields(form):
    return {k: getattr(form, k).data for k in ("title", "description", "priority", "assignee_id", "due_date")}


@bp.route("/issues/new", methods=["GET", "POST"])
@permission_required("risks.view")
def issue_new():
    projects = _open_projects(svc.can_raise_issue)
    if not projects:
        abort(403)
    pid = request.values.get("project", type=int) or projects[0].id
    project = next((p for p in projects if p.id == pid), None) or abort(403)
    form = _issue_form()
    if form.validate_on_submit():
        try:
            i = svc.save_issue(_actor(), project, None, **_issue_fields(form))
        except svc.RiskError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash(f"Issue {i.code} logged.", "success")
            return redirect(url_for("workspace.issue", issue_id=i.id))
    return render_template("workspace/issue_form.html", form=form, project=project, issue=None, projects=projects)


@bp.route("/issues/<int:issue_id>/edit", methods=["GET", "POST"])
@permission_required("risks.view")
def issue_edit(issue_id):
    i = db.session.get(Issue, issue_id) or abort(404)
    if not (svc.can_manage(current_user, i.project) or i.raised_by_id == current_user.id):
        abort(403)
    form = _issue_form(i)
    if form.validate_on_submit():
        try:
            svc.save_issue(_actor(), i.project, i, **_issue_fields(form))
        except svc.RiskError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash("Issue updated.", "success")
            return redirect(url_for("workspace.issue", issue_id=i.id))
    return render_template("workspace/issue_form.html", form=form, project=i.project, issue=i, projects=[])


@bp.route("/issues/<int:issue_id>", methods=["GET", "POST"])
@permission_required("risks.view")
def issue(issue_id):
    i = db.session.get(Issue, issue_id) or abort(404)
    _project(i.project_id)
    sform, cform = IssueStatusForm(prefix="s", obj=i), TaskCommentForm(prefix="c")
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "status":
                sform.validate_on_submit()
                svc.set_issue_status(_actor(), i, sform.status.data, sform.resolution.data or "")
                msg = "Issue updated."
            elif action == "comment":
                if not cform.validate_on_submit():
                    raise svc.RiskError("Write a comment first.")
                svc.comment_issue(_actor(), i, cform.body.data)
                msg = "Comment added."
            else:
                raise svc.RiskError("Unknown action.")
        except svc.RiskError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash(msg, "success")
        return redirect(url_for("workspace.issue", issue_id=i.id))
    return render_template("workspace/issue.html", i=i, sform=sform, cform=cform,
                           can_work=svc.can_work_issue(current_user, i) and i.project.is_open,
                           can_manage=svc.can_manage(current_user, i.project),
                           can_edit=(svc.can_manage(current_user, i.project) or i.raised_by_id == current_user.id)
                           and i.project.is_open,
                           can_comment=svc.can_raise_issue(current_user, i.project) or svc.can_work_issue(current_user, i))
