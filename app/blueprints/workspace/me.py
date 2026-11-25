"""Stage 9: monitoring and evaluation pages."""
from datetime import date

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from app.extensions import db
from app.models import (DAC_CRITERIA, Document, Evaluation, Indicator, IndicatorValue, MonitoringVisit, Project,
                        ResultItem, User, VISIT_STATUSES)
from app.security.rbac import permission_required
from app.services import excel
from app.services import finance as fin_svc
from app.services import me as svc
from app.services import pdf

from .forms import (EvaluationForm, IndicatorForm, IndicatorValueForm, ResultForm, ValueReviewForm, VisitForm,
                    VisitReviewForm)
from .routes import PER_PAGE, bp

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _actor() -> User:
    return current_user._get_current_object()


def _project(project_id) -> Project:
    p = db.session.get(Project, project_id) or abort(404)
    if not current_user.can("projects.view"):
        abort(403)
    return p


def _download(data: bytes, mimetype: str, filename: str) -> Response:
    return Response(data, mimetype=mimetype, headers={"Content-Disposition": f"attachment; filename={filename}",
                                                      "Cache-Control": "private, no-store"})


def _people():
    return db.session.scalars(select(User).where(User.is_active_user.is_(True)).order_by(User.full_name)).all()


def _fail(exc):
    db.session.rollback()
    flash(str(exc), "danger")


# ---------------------------------------------------------------- overview
@bp.route("/me")
@permission_required("me.view")
def me_overview():
    data = svc.overview()
    chart = {"labels": [svc.STATUS_LABELS[k] for k in ("achieved", "on_track", "at_risk", "off_track", "no_data")],
             "values": [data["totals"][k] for k in ("achieved", "on_track", "at_risk", "off_track", "no_data")],
             "colors": ["#16a34a", "#2f7cf6", "#d97706", "#dc2626", "#94a3b8"]}
    pending = svc.pending_values() if svc.can_verify(current_user) else []
    return render_template("workspace/me_overview.html", d=data, chart=chart, pending=pending)


@bp.route("/me/indicators.xlsx")
@permission_required("me.view")
def me_xlsx():
    projects = db.session.scalars(select(Project).where(Project.status.in_(["planning", "active", "on_hold"]))
                                  .order_by(Project.name)).all()
    visits = db.session.scalars(select(MonitoringVisit).where(MonitoringVisit.status != "draft")
                                .order_by(MonitoringVisit.visit_date.desc())).all()
    data = excel.indicator_tracking([(p, svc.project_summary(p)) for p in projects], current_user.organization,
                                    current_user.full_name, visits=visits)
    return _download(data, XLSX, f"indicator-tracking-{date.today():%Y%m%d}.xlsx")


# ---------------------------------------------------------------- project M&E tab
@bp.route("/projects/<int:project_id>/me")
@permission_required("me.view")
def project_me(project_id):
    project = _project(project_id)
    summary = svc.project_summary(project)
    visits = db.session.scalars(select(MonitoringVisit).where(MonitoringVisit.project_id == project.id)
                                .order_by(MonitoringVisit.visit_date.desc()).limit(8)).all()
    evaluations = db.session.scalars(select(Evaluation).where(Evaluation.project_id == project.id)
                                     .order_by(Evaluation.start_date.desc().nulls_last())).all()
    return render_template("workspace/project_me.html", project=project, s=summary,
                           framework=svc.framework(project), visits=visits, evaluations=evaluations,
                           can_manage=svc.can_manage(current_user, project) and project.is_open,
                           can_report=svc.can_report(current_user, project) and project.is_open)


@bp.route("/projects/<int:project_id>/me/report.pdf")
@permission_required("me.view")
def project_me_pdf(project_id):
    project = _project(project_id)
    visits = db.session.scalars(select(MonitoringVisit).where(MonitoringVisit.project_id == project.id,
                                                              MonitoringVisit.status != "draft")
                                .order_by(MonitoringVisit.visit_date.desc()).limit(15)).all()
    evaluations = db.session.scalars(select(Evaluation).where(Evaluation.project_id == project.id)
                                     .order_by(Evaluation.start_date.asc().nulls_last())).all()
    finance = fin_svc.project_finance(project) if fin_svc.can_view(current_user, project) else None
    data = pdf.project_report(project=project, org_name=current_user.organization.name,
                              summary=svc.project_summary(project), framework=svc.framework(project), visits=visits,
                              evaluations=evaluations, finance=finance, generated_by=current_user.full_name)
    from app.services import audit

    audit.record("me.report_exported", summary=f"Downloaded the M&E report for {project.code}", entity=project)
    db.session.commit()
    return _download(data, "application/pdf", f"{project.code}-me-report-{date.today():%Y%m%d}.pdf")


