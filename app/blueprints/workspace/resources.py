"""Stage 7: timesheets, approvals, workload and team performance."""
from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.extensions import db
from app.models import ProjectMember, Timesheet, User, week_start
from app.security.rbac import permission_required, tenant_required
from app.services import resources as svc

from .routes import bp

MAX_ROWS = 30


def _actor() -> User:
    return current_user._get_current_object()


def _week_arg() -> date:
    try:
        return week_start(date.fromisoformat(request.args.get("week", "")))
    except ValueError:
        return week_start(date.today())


def _rows_from_form() -> list[dict]:
    rows = []
    for i in range(MAX_ROWS):
        target = request.form.get(f"row-{i}-target")
        if target is None:
            continue
        hours = []
        for d in range(7):
            raw = (request.form.get(f"row-{i}-h{d}") or "").strip().replace(",", ".")
            if not raw:
                hours.append(None)
                continue
            try:
                hours.append(Decimal(raw))
            except InvalidOperation:
                raise svc.ResourceError("Enter hours as numbers, for example 7.5.") from None
        rows.append({"target": target, "hours": hours, "note": request.form.get(f"row-{i}-note", "")})
    return rows


def _grid(ts: Timesheet | None):
    """Group entries into rows by project/task so the grid shows one line per piece of work."""
    rows: "OrderedDict[str, dict]" = OrderedDict()
    if ts:
        for e in ts.entries:
            key = f"t:{e.task_id}" if e.task_id else f"p:{e.project_id}"
            row = rows.setdefault(key, {"target": key, "hours": [None] * 7, "note": e.note})
            idx = (e.work_date - ts.week_start).days
            row["hours"][idx] = (row["hours"][idx] or Decimal(0)) + e.hours
    out = list(rows.values())
    out += [{"target": "", "hours": [None] * 7, "note": ""} for _ in range(3 if not out else 2)]
    return out


@bp.route("/timesheet", methods=["GET", "POST"])
@tenant_required
def timesheet():
    week = _week_arg()
    if request.method == "POST":
        action = request.form.get("action", "save")
        try:
            if action in ("save", "submit"):
                ts = svc.save_week(_actor(), week, _rows_from_form())
                if action == "submit":
                    svc.submit_week(_actor(), ts)
        except svc.ResourceError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            db.session.commit()
            flash("Timesheet submitted for approval." if action == "submit" else "Timesheet saved.", "success")
        return redirect(url_for("workspace.timesheet", week=week.isoformat()))
    ts = svc.get_timesheet(current_user, week)
    days = [week + timedelta(days=i) for i in range(7)]
    targets = svc.loggable_targets(current_user)
    recent = db.session.query(Timesheet).filter(Timesheet.user_id == current_user.id) \
        .order_by(Timesheet.week_start.desc()).limit(8).all()
    return render_template("workspace/timesheet.html", ts=ts, week=week, days=days, rows=_grid(ts), targets=targets,
                           prev_week=week - timedelta(days=7), next_week=week + timedelta(days=7),
                           today=date.today(), recent=recent, approvals=len(svc.approvable(current_user)))


@bp.route("/timesheets/approvals")
@tenant_required
def timesheet_approvals():
    return render_template("workspace/timesheet_approvals.html", sheets=svc.approvable(current_user))


@bp.route("/timesheets/<int:ts_id>", methods=["GET", "POST"])
@tenant_required
def timesheet_review(ts_id):
    ts = db.session.get(Timesheet, ts_id) or abort(404)
    allowed = svc.can_approve(current_user, ts)
    if not (allowed or ts.user_id == current_user.id or current_user.can("hr.view")):
        abort(403)
    if request.method == "POST":
        try:
            svc.decide_week(_actor(), ts, approve=request.form.get("action") == "approve",
                            note=request.form.get("note", ""))
        except svc.ResourceError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("workspace.timesheet_review", ts_id=ts.id))
        db.session.commit()
        flash("Timesheet approved." if ts.status == "approved" else "Timesheet returned.", "success")
        return redirect(url_for("workspace.timesheet_approvals"))
    days = [ts.week_start + timedelta(days=i) for i in range(7)]
    return render_template("workspace/timesheet_review.html", ts=ts, days=days, rows=_grid(ts)[:-2 if ts.entries else None],
                           can_decide=allowed and ts.status == "submitted")


@bp.route("/projects/<int:project_id>/members/<int:member_id>/allocation", methods=["POST"])
@tenant_required
def member_allocation(project_id, member_id):
    m = db.session.get(ProjectMember, member_id)
    if m is None or m.project_id != project_id:
        abort(404)
    try:
        svc.set_allocation(_actor(), m, request.form.get("allocation", type=int))
    except svc.ResourceError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    else:
        db.session.commit()
        flash("Allocation saved.", "success")
    return redirect(url_for("workspace.project", project_id=project_id, _anchor="team"))


@bp.route("/workload")
@permission_required("hr.view")
def workload():
    rows = svc.workload()
    return render_template("workspace/workload.html", rows=rows,
                           over=sum(1 for r in rows if r["over"]), free=sum(1 for r in rows if r["free"]))


@bp.route("/performance")
@permission_required("hr.view")
def performance():
    days = request.args.get("days", 90, type=int)
    days = days if days in (30, 90, 180, 365) else 90
    return render_template("workspace/performance.html", rows=svc.performance(days), days=days)
