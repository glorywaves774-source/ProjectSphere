"""Stage 8: documents, notifications, messages, announcements and meetings."""
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from flask import Response, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_, select

from app.extensions import db
from app.models import (DOCUMENT_CATEGORIES, Announcement, Conversation, Document, DocumentVersion, Meeting,
                        MeetingAttendee, Notification, Project, User, utcnow)
from app.security.rbac import permission_required, tenant_required
from app.services import audit
from app.services import collab as csvc
from app.services import documents as dsvc
from app.services import notify as nsvc
from app.services.storage import StorageError

from .forms import (AnnouncementForm, DocumentForm, MeetingForm, MessageForm, MinutesForm, NewConversationForm,
                    TaskCommentForm, VersionForm)
from .routes import PER_PAGE, bp


def _actor() -> User:
    return current_user._get_current_object()


def _people(exclude_me=True):
    stmt = select(User).where(User.is_active_user.is_(True)).order_by(User.full_name)
    return [u for u in db.session.scalars(stmt) if not (exclude_me and u.id == current_user.id)]


def _open_projects():
    return db.session.scalars(select(Project).where(Project.status.in_(["planning", "active", "on_hold"]))
                              .order_by(Project.name)).all()


# ---------------------------------------------------------------- documents
def _doc_query(project_id=None):
    a = request.args
    stmt = select(Document)
    if project_id:
        stmt = stmt.where(Document.project_id == project_id)
    elif a.get("project") == "org":
        stmt = stmt.where(Document.project_id.is_(None))
    elif a.get("project", type=int):
        stmt = stmt.where(Document.project_id == a.get("project", type=int))
    if not current_user.can("projects.view"):
        stmt = stmt.where(Document.project_id.is_(None))
    if a.get("category") in dict(DOCUMENT_CATEGORIES):
        stmt = stmt.where(Document.category == a["category"])
    stmt = stmt.where(Document.is_archived.is_(a.get("archived") == "1"))
    q = (a.get("q") or "").strip()
    if q:
        stmt = stmt.where(or_(Document.title.ilike(f"%{q}%"), Document.description.ilike(f"%{q}%")))
    return stmt.order_by(Document.updated_at.desc())


@bp.route("/documents")
@permission_required("documents.view")
def documents():
    page = db.paginate(_doc_query(), per_page=PER_PAGE, error_out=False)
    used = dsvc.storage_used_bytes(current_user.organization_id)
    plan = current_user.organization.subscription.plan if current_user.organization.subscription else None
    return render_template("workspace/documents.html", page=page, projects=_open_projects(),
                           categories=DOCUMENT_CATEGORIES, project=None, used=used,
                           limit_mb=plan.max_storage_mb if plan else None,
                           can_upload=dsvc.can_upload(current_user, None))


@bp.route("/projects/<int:project_id>/documents")
@permission_required("documents.view")
def project_documents(project_id):
    project = db.session.get(Project, project_id) or abort(404)
    if not current_user.can("projects.view"):
        abort(403)
    page = db.paginate(_doc_query(project.id), per_page=PER_PAGE, error_out=False)
    return render_template("workspace/documents.html", page=page, projects=[], categories=DOCUMENT_CATEGORIES,
                           project=project, used=None, limit_mb=None,
                           can_upload=dsvc.can_upload(current_user, project) and project.is_open)


@bp.route("/documents/new", methods=["GET", "POST"])
@permission_required("documents.view")
def document_new():
    form = DocumentForm()
    allowed = [p for p in _open_projects() if dsvc.can_upload(current_user, p)]
    form.project_id.choices = ([(0, "Organization library (no project)")] if dsvc.can_upload(current_user, None) else []) \
        + [(p.id, f"{p.code} · {p.name}") for p in allowed]
    if not form.project_id.choices:
        abort(403)
    if request.method == "GET":
        form.project_id.data = request.args.get("project", 0, type=int)
    if form.validate_on_submit():
        project = db.session.get(Project, form.project_id.data) if form.project_id.data else None
        try:
            doc = dsvc.upload(_actor(), project=project, title=form.title.data, category=form.category.data,
                              description=form.description.data or "", file_storage=form.file.data)
        except dsvc.DocumentError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            db.session.commit()
            flash("Document uploaded.", "success")
            return redirect(url_for("workspace.document", doc_id=doc.id))
    return render_template("workspace/document_new.html", form=form,
                           max_mb=current_app.config["MAX_UPLOAD_MB"],
                           extensions=", ".join(sorted({e.upper() for e in dsvc.ALLOWED if e != "jpeg"})))


