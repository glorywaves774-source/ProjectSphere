from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select, text

from app.extensions import db, limiter
from app.models import Plan

bp = Blueprint("public", __name__)


@bp.route("/")
def landing():
    if current_user.is_authenticated:
        return redirect(url_for("admin.dashboard" if current_user.is_platform_admin else "workspace.dashboard"))
    plans = db.session.scalars(
        select(Plan).where(Plan.is_active.is_(True), Plan.is_public.is_(True)).order_by(Plan.sort_order)
    ).all()
    return render_template("public/landing.html", plans=plans)


@bp.route("/healthz")
@limiter.exempt
def healthz():
    db.session.execute(text("SELECT 1"))
    return jsonify(status="ok")


@bp.route("/invite/<token>", methods=["GET", "POST"])
@limiter.limit("20 per hour", methods=["POST"])
def accept_invite(token):
    from flask import flash, make_response, session
    from flask_login import login_user, logout_user

    from app.blueprints.workspace.forms import AcceptInviteForm
    from app.services import people

    inv = people.find_invitation(token)
    if inv is None or not inv.is_pending:
        return render_template("auth/invite_invalid.html", reason=inv.status if inv else "unknown"), 410 if inv else 404
    form = AcceptInviteForm()
    if request.method == "GET" and inv.full_name:
        form.full_name.data = inv.full_name
    if form.validate_on_submit():
        try:
            user = people.accept(token, full_name=form.full_name.data, password=form.password.data)
        except people.PeopleError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            if current_user.is_authenticated:
                logout_user()
            session.clear()
            login_user(user)
            session.permanent = True
            flash(f"Welcome to {inv.organization.name}!", "success")
            return redirect(url_for("workspace.dashboard"))
    # The site-wide strict-origin-when-cross-origin referrer policy already keeps
    # the token out of requests to other sites.
    resp = make_response(render_template("auth/invite_accept.html", form=form, inv=inv))
    resp.headers["Cache-Control"] = "no-store"
    return resp
