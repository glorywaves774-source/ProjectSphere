"""Platform Super Administrator console (Kayron Global Tech Services)."""
import re
import uuid
from datetime import timedelta

from datetime import datetime, time, timezone

from flask import Blueprint, abort, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import extract, func, or_, select

from app.extensions import db
from app.models import (OPEN_STATUSES, ORG_TYPES, AuditLog, Organization, Plan, Subscription, SupportMessage,
                        SupportTicket, User, utcnow)
from app.security.rbac import platform_admin_required
from app.services import audit
from app.services import settings as platform_settings
from app.services.limits import usage_summary
from app.services.onboarding import (OnboardingError, create_platform_admin, register_organization,
                                     temporary_password)
from app.services.tenants import TenantAdminError, change_subscription, reset_password, set_user_active, unlock

from .forms import (CreateOrganizationForm, OrganizationEditForm, PlanForm, StaffReplyForm, SubscriptionForm,
                    SuperAdminForm, TicketTriageForm)

bp = Blueprint("admin", __name__)
PER_PAGE = 20


def _mrr():
    """Monthly recurring revenue from paying (active) subscriptions, by currency."""
    monthly = func.coalesce(Plan.price_monthly, 0)
    rows = db.session.execute(
        select(Plan.currency, func.sum(monthly))
        .join(Subscription, Subscription.plan_id == Plan.id)
        .where(Subscription.status == Subscription.ACTIVE, Subscription.billing_cycle == "monthly")
        .group_by(Plan.currency)
    ).all()
    annual = db.session.execute(
        select(Plan.currency, func.sum(func.coalesce(Plan.price_annual, 0) / 12))
        .join(Subscription, Subscription.plan_id == Plan.id)
        .where(Subscription.status == Subscription.ACTIVE, Subscription.billing_cycle == "annual")
        .group_by(Plan.currency)
    ).all()
    total: dict[str, float] = {}
    for cur, amt in list(rows) + list(annual):
        total[cur] = total.get(cur, 0) + float(amt or 0)
    return total


