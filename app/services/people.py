"""Stage 3 business rules: staff, invitations, departments and roles.

Safeguards against privilege escalation:
* You can only give someone roles whose permissions you already have.
* You can only change people (or roles) whose access doesn't exceed your own.
* The Organization Admin role always has every permission and can't be edited.
* An organization always keeps at least one active Organization Admin.
"""
import re

from flask import url_for
from sqlalchemy import func, select

from app.extensions import db
from app.models import Department, Invitation, Permission, Role, User, UserRole, utcnow
from app.tenancy import auth_lookup_scope, tenant_scope

from . import audit, mailer
from .limits import LimitExceeded, ensure_within_limit
from .onboarding import temporary_password


class PeopleError(ValueError):
    pass


# ---------------------------------------------------------------- permission helpers
def role_permission_codes(role_ids) -> set[str]:
    if not role_ids:
        return set()
    codes: set[str] = set()
    for role in db.session.scalars(select(Role).where(Role.id.in_(role_ids))):
        codes |= role.permission_codes
    return codes


def assignable_roles(actor: User) -> list[Role]:
    mine = actor.permission_codes
    roles = db.session.scalars(select(Role).order_by(Role.is_system.desc(), Role.name)).all()
    return [r for r in roles if r.permission_codes <= mine]


def check_roles_assignable(actor: User, role_ids: list[int]) -> list[Role]:
    roles = db.session.scalars(select(Role).where(Role.id.in_(role_ids or [-1]))).all()
    if len(roles) != len(set(role_ids)):
        raise PeopleError("One of the selected roles doesn't exist.")
    extra = set().union(*(r.permission_codes for r in roles)) - actor.permission_codes if roles else set()
    if extra:
        raise PeopleError("You can't give someone more access than you have yourself.")
    if not roles:
        raise PeopleError("Choose at least one role.")
    return roles


def check_can_manage(actor: User, member: User) -> None:
    if member.permission_codes - actor.permission_codes:
        raise PeopleError("This person has access you don't have, so only someone with at least their access can change them.")


def active_admin_count() -> int:
    return db.session.scalar(
        select(func.count(func.distinct(User.id))).join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(User.is_active_user.is_(True), Role.key == "org_admin")
    ) or 0


def _check_keeps_an_admin(member: User, losing_admin: bool) -> None:
    if losing_admin and member.is_active_user and "org_admin" in member.role_keys and active_admin_count() <= 1:
        raise PeopleError("This is the only active Organization Admin. Make someone else an admin first.")


def _set_roles(member: User, roles: list[Role]) -> None:
    db.session.execute(UserRole.__table__.delete().where(UserRole.user_id == member.id))
    for r in roles:
        db.session.add(UserRole(organization_id=member.organization_id, user_id=member.id, role_id=r.id))
    db.session.flush()
    db.session.expire(member, ["roles"])


# ---------------------------------------------------------------- seats
def pending_invitation_count() -> int:
    return db.session.scalar(select(func.count(Invitation.id)).where(
        Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None), Invitation.expires_at > utcnow())) or 0


def ensure_seat(org, *, counting_invites: bool = True) -> None:
    """Plan limit on team members, counting pending invitations as reserved seats."""
    try:
        ensure_within_limit(org, "users", adding=1 + (pending_invitation_count() if counting_invites else 0))
    except LimitExceeded as exc:
        raise PeopleError(f"{exc} Pending invitations count towards the limit.") from exc


def email_in_use(email: str) -> bool:
    with auth_lookup_scope():
        return bool(db.session.scalar(select(func.count(User.id)).where(func.lower(User.email) == email.lower())))


# ---------------------------------------------------------------- staff
def _department(dept_id):
    if not dept_id:
        return None
    dept = db.session.get(Department, dept_id)  # tenant-filtered: another org's id finds nothing
    if dept is None:
        raise PeopleError("Choose a valid department.")
    return dept


def add_staff(actor: User, *, full_name: str, email: str, role_ids: list[int], job_title="", phone="",
              department_id=None, staff_number="", employment_type="", start_date=None,
              weekly_hours=None) -> tuple[User, str]:
    email = email.strip().lower()
    org = actor.organization
    roles = check_roles_assignable(actor, role_ids)
    ensure_seat(org)
    if email_in_use(email):
        raise PeopleError("Someone already uses this email in ProjectSphere.")
    _department(department_id)
    temp = temporary_password()
    user = User(organization_id=org.id, email=email, full_name=full_name.strip(), job_title=job_title or "",
                phone=phone or "", department_id=department_id or None, staff_number=staff_number or "",
                employment_type=employment_type or "", start_date=start_date, must_change_password=True,
                weekly_hours=weekly_hours or 40)
    user.set_password(temp)
    db.session.add(user)
    db.session.flush()
    _set_roles(user, roles)
    audit.record("people.staff_added", summary=f"Added {user.full_name} as {', '.join(r.name for r in roles)}",
                 entity=user)
    return user, temp


