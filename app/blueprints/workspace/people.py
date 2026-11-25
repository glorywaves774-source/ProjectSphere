"""Stage 3: staff, invitations, departments and roles (organization side)."""
from flask import abort, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, select

from app.extensions import db
from app.models import Department, Invitation, Role, User, UserRole
from app.security.catalog import PERMISSIONS
from app.security.rbac import permission_required
from app.services import mailer
from app.services import people as svc

from .forms import DepartmentForm, InviteForm, MemberForm, RoleForm, StaffForm
from .routes import bp


def _actor() -> User:
    return current_user._get_current_object()


def _dept_choices():
    depts = db.session.scalars(select(Department).order_by(Department.name)).all()
    return [(0, "No department")] + [(d.id, d.name) for d in depts]


def _role_choices():
    return [(r.id, r.name) for r in svc.assignable_roles(_actor())]


def _one_time(title: str, *, who: str, email: str, password: str | None = None, link: str | None = None,
              note: str = ""):
    resp = make_response(render_template("workspace/one_time.html", title=title, who=who, email=email,
                                         password=password, link=link, note=note))
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _get_member(user_id: int) -> User:
    member = db.session.get(User, user_id)  # tenant-filtered
    if member is None:
        abort(404)
    return member


# ---------------------------------------------------------------- staff
@bp.route("/team/new", methods=["GET", "POST"])
@permission_required("users.manage")
def staff_new():
    form = StaffForm()
    form.department_id.choices = _dept_choices()
    form.roles.choices = _role_choices()
    if request.method == "GET":
        staff = db.session.scalar(select(Role.id).where(Role.key == "staff"))
        form.roles.data = [staff] if staff in dict(form.roles.choices) else []
    if form.validate_on_submit():
        try:
            user, temp = svc.add_staff(
                _actor(), full_name=form.full_name.data, email=form.email.data, role_ids=form.roles.data,
                job_title=form.job_title.data, phone=form.phone.data, department_id=form.department_id.data,
                staff_number=form.staff_number.data, employment_type=form.employment_type.data,
                start_date=form.start_date.data, weekly_hours=form.weekly_hours.data)
        except svc.PeopleError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            db.session.commit()
            return _one_time(f"{user.full_name} has been added", who=user.full_name, email=user.email, password=temp,
                             note="They'll choose their own password the first time they sign in.")
    return render_template("workspace/staff_form.html", form=form)


@bp.route("/team/<int:user_id>", methods=["GET", "POST"])
@permission_required("users.view")
def member(user_id):
    member = _get_member(user_id)
    can_edit = current_user.can("users.manage")
    form = MemberForm(obj=member)
    form.department_id.choices = _dept_choices()
    assignable = _role_choices()
    # Show the member's current roles even if the actor can't assign them
    current_ids = [r.id for r in member.roles]
    extra = [(r.id, r.name) for r in member.roles if r.id not in dict(assignable)]
    form.roles.choices = assignable + extra
    if request.method == "GET":
        form.roles.data = current_ids
        form.department_id.data = member.department_id or 0
    if request.method == "POST":
        if not can_edit:
            abort(403)
        if form.validate_on_submit():
            try:
                changed = svc.update_member(
                    _actor(), member, role_ids=form.roles.data, full_name=form.full_name.data.strip(),
                    job_title=form.job_title.data or "", phone=form.phone.data or "",
                    department_id=form.department_id.data or None, staff_number=form.staff_number.data or "",
                    employment_type=form.employment_type.data or "", start_date=form.start_date.data,
                    weekly_hours=form.weekly_hours.data or member.weekly_hours)
            except svc.PeopleError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
            else:
                db.session.commit()
                flash("Saved." if changed else "No changes to save.", "success" if changed else "info")
                return redirect(url_for("workspace.member", user_id=member.id))
    manageable = can_edit and not (member.permission_codes - current_user.permission_codes)
    wl = None
    if current_user.can("hr.view") or member.id == current_user.id:
        from app.services.resources import workload

        wl = next((r for r in workload() if r["user"].id == member.id), None)
    return render_template("workspace/member.html", member=member, form=form, can_edit=can_edit, wl=wl,
                           manageable=manageable, locked_roles={rid for rid, _ in extra})