@bp.route("/")
@platform_admin_required
def dashboard():
    count = lambda *w: db.session.scalar(select(func.count(Organization.id)).where(*w)) or 0  # noqa: E731
    sub_count = lambda status: db.session.scalar(  # noqa: E731
        select(func.count(Subscription.id)).where(Subscription.status == status)) or 0
    stats = {
        "orgs": count(),
        "active": sub_count(Subscription.ACTIVE),
        "trialing": sub_count(Subscription.TRIALING),
        "past_due": sub_count(Subscription.PAST_DUE),
        "suspended": count(Organization.status == Organization.STATUS_SUSPENDED),
        "users": db.session.scalar(select(func.count(User.id)).where(User.organization_id.is_not(None))) or 0,
        "new_30d": count(Organization.created_at >= utcnow() - timedelta(days=30)),
    }
    # Sign-ups per month, last 6 months
    start = (utcnow().replace(day=1) - timedelta(days=150)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    yr, mo = extract("year", Organization.created_at), extract("month", Organization.created_at)
    rows = db.session.execute(
        select(yr, mo, func.count()).where(Organization.created_at >= start).group_by(yr, mo)
    ).all()
    found = {(int(y), int(m)): c for y, m, c in rows}
    labels, values = [], []
    y, m = start.year, start.month
    for _ in range(6):
        labels.append(f"{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][m-1]} {str(y)[2:]}")
        values.append(found.get((y, m), 0))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    by_plan = db.session.execute(
        select(Plan.name, func.count(Subscription.id)).join(Subscription, Subscription.plan_id == Plan.id)
        .group_by(Plan.name, Plan.sort_order).order_by(Plan.sort_order)
    ).all()
    latest = db.session.scalars(select(Organization).order_by(Organization.created_at.desc()).limit(6)).all()
    soon = utcnow() + timedelta(days=7)
    attention = {
        "trials_ending": db.session.scalars(
            select(Subscription).where(Subscription.status == Subscription.TRIALING,
                                       Subscription.trial_ends_at <= soon).order_by(Subscription.trial_ends_at).limit(8)
        ).all(),
        "past_due": db.session.scalars(
            select(Subscription).where(Subscription.status == Subscription.PAST_DUE).limit(8)).all(),
        "tickets": db.session.scalars(
            select(SupportTicket).where(SupportTicket.status.in_(OPEN_STATUSES))
            .order_by(SupportTicket.priority.in_(["urgent", "high"]).desc(), SupportTicket.last_activity_at.desc())
            .limit(6)).all(),
        "open_tickets": db.session.scalar(
            select(func.count(SupportTicket.id)).where(SupportTicket.status == "open")) or 0,
    }
    return render_template(
        "admin/dashboard.html", stats=stats, mrr=_mrr(), latest=latest, attention=attention,
        signup_chart={"labels": labels, "values": values},
        plan_chart={"labels": [r[0] for r in by_plan], "values": [r[1] for r in by_plan]},
    )


@bp.route("/organizations")
@platform_admin_required
def organizations():
    q = (request.args.get("q") or "").strip()
    status = request.args.get("status", "")
    org_type = request.args.get("type", "")
    plan_code = request.args.get("plan", "")
    stmt = select(Organization).outerjoin(Subscription).outerjoin(Plan).order_by(Organization.created_at.desc())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Organization.name.ilike(like), Organization.email.ilike(like), Organization.slug.ilike(like)))
    if status == "suspended":
        stmt = stmt.where(Organization.status == Organization.STATUS_SUSPENDED)
    elif status in {Subscription.TRIALING, Subscription.ACTIVE, Subscription.PAST_DUE, Subscription.CANCELLED}:
        stmt = stmt.where(Subscription.status == status, Organization.status == Organization.STATUS_ACTIVE)
    if org_type:
        stmt = stmt.where(Organization.org_type == org_type)
    if plan_code:
        stmt = stmt.where(Plan.code == plan_code)
    page = db.paginate(stmt, per_page=PER_PAGE, error_out=False)
    plans = db.session.scalars(select(Plan).order_by(Plan.sort_order)).all()
    return render_template("admin/organizations.html", page=page, q=q, status=status, org_type=org_type,
                           plan_code=plan_code, plans=plans, org_types=ORG_TYPES)


def _get_org(org_id: str) -> Organization:
    try:
        oid = uuid.UUID(org_id)
    except ValueError:
        abort(404)
    org = db.session.get(Organization, oid)
    if org is None:
        abort(404)
    return org


@bp.route("/organizations/<org_id>")
@platform_admin_required
def organization_detail(org_id):
    org = _get_org(org_id)
    users = db.session.scalars(select(User).where(User.organization_id == org.id).order_by(User.full_name)).all()
    events = db.session.scalars(
        select(AuditLog).where(AuditLog.organization_id == org.id).order_by(AuditLog.created_at.desc()).limit(8)
    ).all()
    sub = org.subscription
    sub_form = SubscriptionForm(obj=sub, prefix="sub")
    sub_form.plan_id.choices = _plan_choices(include_id=sub.plan_id if sub else None)
    if sub:
        sub_form.trial_ends_at.data = sub.trial_ends_at.date() if sub.trial_ends_at else None
        sub_form.current_period_end.data = sub.current_period_end.date() if sub.current_period_end else None
    edit_form = OrganizationEditForm(obj=org, prefix="org")
    tickets = db.session.scalars(select(SupportTicket).where(SupportTicket.organization_id == org.id)
                                 .order_by(SupportTicket.last_activity_at.desc()).limit(5)).all()
    from app.models import Invoice

    from .billing import invoice_form

    invoices = db.session.scalars(select(Invoice).where(Invoice.organization_id == org.id)
                                  .order_by(Invoice.created_at.desc()).limit(10)).all()
    return render_template("admin/organization_detail.html", org=org, users=users, events=events,
                           usage=usage_summary(org), sub_form=sub_form, edit_form=edit_form, tickets=tickets,
                           now=utcnow(), invoices=invoices, inv_form=invoice_form(org))