def update_member(actor: User, member: User, *, role_ids: list[int], **fields) -> list[str]:
    check_can_manage(actor, member)
    roles = check_roles_assignable(actor, role_ids)
    new_keys = {r.key for r in roles}
    _check_keeps_an_admin(member, losing_admin="org_admin" not in new_keys)
    if "department_id" in fields:
        _department(fields["department_id"])
        fields["department_id"] = fields["department_id"] or None
    changed = [k for k, v in fields.items() if getattr(member, k) != v]
    for k, v in fields.items():
        setattr(member, k, v if v is not None or k in {"department_id", "start_date"} else "")
    old_roles = {r.id for r in member.roles}
    if old_roles != {r.id for r in roles}:
        _set_roles(member, roles)  # takes effect on their next page load
        changed.append("roles")
    if changed:
        audit.record("people.member_updated", summary=f"Updated {member.full_name}: {', '.join(changed)}",
                     entity=member, details={"fields": changed})
    return changed


def set_member_active(actor: User, member: User, active: bool) -> None:
    if member.id == actor.id:
        raise PeopleError("You can't deactivate your own account.")
    check_can_manage(actor, member)
    if active:
        ensure_seat(actor.organization, counting_invites=False)
    else:
        _check_keeps_an_admin(member, losing_admin=True)
        member.session_version += 1
    member.is_active_user = active
    audit.record("people.member_activated" if active else "people.member_deactivated",
                 summary=f"{'Reactivated' if active else 'Deactivated'} {member.full_name}", entity=member)


def reset_member_password(actor: User, member: User) -> str:
    if member.id == actor.id:
        raise PeopleError("Change your own password from My account.")
    check_can_manage(actor, member)
    temp = temporary_password()
    member.set_password(temp)
    member.must_change_password = True
    member.failed_logins, member.locked_until = 0, None
    audit.record("people.password_reset", summary=f"Issued a one-time password to {member.full_name}", entity=member)
    return temp


# ---------------------------------------------------------------- invitations
def invite(actor: User, *, email: str, role_ids: list[int], full_name="", job_title="",
           department_id=None) -> tuple[Invitation, str, bool]:
    email = email.strip().lower()
    org = actor.organization
    roles = check_roles_assignable(actor, role_ids)
    if email_in_use(email):
        raise PeopleError("Someone already uses this email in ProjectSphere.")
    existing = db.session.scalar(select(Invitation).where(func.lower(Invitation.email) == email,
                                                         Invitation.accepted_at.is_(None),
                                                         Invitation.revoked_at.is_(None),
                                                         Invitation.expires_at > utcnow()))
    if existing:
        raise PeopleError("This person already has a pending invitation. Resend or revoke it instead.")
    ensure_seat(org)
    _department(department_id)
    inv = Invitation(organization_id=org.id, email=email, full_name=(full_name or "").strip(),
                     job_title=job_title or "", department_id=department_id or None,
                     role_ids=[r.id for r in roles], invited_by_id=actor.id)
    token = inv.issue_token()
    db.session.add(inv)
    db.session.flush()
    audit.record("people.invited", summary=f"Invited {email} as {', '.join(r.name for r in roles)}", entity=inv)
    sent = send_invitation_email(inv, token, actor)
    return inv, token, sent


def resend(actor: User, inv: Invitation) -> tuple[str, bool]:
    if inv.accepted_at or inv.revoked_at:
        raise PeopleError("Only open invitations can be resent.")
    token = inv.issue_token()  # the old link stops working
    audit.record("people.invite_resent", summary=f"Resent invitation to {inv.email}", entity=inv)
    return token, send_invitation_email(inv, token, actor)


def revoke(inv: Invitation) -> None:
    if inv.accepted_at:
        raise PeopleError("This invitation was already accepted.")
    inv.revoked_at = utcnow()
    audit.record("people.invite_revoked", summary=f"Revoked invitation to {inv.email}", entity=inv)


def invite_link(token: str) -> str:
    return url_for("public.accept_invite", token=token, _external=True)


def send_invitation_email(inv: Invitation, token: str, actor: User) -> bool:
    link = invite_link(token)
    org_name = actor.organization.name
    body = (f"Hello{(' ' + inv.full_name) if inv.full_name else ''},\n\n"
            f"{actor.full_name} has invited you to join {org_name} on Laari ProjectSphere.\n\n"
            f"Accept the invitation and choose your password here (valid for {Invitation.VALID_DAYS} days):\n{link}\n\n"
            "If you weren't expecting this, you can ignore this email.\n")
    return mailer.send(inv.email, f"You're invited to join {org_name} on Laari ProjectSphere", body)


def find_invitation(token: str) -> Invitation | None:
    """Look up an invitation from its link, before we know which organization it belongs to."""
    if not token or len(token) > 100:
        return None
    with auth_lookup_scope():
        inv = db.session.scalar(select(Invitation).where(Invitation.token_hash == Invitation.hash_token(token)))
        if inv is not None:
            _ = inv.organization, inv.department
    return inv