@bp.route("/projects/<int:project_id>/me/indicators.xlsx")
@permission_required("me.view")
def project_me_xlsx(project_id):
    project = _project(project_id)
    visits = db.session.scalars(select(MonitoringVisit).where(MonitoringVisit.project_id == project.id,
                                                              MonitoringVisit.status != "draft")
                                .order_by(MonitoringVisit.visit_date.desc())).all()
    data = excel.indicator_tracking([(project, svc.project_summary(project))], current_user.organization,
                                    current_user.full_name, visits=visits, title_suffix=f" · {project.code}")
    return _download(data, XLSX, f"{project.code}-indicators-{date.today():%Y%m%d}.xlsx")


# ---------------------------------------------------------------- results framework
def _result_form(project, item=None):
    form = ResultForm(obj=item)
    level = request.values.get("level") or (item.level if item else "outcome")
    if level not in ("goal", "outcome", "output"):
        level = "outcome"
    if request.method == "GET":
        form.level.data = level
        if item is None:
            form.parent_id.data = request.args.get("parent", 0, type=int)
        else:
            form.parent_id.data = item.parent_id or 0
    parents = svc.allowed_parents(project, form.level.data or level)
    form.parent_id.choices = [(0, "—")] + [(p.id, f"{p.code} · {p.statement[:70]}") for p in parents]
    return form


@bp.route("/projects/<int:project_id>/results/new", methods=["GET", "POST"])
@permission_required("me.view")
def result_new(project_id):
    project = _project(project_id)
    if not svc.can_manage(current_user, project):
        abort(403)
    form = _result_form(project)
    if form.validate_on_submit():
        try:
            svc.save_result(_actor(), project, None, level=form.level.data, statement=form.statement.data,
                            parent_id=form.parent_id.data or None, assumptions=form.assumptions.data)
        except svc.MEError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash("Result added to the framework.", "success")
            return redirect(url_for("workspace.project_me", project_id=project.id))
    return render_template("workspace/result_form.html", form=form, project=project, item=None)


@bp.route("/results/<int:result_id>/edit", methods=["GET", "POST"])
@permission_required("me.view")
def result_edit(result_id):
    item = db.session.get(ResultItem, result_id) or abort(404)
    project = _project(item.project_id)
    if not svc.can_manage(current_user, project):
        abort(403)
    if request.method == "POST" and request.form.get("action") == "delete":
        try:
            svc.delete_result(_actor(), item)
        except svc.MEError as exc:
            _fail(exc)
            return redirect(url_for("workspace.result_edit", result_id=item.id))
        db.session.commit()
        flash("Result removed.", "info")
        return redirect(url_for("workspace.project_me", project_id=project.id))
    form = _result_form(project, item)
    if form.validate_on_submit():
        try:
            svc.save_result(_actor(), project, item, level=form.level.data, statement=form.statement.data,
                            parent_id=form.parent_id.data or None, assumptions=form.assumptions.data)
        except svc.MEError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash("Result updated.", "success")
            return redirect(url_for("workspace.project_me", project_id=project.id))
    return render_template("workspace/result_form.html", form=form, project=project, item=item)


# ---------------------------------------------------------------- indicators
def _indicator_form(project, ind=None):
    form = IndicatorForm(obj=ind)
    results = db.session.scalars(select(ResultItem).where(ResultItem.project_id == project.id)
                                 .order_by(ResultItem.level, ResultItem.code)).all()
    order = {"goal": 0, "outcome": 1, "output": 2}
    results = sorted(results, key=lambda r: (order[r.level], [int(x) if x.isdigit() else 0 for x in r.code.split(".")]))
    form.result_id.choices = [(0, "Not linked to a result")] + [(r.id, f"{r.code} · {r.statement[:70]}") for r in results]
    form.responsible_id.choices = [(0, "Nobody yet")] + [(u.id, u.full_name) for u in _people()]
    if request.method == "GET":
        if ind is None:
            form.result_id.data = request.args.get("result", 0, type=int)
            form.baseline_date.data = project.start_date or date.today()
            form.target_date.data = project.end_date
        else:
            form.result_id.data = ind.result_id or 0
            form.responsible_id.data = ind.responsible_id or 0
    return form