@bp.route("/organizations/<org_id>/status", methods=["POST"])
@platform_admin_required
def organization_status(org_id):
    org = _get_org(org_id)
    action = request.form.get("action")
    if action == "suspend":
        org.status = Organization.STATUS_SUSPENDED
        org.suspended_reason = (request.form.get("reason") or "")[:255]
        audit.record("platform.org_suspended", summary=f"Suspended {org.name}", entity=org,
                     organization_id=org.id, details={"reason": org.suspended_reason})
        flash(f"{org.name} is suspended. Its members can no longer sign in.", "warning")
    elif action == "reactivate":
        org.status = Organization.STATUS_ACTIVE
        org.suspended_reason = ""
        audit.record("platform.org_reactivated", summary=f"Reactivated {org.name}", entity=org, organization_id=org.id)
        flash(f"{org.name} is active again.", "success")
    else:
        abort(400)
    db.session.commit()
    return redirect(url_for("admin.organization_detail", org_id=org.id))


@bp.route("/plans")
@platform_admin_required
def plans():
    plans = db.session.scalars(select(Plan).order_by(Plan.sort_order)).all()
    counts = dict(db.session.execute(select(Subscription.plan_id, func.count()).group_by(Subscription.plan_id)).all())
    return render_template("admin/plans.html", plans=plans, counts=counts)


@bp.route("/audit", endpoint="audit")
@platform_admin_required
def audit_log():
    q = (request.args.get("q") or "").strip()
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(AuditLog.summary.ilike(like), AuditLog.action.ilike(like)))
    page = db.paginate(stmt, per_page=PER_PAGE, error_out=False)
    orgs = {o.id: o.name for o in db.session.scalars(select(Organization))}
    return render_template("admin/audit.html", page=page, q=q, orgs=orgs)



# ---------------------------------------------------------------- helpers
def _plan_choices(include_id=None):
    plans = db.session.scalars(select(Plan).order_by(Plan.sort_order)).all()
    return [(p.id, p.name + ("" if p.is_active else " (retired)")) for p in plans if p.is_active or p.id == include_id]


def _to_utc(d, end_of_day=True):
    if d is None:
        return None
    return datetime.combine(d, time(23, 59, 59) if end_of_day else time.min, tzinfo=timezone.utc)


def _show_credentials(title: str, who: str, email: str, password: str, back_url: str):
    """Show a one-time password exactly once; never cached by the browser."""
    resp = make_response(render_template("admin/credentials.html", title=title, who=who, email=email,
                                         password=password, back_url=back_url))
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _get_user(user_id: int) -> User:
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    return user


# ---------------------------------------------------------------- organizations
@bp.route("/organizations/new", methods=["GET", "POST"])
@platform_admin_required
def organization_new():
    form = CreateOrganizationForm()
    plans = db.session.scalars(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order)).all()
    form.plan.choices = [(p.code, p.name) for p in plans]
    if form.validate_on_submit():
        temp = temporary_password()
        try:
            org, user = register_organization(
                org_name=form.org_name.data, org_type=form.org_type.data, country=form.country.data,
                admin_name=form.admin_name.data, admin_email=form.admin_email.data, password=temp,
                plan_code=form.plan.data, status=form.status.data, must_change_password=True,
                created_by_platform=True)
        except OnboardingError as exc:
            form.admin_email.errors.append(str(exc))
        else:
            return _show_credentials(f"{org.name} is ready", user.full_name, user.email, temp,
                                     url_for("admin.organization_detail", org_id=org.id))
    return render_template("admin/organization_new.html", form=form)


@bp.route("/organizations/<org_id>/edit", methods=["POST"])
@platform_admin_required
def organization_edit(org_id):
    org = _get_org(org_id)
    form = OrganizationEditForm(prefix="org")
    if form.validate_on_submit():
        form.populate_obj(org)
        audit.record("platform.org_updated", summary=f"Updated details of {org.name}", entity=org, organization_id=org.id)
        db.session.commit()
        flash("Organization details saved.", "success")
    else:
        flash("Please check the organization details: " + "; ".join(e for errs in form.errors.values() for e in errs), "danger")
    return redirect(url_for("admin.organization_detail", org_id=org.id))