def accept(token: str, *, full_name: str, password: str) -> User:
    inv = find_invitation(token)
    if inv is None or not inv.is_pending:
        raise PeopleError("This invitation link is no longer valid. Ask your administrator for a new one.")
    if inv.organization.is_suspended:
        raise PeopleError("This organization's workspace is suspended.")
    if email_in_use(inv.email):
        raise PeopleError("An account with this email already exists. Sign in instead.")
    with tenant_scope(inv.organization_id):
        inv = db.session.get(Invitation, inv.id)
        org = inv.organization
        ensure_within_limit(org, "users")
        user = User(organization_id=org.id, email=inv.email, full_name=full_name.strip(), job_title=inv.job_title,
                    department_id=inv.department_id)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        roles = db.session.scalars(select(Role).where(Role.id.in_(inv.role_ids or [-1]))).all()
        if not roles:
            roles = [db.session.scalar(select(Role).where(Role.key == "staff"))]
        _set_roles(user, roles)
        inv.accepted_at = utcnow()
        from .notify import notify

        if inv.invited_by_id:
            notify([inv.invited_by_id], "people", f"{user.full_name} accepted your invitation and joined",
                   f"/app/team/{user.id}", actor=user)
        audit.record("people.invite_accepted", summary=f"{user.full_name} accepted the invitation and joined",
                     entity=user, user=user)
        db.session.commit()
        db.session.refresh(user)
        _ = user.roles
    return user


# ---------------------------------------------------------------- roles
def _slug(name: str) -> str:
    return "custom_" + (re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or "role")


def _permissions(codes: set[str]) -> list[Permission]:
    perms = db.session.scalars(select(Permission).where(Permission.code.in_(codes or {"-"}))).all()
    if len(perms) != len(codes):
        raise PeopleError("Unknown permission selected.")
    return perms


def save_role(actor: User, role: Role | None, *, name: str, description: str, codes: set[str]) -> Role:
    if role is not None and role.key == "org_admin":
        raise PeopleError("The Organization Admin role always has full access and can't be changed.")
    if codes - actor.permission_codes:
        raise PeopleError("You can't grant permissions you don't have yourself.")
    if not codes:
        raise PeopleError("Choose at least one permission.")
    name = name.strip()
    clash = db.session.scalar(select(Role).where(func.lower(Role.name) == name.lower(),
                                                 Role.id != (role.id if role else -1)))
    if clash:
        raise PeopleError("Another role already has this name.")
    creating = role is None
    if creating:
        key = _slug(name)
        while db.session.scalar(select(Role.id).where(Role.key == key)):
            key += "_x"
        role = Role(organization_id=actor.organization_id, key=key, is_system=False)
        db.session.add(role)
    before = role.permission_codes if not creating else set()
    role.name, role.description = name, description.strip()
    role.permissions = _permissions(codes)
    db.session.flush()  # people holding this role get the new access on their next page load
    added, removed = codes - before, before - codes
    audit.record("people.role_created" if creating else "people.role_updated",
                 summary=f"{'Created' if creating else 'Updated'} role {role.name}"
                         + (f" (+{len(added)} / −{len(removed)} permissions)" if not creating else ""),
                 entity=role, details={"added": sorted(added), "removed": sorted(removed)})
    return role


def delete_role(role: Role) -> None:
    if role.is_system:
        raise PeopleError("Built-in roles can't be deleted. You can change their permissions instead.")
    members = db.session.scalar(select(func.count()).select_from(UserRole).where(UserRole.role_id == role.id))
    if members:
        raise PeopleError(f"{members} {'person has' if members == 1 else 'people have'} this role. Move them to another role first.")
    audit.record("people.role_deleted", summary=f"Deleted role {role.name}", entity=role)
    db.session.delete(role)


# ---------------------------------------------------------------- departments
def save_department(actor: User, dept: Department | None, *, name: str, code: str, description: str,
                    head_id: int | None) -> Department:
    name = name.strip()
    clash = db.session.scalar(select(Department).where(func.lower(Department.name) == name.lower(),
                                                       Department.id != (dept.id if dept else -1)))
    if clash:
        raise PeopleError("A department with this name already exists.")
    if head_id:
        head = db.session.get(User, head_id)
        if head is None or not head.is_active_user:
            raise PeopleError("Choose an active team member as head.")
    creating = dept is None
    if creating:
        dept = Department(organization_id=actor.organization_id)
        db.session.add(dept)
    dept.name, dept.code, dept.description, dept.head_id = name, code.strip().upper(), description.strip(), head_id or None
    db.session.flush()
    audit.record("people.department_created" if creating else "people.department_updated",
                 summary=f"{'Created' if creating else 'Updated'} department {dept.name}", entity=dept)
    return dept


def delete_department(dept: Department) -> int:
    moved = db.session.execute(User.__table__.update().where(User.department_id == dept.id)
                               .values(department_id=None)).rowcount
    db.session.execute(Invitation.__table__.update().where(Invitation.department_id == dept.id)
                       .values(department_id=None))
    audit.record("people.department_deleted", summary=f"Deleted department {dept.name} ({moved} people unassigned)",
                 entity=dept)
    db.session.delete(dept)
    return moved