@bp.route("/team/<int:user_id>/<action>", methods=["POST"])
@permission_required("users.manage")
def member_action(user_id, action):
    member = _get_member(user_id)
    try:
        if action == "deactivate":
            svc.set_member_active(_actor(), member, False)
            flash(f"{member.full_name} is deactivated and has been signed out.", "warning")
        elif action == "activate":
            svc.set_member_active(_actor(), member, True)
            flash(f"{member.full_name} can sign in again.", "success")
        elif action == "reset":
            temp = svc.reset_member_password(_actor(), member)
            db.session.commit()
            return _one_time("One-time password issued", who=member.full_name, email=member.email, password=temp,
                             note="Their other sessions have been signed out. They'll choose a new password at sign-in.")
        else:
            abort(404)
    except svc.PeopleError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("workspace.member", user_id=member.id))
    db.session.commit()
    return redirect(url_for("workspace.member", user_id=member.id))


# ---------------------------------------------------------------- invitations
@bp.route("/team/invite", methods=["GET", "POST"])
@permission_required("users.manage")
def invite():
    form = InviteForm()
    form.department_id.choices = _dept_choices()
    form.roles.choices = _role_choices()
    if request.method == "GET":
        staff = db.session.scalar(select(Role.id).where(Role.key == "staff"))
        form.roles.data = [staff] if staff in dict(form.roles.choices) else []
    if form.validate_on_submit():
        try:
            inv, token, sent = svc.invite(_actor(), email=form.email.data, role_ids=form.roles.data,
                                          full_name=form.full_name.data, job_title=form.job_title.data,
                                          department_id=form.department_id.data)
        except svc.PeopleError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            db.session.commit()
            if sent:
                flash(f"Invitation emailed to {inv.email}. It's valid for {Invitation.VALID_DAYS} days.", "success")
                return redirect(url_for("workspace.team"))
            return _one_time("Invitation ready", who=inv.full_name or inv.email, email=inv.email,
                             link=svc.invite_link(token),
                             note=f"Email isn't set up yet, so send this link to them yourself. "
                                  f"It works once and expires in {Invitation.VALID_DAYS} days.")
    return render_template("workspace/invite.html", form=form, mail_on=mailer.is_configured())


@bp.route("/team/invitations/<int:inv_id>/<action>", methods=["POST"])
@permission_required("users.manage")
def invitation_action(inv_id, action):
    inv = db.session.get(Invitation, inv_id) or abort(404)
    try:
        if action == "revoke":
            svc.revoke(inv)
            db.session.commit()
            flash(f"Invitation to {inv.email} revoked.", "info")
        elif action == "resend":
            token, sent = svc.resend(_actor(), inv)
            db.session.commit()
            if not sent:
                return _one_time("New invitation link", who=inv.full_name or inv.email, email=inv.email,
                                 link=svc.invite_link(token), note="The previous link no longer works.")
            flash(f"Invitation re-sent to {inv.email}.", "success")
        else:
            abort(404)
    except svc.PeopleError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("workspace.team"))


# ---------------------------------------------------------------- departments
@bp.route("/departments")
@permission_required("org.view")
def departments():
    depts = db.session.scalars(select(Department).order_by(Department.name)).all()
    counts = dict(db.session.execute(select(User.department_id, func.count(User.id))
                                     .where(User.is_active_user.is_(True)).group_by(User.department_id)).all())
    return render_template("workspace/departments.html", depts=depts, counts=counts)


def _dept_form(dept=None):
    form = DepartmentForm(obj=dept)
    people = db.session.scalars(select(User).where(User.is_active_user.is_(True)).order_by(User.full_name)).all()
    form.head_id.choices = [(0, "No head yet")] + [(u.id, u.full_name) for u in people]
    if request.method == "GET" and dept:
        form.head_id.data = dept.head_id or 0
    return form