@bp.route("/organizations/<org_id>/subscription", methods=["POST"])
@platform_admin_required
def organization_subscription(org_id):
    org = _get_org(org_id)
    form = SubscriptionForm(prefix="sub")
    form.plan_id.choices = _plan_choices(include_id=org.subscription.plan_id)
    if not form.validate_on_submit():
        flash("Please check the subscription details.", "danger")
        return redirect(url_for("admin.organization_detail", org_id=org.id))
    plan = db.session.get(Plan, form.plan_id.data)
    try:
        changed = change_subscription(org, plan=plan, status=form.status.data, billing_cycle=form.billing_cycle.data,
                                      trial_ends_at=_to_utc(form.trial_ends_at.data),
                                      current_period_end=_to_utc(form.current_period_end.data))
    except TenantAdminError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("admin.organization_detail", org_id=org.id))
    db.session.commit()
    flash("Subscription updated." if changed else "No changes to save.", "success" if changed else "info")
    return redirect(url_for("admin.organization_detail", org_id=org.id))


@bp.route("/users/<int:user_id>/<action>", methods=["POST"])
@platform_admin_required
def user_action(user_id, action):
    user = _get_user(user_id)
    back = request.form.get("back") or (url_for("admin.organization_detail", org_id=user.organization_id)
                                        if user.organization_id else url_for("admin.admins"))
    if not back.startswith("/admin/"):
        back = url_for("admin.dashboard")
    if user.is_platform_admin and user.id == current_user.id and action in {"deactivate", "reset"}:
        flash("You can't do that to your own account here. Use My account instead.", "warning")
        return redirect(back)
    try:
        if action == "deactivate":
            if user.is_platform_admin and _active_admins() <= 1:
                raise TenantAdminError("There must always be at least one active Super Admin.")
            set_user_active(user, False)
            flash(f"{user.full_name} is deactivated and has been signed out.", "warning")
        elif action == "activate":
            set_user_active(user, True)
            flash(f"{user.full_name} can sign in again.", "success")
        elif action == "unlock":
            unlock(user)
            flash(f"{user.full_name} is unlocked.", "success")
        elif action == "reset":
            temp = reset_password(user)
            db.session.commit()
            return _show_credentials("Temporary password issued", user.full_name, user.email, temp, back)
        else:
            abort(404)
    except TenantAdminError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(back)
    db.session.commit()
    return redirect(back)


# ---------------------------------------------------------------- plans
def _fill_plan(plan: Plan, form: PlanForm) -> None:
    for field in ("name", "audience", "description", "currency", "price_monthly", "price_annual", "max_users",
                  "max_projects", "max_storage_mb", "is_public", "is_active"):
        setattr(plan, field, getattr(form, field).data)
    plan.audience = plan.audience or ""
    plan.description = plan.description or ""
    plan.sort_order = form.sort_order.data or 0
    plan.features = [line.strip() for line in (form.features.data or "").splitlines() if line.strip()]


@bp.route("/plans/new", methods=["GET", "POST"])
@platform_admin_required
def plan_new():
    form = PlanForm()
    if form.validate_on_submit():
        if db.session.scalar(select(Plan).where(Plan.code == form.code.data)):
            form.code.errors.append("A plan with this code already exists.")
        else:
            plan = Plan(code=form.code.data)
            _fill_plan(plan, form)
            db.session.add(plan)
            db.session.flush()
            audit.record("platform.plan_created", summary=f"Created plan {plan.name}", entity=plan, organization_id=None)
            db.session.commit()
            flash(f"Plan {plan.name} created.", "success")
            return redirect(url_for("admin.plans"))
    return render_template("admin/plan_form.html", form=form, plan=None)