@bp.route("/documents/<int:doc_id>", methods=["GET", "POST"])
@permission_required("documents.view")
def document(doc_id):
    doc = db.session.get(Document, doc_id) or abort(404)
    if not dsvc.can_view(current_user, doc):
        abort(403)
    vform, cform = VersionForm(prefix="v"), TaskCommentForm(prefix="c")
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "version" and vform.validate_on_submit():
                v = dsvc.add_version(_actor(), doc, vform.file.data, vform.note.data or "")
                msg = f"Version {v.version_no} uploaded."
            elif action == "comment" and cform.validate_on_submit():
                dsvc.comment(_actor(), doc, cform.body.data)
                msg = "Comment added."
            elif action in ("archive", "restore"):
                dsvc.set_archived(_actor(), doc, action == "archive")
                msg = "Document archived." if action == "archive" else "Document restored."
            elif action == "delete_version":
                v = db.session.get(DocumentVersion, request.form.get("version", type=int) or 0)
                if v is None or v.document_id != doc.id:
                    abort(404)
                dsvc.delete_version(_actor(), v)
                msg = "Version deleted."
            elif action == "delete":
                back = url_for("workspace.project_documents", project_id=doc.project_id) if doc.project_id \
                    else url_for("workspace.documents")
                dsvc.delete(_actor(), doc)
                db.session.commit()
                flash("Document deleted.", "info")
                return redirect(back)
            else:
                errors = [e for f in (vform, cform) for errs in f.errors.values() for e in errs]
                raise dsvc.DocumentError(errors[0] if errors else "Please check the form.")
        except dsvc.DocumentError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            db.session.commit()
            flash(msg, "success")
        return redirect(url_for("workspace.document", doc_id=doc.id))
    return render_template("workspace/document.html", doc=doc, vform=vform, cform=cform,
                           can_manage=dsvc.can_manage(current_user, doc),
                           can_upload=dsvc.can_upload(current_user, doc.project) or dsvc.can_manage(current_user, doc))


@bp.route("/documents/<int:doc_id>/versions/<int:version_id>/download")
@permission_required("documents.view")
def document_download(doc_id, version_id):
    v = db.session.get(DocumentVersion, version_id)
    if v is None or v.document_id != doc_id:
        abort(404)
    try:
        data = dsvc.read(_actor(), v)
    except dsvc.DocumentError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("workspace.document", doc_id=doc_id))
    except StorageError:
        flash("This file couldn't be found in storage. Contact your administrator.", "danger")
        return redirect(url_for("workspace.document", doc_id=doc_id))
    db.session.commit()
    ascii_name = v.filename.encode("ascii", "ignore").decode() or f"document.{v.extension}"
    return Response(data, mimetype=v.content_type, headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(v.filename)}",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Cache-Control": "private, no-store",
    })


# Talisman would otherwise replace the download's own locked-down policy with the site-wide one.
document_download.talisman_view_options = {"content_security_policy": "default-src 'none'; sandbox"}


# ---------------------------------------------------------------- notifications
@bp.route("/notifications", methods=["GET", "POST"])
@tenant_required
def notifications():
    if request.method == "POST":
        ids = [int(x) for x in request.form.getlist("id") if x.isdigit()]
        nsvc.mark_read(current_user, ids or None)
        db.session.commit()
        return redirect(url_for("workspace.notifications", show=request.args.get("show", "")))
    show = request.args.get("show", "")
    stmt = select(Notification).where(Notification.user_id == current_user.id)
    if show == "unread":
        stmt = stmt.where(Notification.read_at.is_(None))
    page = db.paginate(stmt.order_by(Notification.created_at.desc()), per_page=30, error_out=False)
    return render_template("workspace/notifications.html", page=page, show=show)


@bp.route("/notifications/<int:nid>/open")
@tenant_required
def notification_open(nid):
    n = db.session.get(Notification, nid)
    if n is None or n.user_id != current_user.id:
        abort(404)
    if n.read_at is None:
        n.read_at = utcnow()
        db.session.commit()
    target = n.link if n.link.startswith("/app/") else url_for("workspace.notifications")
    return redirect(target)