@bp.route("/departments/new", methods=["GET", "POST"])
@permission_required("org.manage")
def department_new():
    form = _dept_form()
    if form.validate_on_submit():
        try:
            dept = svc.save_department(_actor(), None, name=form.name.data, code=form.code.data or "",
                                       description=form.description.data or "", head_id=form.head_id.data)
        except svc.PeopleError as exc:
            db.session.rollback()
            form.name.errors.append(str(exc))
        else:
            db.session.commit()
            flash(f"Department {dept.name} created.", "success")
            return redirect(url_for("workspace.departments"))
    return render_template("workspace/department_form.html", form=form, dept=None)


@bp.route("/departments/<int:dept_id>", methods=["GET", "POST"])
@permission_required("org.view")
def department(dept_id):
    dept = db.session.get(Department, dept_id) or abort(404)
    form = _dept_form(dept)
    if request.method == "POST":
        if not current_user.can("org.manage"):
            abort(403)
        if request.form.get("action") == "delete":
            moved = svc.delete_department(dept)
            db.session.commit()
            flash(f"Department deleted. {moved} {'person is' if moved == 1 else 'people are'} now without a department.", "info")
            return redirect(url_for("workspace.departments"))
        if form.validate_on_submit():
            try:
                svc.save_department(_actor(), dept, name=form.name.data, code=form.code.data or "",
                                    description=form.description.data or "", head_id=form.head_id.data)
            except svc.PeopleError as exc:
                db.session.rollback()
                form.name.errors.append(str(exc))
            else:
                db.session.commit()
                flash("Department saved.", "success")
                return redirect(url_for("workspace.department", dept_id=dept.id))
    members = db.session.scalars(select(User).where(User.department_id == dept.id).order_by(User.full_name)).all()
    return render_template("workspace/department_form.html", form=form, dept=dept, members=members,
                           can_edit=current_user.can("org.manage"))


# ---------------------------------------------------------------- roles
def _modules():
    modules: dict[str, list[tuple[str, str]]] = {}
    for code, (module, desc) in PERMISSIONS.items():
        modules.setdefault(module, []).append((code, desc))
    return modules


def _role_page(form, role, selected=None):
    if selected is None:
        selected = set(request.form.getlist("permissions")) if request.method == "POST" \
            else (role.permission_codes if role else set())
    return render_template("workspace/role_form.html", form=form, role=role, modules=_modules(),
                           selected=selected,
                           mine=current_user.permission_codes,
                           members=db.session.scalar(select(func.count()).select_from(UserRole)
                                                     .where(UserRole.role_id == role.id)) if role else 0)


@bp.route("/roles/new", methods=["GET", "POST"])
@permission_required("roles.manage")
def role_new():
    form = RoleForm()
    base = request.args.get("copy", type=int)
    if request.method == "GET" and base:
        src = db.session.get(Role, base)
        if src:
            form.name.data, form.description.data = f"{src.name} (copy)", src.description
            return _role_page(form, None, selected=src.permission_codes & current_user.permission_codes)
    if form.validate_on_submit():
        try:
            role = svc.save_role(_actor(), None, name=form.name.data, description=form.description.data or "",
                                 codes=set(request.form.getlist("permissions")))
        except svc.PeopleError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            db.session.commit()
            flash(f"Role {role.name} created. You can now give it to people from Team.", "success")
            return redirect(url_for("workspace.roles"))
    return _role_page(form, None)


@bp.route("/roles/<int:role_id>", methods=["GET", "POST"])
@permission_required("roles.manage")
def role_edit(role_id):
    role = db.session.get(Role, role_id) or abort(404)
    form = RoleForm(obj=role)
    if request.method == "POST" and request.form.get("action") == "delete":
        try:
            svc.delete_role(role)
        except svc.PeopleError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("workspace.role_edit", role_id=role.id))
        db.session.commit()
        flash("Role deleted.", "info")
        return redirect(url_for("workspace.roles"))
    if form.validate_on_submit():
        try:
            svc.save_role(_actor(), role, name=form.name.data, description=form.description.data or "",
                          codes=set(request.form.getlist("permissions")))
        except svc.PeopleError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            db.session.commit()
            flash("Role saved. People with this role get the new access on their next page.", "success")
            return redirect(url_for("workspace.roles"))
    return _role_page(form, role)
