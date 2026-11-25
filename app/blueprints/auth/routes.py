from datetime import timedelta
from urllib.parse import urlsplit

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, select
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, limiter
from app.models import Plan, User, utcnow
from app.services import audit
from app.services.onboarding import OnboardingError, register_organization
from app.tenancy import auth_lookup_scope, tenant_scope

from .forms import LoginForm, RegisterForm

bp = Blueprint("auth", __name__)

_DUMMY_HASH = generate_password_hash("timing-equaliser-not-a-real-password")
GENERIC_FAIL = "That email and password don't match an active account."


def _home_for(user) -> str:
    return url_for("admin.dashboard") if user.is_platform_admin else url_for("workspace.dashboard")


def _safe_next(target: str | None) -> str | None:
    if not target:
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute; 60 per hour", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(_home_for(current_user))
    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        with auth_lookup_scope():
            user = db.session.scalar(select(User).where(func.lower(User.email) == email))
        if user is None:
            check_password_hash(_DUMMY_HASH, form.password.data)  # equalise timing
            current_app.logger.info("login failed for unknown email")
            flash(GENERIC_FAIL, "danger")
            return render_template("auth/login.html", form=form), 401

        with tenant_scope(user.organization_id, platform=user.is_platform_admin):
            user = db.session.get(User, user.id)
            now = utcnow()
            if user.locked_until and user.locked_until > now:
                flash("Too many failed attempts. This account is locked for a few minutes.", "danger")
                return render_template("auth/login.html", form=form), 423
            if not user.check_password(form.password.data) or not user.is_active_user:
                user.failed_logins += 1
                if user.failed_logins >= current_app.config["LOGIN_MAX_FAILED"]:
                    user.locked_until = now + timedelta(minutes=current_app.config["LOGIN_LOCKOUT_MINUTES"])
                    user.failed_logins = 0
                    audit.record("auth.locked", summary="Account locked after repeated failed sign-ins", user=user,
                                 organization_id=user.organization_id)
                audit.record("auth.login_failed", summary="Failed sign-in", user=user,
                             organization_id=user.organization_id)
                db.session.commit()
                flash(GENERIC_FAIL, "danger")
                return render_template("auth/login.html", form=form), 401
            if user.organization is not None and user.organization.is_suspended:
                flash("Your organization's workspace is suspended. Contact Kayron Global Tech Services support.", "warning")
                return render_template("auth/login.html", form=form), 403

            user.failed_logins = 0
            user.locked_until = None
            user.last_login_at = now
            audit.record("auth.login", summary="Signed in", user=user, organization_id=user.organization_id)
            db.session.commit()
            db.session.refresh(user)
            _ = user.roles

        session.clear()  # prevent session fixation
        login_user(user, remember=form.remember.data)
        session.permanent = True
        return redirect(_safe_next(request.args.get("next")) or _home_for(user))
    return render_template("auth/login.html", form=form)


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(_home_for(current_user))
    from app.services import settings as platform_settings

    if not platform_settings.get_bool("allow_signup"):
        return render_template("auth/signup_closed.html"), 403
    plans = db.session.scalars(
        select(Plan).where(Plan.is_active.is_(True), Plan.is_public.is_(True), Plan.price_monthly.is_not(None))
        .order_by(Plan.sort_order)
    ).all()
    form = RegisterForm()
    form.plan.choices = [(p.code, f"{p.name} — GH₵{p.price_monthly:,.0f}/month") for p in plans]
    if request.method == "GET" and request.args.get("plan") in {p.code for p in plans}:
        form.plan.data = request.args["plan"]
    if form.validate_on_submit():
        try:
            org, user = register_organization(
                org_name=form.org_name.data, org_type=form.org_type.data, country=form.country.data,
                admin_name=form.full_name.data, admin_email=form.email.data,
                password=form.password.data, plan_code=form.plan.data,
            )
        except OnboardingError as exc:
            flash(str(exc), "danger")
            return render_template("auth/register.html", form=form, plans=plans), 400
        with tenant_scope(org.id):
            user = db.session.get(User, user.id)
            _ = user.roles
        session.clear()
        login_user(user)
        session.permanent = True
        days = org.subscription.trial_days_left if org.subscription else None
        flash(f"Welcome to {current_app.config['APP_NAME']}." + (f" Your {days}-day free trial has started." if days else ""), "success")
        return redirect(url_for("workspace.dashboard"))
    return render_template("auth/register.html", form=form, plans=plans)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    audit.record("auth.logout", summary="Signed out")
    db.session.commit()
    logout_user()
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))