def _indicator_fields(form, ind=None):
    return dict(name=form.name.data, result_id=form.result_id.data or None, definition=form.definition.data,
                measure=form.measure.data, unit=form.unit.data, direction=form.direction.data,
                baseline_value=form.baseline_value.data, baseline_date=form.baseline_date.data,
                target_value=form.target_value.data, target_date=form.target_date.data, frequency=form.frequency.data,
                responsible_id=form.responsible_id.data or None, data_source=form.data_source.data,
                disaggregation=form.disaggregation.data, endline_value=form.endline_value.data,
                endline_date=form.endline_date.data,
                _responsible_changed=ind is not None and (form.responsible_id.data or None) != ind.responsible_id)


@bp.route("/projects/<int:project_id>/indicators/new", methods=["GET", "POST"])
@permission_required("me.view")
def indicator_new(project_id):
    project = _project(project_id)
    if not svc.can_manage(current_user, project):
        abort(403)
    form = _indicator_form(project)
    if form.validate_on_submit():
        try:
            ind = svc.save_indicator(_actor(), project, None, **_indicator_fields(form))
        except svc.MEError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash(f"Indicator {ind.code} added.", "success")
            return redirect(url_for("workspace.indicator", indicator_id=ind.id))
    return render_template("workspace/indicator_form.html", form=form, project=project, ind=None)


@bp.route("/indicators/<int:indicator_id>/edit", methods=["GET", "POST"])
@permission_required("me.view")
def indicator_edit(indicator_id):
    ind = db.session.get(Indicator, indicator_id) or abort(404)
    project = _project(ind.project_id)
    if not svc.can_manage(current_user, project):
        abort(403)
    form = _indicator_form(project, ind)
    if form.validate_on_submit():
        try:
            svc.save_indicator(_actor(), project, ind, **_indicator_fields(form, ind))
        except svc.MEError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash("Indicator updated.", "success")
            return redirect(url_for("workspace.indicator", indicator_id=ind.id))
    return render_template("workspace/indicator_form.html", form=form, project=project, ind=ind)


@bp.route("/indicators/<int:indicator_id>", methods=["GET", "POST"])
@permission_required("me.view")
def indicator(indicator_id):
    ind = db.session.get(Indicator, indicator_id) or abort(404)
    project = _project(ind.project_id)
    vform, rform = IndicatorValueForm(prefix="v"), ValueReviewForm(prefix="r")
    if request.method == "GET":
        vform.period_end.data = min(date.today(), ind.target_date)
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "record":
                if not vform.validate_on_submit():
                    errs = [e for es in vform.errors.values() for e in es]
                    raise svc.MEError(errs[0] if errs else "Please check the form.")
                row = svc.record_value(_actor(), ind, period_end=vform.period_end.data, value=vform.value.data,
                                       notes=vform.notes.data, source=vform.source.data)
                msg = "Result recorded." if row.status == "verified" else \
                    "Result sent to the M&E team for verification."
            elif action in ("verify", "reject"):
                rform.validate_on_submit()
                row = db.session.get(IndicatorValue, request.form.get("value", type=int) or 0)
                if row is None or row.indicator_id != ind.id:
                    abort(404)
                svc.review_value(_actor(), row, action == "verify", rform.note.data or "")
                msg = "Result verified." if action == "verify" else "Result rejected. The reporter has been told."
            elif action == "delete_value":
                row = db.session.get(IndicatorValue, request.form.get("value", type=int) or 0)
                if row is None or row.indicator_id != ind.id:
                    abort(404)
                svc.delete_value(_actor(), row)
                msg = "Result removed."
            elif action in ("retire", "reactivate"):
                svc.set_active(_actor(), ind, action == "reactivate")
                msg = "Indicator retired." if action == "retire" else "Indicator reactivated."
            elif action == "delete":
                svc.delete_indicator(_actor(), ind)
                db.session.commit()
                flash("Indicator deleted.", "info")
                return redirect(url_for("workspace.project_me", project_id=project.id))
            else:
                raise svc.MEError("Unknown action.")
        except svc.MEError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash(msg, "success")
        return redirect(url_for("workspace.indicator", indicator_id=ind.id))
    p = svc.progress(ind)
    series = sorted([v for v in ind.values if v.status == "verified"], key=lambda v: v.period_end)
    chart = {
        "labels": [ind.baseline_date.strftime("%b %Y")] + [v.period_end.strftime("%b %Y") for v in series]
        + ([ind.endline_date.strftime("%b %Y")] if ind.endline_value is not None and ind.endline_date else [])
        + [ind.target_date.strftime("%b %Y")],
    }
    actual = [float(ind.baseline_value)] + [float(v.value) for v in series] + (
        [float(ind.endline_value)] if ind.endline_value is not None and ind.endline_date else [])
    chart["series"] = [actual + [None], [None] * len(actual) + [float(ind.target_value)]]
    chart["seriesLabels"] = ["Actual", "Target"]
    chart["colors"] = ["#6d5bf6", "#16a34a"]
    return render_template("workspace/indicator.html", ind=ind, project=project, p=p, vform=vform, rform=rform,
                           chart=chart, can_manage=svc.can_manage(current_user, project) and project.is_open,
                           can_report=svc.can_report(current_user, project) and project.is_open and ind.is_active,
                           can_verify=svc.can_verify(current_user))