@bp.route("/plans/<int:plan_id>/edit", methods=["GET", "POST"])
@platform_admin_required
def plan_edit(plan_id):
    plan = db.session.get(Plan, plan_id) or abort(404)
    form = PlanForm(obj=plan)
    if request.method == "GET":
        form.features.data = "\n".join(plan.features or [])
    form.code.data = plan.code  # the code never changes
    if form.validate_on_submit():
        before = {"price_monthly": plan.price_monthly, "max_users": plan.max_users, "is_active": plan.is_active}
        _fill_plan(plan, form)
        audit.record("platform.plan_updated", summary=f"Updated plan {plan.name}", entity=plan, organization_id=None,
                     details={k: f"{v} → {getattr(plan, k)}" for k, v in before.items() if v != getattr(plan, k)})
        db.session.commit()
        flash(f"Plan {plan.name} saved. Existing subscribers keep their subscription and see the new limits.", "success")
        return redirect(url_for("admin.plans"))
    subscribers = db.session.scalar(select(func.count(Subscription.id)).where(Subscription.plan_id == plan.id))
    return render_template("admin/plan_form.html", form=form, plan=plan, subscribers=subscribers)


# ---------------------------------------------------------------- settings
@bp.route("/settings", methods=["GET", "POST"])
@platform_admin_required
def settings():
    values = platform_settings.all_settings()
    errors = {}
    if request.method == "POST":
        new = {}
        for d in platform_settings.SETTINGS:
            raw = (request.form.get(d.key) or "").strip()
            if d.kind == "bool":
                raw = "true" if request.form.get(d.key) == "on" else "false"
            elif d.kind == "int":
                if not raw.isdigit() or int(raw) > 365:
                    errors[d.key] = "Enter a whole number from 0 to 365."
            elif d.kind == "email":
                if raw and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", raw):
                    errors[d.key] = "Enter a valid email address."
            elif d.kind == "choice":
                if raw not in {c[0] for c in d.choices}:
                    errors[d.key] = "Choose one of the options."
            elif d.kind == "text":
                if len(raw) > 300:
                    errors[d.key] = "Keep this under 300 characters."
                if d.key == "platform_name" and not raw:
                    errors[d.key] = "The platform needs a name."
            new[d.key] = raw
        if not errors:
            changed = platform_settings.save(new)
            if changed:
                audit.record("platform.settings_updated", summary="Platform settings changed: " + ", ".join(
                    platform_settings.BY_KEY[k].label for k in changed), organization_id=None,
                    details={k: new[k] for k in changed})
            db.session.commit()
            flash("Settings saved." if changed else "No changes to save.", "success" if changed else "info")
            return redirect(url_for("admin.settings"))
        values = {**values, **new}
    return render_template("admin/settings.html", defs=platform_settings.SETTINGS, values=values, errors=errors)


# ---------------------------------------------------------------- super admins
def _active_admins() -> int:
    return db.session.scalar(select(func.count(User.id)).where(User.is_platform_admin.is_(True),
                                                                User.is_active_user.is_(True))) or 0


@bp.route("/admins", methods=["GET", "POST"])
@platform_admin_required
def admins():
    form = SuperAdminForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        if db.session.scalar(select(User).where(func.lower(User.email) == email)):
            form.email.errors.append("An account with this email already exists.")
        else:
            temp = temporary_password()
            try:
                create_platform_admin(email, form.full_name.data.strip(), temp, must_change_password=True)
            except OnboardingError as exc:
                form.email.errors.append(str(exc))
            else:
                return _show_credentials("Super Admin added", form.full_name.data, email, temp, url_for("admin.admins"))
    people = db.session.scalars(select(User).where(User.is_platform_admin.is_(True)).order_by(User.full_name)).all()
    return render_template("admin/admins.html", people=people, form=form)


# ---------------------------------------------------------------- support desk
@bp.route("/support")
@platform_admin_required
def support():
    status = request.args.get("status", "open_all")
    priority = request.args.get("priority", "")
    q = (request.args.get("q") or "").strip()
    stmt = select(SupportTicket).join(Organization, Organization.id == SupportTicket.organization_id)
    if status == "open_all":
        stmt = stmt.where(SupportTicket.status.in_(OPEN_STATUSES))
    elif status:
        stmt = stmt.where(SupportTicket.status == status)
    if priority:
        stmt = stmt.where(SupportTicket.priority == priority)
    if q:
        like = f"%{q}%"
        digits = re.sub(r"\D", "", q)
        conds = [SupportTicket.subject.ilike(like), Organization.name.ilike(like)]
        if digits:
            conds.append(SupportTicket.id == int(digits))
        stmt = stmt.where(or_(*conds))
    stmt = stmt.order_by(SupportTicket.last_activity_at.desc())
    page = db.paginate(stmt, per_page=PER_PAGE, error_out=False)
    from app.models import TICKET_PRIORITIES, TICKET_STATUSES

    return render_template("admin/support.html", page=page, status=status, priority=priority, q=q,
                           statuses=TICKET_STATUSES, priorities=TICKET_PRIORITIES)