# ---------------------------------------------------------------- messages
@bp.route("/messages")
@tenant_required
def messages():
    return render_template("workspace/messages.html", rows=csvc.inbox(current_user), conv=None)


@bp.route("/messages/new", methods=["GET", "POST"])
@tenant_required
def message_new():
    form = NewConversationForm()
    form.people.choices = [(u.id, u.full_name) for u in _people()]
    if request.method == "GET" and request.args.get("to", type=int):
        form.people.data = [request.args.get("to", type=int)]
    if form.validate_on_submit():
        try:
            ids = form.people.data or []
            if len(ids) == 1:
                conv = csvc.direct(_actor(), ids[0])
            else:
                conv = csvc.group(_actor(), form.title.data or "", ids)
        except csvc.CollabError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            db.session.commit()
            return redirect(url_for("workspace.conversation", conv_id=conv.id))
    return render_template("workspace/message_new.html", form=form)


@bp.route("/messages/<int:conv_id>", methods=["GET", "POST"])
@tenant_required
def conversation(conv_id):
    conv = db.session.get(Conversation, conv_id) or abort(404)
    if not csvc.can_access(current_user, conv):
        abort(403)
    form = MessageForm()
    if request.method == "POST":
        try:
            if not form.validate_on_submit():
                raise csvc.CollabError("Write a message first.")
            csvc.send(_actor(), conv, form.body.data)
        except csvc.CollabError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            db.session.commit()
        return redirect(url_for("workspace.conversation", conv_id=conv.id, _anchor="latest"))
    csvc.mark_read(current_user, conv)
    db.session.commit()
    return render_template("workspace/messages.html", rows=csvc.inbox(current_user), conv=conv, form=form,
                           title=csvc.display_name(conv, current_user))


@bp.route("/projects/<int:project_id>/discussion")
@permission_required("projects.view")
def project_discussion(project_id):
    project = db.session.get(Project, project_id) or abort(404)
    try:
        conv = csvc.project_channel(_actor(), project)
    except csvc.CollabError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
        return redirect(url_for("workspace.project", project_id=project.id))
    db.session.commit()
    return redirect(url_for("workspace.conversation", conv_id=conv.id))


# ---------------------------------------------------------------- announcements
@bp.route("/announcements", methods=["GET", "POST"])
@tenant_required
def announcements():
    form = AnnouncementForm()
    if request.method == "POST":
        if not current_user.can("org.manage"):
            abort(403)
        action = request.form.get("action", "post")
        if action in ("delete", "pin", "unpin"):
            a = db.session.get(Announcement, request.form.get("id", type=int) or 0) or abort(404)
            if action == "delete":
                audit.record("collab.announcement_deleted", summary=f"Deleted announcement \u201c{a.title}\u201d", entity=a)
                db.session.delete(a)
            else:
                a.pinned = action == "pin"
                audit.record("collab.announcement_pinned", summary=f"{'Pinned' if a.pinned else 'Unpinned'} \u201c{a.title}\u201d",
                             entity=a)
            db.session.commit()
            return redirect(url_for("workspace.announcements"))
        if form.validate_on_submit():
            try:
                csvc.announce(_actor(), form.title.data, form.body.data, bool(form.pinned.data))
            except csvc.CollabError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
            else:
                db.session.commit()
                flash("Announcement posted. Everyone has been notified.", "success")
                return redirect(url_for("workspace.announcements"))
    items = db.session.scalars(select(Announcement).order_by(Announcement.pinned.desc(),
                                                             Announcement.created_at.desc()).limit(50)).all()
    return render_template("workspace/announcements.html", items=items, form=form,
                           can_post=current_user.can("org.manage"))


# ---------------------------------------------------------------- meetings
def _tz():
    try:
        return ZoneInfo(current_user.organization.timezone or "Africa/Accra")
    except Exception:
        return ZoneInfo("Africa/Accra")