# ---------------------------------------------------------------- monitoring visits
@bp.route("/visits")
@permission_required("me.view")
def visits():
    a = request.args
    stmt = select(MonitoringVisit)
    if a.get("project", type=int):
        stmt = stmt.where(MonitoringVisit.project_id == a.get("project", type=int))
    if a.get("status") in dict(VISIT_STATUSES):
        stmt = stmt.where(MonitoringVisit.status == a["status"])
    if a.get("mine") == "1":
        stmt = stmt.where(MonitoringVisit.visited_by_id == current_user.id)
    # drafts are private to their author (and people who manage M&E)
    if not current_user.can("me.manage"):
        stmt = stmt.where((MonitoringVisit.status != "draft") | (MonitoringVisit.visited_by_id == current_user.id))
    page = db.paginate(stmt.order_by(MonitoringVisit.visit_date.desc(), MonitoringVisit.id.desc()),
                       per_page=PER_PAGE, error_out=False)
    projects = db.session.scalars(select(Project).order_by(Project.name)).all()
    can_add = any(svc.can_report(current_user, p) for p in projects if p.is_open)
    return render_template("workspace/visits.html", page=page, projects=projects, statuses=VISIT_STATUSES,
                           can_add=can_add)


def _visit_form(visit=None):
    form = VisitForm(obj=visit)
    projects = db.session.scalars(select(Project).where(Project.status.in_(["planning", "active", "on_hold"]))
                                  .order_by(Project.name)).all()
    form.project_id.choices = [(p.id, f"{p.code} · {p.name}") for p in projects if svc.can_report(current_user, p)]
    if not form.project_id.choices:
        abort(403)
    if request.method == "GET":
        if visit is None:
            form.visit_date.data = date.today()
            form.project_id.data = request.args.get("project", type=int) or form.project_id.choices[0][0]
        else:
            form.latitude.data = str(visit.latitude) if visit.latitude is not None else ""
            form.longitude.data = str(visit.longitude) if visit.longitude is not None else ""
    return form


def _visit_fields(form):
    return {k: getattr(form, k).data for k in ("visit_date", "location", "latitude", "longitude", "purpose",
                                               "people_met", "findings", "recommendations", "rating")}


@bp.route("/visits/new", methods=["GET", "POST"])
@permission_required("me.view")
def visit_new():
    form = _visit_form()
    if form.validate_on_submit():
        project = db.session.get(Project, form.project_id.data) or abort(404)
        try:
            v = svc.save_visit(_actor(), None, project, **_visit_fields(form))
        except svc.MEError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash("Visit saved as a draft. Add photos if you have them, then submit it for review.", "success")
            return redirect(url_for("workspace.visit", visit_id=v.id))
    return render_template("workspace/visit_form.html", form=form, visit=None)


@bp.route("/visits/<int:visit_id>/edit", methods=["GET", "POST"])
@permission_required("me.view")
def visit_edit(visit_id):
    v = db.session.get(MonitoringVisit, visit_id) or abort(404)
    if not svc.can_edit_visit(current_user, v):
        abort(403)
    form = _visit_form(v)
    if form.validate_on_submit():
        project = db.session.get(Project, form.project_id.data) or abort(404)
        try:
            svc.save_visit(_actor(), v, project, **_visit_fields(form))
        except svc.MEError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash("Visit updated.", "success")
            return redirect(url_for("workspace.visit", visit_id=v.id))
    return render_template("workspace/visit_form.html", form=form, visit=v)


def _can_see_visit(v: MonitoringVisit) -> bool:
    return v.status != "draft" or v.visited_by_id == current_user.id or current_user.can("me.manage") \
        or svc.can_manage(current_user, v.project)