@bp.route("/support/<int:ticket_id>", methods=["GET", "POST"])
@platform_admin_required
def support_ticket(ticket_id):
    ticket = db.session.get(SupportTicket, ticket_id) or abort(404)
    reply = StaffReplyForm(prefix="r")
    triage = TicketTriageForm(obj=ticket, prefix="t")
    staff = db.session.scalars(select(User).where(User.is_platform_admin.is_(True), User.is_active_user.is_(True))
                               .order_by(User.full_name)).all()
    triage.assigned_to_id.choices = [(0, "Unassigned")] + [(u.id, u.full_name) for u in staff]
    if request.method == "GET":
        reply.status.data = "waiting" if ticket.status in {"open", "in_progress"} else ticket.status
        triage.assigned_to_id.data = ticket.assigned_to_id or 0
    if request.method == "POST" and request.form.get("form") == "reply" and reply.validate_on_submit():
        db.session.add(SupportMessage(organization_id=ticket.organization_id, ticket=ticket, author_id=current_user.id,
                                      body=reply.body.data.strip(), is_staff=True, is_internal=reply.internal.data))
        if not reply.internal.data:
            ticket.status = reply.status.data
        ticket.last_activity_at = utcnow()
        if not ticket.assigned_to_id:
            ticket.assigned_to_id = current_user.id
        if not reply.internal.data and ticket.created_by_id:
            from app.services.notify import notify

            notify([ticket.created_by_id], "support", f"Kayron support replied on {ticket.reference}",
                   f"/app/support/{ticket.id}", organization_id=ticket.organization_id)
        audit.record("support.staff_note" if reply.internal.data else "support.staff_replied",
                     summary=f"{'Internal note on' if reply.internal.data else 'Support replied to'} {ticket.reference}",
                     entity=ticket, organization_id=ticket.organization_id)
        db.session.commit()
        flash("Note added." if reply.internal.data else "Reply sent.", "success")
        return redirect(url_for("admin.support_ticket", ticket_id=ticket.id))
    if request.method == "POST" and request.form.get("form") == "triage" and triage.validate_on_submit():
        ticket.status, ticket.priority = triage.status.data, triage.priority.data
        ticket.assigned_to_id = triage.assigned_to_id.data or None
        audit.record("support.triaged", summary=f"{ticket.reference}: {ticket.status_label}, {ticket.priority} priority",
                     entity=ticket, organization_id=ticket.organization_id)
        db.session.commit()
        flash("Ticket updated.", "success")
        return redirect(url_for("admin.support_ticket", ticket_id=ticket.id))
    return render_template("admin/support_ticket.html", ticket=ticket, reply=reply, triage=triage)


# ---------------------------------------------------------------- own account
@bp.route("/account", methods=["GET", "POST"])
@platform_admin_required
def account():
    from flask_login import login_user

    from app.blueprints.workspace.forms import PasswordChangeForm

    pw = PasswordChangeForm(prefix="pw")
    if pw.validate_on_submit():
        if not current_user.check_password(pw.current_password.data):
            pw.current_password.errors.append("Current password is incorrect.")
        elif pw.new_password.data == pw.current_password.data:
            pw.new_password.errors.append("Choose a password different from the current one.")
        else:
            user = current_user._get_current_object()
            user.set_password(pw.new_password.data)
            user.must_change_password = False
            audit.record("user.password_changed", summary="Changed password", entity=user, organization_id=None)
            db.session.commit()
            login_user(user)
            flash("Password changed.", "success")
            return redirect(url_for("admin.dashboard"))
    return render_template("admin/account.html", pw=pw)


from . import billing as _billing  # noqa: E402,F401  (Stage 11 billing console)