def _meeting_form(m: Meeting | None = None):
    form = MeetingForm(obj=m)
    form.project_id.choices = [(0, "No project")] + [(p.id, f"{p.code} · {p.name}") for p in _open_projects()]
    form.attendees.choices = [(u.id, u.full_name) for u in _people()]
    if request.method == "GET":
        if m:
            local = m.starts_at.astimezone(_tz())
            form.day.data, form.time.data = local.date(), local.time().replace(second=0, microsecond=0)
            form.project_id.data = m.project_id or 0
            form.attendees.data = [a.user_id for a in m.attendees if a.user_id != current_user.id]
        else:
            form.project_id.data = request.args.get("project", 0, type=int)
            if form.project_id.data:
                p = db.session.get(Project, form.project_id.data)
                form.attendees.data = [mm.user_id for mm in p.members if mm.user_id != current_user.id] if p else []
    return form


def _save_meeting(form, m):
    starts = datetime.combine(form.day.data, form.time.data).replace(tzinfo=_tz())
    return csvc.save_meeting(_actor(), m, title=form.title.data, starts_at=starts, duration_min=form.duration_min.data,
                             location=form.location.data or "", agenda=form.agenda.data or "",
                             project_id=form.project_id.data or None, attendee_ids=form.attendees.data or [])


@bp.route("/meetings")
@tenant_required
def meetings():
    past = request.args.get("when") == "past"
    now = utcnow() - timedelta(hours=1)
    stmt = select(Meeting).join(MeetingAttendee).where(MeetingAttendee.user_id == current_user.id)
    stmt = stmt.where(Meeting.starts_at < now).order_by(Meeting.starts_at.desc()) if past else \
        stmt.where(Meeting.starts_at >= now).order_by(Meeting.starts_at)
    items = db.session.scalars(stmt.limit(100)).unique().all()
    return render_template("workspace/meetings.html", items=items, past=past, tz=_tz())


@bp.route("/meetings/new", methods=["GET", "POST"])
@tenant_required
def meeting_new():
    form = _meeting_form()
    if form.validate_on_submit():
        try:
            m = _save_meeting(form, None)
        except csvc.CollabError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            db.session.commit()
            flash("Meeting scheduled. Invitations sent.", "success")
            return redirect(url_for("workspace.meeting", meeting_id=m.id))
    return render_template("workspace/meeting_form.html", form=form, m=None)


@bp.route("/meetings/<int:meeting_id>", methods=["GET", "POST"])
@tenant_required
def meeting(meeting_id):
    m = db.session.get(Meeting, meeting_id) or abort(404)
    attending = any(a.user_id == current_user.id for a in m.attendees)
    if not (attending or csvc.can_edit_meeting(current_user, m)):
        abort(403)
    mform = MinutesForm(obj=m)
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "minutes" and mform.validate_on_submit():
                csvc.save_minutes(_actor(), m, mform.minutes.data or "")
                msg = "Minutes saved."
            elif action == "cancel":
                csvc.cancel_meeting(_actor(), m)
                msg = "Meeting cancelled. Attendees have been told."
            else:
                raise csvc.CollabError("Please check the form.")
        except csvc.CollabError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            db.session.commit()
            flash(msg, "success")
        return redirect(url_for("workspace.meeting", meeting_id=m.id))
    can_edit = csvc.can_edit_meeting(current_user, m)
    return render_template("workspace/meeting.html", m=m, mform=mform, tz=_tz(), can_edit=can_edit,
                           can_minutes=can_edit or attending)


@bp.route("/meetings/<int:meeting_id>/edit", methods=["GET", "POST"])
@tenant_required
def meeting_edit(meeting_id):
    m = db.session.get(Meeting, meeting_id) or abort(404)
    if not csvc.can_edit_meeting(current_user, m):
        abort(403)
    form = _meeting_form(m)
    if form.validate_on_submit():
        try:
            _save_meeting(form, m)
        except csvc.CollabError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            db.session.commit()
            flash("Meeting updated. Attendees have been told.", "success")
            return redirect(url_for("workspace.meeting", meeting_id=m.id))
    return render_template("workspace/meeting_form.html", form=form, m=m)


@bp.route("/meetings/<int:meeting_id>.ics")
@tenant_required
def meeting_ics(meeting_id):
    m = db.session.get(Meeting, meeting_id) or abort(404)
    if not (any(a.user_id == current_user.id for a in m.attendees) or csvc.can_edit_meeting(current_user, m)):
        abort(403)
    return Response(csvc.ics(m, current_user.organization.name), mimetype="text/calendar",
                    headers={"Content-Disposition": f"attachment; filename=meeting-{m.id}.ics"})