@bp.route("/visits/<int:visit_id>", methods=["GET", "POST"])
@permission_required("me.view")
def visit(visit_id):
    v = db.session.get(MonitoringVisit, visit_id) or abort(404)
    if not _can_see_visit(v):
        abort(403)
    rform = VisitReviewForm()
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "submit":
                svc.submit_visit(_actor(), v)
                msg = "Visit submitted for review."
            elif action in ("accept", "return"):
                rform.validate_on_submit()
                svc.review_visit(_actor(), v, action == "accept", rform.note.data or "")
                msg = "Visit accepted." if action == "accept" else "Visit returned for changes."
            elif action == "attach":
                f = request.files.get("photo")
                svc.attach_to_visit(_actor(), v, f, request.form.get("caption", ""))
                msg = "Attachment added."
            elif action == "delete":
                svc.delete_visit(_actor(), v)
                db.session.commit()
                flash("Draft visit deleted.", "info")
                return redirect(url_for("workspace.visits"))
            else:
                raise svc.MEError("Unknown action.")
        except (svc.MEError, ValueError) as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash(msg, "success")
        return redirect(url_for("workspace.visit", visit_id=v.id))
    return render_template("workspace/visit.html", v=v, rform=rform, can_edit=svc.can_edit_visit(current_user, v),
                           can_review=svc.can_review_visit(current_user, v))


@bp.route("/visits/<int:visit_id>.pdf")
@permission_required("me.view")
def visit_pdf(visit_id):
    v = db.session.get(MonitoringVisit, visit_id) or abort(404)
    if not _can_see_visit(v):
        abort(403)
    data = pdf.visit_report(visit=v, org_name=current_user.organization.name, generated_by=current_user.full_name)
    return _download(data, "application/pdf", f"{v.reference}.pdf")


# ---------------------------------------------------------------- evaluations
def _evaluation_form(project, ev=None):
    form = EvaluationForm(obj=ev)
    docs = db.session.scalars(select(Document).where(Document.project_id == project.id,
                                                     Document.is_archived.is_(False)).order_by(Document.title)).all()
    form.report_document_id.choices = [(0, "None yet")] + [(d.id, d.title) for d in docs]
    if request.method == "GET" and ev is not None:
        form.report_document_id.data = ev.report_document_id or 0
        for key, _ in DAC_CRITERIA:
            getattr(form, key).data = getattr(ev, key) or 0
    return form


def _evaluation_fields(form):
    fields = {k: getattr(form, k).data for k in ("title", "kind", "status", "evaluator", "is_external", "start_date",
                                                 "end_date", "scope", "findings", "recommendations",
                                                 "management_response", "report_document_id")}
    fields.update({k: getattr(form, k).data for k, _ in DAC_CRITERIA})
    return fields


@bp.route("/projects/<int:project_id>/evaluations/new", methods=["GET", "POST"])
@permission_required("me.view")
def evaluation_new(project_id):
    project = _project(project_id)
    if not svc.can_manage(current_user, project):
        abort(403)
    form = _evaluation_form(project)
    if form.validate_on_submit():
        try:
            ev = svc.save_evaluation(_actor(), project, None, **_evaluation_fields(form))
        except svc.MEError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash("Evaluation saved.", "success")
            return redirect(url_for("workspace.evaluation", evaluation_id=ev.id))
    return render_template("workspace/evaluation_form.html", form=form, project=project, ev=None)


@bp.route("/evaluations/<int:evaluation_id>/edit", methods=["GET", "POST"])
@permission_required("me.view")
def evaluation_edit(evaluation_id):
    ev = db.session.get(Evaluation, evaluation_id) or abort(404)
    project = _project(ev.project_id)
    if not svc.can_manage(current_user, project):
        abort(403)
    form = _evaluation_form(project, ev)
    if form.validate_on_submit():
        try:
            svc.save_evaluation(_actor(), project, ev, **_evaluation_fields(form))
        except svc.MEError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash("Evaluation updated.", "success")
            return redirect(url_for("workspace.evaluation", evaluation_id=ev.id))
    return render_template("workspace/evaluation_form.html", form=form, project=project, ev=ev)


@bp.route("/evaluations/<int:evaluation_id>", methods=["GET", "POST"])
@permission_required("me.view")
def evaluation(evaluation_id):
    ev = db.session.get(Evaluation, evaluation_id) or abort(404)
    project = _project(ev.project_id)
    if request.method == "POST" and request.form.get("action") == "delete":
        try:
            svc.delete_evaluation(_actor(), ev)
        except svc.MEError as exc:
            _fail(exc)
            return redirect(url_for("workspace.evaluation", evaluation_id=ev.id))
        db.session.commit()
        flash("Evaluation deleted.", "info")
        return redirect(url_for("workspace.project_me", project_id=project.id))
    return render_template("workspace/evaluation.html", ev=ev, project=project,
                           can_manage=svc.can_manage(current_user, project))
