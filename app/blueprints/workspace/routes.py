from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user
from sqlalchemy import func, or_, select

from app.extensions import db
from app.models import AuditLog, Role, User, UserRole, utcnow
from app.security.rbac import permission_required, tenant_required
from app.services import audit
from app.services.limits import usage_summary

from .forms import OrganizationForm, PasswordChangeForm, ProfileForm

bp = Blueprint("workspace", __name__)

PER_PAGE = 20


@bp.route("/")
@tenant_required
def dashboard():
    org = current_user.organization
    sub = org.subscription
    members = db.session.scalar(select(func.count(User.id)).where(User.is_active_user.is_(True))) or 0
    role_rows = db.session.execute(
        select(Role.name, func.count(UserRole.user_id))
        .join(UserRole, UserRole.role_id == Role.id)
        .join(User, User.id == UserRole.user_id)
        .where(User.is_active_user.is_(True))
        .group_by(Role.name)
        .order_by(func.count(UserRole.user_id).desc())
    ).all()
    recent = []
    if current_user.can("audit.view"):
        recent = db.session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(8)).all()
    proj = None
    my_projects = []
    if current_user.can("projects.view"):
        from app.models import Project, ProjectMember
        from app.services.projects import portfolio_summary

        proj = portfolio_summary()
        my_projects = db.session.scalars(
            select(Project).where(Project.status.in_(["planning", "active", "on_hold"]),
                                  or_(Project.manager_id == current_user.id,
                                      Project.id.in_(select(ProjectMember.project_id)
                                                     .where(ProjectMember.user_id == current_user.id))))
            .order_by(Project.end_date.asc().nulls_last()).limit(6)).all()
    checklist = [
        ("Create your workspace", True, None),
        ("Complete your organization profile", org.profile_complete,
         url_for("workspace.organization") if current_user.can("org.manage") else None),
        ("Add your team members", members > 1,
         url_for("workspace.invite") if current_user.can("users.manage") else None),
        ("Register your first project", bool(proj and proj["total"]),
         url_for("workspace.project_new") if current_user.can("projects.manage") else None),
        ("Choose your subscription plan", sub is not None and sub.status == sub.ACTIVE,
         url_for("workspace.subscription") if current_user.can("subscription.view") else None),
    ]
    from datetime import timedelta as _td

    from app.models import Announcement
    from app.services.collab import upcoming_meetings

    news = db.session.scalars(select(Announcement).where(or_(Announcement.pinned.is_(True),
                                                             Announcement.created_at >= utcnow() - _td(days=14)))
                              .order_by(Announcement.pinned.desc(), Announcement.created_at.desc()).limit(3)).all()
    return render_template(
        "workspace/dashboard.html", news=news, meetings=upcoming_meetings(current_user, 4), org_tz=_org_tz(org),
        org=org, sub=sub, members=members, roles_total=db.session.scalar(select(func.count(Role.id))),
        role_chart={"labels": [r[0] for r in role_rows], "values": [r[1] for r in role_rows]},
        usage=usage_summary(org), recent=recent, checklist=checklist,
        checklist_done=sum(1 for _, done, _ in checklist if done), proj=proj, my_projects=my_projects,
    )


def _org_tz(org):
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(org.timezone or "Africa/Accra")
    except Exception:
        return ZoneInfo("Africa/Accra")


@bp.route("/team")
@permission_required("users.view")
def team():
    from app.models import Department, Invitation

    q = (request.args.get("q") or "").strip()
    status = request.args.get("status", "active")
    dept = request.args.get("dept", type=int)
    stmt = select(User).order_by(User.full_name)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(User.full_name.ilike(like), User.email.ilike(like), User.job_title.ilike(like),
                              User.staff_number.ilike(like)))
    if status == "active":
        stmt = stmt.where(User.is_active_user.is_(True))
    elif status == "inactive":
        stmt = stmt.where(User.is_active_user.is_(False))
    if dept == 0:
        stmt = stmt.where(User.department_id.is_(None))
    elif dept:
        stmt = stmt.where(User.department_id == dept)
    page = db.paginate(stmt, per_page=PER_PAGE, error_out=False)
    departments = db.session.scalars(select(Department).order_by(Department.name)).all()
    invitations = []
    if current_user.can("users.manage"):
        invitations = [i for i in db.session.scalars(
            select(Invitation).where(Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None))
            .order_by(Invitation.created_at.desc()))]
    return render_template("workspace/team.html", page=page, q=q, status=status, dept=dept,
                           departments=departments, invitations=invitations)


@bp.route("/roles")
@permission_required("roles.view")
def roles():
    roles = db.session.scalars(select(Role).order_by(Role.is_system.desc(), Role.name)).all()
    counts = dict(db.session.execute(select(UserRole.role_id, func.count()).group_by(UserRole.role_id)).all())
    from app.security.catalog import PERMISSIONS

    modules: dict[str, list[tuple[str, str]]] = {}
    for code, (module, desc) in PERMISSIONS.items():
        modules.setdefault(module, []).append((code, desc))
    return render_template("workspace/roles.html", roles=roles, counts=counts, modules=modules)


@bp.route("/activity")
@permission_required("audit.view")
def activity():
    q = (request.args.get("q") or "").strip()
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(AuditLog.summary.ilike(like), AuditLog.action.ilike(like)))
    page = db.paginate(stmt, per_page=PER_PAGE, error_out=False)
    return render_template("workspace/activity.html", page=page, q=q)


@bp.route("/organization", methods=["GET", "POST"])
@permission_required("org.view")
def organization():
    org = current_user.organization
    can_edit = current_user.can("org.manage")
    form = OrganizationForm(obj=org)
    if request.method == "POST":
        if not can_edit:
            abort(403)
        if form.validate_on_submit():
            changed = [f.name for f in form if f.name != "csrf_token" and getattr(org, f.name, None) != f.data]
            form.populate_obj(org)
            audit.record("org.updated", summary="Organization profile updated", entity=org,
                         details={"fields": changed})
            db.session.commit()
            flash("Organization profile saved.", "success")
            return redirect(url_for("workspace.organization"))
    return render_template("workspace/organization.html", form=form, org=org, can_edit=can_edit)


@bp.route("/account", methods=["GET", "POST"])
@tenant_required
def account():
    profile = ProfileForm(obj=current_user, prefix="p")
    if request.method == "POST" and request.form.get("form") == "notify":
        current_user.email_notify = request.form.get("email_notify") == "y"
        audit.record("user.notifications_changed", summary="Email notifications turned "
                     + ("on" if current_user.email_notify else "off"), entity=current_user)
        db.session.commit()
        flash("Email notifications " + ("on." if current_user.email_notify else "off."), "success")
        return redirect(url_for("workspace.account"))
    pw = PasswordChangeForm(prefix="pw")
    if request.method == "POST" and request.form.get("form") == "profile" and profile.validate_on_submit():
        profile.populate_obj(current_user)
        audit.record("user.profile_updated", summary="Updated own profile", entity=current_user)
        db.session.commit()
        flash("Profile saved.", "success")
        return redirect(url_for("workspace.account"))
    if request.method == "POST" and request.form.get("form") == "password" and pw.validate_on_submit():
        if not current_user.check_password(pw.current_password.data):
            pw.current_password.errors.append("Current password is incorrect.")
        elif pw.new_password.data == pw.current_password.data:
            pw.new_password.errors.append("Choose a password different from the current one.")
        else:
            current_user.set_password(pw.new_password.data)  # signs out other sessions
            current_user.must_change_password = False
            audit.record("user.password_changed", summary="Changed password", entity=current_user)
            db.session.commit()
            login_user(current_user._get_current_object())  # keep this session with the new session version
            flash("Password changed. Other devices have been signed out.", "success")
            return redirect(url_for("workspace.account"))
    return render_template("workspace/account.html", profile=profile, pw=pw)


# ---------------------------------------------------------------- support
def _visible_tickets():
    """Organization admins see every request from their organization; others see their own."""
    from app.models import SupportTicket

    stmt = select(SupportTicket)
    if not current_user.can("org.manage"):
        stmt = stmt.where(SupportTicket.created_by_id == current_user.id)
    return stmt


@bp.route("/support")
@tenant_required
def support():
    from app.models import SupportTicket

    status = request.args.get("status", "open")
    stmt = _visible_tickets()
    if status == "open":
        stmt = stmt.where(SupportTicket.status.in_(["open", "in_progress", "waiting"]))
    elif status == "closed":
        stmt = stmt.where(SupportTicket.status.in_(["resolved", "closed"]))
    page = db.paginate(stmt.order_by(SupportTicket.last_activity_at.desc()), per_page=PER_PAGE, error_out=False)
    return render_template("workspace/support.html", page=page, status=status)


@bp.route("/support/new", methods=["GET", "POST"])
@tenant_required
def support_new():
    from app.models import SupportMessage, SupportTicket

    from .forms import TicketForm

    form = TicketForm()
    if form.validate_on_submit():
        ticket = SupportTicket(subject=form.subject.data.strip(), category=form.category.data,
                               priority=form.priority.data, created_by_id=current_user.id)
        db.session.add(ticket)
        db.session.flush()
        db.session.add(SupportMessage(ticket_id=ticket.id, author_id=current_user.id, body=form.body.data.strip()))
        audit.record("support.created", summary=f"Opened support request {ticket.reference}: {ticket.subject}",
                     entity=ticket)
        db.session.commit()
        flash(f"Support request {ticket.reference} sent. We'll reply here.", "success")
        return redirect(url_for("workspace.support_ticket", ticket_id=ticket.id))
    return render_template("workspace/support_new.html", form=form)


@bp.route("/support/<int:ticket_id>", methods=["GET", "POST"])
@tenant_required
def support_ticket(ticket_id):
    from app.models import SupportMessage, SupportTicket, utcnow

    from .forms import TicketReplyForm

    ticket = db.session.scalar(_visible_tickets().where(SupportTicket.id == ticket_id))
    if ticket is None:
        abort(404)
    form = TicketReplyForm()
    action = request.form.get("action")
    if request.method == "POST" and action == "close":
        ticket.status = "closed"
        ticket.last_activity_at = utcnow()
        audit.record("support.closed", summary=f"Closed support request {ticket.reference}", entity=ticket)
        db.session.commit()
        flash("Request closed. You can reopen it by replying.", "info")
        return redirect(url_for("workspace.support_ticket", ticket_id=ticket.id))
    if request.method == "POST" and action == "reply" and form.validate_on_submit():
        db.session.add(SupportMessage(ticket_id=ticket.id, author_id=current_user.id, body=form.body.data.strip()))
        ticket.status = "open"  # a customer reply always puts it back in the queue
        ticket.last_activity_at = utcnow()
        audit.record("support.replied", summary=f"Replied on {ticket.reference}", entity=ticket)
        db.session.commit()
        flash("Reply sent.", "success")
        return redirect(url_for("workspace.support_ticket", ticket_id=ticket.id))
    return render_template("workspace/support_ticket.html", ticket=ticket, form=form)


from . import billing, collab, finance, me, people, procurement, projects, resources, risk, tasks  # noqa: E402,F401  (Stage 3-11)
